"""Regression tests for engine data-discipline defects, through the real entry point (SQLEngine.query)
against respx-mocked APIs: timeouts, pushdown bookkeeping, LIMIT pushdown, zero-fill, pagination,
positive controls, computed tables, the shared FetchCache, source instances and Python fetchers."""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from omniops.engine import pushdown, timeutil
from omniops.engine.catalog import Catalog
from omniops.engine.engine import EngineError, FetchCache, FetchOutcome, QueryTimeoutError, SQLEngine

TRIGGER = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
WIN_START_NS = int((TRIGGER - timedelta(minutes=15)).timestamp() * 1e9)


def _anchor() -> timeutil.TimeAnchor:
    return timeutil.TimeAnchor.for_trigger(TRIGGER, wall_now=TRIGGER + timedelta(hours=1))


def _engine(*only: str, **kw: object) -> SQLEngine:
    return SQLEngine(Catalog.from_env(only=list(only)), _anchor(), **kw)  # type: ignore[arg-type]


def _streams(svc: str, end_ns: int, n: int, line: object = None) -> dict:
    vals = [[str(end_ns - (i + 1) * 1_000_000), line(i) if callable(line) else f"line {i}"] for i in range(n)]
    return {"status": "success", "data": {"resultType": "streams",
                                          "result": [{"stream": {"service_name": svc}, "values": vals}] if vals else []}}


def _svc(query: str) -> str:
    m = re.search(r'service_name="([^"]*)"', query)
    return m.group(1) if m else ""


# --- E1: the SQL timeout stops the query, and a slow query never blocks the event loop ----------------


@respx.mock
async def test_runaway_sql_times_out_and_never_blocks_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom.test")
    engine = _engine("prometheus", positive_controls=False, query_timeout_s=0.5)

    def handler(request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        values = [[t, "1"] for t in range(int(p["start"]), int(p["end"]) + 1, int(p["step"]))]
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": [
            {"metric": {"job": "api"}, "values": values}]}})

    respx.get("http://prom.test/api/v1/query_range").mock(side_effect=handler)
    gaps: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        last = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(0.02)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    hb = asyncio.create_task(heartbeat())
    t0 = time.monotonic()
    slow = asyncio.create_task(engine.query(
        "SELECT count(*) FROM range(2000) a, range(1000) b, range(1000) c WHERE a.range + b.range + c.range < 0"))
    await asyncio.sleep(0.2)
    fast = await engine.query("SELECT count(*) AS n FROM prometheus_query_range WHERE query = 'up'")
    with pytest.raises(QueryTimeoutError):
        await slow
    elapsed = time.monotonic() - t0
    stop.set()
    await hb
    assert fast.rows[0][0] > 0
    assert elapsed < 5, f"the 0.5 s timeout did not stop the query ({elapsed:.1f} s)"
    assert max(gaps) < 0.5, f"event loop stalled for {max(gaps):.2f} s while a query ran"


# --- E9: cancelling the task that owns a shared fetch does not cancel the other waiters -------------


async def test_cancelling_the_fetch_owner_does_not_cancel_other_waiters() -> None:
    cache = FetchCache()
    started = asyncio.Event()

    async def slow() -> FetchOutcome:
        started.set()
        await asyncio.sleep(0.2)
        return FetchOutcome([{"a": 1}], [], [], [])

    owner = asyncio.create_task(cache.get_or_fetch("k", slow))
    await started.wait()
    waiter = asyncio.create_task(cache.get_or_fetch("k", slow))
    await asyncio.sleep(0)
    owner.cancel()
    out, hit = await waiter
    assert out.rows == [{"a": 1}] and hit
    assert owner.cancelled()
    assert (await cache.get_or_fetch("k", slow))[0].rows == [{"a": 1}]  # cached for the next agent


async def test_owner_deadline_does_not_leak_into_other_waiters() -> None:
    cache = FetchCache()
    started = asyncio.Event()

    async def slow() -> FetchOutcome:
        started.set()
        await asyncio.sleep(0.2)
        return FetchOutcome([{"a": 1}], [], [], [])

    owner = asyncio.create_task(asyncio.wait_for(cache.get_or_fetch("k", slow), 0.05))
    await started.wait()
    waiter = asyncio.create_task(cache.get_or_fetch("k", slow))
    with pytest.raises(TimeoutError):
        await owner
    out, _ = await waiter
    assert out.rows == [{"a": 1}]


