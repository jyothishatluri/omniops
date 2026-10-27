"""Runtime CLI (catalog, describe, sql, selftest, serve, ask, investigate), the Slack app manifest and
.env.example, driven through the real argument parser. HTTP is mocked with respx (Thanos API)."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
import yaml

from omniops import cli_runtime
from omniops.agent.context import Deps
from omniops.agent.types import Alert, ConclusionCard, RunResult
from omniops.engine.catalog import Catalog
from omniops.llm.scripted import ScriptedLLM
from omniops.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
THANOS = "http://thanos.test"
MATRIX = {"status": "success", "data": {"resultType": "matrix", "result": [
    {"metric": {"job": "api"}, "values": [[1760958000, "1"], [1760961600, "1"]]}]}}
VECTOR = {"status": "success", "data": {"resultType": "vector", "result": [
    {"metric": {"job": "api"}, "value": [1760961600, "1"]}]}}


def run_cli(argv: list[str]) -> int:
    """``omniops <argv>`` without loading .env (tests must not pick up a developer's secrets)."""
    parser = argparse.ArgumentParser(prog="omniops")
    cli_runtime.register(parser.add_subparsers(dest="cmd", required=True))
    args = parser.parse_args(argv)
    rc = args.func(args)
    if asyncio.iscoroutine(rc):
        rc = asyncio.run(rc)
    return int(rc or 0)


@pytest.fixture(scope="module")
def thanos_catalog() -> Catalog:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("THANOS_URL", THANOS)
        for var in ("THANOS_TOKEN", "THANOS_USERNAME", "THANOS_TENANT"):
            mp.delenv(var, raising=False)
        return Catalog.from_env(only=["thanos"])


@pytest.fixture
def thanos(thanos_catalog: Catalog, monkeypatch: pytest.MonkeyPatch) -> Catalog:
    """Every ``Catalog.from_env`` in the CLI returns the Thanos-only catalog (manifests load once)."""
    monkeypatch.setattr(Catalog, "from_env", classmethod(lambda cls, *a, **k: thanos_catalog))
    return thanos_catalog


def test_selftest_reports_pass_and_fail_loudly(thanos: Catalog, capsys: pytest.CaptureFixture[str]) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{THANOS}/api/v1/query_range").mock(return_value=httpx.Response(200, json=MATRIX))
        mock.get(f"{THANOS}/api/v1/query").mock(return_value=httpx.Response(
            400, json={"status": "error", "errorType": "bad_data", "error": "parse error: unexpected end"}))
        rc = run_cli(["selftest", "--source", "thanos"])
    out = capsys.readouterr().out
    assert rc == 1
    assert re.search(r"PASS thanos\.thanos_query_range", out)
    assert re.search(r"FAIL thanos\.thanos_query: SELECT value FROM thanos_query", out)
    assert "SELFTEST FAILED" in out


def test_selftest_all_green(thanos: Catalog, capsys: pytest.CaptureFixture[str]) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{THANOS}/api/v1/query_range").mock(return_value=httpx.Response(200, json=MATRIX))
        mock.get(f"{THANOS}/api/v1/query").mock(return_value=httpx.Response(200, json=VECTOR))
        rc = run_cli(["selftest"])
    assert rc == 0 and "SELFTEST PASSED: 2 test(s)" in capsys.readouterr().out


async def test_selftest_zero_tests_and_unknown_sources_fail(capsys: pytest.CaptureFixture[str]) -> None:
    assert await cli_runtime.run_selftest(Catalog()) == 2
    assert "SELFTEST RAN NOTHING" in capsys.readouterr().out
    empty = Catalog(unavailable={"datadog": ["DD_API_KEY", "DD_APP_KEY"]})
    assert await cli_runtime.run_selftest(empty, ["datadog"]) == 1
    out = capsys.readouterr().out
    assert "FAIL datadog: not configured (missing DD_API_KEY, DD_APP_KEY)" in out


def test_sql_pins_now_to_the_anchor(thanos: Catalog, capsys: pytest.CaptureFixture[str]) -> None:
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{THANOS}/api/v1/query_range").mock(return_value=httpx.Response(200, json=MATRIX))
        rc = run_cli(["sql", "SELECT ts, value FROM thanos_query_range WHERE query = 'up'",
                      "--at", "2026-10-20T12:00:00Z", "--window", "1h"])
    out = capsys.readouterr().out
    assert rc == 0, out
    params = route.calls.last.request.url.params
    end = int(datetime(2026, 10, 20, 12, tzinfo=UTC).timestamp())
    assert int(float(params["end"])) == end and int(float(params["start"])) == end - 3600
    assert "'now' means 2026-10-20T12:00:00Z" in out and "[result] 2 row(s)" in out


def test_sql_error_prints_the_lesson(thanos: Catalog, capsys: pytest.CaptureFixture[str]) -> None:
    rc = run_cli(["sql", "SELECT * FROM no_such_table"])
    out = capsys.readouterr().out
    assert rc == 1 and "ERROR" in out and "lesson: search the catalog" in out


def test_catalog_and_describe(thanos: Catalog, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cli(["catalog", "range query"]) == 0
    assert "thanos_query_range" in capsys.readouterr().out
    assert run_cli(["describe", "thanos_query"]) == 0
    assert "TABLE thanos_query" in capsys.readouterr().out
    assert run_cli(["describe", "thanos_nope"]) == 1


def test_serve_needs_both_slack_tokens(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-not-a-real-token")
    assert run_cli(["serve"]) == 2
    out = capsys.readouterr().out
    assert "SLACK_BOT_TOKEN" in out and "xapp-not-a-real-token" not in out


# ---------------------------------------------------------------------------------------------
# ask / investigate through the simulator (agent flows stubbed)
# ---------------------------------------------------------------------------------------------


class CliRunner:
    def __init__(self, anchor: Any = None) -> None:
        self.anchor = anchor

    async def investigate(self, alert: Alert, deps: Deps, *, status: Any = None, thread: str | None = None
                          ) -> RunResult:
        card = ConclusionCard(title="Checkout 5xx spike", verdict="Deploy v2.4.1 broke the payment client",
                              confidence=0.8, causal_level="trigger")
        return RunResult(run_id="inv", kind="investigation", card=card,
                         answer_markdown=f"*Root cause* payment client timeout ({alert.platform} alert).")

    async def answer(self, question: str, deps: Deps, *, thread: str | None = None, status: Any = None
                     ) -> RunResult:
        return RunResult(run_id="chat", kind="chat", answer_markdown=f"Answer to: {question}")


@pytest.fixture
def stubbed_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    from omniops.slack import simulator

    deps = Deps(catalog=Catalog(), llm=ScriptedLLM(), settings=Settings(), memory=None)
    monkeypatch.setattr(cli_runtime, "make_deps", lambda **_: deps)
    monkeypatch.setattr(simulator, "AgentRunner", CliRunner)


def test_ask_prints_the_slack_answer(stubbed_runtime: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cli(["ask", "what is the p95 latency of checkout?"]) == 0
    assert "Answer to: what is the p95 latency of checkout?" in capsys.readouterr().out


def test_investigate_alert_file_prints_card_and_report(stubbed_runtime: None, tmp_path: Path,
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    title = "[FIRING:1] Platform alerts (HighErrorRate checkout)"
    event = {"username": "Grafana", "text": "", "attachments": [{
        "title": title, "title_link": "http://grafana.test/alerting/list", "footer": "Grafana v13.2.3",
        "text": "**Firing**\n\nValue: A=12.5\nLabels:\n - alertname = HighErrorRate\n - service = checkout\n"}]}
    f = tmp_path / "alert.json"
    f.write_text(json.dumps(event))
    assert run_cli(["investigate", "--alert-file", str(f)]) == 0
    out = capsys.readouterr().out
    assert "Deploy v2.4.1 broke the payment client" in out and "payment client timeout (grafana alert)" in out


def test_investigate_replays_a_captured_event_into_the_alert_channel(stubbed_runtime: None,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    fixture = ROOT / "tests" / "fixtures" / "slack_alerts" / "datadog_triggered.json"
    assert json.loads(fixture.read_text())["event"]["channel"] == "C07ALERTS01"     # a real capture's channel
    assert run_cli(["investigate", "--alert-file", str(fixture)]) == 0
    out = capsys.readouterr().out
    assert "payment client timeout (datadog alert)" in out and "No investigation ran" not in out


def test_investigate_rejects_a_non_object_file(tmp_path: Path) -> None:
    f = tmp_path / "alert.json"
    f.write_text("[1, 2]")
    assert run_cli(["investigate", "--alert-file", str(f)]) == 2


# ---------------------------------------------------------------------------------------------
# App manifest and .env.example
# ---------------------------------------------------------------------------------------------


def test_slack_app_manifest_has_scopes_events_and_socket_mode() -> None:
    m = yaml.safe_load((ROOT / "slack_app_manifest.yaml").read_text())
    scopes = set(m["oauth_config"]["scopes"]["bot"])
    assert {"app_mentions:read", "channels:history", "groups:history", "im:history", "mpim:history",
            "channels:read", "groups:read", "im:read", "im:write", "chat:write", "chat:write.public", "commands",
            "reactions:read", "reactions:write", "users:read", "files:write"} <= scopes
    assert set(m["settings"]["event_subscriptions"]["bot_events"]) == {
        "app_mention", "message.channels", "message.groups", "message.im"}
    assert m["settings"]["socket_mode_enabled"] is True and m["settings"]["interactivity"]["is_enabled"] is True
    assert [c["command"] for c in m["features"]["slash_commands"]] == ["/investigate"]


def test_env_example_lists_runtime_variables_without_values() -> None:
    text = (ROOT / ".env.example").read_text()
    names = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", text, re.MULTILINE))
    read_by_code = set()
    for src in ("settings.py", "llm/models.py", "engine/catalog.py", "engine/loader.py", "engine/http.py"):
        read_by_code |= set(re.findall(r"\"((?:OMNIOPS|MODEL)_[A-Z0-9_]+)\"",
                                       (ROOT / "src/omniops" / src).read_text()))
    read_by_code |= {"SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_USER_TOKEN", "OMNIOPS_MODEL_HEAVY",
                     "OMNIOPS_MODEL_MID", "OMNIOPS_MODEL_FAST"}
    assert read_by_code <= names, sorted(read_by_code - names)
    declared: set[str] = set()                   # every source credential/setting a manifest declares
    for f in [*(ROOT / "src/omniops/sources/manifests").glob("*.yaml"),
              *(ROOT / "src/omniops/sources/templates").glob("*.yaml")]:
        m = yaml.safe_load(f.read_text()) or {}
        entries = [*(m.get("env") or []), *((m.get("append") or {}).get("env") or [])]
        declared |= {str(e["name"] if isinstance(e, dict) else e) for e in entries} | set(m.get("required_env") or [])
    assert declared and declared <= names, sorted(declared - names)
    secrets = re.findall(r"^([A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD))=(\S*)", text, re.MULTILINE)
    assert secrets and all(value == "" for _, value in secrets), "no secret values in the example"


def test_settings_read_watchdog_grace_and_display_zone_and_nest_the_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNIOPS_WATCHDOG_GRACE_S", "45")
    monkeypatch.setenv("OMNIOPS_DISPLAY_TZ", "America/Chicago")
    monkeypatch.setenv("OMNIOPS_INVESTIGATION_BUDGET_S", "600")
    monkeypatch.setenv("OMNIOPS_REPORT_RESERVE_S", "60")
    monkeypatch.setenv("OMNIOPS_REPORT_CALL_S", "300")
    s = Settings.from_env()
    assert s.watchdog_grace_s == 45 and s.display_tz == "America/Chicago"
    assert s.investigation_guard_s == 600 + (300 - 60) + 45          # the report call's own allowance (D25)
    assert s.chat_guard_s == s.chat_budget_s + 45
