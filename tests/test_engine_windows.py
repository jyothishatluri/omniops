"""Window arithmetic and result honesty through the real entry point (SQLEngine.query), with
respx-mocked Loki / Prometheus APIs.

- Step-sized buckets ([$__auto] / [$__interval]) tile exactly the requested window: a series that
  starts at the window start counts the step BEFORE the window (one extra step of data).
- The [window] line states the effective step and the span the buckets actually cover.
- Grafana's interval macros are expanded in PromQL like in LogQL.
- A PromQL scalar result is one row; an empty vector is no row.
- NaN/Inf values are announced, and a known PromQL error carries its fix.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from omniops.engine import http, templating, timeutil, transform
from omniops.engine.catalog import Catalog
from omniops.engine.engine import SQLEngine
from omniops.engine.loader import load_all_manifests, table_index
from omniops.engine.results import QueryResult

TRIGGER = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
WIN_START = TRIGGER - timedelta(minutes=15)  # trigger window: 15 min before -> 5 min after
WIN_END = TRIGGER + timedelta(minutes=5)
FIXTURES = Path(__file__).resolve().parents[1] / "src" / "omniops" / "sources" / "fixtures" / "prometheus"


def _engine(*only: str) -> SQLEngine:
    anchor = timeutil.TimeAnchor.for_trigger(TRIGGER, wall_now=TRIGGER + timedelta(hours=1))
    return SQLEngine(Catalog.from_env(only=list(only)), anchor, positive_controls=False)


def _matrix(start: float, end: float, step: float, value: str = "1") -> dict:
    values = []
    t = start
    while t <= end + 1e-9:
        values.append([t, value])
        t += step
    return {"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": {"service_name": "checkout"}, "values": values}]}}


# --- the bucket grid --------------------------------------------------------------------------------


def test_a_day_at_five_minute_steps_is_288_buckets_starting_one_step_in() -> None:
    end = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)
    start = end - timedelta(hours=24)
    g = timeutil.bucket_grid(start, end, 300)
    assert g.points == 288
    assert g.start == start + timedelta(seconds=300)
    assert g.end == end and g.cover_start == start and g.exact
    # The points really are the grid the API evaluates: start + k*step up to end.
    assert g.start + timedelta(seconds=(g.points - 1) * 300) == end


def test_short_and_ragged_windows_say_what_the_buckets_cover() -> None:
    end = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    short = timeutil.bucket_grid(end - timedelta(seconds=100), end, 300)
    assert (short.points, short.start) == (1, end)  # one point at the window end
    assert short.cover_start == end - timedelta(seconds=300) and not short.exact
    assert "shorter than one step" in short.describe()

    ragged = timeutil.bucket_grid(end - timedelta(seconds=1207), end, 10)
    assert ragged.points == 120 and ragged.cover_start == end - timedelta(seconds=1200)
    assert "first 7s of the window are not counted" in ragged.describe()


def test_time_ctx_offers_bucket_start_and_records_the_grid_a_template_used() -> None:
    end = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    t = http.time_ctx(end - timedelta(hours=1), end)
    assert t["step_s"] == 15 and t["bucket_start"] == end - timedelta(hours=1) + timedelta(seconds=15)
    assert t["bucket_points"] == 240 and t["bucket_cover_start"] == end - timedelta(hours=1)
    assert "buckets_used" not in t  # nothing recorded until a template asks for the grid
    out = templating.render_value("{{ time.buckets(filters.step).start | epoch_s }}", {"time": t, "filters": {"step": 60}})
    assert out == int((end - timedelta(minutes=59)).timestamp())
    assert t["buckets_used"].step_s == 60


# --- Loki: step-sized buckets tile the window, shape-only queries keep the window start -------------


@respx.mock
async def test_loki_count_buckets_tile_the_window_and_the_window_line_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOKI_URL", "http://loki.test")
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        calls.append(p)
        return httpx.Response(200, json=_matrix(int(p["start"]) / 1e9, int(p["end"]) / 1e9, float(p["step"])))

    respx.get("http://loki.test/loki/api/v1/query_range").mock(side_effect=handler)
    res = await _engine("loki").query(
        "SELECT count(*) AS buckets, sum(value) AS lines, min(ts) AS first_ts, max(ts) AS last_ts FROM loki_query_range "
        "WHERE query = 'sum(count_over_time({service_name=\"checkout\"} |= \"error\" [$__auto]))'")
    sent = calls[-1]
    assert sent["step"] == "10" and "[10s]" in sent["query"]
    assert int(sent["start"]) == int((WIN_START + timedelta(seconds=10)).timestamp() * 1e9)
    assert int(sent["end"]) == int(WIN_END.timestamp() * 1e9)
    buckets, lines, first_ts, last_ts = res.rows[0]
    assert buckets == 120 and lines == 120  # 20 min / 10 s: no step from before the window
    assert first_ts == WIN_START + timedelta(seconds=10) and last_ts == WIN_END
    window = next(line for line in res.render().splitlines() if line.startswith("[window] loki_query_range"))
    assert "step 10s" in window and "buckets cover 2026-10-01T11:45:00Z -> 2026-10-01T12:05:00Z" in window

    # An explicit step moves the start by that step.
    await _engine("loki").query(
        "SELECT ts, value FROM loki_query_range "
        "WHERE query = 'sum(count_over_time({service_name=\"checkout\"} [$__interval]))' AND step = 60")
    assert int(calls[-1]["start"]) == int((WIN_START + timedelta(seconds=60)).timestamp() * 1e9)
    assert "[60s]" in calls[-1]["query"]

    # A shape-only query (its own range) keeps the window start and gets no bucket claim.
    res = await _engine("loki").query(
        "SELECT ts, value FROM loki_query_range WHERE query = 'sum(rate({service_name=\"checkout\"}[5m]))'")
    assert int(calls[-1]["start"]) == int(WIN_START.timestamp() * 1e9)
    assert "buckets cover" not in res.render()


@respx.mock
async def test_loki_log_counts_role_binding_tiles_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOKI_URL", "http://loki.test")
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        return httpx.Response(200, json=_matrix(0, 0, 1))

    respx.get("http://loki.test/loki/api/v1/query_range").mock(side_effect=handler)
    engine = _engine("loki")
    ((_, binding),) = engine.catalog.role_bindings("log_counts")
    query = templating.render_value(binding.filters["query"], {"service": "checkout", "error_only": True}).strip()
    assert "[$__auto]" in query
    await engine.query(f"SELECT ts, value FROM {binding.table} WHERE query = '{query.replace(chr(39), chr(39) * 2)}'")
    assert int(calls[-1]["start"]) == int((WIN_START + timedelta(seconds=10)).timestamp() * 1e9)


# --- Prometheus: interval macros, aligned start, scalars, NaN, error hints ----------------------------


@respx.mock
async def test_prometheus_range_expands_grafana_macros_and_tiles_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom.test")
    monkeypatch.setenv("PROMETHEUS_SCRAPE_INTERVAL", "30s")
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        calls.append(p)
        return httpx.Response(200, json=_matrix(int(p["start"]), int(p["end"]), int(p["step"])))

    respx.get("http://prom.test/api/v1/query_range").mock(side_effect=handler)
    res = await _engine("prometheus").query(
        "SELECT ts, value FROM prometheus_query_range WHERE query = "
        "'sum(increase(errors_total[$__interval])) / $__range_s + sum(rate(x[$__rate_interval])) + y[$__range]'")
    sent = calls[-1]
    # step 10s; $__rate_interval = max(step + scrape, 4 x scrape) = max(40s, 120s) = 2m.
    assert sent["query"] == "sum(increase(errors_total[10s])) / 1200 + sum(rate(x[2m])) + y[20m]"
    assert int(sent["start"]) == int((WIN_START + timedelta(seconds=10)).timestamp())
    assert "step 10s" in res.render()

    await _engine("prometheus").query(
        "SELECT ts, value FROM prometheus_query_range WHERE query = 'sum(rate(x[$__rate_interval]))'")
    assert int(calls[-1]["start"]) == int(WIN_START.timestamp())  # rate_interval is not step-sized


@respx.mock
async def test_prometheus_instant_query_macros_cover_the_whole_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom.test")
    monkeypatch.delenv("PROMETHEUS_SCRAPE_INTERVAL", raising=False)
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        return httpx.Response(200, text=(FIXTURES / "query_vector.json").read_text())

    respx.get("http://prom.test/api/v1/query").mock(side_effect=handler)
    await _engine("prometheus").query(
        "SELECT value FROM prometheus_query WHERE query = "
        "'histogram_quantile(0.95, sum by (le) (rate(h_bucket[$__range]))) + rate(c[$__rate_interval])' "
        "AND ts BETWEEN TIMESTAMP '2026-10-01 10:00:00' AND TIMESTAMP '2026-10-01 11:00:00'")
    # $__range = the ts window; $__rate_interval = max(1h + 15s default scrape, 60s).
    assert calls[-1]["query"] == "histogram_quantile(0.95, sum by (le) (rate(h_bucket[1h]))) + rate(c[3615s])"
    assert int(calls[-1]["time"]) == int(datetime(2026, 10, 1, 11, 0, tzinfo=UTC).timestamp())


@respx.mock
async def test_prometheus_scalar_result_is_one_row_and_empty_vector_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom.test")
    bodies = {"time()": "query_scalar.json", "up": "query_vector.json", "absent": "query_vector_empty.json"}
    route = respx.get("http://prom.test/api/v1/query").mock(side_effect=lambda request: httpx.Response(
        200, text=(FIXTURES / bodies[request.url.params["query"]]).read_text()))

    res = await _engine("prometheus").query("SELECT ts, value, labels FROM prometheus_query WHERE query = 'time()'")
    assert res.total_rows == 1
    ts, value, labels = res.rows[0]
    assert value == 1435781451.781 and ts == datetime(2015, 7, 1, 20, 10, 51, 781000, tzinfo=UTC) and labels is None

    res = await _engine("prometheus").query(
        "SELECT labels->>'job' AS job, value FROM prometheus_query WHERE query = 'up' ORDER BY job")
    assert res.rows == [("node", 0.0), ("prometheus", 1.0)]

    res = await _engine("prometheus").query("SELECT value FROM prometheus_query WHERE query = 'absent'")
    assert res.total_rows == 0
    assert route.call_count == 3


def test_derived_instant_tables_that_override_columns_still_read_vectors() -> None:
    """Derived sources reuse the template's response but some keep their own relative column paths."""
    manifests, _ = load_all_manifests(strict=False)
    idx = table_index(manifests)
    body = json.loads((FIXTURES / "query_vector.json").read_text())
    derived = [name for name, (m, t) in idx.items() if name.endswith("_query") and t.response.rows_path == "$.data"
               and t.request is not None and t.request.path == "/api/v1/query"]
    assert {"prometheus_query", "thanos_query", "mimir_query"} <= set(derived), derived
    for name in derived:
        m, t = idx[name]
        rows = transform.build_rows(t, body, filters={"query": "up"}, extra_error_rules=m.errors)
        assert [r["value"] for r in rows] == [1.0, 0.0], name
        assert rows[0]["ts"] == datetime(2015, 7, 1, 20, 10, 51, 781000, tzinfo=UTC), name
        assert rows[0]["metric"] == "up", name