async def test_fetch_is_cancelled_when_its_last_waiter_goes() -> None:
    cache = FetchCache()
    cancelled = asyncio.Event()

    async def slow() -> FetchOutcome:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return FetchOutcome([], [], [], [])

    only = asyncio.create_task(cache.get_or_fetch("k", slow))
    await asyncio.sleep(0.01)
    only.cancel()
    with pytest.raises(asyncio.CancelledError):
        await only
    await asyncio.wait_for(cancelled.wait(), 1)


# --- E2: two predicates on one pushed column ------------------------------------------------------------


@respx.mock
async def test_range_bounds_on_one_column_are_both_pushed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JAEGER_URL", "http://jaeger.test")
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        return httpx.Response(200, json={"result": {"resourceSpans": []}})

    respx.get("http://jaeger.test/api/v3/traces").mock(side_effect=handler)
    for cond in ("duration_ms > 1000 AND duration_ms < 5000", "duration_ms < 5000 AND duration_ms > 1000"):
        engine = _engine("jaeger", positive_controls=False)
        await engine.query(f"SELECT * FROM jaeger_traces WHERE service = 'checkout' AND {cond}")
    assert len(calls) == 2
    for c in calls:
        assert c.get("query.durationMin") == "1.000000s" and c.get("query.durationMax") == "5.000000s", c


def test_unmergeable_range_pushes_the_lower_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_LOG_ANALYTICS_WORKSPACE_ID"):
        monkeypatch.setenv(var, "x")
    engine = _engine("azure_log_analytics")
    for cond in ("status_code >= 500 AND status_code < 600", "status_code < 600 AND status_code >= 500"):
        tree = pushdown.parse(f"SELECT * FROM azure_log_analytics_app_requests WHERE {cond}")
        occ = pushdown.extract_occurrences(tree, engine.catalog.columns_map(),
                                           pushdown.ConstantEvaluator(engine.con))[0]
        plan = engine.plan(occ)
        # 'between' is not supported: the 5xx lower bound is pushed (an API row cap must not cut the
        # rare tail) and DuckDB applies the upper bound, which stays in the SQL.
        assert plan.filters["status_code"] == 500 and plan.filters["status_code__op"] == ">="
        assert sorted(p.pushed for p in occ.predicates) == [False, True]
        assert plan.limit is None


async def test_conflicting_predicates_on_a_filter_only_column_are_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOKI_URL", "http://loki.test")
    engine = _engine("loki", positive_controls=False)
    with pytest.raises(EngineError, match="filter-only"):
        await engine.query("SELECT template FROM omni_log_patterns WHERE service = 'cart' AND service = 'checkout'")


# --- E3: LIMIT pushdown only when the API rows are the SQL's rows -----------------------------------


@respx.mock
async def test_limit_is_not_pushed_when_sql_still_filters_or_offsets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOKI_URL", "http://loki.test")
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        calls.append(p)
        n = min(int(p["limit"]), 1000)
        return httpx.Response(200, json=_streams(_svc(p["query"]), int(p["end"]), n,
                                                 lambda i: f"GET /x status={500 if i % 10 == 0 else 200} i={i}"))

    respx.get("http://loki.test/loki/api/v1/query_range").mock(side_effect=handler)
    res = await _engine("loki", positive_controls=False).query(
        "SELECT ts, message FROM loki_logs WHERE service = 'api' AND message NOT LIKE '%status=200%' LIMIT 20")
    assert calls[-1]["limit"] != "20"
    assert "status=200" not in calls[-1]["query"], "NOT LIKE must never be pushed as LIKE"
    assert res.total_rows == 20

    res = await _engine("loki", positive_controls=False).query(
        "SELECT ts, message FROM loki_logs WHERE service = 'api' AND host = 'h1' LIMIT 20")
    assert calls[-1]["limit"] != "20"  # host has no filter: DuckDB applies it

    res = await _engine("loki", positive_controls=False).query(
        "SELECT ts, message FROM loki_logs WHERE service = 'api' LIMIT 10 OFFSET 10")
    assert calls[-1]["limit"] == "20"
    assert res.total_rows == 10
    assert not any("filtered all of them out" in n for n in res.notes)


