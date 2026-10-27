"""Regressions for the cross-area follow-ups of the review: retention prose, computed-table sources,
positive controls over empty-fetch aggregates, private /investigate memory, thread clipping."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
import respx
from test_engine_fixes import _engine, _streams, _svc

from omniops.agent.context import keep_ends
from omniops.engine.timeutil import parse_retention


@pytest.mark.parametrize("text,days", [
    ("35d (Log Monitoring Classic)", 35), ("150 days by default; configurable", 150), ("10d", 10),
    ("18 months (not configurable)", 540), ("15mo (default_metrics)", 450),
])
def test_retention_takes_a_leading_duration(text: str, days: int) -> None:
    assert parse_retention(text) == timedelta(days=days)


@pytest.mark.parametrize("text", ["set by ILM / data stream lifecycle", "1-3y (Davis problems)",
                                  "-retentionPeriod (default 7d)", "per tenant contract", "", None])
def test_retention_prose_never_clamps(text: str | None) -> None:
    assert parse_retention(text) is None


@respx.mock
async def test_computed_patterns_filter_on_the_configured_source_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOKI_URL", "http://loki.test")
    respx.get("http://loki.test/loki/api/v1/query_range").mock(side_effect=lambda req: httpx.Response(
        200, json=_streams(_svc(dict(req.url.params)["query"]), int(dict(req.url.params)["end"]), 5,
                           lambda i: f"error connecting to db host=1.2.3.{i}")))
    res = await _engine("loki", positive_controls=False).query(
        "SELECT template, source, table_name FROM omni_log_patterns WHERE service = 'cart' AND source = 'loki'")
    assert res.total_rows >= 1, res.notes          # DuckDB re-applies source = 'loki': it must match
    assert {r[1] for r in res.rows} == {"loki"} and all(r[2].startswith("loki_") for r in res.rows)


@respx.mock
async def test_count_over_an_empty_fetch_gets_a_positive_control(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOKI_URL", "http://loki.test")

    respx.get("http://loki.test/loki/api/v1/query_range").mock(return_value=httpx.Response(
        200, json={"status": "success", "data": {"resultType": "streams", "result": []}}))
    res = await _engine("loki").query("SELECT count(*) AS n FROM loki_logs WHERE service = 'cart'")
    assert res.total_rows == 1 and res.rows[0][0] == 0
    assert res.empty_verdict == "inconclusive"     # the control ran (and was also empty): not a silent 0
    assert any("positive control" in n for n in res.notes), res.notes


def test_keep_ends_keeps_the_root_and_the_newest_replies() -> None:
    thread = "ROOT alert text\n" + "\n".join(f"reply {i}" for i in range(2000)) + "\nNEWEST finding"
    out = keep_ends(thread, 500)
    assert out.startswith("ROOT alert text") and out.endswith("NEWEST finding") and "omitted" in out
    assert len(out) <= 520


async def test_private_investigation_is_remembered_only_when_shared() -> None:
    from omniops.slack.app import _call_investigate

    calls: list[dict] = []

    class NewRunner:
        async def investigate(self, alert, deps, *, status=None, thread=None, private=False):
            calls.append({"private": private})

    class OldRunner:
        async def investigate(self, alert, deps, *, status=None, thread=None):
            calls.append({"private": "n/a"})

    await _call_investigate(NewRunner(), None, None, private=True)
    await _call_investigate(OldRunner(), None, None, private=True)
    assert calls == [{"private": True}, {"private": "n/a"}]


@respx.mock
async def test_404_teaches_that_the_id_does_not_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    from omniops.engine.catalog import Catalog
    from omniops.engine.engine import SQLEngine
    from omniops.engine.http import NotFoundError

    monkeypatch.setenv("PROMETHEUS_URL", "http://prom.test")
    respx.get(url__regex=r"http://prom.test/api/v1/label/.*/values").mock(
        return_value=httpx.Response(404, text="404 page not found"))
    with pytest.raises(NotFoundError) as ei:
        await SQLEngine(Catalog.from_env(only=["prometheus"])).query(
            "SELECT value FROM prometheus_label_values WHERE label = 'nope'")
    assert "does not exist" in ei.value.lesson and "schema" in ei.value.lesson