def test_every_instant_table_on_the_template_response_reads_a_scalar_result() -> None:
    """A scalar (time(), scalar(x)) is one row with its ts and value, also where a source overrides columns."""
    manifests, _ = load_all_manifests(strict=False)
    body = json.loads((FIXTURES / "query_scalar.json").read_text())
    tables = [(name, m, t) for name, (m, t) in table_index(manifests).items() if t.response.rows_path == "$.data"
              and t.request is not None and t.request.path == "/api/v1/query"]
    assert {"thanos_query", "mimir_query", "victoriametrics_query"} <= {n for n, _, _ in tables}
    for name, m, t in tables:
        rows = transform.build_rows(t, body, filters={"query": "time()"}, extra_error_rules=m.errors)
        assert [(r["ts"], r["value"]) for r in rows] == [
            (datetime(2015, 7, 1, 20, 10, 51, 781000, tzinfo=UTC), 1435781451.781)], name
        # ...so no PromQL note on such a source still tells the model to wrap results in vector().
        note = " ".join(m.query_languages.get("promql", "").split())
        assert "wrap scalars with vector()" not in note, m.name


@respx.mock
async def test_nan_and_inf_values_are_announced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom.test")
    payload = {"status": "success", "data": {"resultType": "matrix", "result": [{"metric": {"job": "api"}, "values": [
        [1790769600, "NaN"], [1790769660, "0.5"], [1790769720, "+Inf"], [1790769780, "0.7"]]}]}}
    respx.get("http://prom.test/api/v1/query_range").mock(return_value=httpx.Response(200, json=payload))
    res = await _engine("prometheus").query(
        "SELECT ts, value FROM prometheus_query_range WHERE query = 'sum(rate(a[5m])) / sum(rate(b[5m]))'")
    assert "2 NaN/Inf value(s) in value; filter with isfinite(value)" in res.render()

    res = await _engine("prometheus").query(
        "SELECT max(value) AS peak FROM prometheus_query_range WHERE query = 'sum(rate(a[5m])) / sum(rate(b[5m]))' "
        "AND isfinite(value)")
    assert res.rows == [(0.7,)] and not any("NaN" in n for n in res.notes)