# --- E4: zero-fill uses the bucket size the API actually used ---------------------------------------


@respx.mock
async def test_zero_fill_uses_the_api_bucket_step(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELASTICSEARCH_URL", "http://es.test")
    steps: list[int] = []
    hole = set()

    def handler(request: httpx.Request) -> httpx.Response:
        hist = json.loads(request.content)["aggs"]["h"]["date_histogram"]
        step = int(hist["fixed_interval"].rstrip("s"))
        steps.append(step)
        lo, hi = int(hist["extended_bounds"]["min"]) // 1000, int(hist["extended_bounds"]["max"]) // 1000
        grid = list(range(lo - lo % step, hi + 1, step))
        hole.update(grid[60:63])  # three empty buckets the API omits
        buckets = [{"key": t * 1000, "doc_count": 100} for t in grid if t not in hole]
        return httpx.Response(200, json={"aggregations": {"h": {"buckets": buckets}}})

    respx.post(url__regex=r"http://es\.test/.*_search.*").mock(side_effect=handler)
    engine = _engine("elasticsearch", positive_controls=False)
    res = await engine.query(
        "SELECT count(*) AS n, sum(value) AS s, count(*) FILTER (WHERE value = 0) AS zeros "
        "FROM elasticsearch_log_counts WHERE level = 'error' "
        "AND ts BETWEEN TIMESTAMP '2026-10-01 11:00:00+00' AND TIMESTAMP '2026-10-01 12:00:00+00'")
    assert steps == [30]
    n, s, zeros = res.rows[0]
    assert (n, zeros) == (121, 3), res.rows  # the 3 omitted buckets come back as 0, no fake ones
    assert s == 118 * 100


# --- E5: offset pagination advances by API items, not expanded rows ---------------------------------


@respx.mock
async def test_offset_pagination_counts_api_items_not_expanded_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_URL", "http://jira.test")
    monkeypatch.setenv("JIRA_API_TOKEN", "x")
    monkeypatch.setenv("JIRA_EMAIL", "a@b.c")
    histories = [{"id": str(i), "created": "2026-10-01T10:00:00.000+0000", "author": {"displayName": "u"},
                  "items": [{"field": "status", "fromString": "a", "toString": "b"},
                            {"field": "x", "fromString": "c", "toString": "d"}]} for i in range(150)]
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        calls.append(p)
        s, m = int(p.get("startAt", 0)), int(p.get("maxResults", 50))
        return httpx.Response(200, json={"startAt": s, "maxResults": m, "total": 150, "values": histories[s:s + m]})

    respx.get(url__regex=r"http://jira\.test/rest/api/3/issue/.*/changelog.*").mock(side_effect=handler)
    res = await _engine("jira", positive_controls=False).query(
        "SELECT count(DISTINCT history_id) AS h, count(*) AS n FROM jira_issue_changelog WHERE key = 'OPS-1'")
    assert [c.get("startAt") for c in calls] == ["0", "100"]
    assert res.rows == [(150, 300)]


# --- E6: the positive control never calls partial coverage "absent" --------------------------------


@respx.mock
async def test_positive_control_with_partial_or_typo_fan_out_is_inconclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOKI_URL", "http://loki.test")

    def handler(request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        svc = _svc(p["query"])
        if int(p["end"]) > WIN_START_NS:  # the incident window
            if svc == "b":
                return httpx.Response(400, text="bad request")
            return httpx.Response(200, json=_streams(svc, int(p["end"]), 0))
        return httpx.Response(200, json=_streams(svc, int(p["end"]), 5 if svc in ("a", "cart") else 0))

    respx.get("http://loki.test/loki/api/v1/query_range").mock(side_effect=handler)
    res = await _engine("loki").query("SELECT ts, message FROM loki_logs WHERE service IN ('a', 'b')")
    assert res.total_rows == 0 and res.empty_verdict == "inconclusive", res.notes
    assert any("PARTIAL" in n for n in res.notes) and any("positive control skipped" in n for n in res.notes)

    res = await _engine("loki").query("SELECT ts, message FROM loki_logs WHERE service IN ('cart', 'checkuot')")
    assert res.empty_verdict == "inconclusive", res.notes
    assert any("checkuot" in n and "probably wrong" in n for n in res.notes), res.notes

    res = await _engine("loki").query("SELECT ts, message FROM loki_logs WHERE service = 'cart'")
    assert res.empty_verdict == "absent", res.notes  # a valid filter over an empty window is still absent


# --- E8: computed tables read the per-request context; their notes are cached with the rows ------------


@respx.mock
async def test_computed_table_fans_out_per_value_and_keeps_notes_on_cache_hits(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOKI_URL", "http://loki.test")
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        queries.append(p["query"])
        return httpx.Response(200, json=_streams(_svc(p["query"]), int(p["end"]), 5,
                                                 lambda i: f"error connecting to db host=1.2.3.{i}"))

    route = respx.get("http://loki.test/loki/api/v1/query_range").mock(side_effect=handler)
    res = await _engine("loki", positive_controls=False).query(
        "SELECT template, count FROM omni_log_patterns WHERE service IN ('cart', 'checkout')")
    assert sorted(_svc(q) for q in queries) == ["cart", "checkout"], queries
    assert res.total_rows == 2

    queries.clear()
    await _engine("loki", positive_controls=False).query(
        "SELECT template FROM omni_log_patterns WHERE service = 'cart' AND source IN ('loki', 'other')")
    assert [_svc(q) for q in queries] == ["cart"], "the IN-listed source must not be skipped"

    route.mock(return_value=httpx.Response(401, text="no org id"))
    cache = FetchCache()
    sql = "SELECT template, count FROM omni_log_patterns WHERE service = 'payments'"
    first = await SQLEngine(Catalog.from_env(only=["loki"]), _anchor(), cache=cache, positive_controls=False).query(sql)
    second = await SQLEngine(Catalog.from_env(only=["loki"]), _anchor(), cache=cache, positive_controls=False).query(sql)
    for res in (first, second):
        assert any("AuthError" in n for n in res.notes), res.notes
        assert any("PARTIAL" in n for n in res.notes), res.notes


# --- E10: two instances of one manifest both stay reachable ------------------------------------------


@respx.mock
async def test_second_instance_of_a_manifest_gets_its_own_tables(tmp_path: Path) -> None:
    cfg = tmp_path / "omniops.yaml"
    cfg.write_text("sources:\n"
                   "  - {use: prometheus, name: prom-prod, env: {PROMETHEUS_URL: 'http://prod.test'}}\n"
                   "  - {use: prometheus, name: prom-staging, env: {PROMETHEUS_URL: 'http://staging.test'}}\n")
    cat = Catalog.from_env(config_path=str(cfg))
    assert cat.get("prometheus_query_range").runtime.name == "prom-prod"
    assert cat.get("prom_staging__prometheus_query_range").runtime.name == "prom-staging"
    overview = cat.sources_overview()
    assert "prom-prod:" in overview and "prom-staging:" in overview
    bound = {rt.name: cat.get(b.table).runtime.name for rt, b in cat.role_bindings("active_alerts")}
    assert bound == {"prom-prod": "prom-prod", "prom-staging": "prom-staging"}

    empty = {"status": "success", "data": {"resultType": "matrix", "result": []}}
    staging = respx.get("http://staging.test/api/v1/query_range").mock(return_value=httpx.Response(200, json=empty))
    prod = respx.get("http://prod.test/api/v1/query_range").mock(return_value=httpx.Response(200, json=empty))
    engine = SQLEngine(cat, _anchor(), positive_controls=False)
    await engine.query("SELECT * FROM prom_staging__prometheus_query_range WHERE query = 'up'")
    assert staging.call_count == 1 and prod.call_count == 0


# --- E11: the hostname-variant relabel does not corrupt the shared cache ----------------------------


@respx.mock
async def test_host_variant_relabel_leaves_cached_rows_intact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DD_API_KEY", "k")
    monkeypatch.setenv("DD_APP_KEY", "a")
    monkeypatch.setenv("DD_API_BASE_URL", "http://dd.test")

    def handler(request: httpx.Request) -> httpx.Response:
        q = json.loads(request.content)["filter"]["query"]
        hits = [] if "host:web01.prod.example.com" in q else [
            {"id": str(i), "attributes": {"timestamp": f"2026-10-01T11:50:0{i}Z", "service": "api",
                                          "host": "web01", "status": "error", "message": "boom"}} for i in range(3)]
        return httpx.Response(200, json={"data": hits, "meta": {"page": {}}})

    respx.post("http://dd.test/api/v2/logs/events/search").mock(side_effect=handler)
    engine = _engine("datadog", positive_controls=False)
    long = await engine.query("SELECT ts, host, message FROM datadog_logs WHERE host = 'web01.prod.example.com'")
    assert [r[1] for r in long.rows] == ["web01.prod.example.com"] * 3
    short = await engine.query("SELECT ts, host, message FROM datadog_logs WHERE host = 'web01'")
    assert short.total_rows == 3, short.notes


# --- E12: Python fetchers report truncation instead of returning partial data as complete ------------


@respx.mock
async def test_signalflow_stream_cut_at_max_rows_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPLUNK_O11Y_REALM", "us1")
    monkeypatch.setenv("SPLUNK_O11Y_TOKEN", "t")
    monkeypatch.setenv("SPLUNK_O11Y_STREAM_URL", "http://sf.test")
    engine = _engine("splunk_o11y", positive_controls=False)
    monkeypatch.setattr(engine.catalog.get("splunk_o11y_signalflow").table, "max_rows", 3)
    t0 = int((TRIGGER - timedelta(minutes=10)).timestamp() * 1000)
    msgs = ["event: metadata\ndata: " + json.dumps({"tsId": "A", "properties": {"sf_metric": "cpu", "host": "h1"}})]
    msgs += ["event: data\ndata: " + json.dumps({"data": [{"tsId": "A", "value": i}], "logicalTimestampMs": t0 + i * 60000})
             for i in range(6)]
    msgs.append("event: control-message\ndata: " + json.dumps({"event": "END_OF_CHANNEL"}))
    respx.post("http://sf.test/v2/signalflow/execute").mock(
        return_value=httpx.Response(200, text="\n\n".join(msgs) + "\n\n", headers={"Content-Type": "text/event-stream"}))
    res = await engine.query("SELECT ts, value FROM splunk_o11y_signalflow WHERE program = 'data(\"cpu\").publish()'")
    assert res.total_rows == 3
    assert any("source data truncated" in n for n in res.notes), res.notes
    assert any("END of the window is missing" in n for n in res.notes), res.notes


@respx.mock
async def test_kubernetes_env_listing_stopped_at_max_pages_is_reported(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\nclusters: [{name: c, cluster: {server: 'http://k8s.test'}}]\n"
                          "contexts: [{name: x, context: {cluster: c, user: u}}]\ncurrent-context: x\n"
                          "users: [{name: u, user: {token: t}}]\n")
    monkeypatch.setenv("KUBECONFIG", str(kubeconfig))
    page = {"metadata": {"continue": "more"}, "items": [
        {"metadata": {"name": "web", "namespace": "shop"},
         "spec": {"template": {"spec": {"containers": [{"name": "app", "env": [{"name": "MODE", "value": "x"}]}]}}}}]}
    route = respx.get("http://k8s.test/apis/apps/v1/namespaces/shop/deployments").mock(
        return_value=httpx.Response(200, json=page))
    res = await _engine("kubernetes", positive_controls=False).query(
        "SELECT workload, env_name FROM kubernetes_container_env WHERE namespace = 'shop'")
    assert route.call_count == 10 and res.total_rows == 10
    assert any("source data truncated" in n for n in res.notes), res.notes
    assert any("more workloads exist" in n for n in res.notes), res.notes
