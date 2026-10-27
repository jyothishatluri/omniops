"""SQL engine behaviour through its real entry point (SQLEngine.query) against a mocked Prometheus."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import duckdb
import httpx
import pytest
import respx

from omniops.engine import timeutil
from omniops.engine.catalog import Catalog
from omniops.engine.engine import MissingFilterError, SQLEngine, SQLExecutionError, UnknownTableError
from omniops.engine.pushdown import SQLAnalysisError

TRIGGER = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _matrix(start: int, end: int, step: int, base: float, spike_at: int | None = None) -> dict:
    values = []
    for t in range(start, end + 1, step):
        v = base * 10 if spike_at is not None and t >= spike_at else base
        values.append([t, str(v)])
    return {"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": {"job": "api", "instance": "a:9090"}, "values": values}]}}


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> SQLEngine:
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom.test")
    cat = Catalog.from_env(only=["prometheus"])
    anchor = timeutil.TimeAnchor.for_trigger(TRIGGER, wall_now=TRIGGER + timedelta(hours=1))
    return SQLEngine(cat, anchor)


@respx.mock
async def test_range_query_pushdown_and_digest(engine: SQLEngine) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        calls.append(p)
        return httpx.Response(200, json=_matrix(int(p["start"]), int(p["end"]), int(p["step"]), 1.0,
                                                spike_at=int(TRIGGER.timestamp()) - 300))

    respx.get("http://prom.test/api/v1/query_range").mock(side_effect=handler)
    res = await engine.query(
        "SELECT ts, labels->>'job' AS job, value FROM prometheus_query_range "
        "WHERE query = 'sum by (job) (rate(errors_total[5m]))' ORDER BY ts")
    assert len(calls) == 1
    # Default window = trigger window: 15 min before to 5 min after the trigger.
    assert int(calls[0]["start"]) == int((TRIGGER - timedelta(minutes=15)).timestamp())
    assert int(calls[0]["end"]) == int((TRIGGER + timedelta(minutes=5)).timestamp())
    assert calls[0]["query"] == "sum by (job) (rate(errors_total[5m]))"
    assert res.total_rows > 10 and res.columns == ["ts", "job", "value"]
    assert any("onset" in d for d in res.digests), res.digests
    assert "[digest]" in res.render()


@respx.mock
async def test_baseline_and_incident_windows_in_one_statement(engine: SQLEngine) -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        seen.append((int(p["start"]), int(p["end"])))
        return httpx.Response(200, json=_matrix(int(p["start"]), int(p["end"]), int(p["step"]), float(len(seen))))

    respx.get("http://prom.test/api/v1/query_range").mock(side_effect=handler)
    sql = """
      WITH incident AS (
        SELECT avg(value) AS v FROM prometheus_query_range
        WHERE query = 'up' AND ts BETWEEN TIMESTAMP '2026-10-01 11:45:00+00' AND TIMESTAMP '2026-10-01 12:05:00+00'),
      baseline AS (
        SELECT avg(value) AS v FROM prometheus_query_range
        WHERE query = 'up' AND ts BETWEEN TIMESTAMP '2026-09-30 11:45:00+00' AND TIMESTAMP '2026-09-30 12:05:00+00')
      SELECT incident.v AS incident, baseline.v AS baseline FROM incident, baseline"""
    res = await engine.query(sql)
    assert len(seen) == 2
    assert {s for s, _ in seen} == {int(datetime(2026, 10, 1, 11, 45, tzinfo=UTC).timestamp()),
                                    int(datetime(2026, 9, 30, 11, 45, tzinfo=UTC).timestamp())}
    assert res.total_rows == 1


@respx.mock
async def test_now_is_pinned_to_anchor(engine: SQLEngine) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        return httpx.Response(200, json=_matrix(int(calls[-1]["start"]), int(calls[-1]["end"]), 60, 1.0))

    respx.get("http://prom.test/api/v1/query_range").mock(side_effect=handler)
    await engine.query("SELECT count(*) FROM prometheus_query_range WHERE query='up' AND ts >= now() - INTERVAL 2 HOUR")
    assert int(calls[0]["end"]) == int(engine.anchor.now.timestamp())
    assert int(calls[0]["start"]) == int((engine.anchor.now - timedelta(hours=2)).timestamp())


async def test_missing_required_filter_teaches(engine: SQLEngine) -> None:
    with pytest.raises(MissingFilterError) as ei:
        await engine.query("SELECT * FROM prometheus_query_range")
    assert "query" in str(ei.value) and "Example" in str(ei.value)


async def test_unknown_table_suggests(engine: SQLEngine) -> None:
    with pytest.raises(UnknownTableError) as ei:
        await engine.query("SELECT * FROM prometheus_range_queries WHERE query='up'")
    assert "prometheus_query_range" in str(ei.value)


@respx.mock
async def test_empty_result_positive_control(engine: SQLEngine) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        if int(p["end"]) > int((TRIGGER - timedelta(minutes=15)).timestamp()):
            return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": []}})
        return httpx.Response(200, json=_matrix(int(p["start"]), int(p["end"]), int(p["step"]), 1.0))

    respx.get("http://prom.test/api/v1/query_range").mock(side_effect=handler)
    res = await engine.query("SELECT * FROM prometheus_query_range WHERE query='pool_timeouts_total'")
    assert res.total_rows == 0
    assert res.empty_verdict == "absent"
    assert any("positive control" in n for n in res.notes)


@respx.mock
async def test_empty_everywhere_is_inconclusive(engine: SQLEngine) -> None:
    respx.get("http://prom.test/api/v1/query_range").mock(return_value=httpx.Response(
        200, json={"status": "success", "data": {"resultType": "matrix", "result": []}}))
    res = await engine.query("SELECT * FROM prometheus_query_range WHERE query='nonexistent_metric'")
    assert res.empty_verdict == "inconclusive"
    assert "INCONCLUSIVE" in res.render()


@respx.mock
async def test_error_in_body_is_loud(engine: SQLEngine) -> None:
    respx.get("http://prom.test/api/v1/query_range").mock(return_value=httpx.Response(
        200, json={"status": "error", "errorType": "bad_data", "error": "parse error at char 5"}))
    with pytest.raises(Exception) as ei:
        await engine.query("SELECT * FROM prometheus_query_range WHERE query='sum(('")
    assert "parse error" in str(ei.value)


@respx.mock
async def test_filter_only_time_column_is_stripped(engine: SQLEngine) -> None:
    respx.get("http://prom.test/api/v1/series").mock(return_value=httpx.Response(200, json={
        "status": "success", "data": [{"__name__": "up", "job": "api"}, {"__name__": "up", "job": "db"}]}))
    res = await engine.query("SELECT labels->>'job' AS job FROM prometheus_series WHERE match = 'up' "
                             "AND ts >= TIMESTAMP '2026-10-01 11:00:00+00'")
    assert sorted(r[0] for r in res.rows) == ["api", "db"]


@respx.mock
async def test_in_list_fans_out(engine: SQLEngine) -> None:
    route = respx.get(url__regex=r"http://prom.test/api/v1/label/.*/values").mock(
        side_effect=lambda req: httpx.Response(200, json={"status": "success", "data": [req.url.path.split("/")[-2]]}))
    res = await engine.query("SELECT label, value FROM prometheus_label_values WHERE label IN ('job', 'instance')")
    assert route.call_count == 2
    assert sorted(r[1] for r in res.rows) == ["instance", "job"]


async def test_future_window_rejected(engine: SQLEngine) -> None:
    with pytest.raises(Exception) as ei:
        await engine.query("SELECT * FROM prometheus_query_range WHERE query='up' "
                           "AND ts >= TIMESTAMP '2030-01-01 00:00:00+00'")
    assert "future" in str(ei.value)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM read_csv('/etc/hosts')",
    "SELECT * FROM read_text('/etc/hosts')",
    "SELECT * FROM read_json_auto('https://example.com/x.json')",
])
async def test_model_sql_cannot_touch_files_or_network(engine: SQLEngine, sql: str) -> None:
    with pytest.raises(SQLExecutionError) as ei:
        await engine.query(sql)
    assert "Permission Error" in str(ei.value) and "localhost" not in str(ei.value)


async def test_model_sql_cannot_reenable_external_access(engine: SQLEngine) -> None:
    with pytest.raises(SQLAnalysisError):
        await engine.query("SET enable_external_access = true")
    cur = engine.con.cursor()
    with pytest.raises(duckdb.InvalidInputException):
        cur.execute("SET enable_external_access = true")