def test_nonfinite_note_quotes_odd_column_names() -> None:
    res = QueryResult(sql="", columns=["p95 ms", "n"], types=["DOUBLE", "BIGINT"],
                      rows=[(float("nan"), 1), (1.0, 2)], total_rows=2)
    from omniops.engine.results import nonfinite_notes

    assert nonfinite_notes(res.columns, res.types, res.rows) == [
        ('1 NaN/Inf value(s) in p95 ms; filter with isfinite("p95 ms") (DuckDB sorts NaN above every number, '
         "so max() and ORDER BY ... DESC pick it)")]


@respx.mock
async def test_vector_of_an_aggregation_error_teaches_the_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom.test")
    err = {"status": "error", "errorType": "bad_data", "error": 'invalid parameter "query": 1:1: parse error: '
           'expected type scalar in call to function "vector", got instant vector'}
    respx.get("http://prom.test/api/v1/query").mock(return_value=httpx.Response(400, json=err))
    with pytest.raises(http.FetchError) as caught:
        await _engine("prometheus").query("SELECT value FROM prometheus_query WHERE query = 'vector(sum(up))'")
    assert "vector() takes only a scalar" in caught.value.lesson
    assert "describe the table" in caught.value.lesson  # the generic lesson is kept after the hint

    # The same message in a 2xx error body (a proxy answering 200) carries the same fix.
    respx.get("http://prom.test/api/v1/query").mock(return_value=httpx.Response(200, json=err))
    with pytest.raises(transform.ApiBodyError) as caught2:
        await _engine("prometheus").query("SELECT value FROM prometheus_query WHERE query = 'vector(sum(up))'")
    assert caught2.value.lesson.startswith("vector() takes only a scalar")
    assert http.error_hint("parse error: unexpected right brace") is None
