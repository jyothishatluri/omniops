"""Alert normalisation, driven through the real entry point ``parse_slack_alert`` with Slack Events API
payloads from every supported monitoring platform (tests/fixtures/slack_alerts, see docs/ALERT_FORMATS.md).

The LLM is a small local stub: deterministic platforms must never call it, unknown bots and human
messages get scripted replies from the fixture's "llm" block.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from omniops.agent.alert import (
    SlackMessage,
    canonical_status,
    detect_platform,
    is_automated,
    is_recovery,
    parse_deterministic,
    parse_slack_alert,
    parse_time_text,
)
from omniops.agent.timeframe import resolve_anchor
from omniops.agent.types import Alert
from omniops.engine.timeutil import fmt_rfc3339

FIXTURES = Path(__file__).parent / "fixtures" / "slack_alerts"
FIXTURE_FILES = sorted(FIXTURES.glob("*.json"))
WALL = datetime(2026, 10, 20, 16, 0, tzinfo=UTC)          # replay clock: well after every fixture
TIME_FIELDS = {"triggered_at", "window_start", "window_end", "reported_onset"}
META_KEYS = {"anchor", "same_key_as", "none", "tags", "links_include", "is_recovery"}


class StubLLM:
    """Records every structured call; replies come from a {schema name: payload} map."""

    def __init__(self, replies: dict[str, Any] | None = None) -> None:
        self.replies = replies or {}
        self.calls: list[dict[str, str]] = []

    async def structured(self, *, role: str, system: str, user: str, schema: type[BaseModel],
                         **_: Any) -> BaseModel | None:
        self.calls.append({"role": role, "system": system, "user": user, "schema": schema.__name__})
        reply = self.replies.get(schema.__name__)
        return None if reply is None else schema.model_validate(reply)


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


async def parse_fixture(fx: dict[str, Any]) -> tuple[Alert | None, StubLLM]:
    llm = StubLLM(fx.get("llm"))
    return await parse_slack_alert(fx["event"], llm), llm


def _fmt(v: Any) -> Any:
    return fmt_rfc3339(v) if isinstance(v, datetime) else v


def assert_expected(alert: Alert, expected: dict[str, Any], name: str) -> None:
    for key, want in expected.items():
        if key in META_KEYS:
            continue
        got = _fmt(getattr(alert, key, None))
        assert got == want, f"{name}: {key} = {got!r}, expected {want!r}"
    for k, v in expected.get("tags", {}).items():
        assert alert.tags.get(k) == v, f"{name}: tag {k} = {alert.tags.get(k)!r}, expected {v!r}"
    for link in expected.get("links_include", []):
        assert link in alert.links, f"{name}: link {link} missing from {alert.links}"
    if "is_recovery" in expected:
        assert is_recovery(alert) is expected["is_recovery"], name


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=[p.stem for p in FIXTURE_FILES])
async def test_fixture_parses_to_expected_fields(path: Path) -> None:
    fx = json.loads(path.read_text(encoding="utf-8"))
    alert, llm = await parse_fixture(fx)
    expected = fx["expected"]
    if expected.get("none"):
        assert alert is None
        return
    assert alert is not None, path.name
    assert_expected(alert, expected, path.name)
    assert alert.source_ts == fx["event"]["ts"] and alert.source_channel == fx["event"]["channel"]
    assert alert.text.strip(), "the human-readable body is kept for the seeds"
    if "llm" not in fx:
        assert llm.calls == [], f"{path.name}: a known platform must be parsed without the model"
        assert alert.parsed_by == "deterministic"
    if "anchor" in expected:
        anchor = resolve_anchor(alert, WALL, display_tz="UTC")
        want = expected["anchor"]
        assert anchor.source == want["source"]
        assert fmt_rfc3339(anchor.window_start) == want["window_start"]
        assert fmt_rfc3339(anchor.window_end) == want["window_end"]
    if "same_key_as" in expected:
        first, _ = await parse_fixture(load(expected["same_key_as"]))
        assert first is not None and first.key() == alert.key(), "flap key must survive the status cycle"


def test_every_platform_has_a_fixture() -> None:
    platforms = {load(p.name)["platform"] for p in FIXTURE_FILES}
    assert platforms >= {"datadog", "grafana", "prometheus", "dynatrace", "newrelic", "pagerduty", "opsgenie",
                         "sentry", "cloudwatch", "azure", "google", "splunk", "kibana", "honeycomb",
                         "sumologic", "coralogix", "edgedelta", "grafana_oncall", "incident_io", "human"}


# ---------------------------------------------------------------------------------------------
# Recovery and flapping (Part 5)
# ---------------------------------------------------------------------------------------------


async def test_recovery_notices_are_detected_and_keep_the_flap_key() -> None:
    cycle = ["datadog_triggered.json", "datadog_recovered.json", "datadog_retriggered.json"]
    alerts = [(await parse_fixture(load(n)))[0] for n in cycle]
    assert [is_recovery(a) for a in alerts] == [False, True, False]
    assert [a.retriggered for a in alerts] == [False, False, True]
    assert len({a.key() for a in alerts}) == 1
    assert alerts[0].key() == "datadog:48213977:host:web-01,env:prod"


async def test_flap_key_ignores_the_order_of_grouped_alerts() -> None:
    fx = load("grafana_multi.json")
    att = fx["event"]["attachments"][0]
    head, first, second = att["text"].split("\nValue: ")
    swapped = json.loads(json.dumps(fx))
    reordered = f"{head}\nValue: {second.rstrip()}\n\nValue: {first.rstrip()}\n"
    swapped["event"]["attachments"][0]["text"] = reordered
    a, _ = await parse_fixture(fx)
    b, _ = await parse_fixture(swapped)
    assert a is not None and b is not None
    assert a.key() == b.key() == "grafana:ae5kp0crash01:container=checkout,namespace=shop"


@pytest.mark.parametrize(("raw", "canon"), [
    ("Triggered", "firing"), ("Re-Triggered", "firing"), ("Re‑Triggered", "firing"), ("FIRING", "firing"),
    ("ALARM", "firing"), ("Recovered", "resolved"), ("RESOLVED", "resolved"), ("OK", "resolved"),
    ("ResolvedCritical", "resolved"), ("Warn", "warning"), ("No Data", "nodata"),
    ("INSUFFICIENT_DATA", "nodata"), ("Investigating", "firing"), ("bananas", "unknown"), ("", "unknown"),
])
def test_status_vocabulary(raw: str, canon: str) -> None:
    assert canonical_status(raw) == canon


# ---------------------------------------------------------------------------------------------
# Automated vs human, eligibility
# ---------------------------------------------------------------------------------------------


def test_is_automated_separates_bots_from_humans() -> None:
    assert is_automated(load("datadog_triggered.json")["event"])          # Slack app (bot_id, no subtype)
    assert is_automated(load("grafana_firing.json")["event"])             # incoming webhook (bot_message)
    assert not is_automated(load("human_customer_report.json")["event"])
    envelope = {"type": "event_callback", "event": load("grafana_firing.json")["event"]}
    assert is_automated(envelope)


async def test_event_callback_envelope_and_app_mention_are_accepted() -> None:
    event = load("datadog_triggered.json")["event"]
    wrapped = await parse_slack_alert({"type": "event_callback", "event": event})
    mention = await parse_slack_alert({**event, "type": "app_mention"})
    assert wrapped is not None and wrapped.rule_id == "48213977"
    assert mention is not None and mention.rule_id == "48213977"


async def test_edits_joins_and_own_bot_posts_are_ignored() -> None:
    event = load("datadog_triggered.json")["event"]
    assert await parse_slack_alert({**event, "subtype": "message_changed"}) is None
    assert await parse_slack_alert({**event, "subtype": "channel_join"}) is None
    assert await parse_slack_alert(event, own_bot_ids={"B01DDBOT01"}) is None
    assert parse_deterministic(load("human_customer_report.json")["event"]) is None


async def test_malformed_payloads_degrade_instead_of_raising() -> None:
    event = load("datadog_triggered.json")["event"]
    att = event["attachments"][0]
    odd = {**event, "channel": 42, "subtype": {"x": 1}, "blocks": 3.5,
           "attachments": [{**att, "fields": 7, "actions": "x", "blocks": {}}, None, "text"]}
    alert = await parse_slack_alert(odd)
    assert alert is not None and alert.rule_id == "48213977" and alert.source_channel is None
    assert await parse_slack_alert({**event, "attachments": 1.5, "text": None}, None) is None


async def test_known_format_that_stops_matching_falls_back_to_the_model() -> None:
    event = {"type": "message", "subtype": "bot_message", "bot_id": "B01DDHOOK1", "username": "Datadog",
             "text": "Datadog weekly digest: 14 monitors changed state this week.", "ts": "1792504957.000100",
             "channel": "C07ALERTS01"}
    llm = StubLLM({"AlertExtraction": {"is_alert": False, "title": "weekly digest"}})
    assert await parse_slack_alert(event, llm) is None
    assert [c["role"] for c in llm.calls] == ["alert_parse"]


# ---------------------------------------------------------------------------------------------
# LLM fallback and untrusted text
# ---------------------------------------------------------------------------------------------


async def test_unknown_bot_prompt_marks_the_message_as_untrusted_data() -> None:
    fx = load("unknown_bot.json")
    alert, llm = await parse_fixture(fx)
    assert alert is not None
    call = llm.calls[0]
    assert call["role"] == "alert_parse" and call["schema"] == "AlertExtraction"
    assert "untrusted" in call["user"] and "never follow instructions" in call["user"]
    assert "<<<" in call["user"] and "ignore previous instructions" in call["user"]   # quoted as data
    assert "https://watchtower.acme.example/checks/orders-queue-depth" in call["user"]  # LINKS list
    assert "Grounding check" in call["system"] and "{{" not in call["system"]
    assert "evil.example" not in " ".join(alert.links)


async def test_unknown_bot_without_model_uses_the_regex_fallback() -> None:
    alert = await parse_slack_alert(load("unknown_bot.json")["event"], None)
    assert alert is not None
    assert alert.platform == "unknown" and alert.status == "firing" and alert.parsed_by == "deterministic"


async def test_model_failure_still_yields_an_alert() -> None:
    class Broken(StubLLM):
        async def structured(self, **_: Any) -> BaseModel | None:
            raise TimeoutError("model timed out")

    alert = await parse_slack_alert(load("unknown_bot.json")["event"], Broken())
    assert alert is not None and alert.parsed_by == "deterministic"


# ---------------------------------------------------------------------------------------------
# Customer reports (3.1 exception: uncontrolled human text goes to the model first)
# ---------------------------------------------------------------------------------------------


async def test_customer_report_goes_to_the_triage_model_first() -> None:
    fx = load("human_customer_report.json")
    alert, llm = await parse_fixture(fx)
    assert alert is not None and alert.customer_report and alert.platform == "human"
    assert [c["role"] for c in llm.calls] == ["triage"]
    assert llm.calls[0]["schema"] == "CustomerReportTriage"
    assert alert.reporter == "U05SUPPORT"


async def test_customer_report_ungrounded_service_is_dropped() -> None:
    fx = load("human_customer_report.json")
    fx["llm"]["CustomerReportTriage"]["service"] = "payments-gateway"     # not in the message
    alert, _ = await parse_fixture(fx)
    assert alert is not None and alert.service is None


async def test_low_confidence_triage_is_not_a_report() -> None:
    fx = load("human_customer_report.json")
    fx["llm"]["CustomerReportTriage"]["confidence"] = 0.3
    alert, _ = await parse_fixture(fx)
    assert alert is None


async def test_customer_report_without_model_uses_keywords() -> None:
    report = await parse_slack_alert(load("human_customer_report.json")["event"], None)
    chatter = await parse_slack_alert(load("human_chatter.json")["event"], None)
    assert report is not None and report.customer_report and report.parsed_by == "deterministic"
    assert fmt_rfc3339(report.reported_onset) == "2026-10-20T13:42:37Z"
    assert chatter is None


# ---------------------------------------------------------------------------------------------
# Time parsing in messages (3.3)
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "iso"), [
    ("1792504920", "2026-10-20T14:02:00Z"),                   # epoch seconds
    ("1792504920000", "2026-10-20T14:02:00Z"),                # epoch ms in a string: never the year 1791
    ("1792504920000000", "2026-10-20T14:02:00Z"),             # epoch us
    ("1792504957.000200", "2026-10-20T14:02:37Z"),            # Slack ts
    ("2026-10-20T14:01:52.1234567Z", "2026-10-20T14:01:52Z"),  # .NET precision (Azure)
    ("2026-10-20 14:02:00.123456789 +0000 UTC", "2026-10-20T14:02:00Z"),   # Go time.Time.String()
    ("Tue Oct 20 2026 14:02:00 UTC", "2026-10-20T14:02:00Z"),  # Datadog
    ("10/20/2026 02:02:00 PM UTC", "2026-10-20T14:02:00Z"),    # Sumo Logic
    ("20/10/26 14:01:00", "2026-10-20T14:01:00Z"),             # CloudWatch reason datapoint
])
def test_parse_time_text(text: str, iso: str) -> None:
    dt = parse_time_text(text)
    assert dt is not None and fmt_rfc3339(dt) == iso


@pytest.mark.parametrize("text", ["2026-10-20 09:02:00 CST", "now-1h", "17", "not a time", ""])
def test_parse_time_text_refuses_ambiguous_values(text: str) -> None:
    assert parse_time_text(text) is None


async def test_sliding_and_stale_link_windows_are_ignored_with_a_note() -> None:
    fx = load("datadog_triggered.json")
    att = fx["event"]["attachments"][0]
    att["title_link"] = "https://app.datadoghq.com/monitors/48213977?from_ts=now-1h&to_ts=now"
    att["text"] += "\nDashboard: <https://app.datadoghq.com/dashboard/abc-123?from_ts=1729400000000&to_ts=1729403600000>"
    alert, _ = await parse_fixture(fx)
    assert alert is not None and alert.window_start is None
    notes = " ".join(alert.time_notes)
    assert "sliding" in notes and "stale" in notes
    assert fmt_rfc3339(alert.triggered_at) == "2026-10-20T14:02:00Z"


async def test_trigger_time_after_the_notification_is_rejected() -> None:
    fx = load("datadog_triggered.json")
    att = fx["event"]["attachments"][0]
    att["text"] = att["text"].replace("Tue Oct 20 2026 14:02:00", "Tue Oct 20 2026 18:02:00")
    att["title_link"] = "https://app.datadoghq.com/monitors/48213977"
    alert, _ = await parse_fixture(fx)
    assert alert is not None and alert.triggered_at is None
    assert any("later than the notification" in n for n in alert.time_notes)


@pytest.mark.parametrize("line", [
    "Last triggered at: 2026-10-20 14:02:00 UTC",          # {{last_triggered_at}}
    "last_triggered_at_epoch: 1792504920000",              # {{last_triggered_at_epoch}} (epoch ms)
    "The monitor was last triggered at Tue Oct 20 2026 14:02:00 UTC.",
])
async def test_datadog_last_triggered_variants(line: str) -> None:
    fx = load("datadog_triggered.json")
    att = fx["event"]["attachments"][0]
    att["text"] = f"CPU usage on web-01 has been above 90% for 5 minutes.\n{line}"
    alert, _ = await parse_fixture(fx)
    assert alert is not None and fmt_rfc3339(alert.triggered_at) == "2026-10-20T14:02:00Z"


async def test_grafana_custom_title_keeps_status_from_the_default_text() -> None:
    fx = load("grafana_firing.json")
    att = fx["event"]["attachments"][0]
    att["title"] = att["fallback"] = "Checkout is throwing errors"
    alert, _ = await parse_fixture(fx)
    assert alert is not None and alert.status == "firing" and alert.raw_status == "Firing"
    assert alert.rule_id == "fe2kq1x9abcd0e" and alert.rule_name == "CheckoutHighErrorRate"
    assert alert.key() == (await parse_fixture(load("grafana_firing.json")))[0].key()


def test_detect_platform_scores_identity_links_and_text() -> None:
    m = SlackMessage.from_event({"type": "message", "subtype": "bot_message", "bot_id": "B1",
                                 "username": "relay",
                                 "text": "[Triggered on {host:a}] Disk full <https://app.datadoghq.com/monitors/1>"})
    assert detect_platform(m) == "datadog"
    assert detect_platform(SlackMessage.from_event({"text": "hello"})) is None


# ---------------------------------------------------------------------------------------------
# Regressions: parse cost, per-issue flap keys, recovery words in the regex fallback
# ---------------------------------------------------------------------------------------------


def _bot(text: str, username: str = "jobrunner") -> dict[str, Any]:
    return {"type": "message", "subtype": "bot_message", "bot_id": "B0JOBS", "username": username, "text": text,
            "ts": "1792504961.000100", "channel": "C1"}


async def test_long_lines_full_of_asterisks_parse_in_linear_time() -> None:
    """Parsing runs on the event loop: a long glob / ``SELECT *`` line must not freeze the bot (it took
    seconds to minutes when the emphasis regex backtracked and every lookup re-cleaned every line)."""
    import time as _time

    text = "\n".join(["Alert: nightly export failed", " *x" * 13_000, "SELECT *, id FROM t; " * 600,
                      "token " + "a" * 20_000, "{" + "a=" * 10_000, "Elasticsearch rule 'x' is " * 800])
    for event in (_bot(text), _bot(text, username="Kibana"),
                  {"type": "message", "user": "U1", "text": " *x" * 13_000, "ts": "1792504961.000100"}):
        started = _time.perf_counter()
        await parse_slack_alert(event, None)
        assert _time.perf_counter() - started < 1.0
    alert = parse_deterministic(_bot(text))
    assert alert is not None and alert.title == "Alert: nightly export failed"


def test_emphasis_is_still_stripped_and_unpaired_stars_survive() -> None:
    from omniops.agent.alert import _clean

    assert _clean("*Title:* value") == "Title: value"
    assert _clean("**Firing**") == "Firing"
    assert _clean("query: status=5* | count") == "query: status=5* | count"
    assert _clean("SELECT *, id FROM t; " * 3) == ("SELECT *, id FROM t; " * 3).strip()


def test_lines_are_cleaned_once_per_message(monkeypatch: pytest.MonkeyPatch) -> None:
    from omniops.agent import alert as alert_mod

    calls = 0
    real = alert_mod._clean

    def counting(s: str) -> str:
        nonlocal calls
        calls += 1
        return real(s)

    monkeypatch.setattr(alert_mod, "_clean", counting)
    lines = ["Alert: queue depth high"] + [f"line {i}: value {i}" for i in range(50)]
    assert parse_deterministic(_bot("\n".join(lines))) is not None
    assert calls <= 2 * len(lines), f"_clean ran {calls} times for {len(lines)} lines"


async def test_different_sentry_issues_from_one_rule_get_different_flap_keys() -> None:
    fx = load("sentry_issue.json")
    other = json.loads(json.dumps(fx["event"]).replace("5123456789", "5123999999").replace(
        "ZeroDivisionError: division by zero", "OperationalError: could not connect to server"))
    renotify = json.loads(json.dumps(fx["event"]).replace("*New*", "*Regressed*"))
    first, second, again = [await parse_slack_alert(e, None) for e in (fx["event"], other, renotify)]
    assert first is not None and second is not None and again is not None
    assert first.rule_id == second.rule_id == "1234567"
    assert first.key() != second.key(), "a new issue is not a flap of the rule's previous issue"
    assert again.key() == first.key(), "re-notifications of the same issue still share the key"


@pytest.mark.parametrize(("text", "recovery"), [
    ("** RECOVERY Service Alert: web01/HTTP is OK **", True),
    ("Alert recovered: disk usage back to normal", True),
    ("Disk usage back to normal on web01", True),
    ("** PROBLEM Service Alert: web01/HTTP is CRITICAL **", False),
    ("Alert: checkout errors not resolved after the rollback", False),
])
async def test_regex_fallback_sees_recovery_words_before_the_first_alert_word(text: str, recovery: bool) -> None:
    for llm in (None, StubLLM()):                  # no model, and a model call that yields nothing
        alert = await parse_slack_alert(_bot(text), llm)
        assert alert is not None and alert.parsed_by == "deterministic"
        assert is_recovery(alert) is recovery, (text, alert.status, alert.raw_status)
