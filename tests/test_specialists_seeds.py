"""Deterministic seeds through ``run_seeds`` over a respx-mocked Prometheus / Loki / Kubernetes / deploy catalog."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import respx
from test_specialists_support import (  # noqa: F401 - source_env is a fixture (pytestmark)
    ALERTS,
    CHANGE_URL,
    close_ctx,
    make_alert,
    make_ctx,
    mock_sources,
    source_env,
)

from omniops.agent import seeds
from omniops.agent.memory import MemoryStore
from omniops.agent.types import EvidenceRecord
from omniops.engine import loader, pushdown
from omniops.engine.catalog import Catalog
from omniops.engine.engine import SQLEngine
from omniops.engine.manifest import RoleBinding
from omniops.llm.scripted import ScriptedLLM

pytestmark = pytest.mark.usefixtures("source_env")


def _by_title(recs: list[EvidenceRecord], needle: str) -> EvidenceRecord:
    hits = [r for r in recs if needle.lower() in r.title.lower()]
    assert hits, f"no record titled like {needle!r}: {[r.title for r in recs]}"
    return hits[0]


async def test_seed_pack_grounds_the_run() -> None:
    mem = MemoryStore(":memory:")
    mem.add_episode(run_id="old", title="checkout 5xx after batch size change", service="checkout",
                    summary="checkout pods OOMKilled after the batch size was raised",
                    root_cause="batch size 5000 exhausted the heap", causal_level="trigger")
    mem.add_fact(kind="ruled_out", text="Redis latency was refuted as the cause of checkout 5xx", service="checkout")
    with respx.mock(assert_all_called=False) as m:
        routes = mock_sources(m)
        ctx = make_ctx(llm=ScriptedLLM(), memory=mem)
        recs = await seeds.run_seeds(ctx)
        await close_ctx(ctx)

    # Published in a fixed order, all pinned, every one with an id on the board; E1 is the alert.
    assert [r.id for r in recs] == [f"E{i}" for i in range(1, len(recs) + 1)]
    assert all(r.pinned for r in recs) and recs[0].kind == "alert"
    assert {r.id for r in ctx.board.pinned()} == {r.id for r in recs}

    alert = recs[0]
    assert "CRITICAL threshold (the paging level): value above 5" in alert.summary
    assert "WARNING threshold: value above 2 - a warning level, NOT the critical threshold" in alert.summary
    assert "observed value 9 is past the CRITICAL threshold" in alert.summary

    rule = _by_title(recs, "Alert rule definition")
    assert rule.sql == "SELECT * FROM prometheus_rules WHERE rule_name = 'CheckoutErrors' LIMIT 20"
    assert "sustained for 5m" in rule.summary

    changes = _by_title(recs, "Change events 48h")
    assert "omni_change_events WHERE ts BETWEEN TIMESTAMP '2026-09-29 12:00:00+00' AND TIMESTAMP " \
           "'2026-10-01 13:00:00+00'" in changes.sql
    assert "within 60 min BEFORE onset" in changes.summary and "8 min before onset" in changes.summary
    assert changes.data["near_onset"][0]["version"] == "abc123"
    assert CHANGE_URL in [lk["url"] for lk in changes.links]        # links come from data
    assert routes["changes"].called

    logs = _by_title(recs, "Error log patterns")
    assert "1 NEW vs the same window a day earlier" in logs.summary
    assert "payment client timeout" in logs.data["new_templates"][0]
    assert any("cache miss" in t for t in logs.data["chronic_templates"])
    assert "[baseline sql]" in logs.rendered and "2026-09-30 11:45:00" in logs.rendered

    query = _by_title(recs, "alert's own query over 6h")
    assert "query = 'sum(rate(http_requests_total" in query.sql and "> 5" not in query.sql
    assert "2026-10-01 06:05:00+00" in query.sql
    assert query.meaningful and query.row_count > 0

    k8s = _by_title(recs, "Kubernetes state")
    assert "CrashLoopBackOff" in k8s.summary and "OOMKilled" in k8s.summary and "exit 137" in k8s.summary
    assert k8s.data["problems"][0]["name"] == "checkout-7f9c-abc" and k8s.data["problems"][0]["oom"]
    events = _by_title(recs, "Kubernetes warning events")
    digest = events.summary.splitlines()[0]                          # computed digest: warnings only
    assert "1 warning event(s)" in digest and "BackOff x7" in digest and "Pulled" not in digest

    active = _by_title(recs, "Other active alerts")
    assert "RedisMemoryHigh" in active.summary and "1 besides this one" in active.summary

    memory = _by_title(recs, "Memory recall")
    assert memory.kind == "memory" and "ruled out" in memory.summary and "refuted" not in memory.summary
    assert "batch size 5000" in memory.summary
    assert ctx.warnings == []
    # Seed SQL is traced like a tool call so trajectories show the exact query shapes.
    assert any(e.name == "seed_sql" and e.evidence_id == rule.id for e in ctx.tool_events)


async def test_failing_seed_is_loud_not_silent() -> None:
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m, loki_status=500)
        ctx = make_ctx(llm=ScriptedLLM())
        recs = await seeds.run_seeds(ctx)
        await close_ctx(ctx)
    logs = _by_title(recs, "Error log patterns")
    assert not logs.meaningful
    assert logs.row_count == 0 or logs.error
    assert any("loki" in n.lower() for n in [logs.summary, logs.rendered])
    # The rest of the pack still landed.
    assert _by_title(recs, "Kubernetes state").meaningful


async def test_k8s_seed_also_shows_other_namespaces() -> None:
    """The actor is often a job in another namespace: problems outside the alert's namespace are seeded too."""
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        ctx = make_ctx(llm=ScriptedLLM())
        recs = await seeds.run_seeds(ctx)
        await close_ctx(ctx)
    outside = _by_title(recs, "Kubernetes state: Pods (testkube) (outside namespace shop)")
    assert outside.sql == "SELECT * FROM testkube_pods LIMIT 500"
    assert "batch/cache-warmer-fill-x1" in outside.summary and "restarts 9" in outside.summary
    assert "checkout-7f9c-abc" not in outside.summary.splitlines()[0]            # already in the scoped record
    events = _by_title(recs, "Kubernetes warning events (testkube) (outside namespace shop)")
    assert "BackOff x21 on batch/Pod/cache-warmer-fill-x1" in events.summary


class _RecordingEngine:
    """Records the seed SQL instead of fetching (every query fails, so each record keeps its SQL)."""

    def __init__(self) -> None:
        self.sql: list[str] = []

    async def query(self, sql: str) -> None:
        self.sql.append(sql)
        raise RuntimeError("not fetched in this test")


async def test_k8s_seed_scopes_the_shipped_kubernetes_manifest_to_the_namespace() -> None:
    """The shipped kubernetes.yaml bindings carry no namespace filter template: the scope must be in the seed
    SQL itself, or "namespace shop" is the cluster's first 500 objects and "outside" repeats the same list."""
    manifests, _ = loader.load_all_manifests(strict=False)
    cat = Catalog()
    cat.add_runtime(manifests["kubernetes"], {"KUBECONFIG": "/dev/null"}, "kubernetes", base_url="http://kube.test")
    ctx = make_ctx(llm=ScriptedLLM(), sources=["prometheus"])
    ctx.deps.catalog = cat
    recs = await seeds.seed_k8s(ctx, _RecordingEngine())      # type: ignore[arg-type]
    ctx.close()

    scoped = [r for r in recs if r.title.endswith("(namespace shop)")]
    outside = [r for r in recs if r.title.endswith("(outside namespace shop)")]
    assert scoped and len(scoped) == len(outside)
    assert all(r.sql and "namespace = 'shop'" in r.sql for r in scoped)
    assert all(r.sql and "namespace != 'shop'" in r.sql for r in outside)
    pods = next(r.sql for r in scoped if "FROM kubernetes_pods" in (r.sql or ""))
    assert pods == "SELECT * FROM kubernetes_pods WHERE namespace = 'shop' LIMIT 500"
    events = next(r.sql for r in outside if "FROM kubernetes_events" in (r.sql or ""))
    assert "type = 'Warning'" in events and "namespace != 'shop'" in events
    # Both scopes are pushed to the API (path /namespaces/shop, fieldSelector metadata.namespace!=shop).
    eng = SQLEngine(cat, ctx.anchor)
    for sql, op in ((pods, None), (events, "!=")):
        occ = pushdown.extract_occurrences(pushdown.parse(sql), cat.columns_map(),
                                           pushdown.ConstantEvaluator(eng.con))[0]
        plan = eng.plan(occ)
        assert plan.filters["namespace"] == "shop" and plan.filters.get("namespace__op") == op
    eng.close()


async def test_active_alerts_listing_only_this_alert_is_not_meaningful() -> None:
    """3.4: a firing alert always lists itself; a list holding only this alert measures nothing and must not
    lift a run with no measurement over the evidence floor."""
    with respx.mock(assert_all_called=False) as m:
        routes = mock_sources(m)
        routes["alerts"].respond(json={"status": "success", "data": {"alerts": ALERTS["data"]["alerts"][:1]}})
        ctx = make_ctx(llm=ScriptedLLM())
        [alone] = await seeds.seed_active_alerts(ctx, ctx.engine)
        await close_ctx(ctx)
        routes["alerts"].respond(json=ALERTS)
        ctx = make_ctx(llm=ScriptedLLM())
        [others] = await seeds.seed_active_alerts(ctx, ctx.engine)
        await close_ctx(ctx)
    assert "1 active alert(s); 0 besides this one" in alone.summary
    assert not alone.meaningful and alone.confidence < 1.0
    assert "1 besides this one" in others.summary and others.meaningful


async def test_k8s_seed_without_namespace_lists_all_or_says_why() -> None:
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        ctx = make_ctx(llm=ScriptedLLM(), alert=make_alert(namespace=None))
        recs = await seeds.run_seeds(ctx)
        entry = ctx.deps.catalog.get("testkube_pod_logs")
        binding = ctx.deps.catalog.role_bindings("k8s_logs")[0][1]
        await close_ctx(ctx)
    state = _by_title(recs, "Kubernetes state: Pods (testkube) (all namespaces)")
    assert state.meaningful and "shop/checkout-7f9c-abc" in state.summary and "batch/cache-warmer" in state.summary
    assert entry is not None
    sql, why = seeds.binding_sql(entry.table, binding, seeds.seed_vars(ctx))
    assert sql is None and why is not None and "needs a filter on namespace, pod" in why


async def test_shared_seed_is_fetched_once() -> None:
    with respx.mock(assert_all_called=False) as m:
        routes = mock_sources(m)
        ctx = make_ctx(llm=ScriptedLLM())
        recs = await seeds.run_seeds(ctx)
        calls = routes["changes"].call_count
        again = await seeds.ensure_seed(ctx, "changes", "changes", lambda: seeds.seed_changes(ctx, ctx.engine))
        await close_ctx(ctx)
    assert [r.id for r in again] == [_by_title(recs, "Change events 48h").id]
    assert routes["changes"].call_count == calls
    assert len(ctx.board) == len(recs)


async def test_alert_query_falls_back_to_the_rule_definition() -> None:
    """A notification without its expression: the rule definition supplies it (same fetch as the rule seed)."""
    with respx.mock(assert_all_called=False) as m:
        routes = mock_sources(m)
        ctx = make_ctx(llm=ScriptedLLM(), alert=make_alert(query=None))
        recs = await seeds.run_seeds(ctx)
        await close_ctx(ctx)
    query = _by_title(recs, "alert's own query over 6h")
    assert query.sql is not None and "query = 'sum(rate(http_requests_total" in query.sql and "> 5" not in query.sql
    assert "from the alert rule definition" in query.summary and query.meaningful
    assert routes["rules"].call_count == 1


async def test_alert_query_without_a_matching_source_is_loud() -> None:
    alert = make_alert(platform="datadog", query="avg(last_5m):avg:system.cpu.user{*} > 90")
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        ctx = make_ctx(llm=ScriptedLLM(), alert=alert)
        recs = await seeds.run_seeds(ctx)
        await close_ctx(ctx)
    rec = _by_title(recs, "alert's own query over 6h")
    assert not rec.meaningful and rec.pinned
    assert "no configured metrics / log-count source speaks datadog" in rec.summary


async def test_alert_expression_replaces_the_binding_default_query() -> None:
    """A log-count binding defaults to the service's error count; the alert's own LogQL must win."""
    with respx.mock(assert_all_called=False):
        ctx = make_ctx(llm=ScriptedLLM(), sources=["loki"])
        (_, binding), = ctx.deps.catalog.role_bindings("log_counts")
        entry = ctx.deps.catalog.get(binding.table)
        await close_ctx(ctx)
    assert entry is not None and binding.filters.get("query")
    expr = 'sum(count_over_time({app="cart"} |= "EmptyCart" [5m]))'
    sql, why = seeds.binding_sql(entry.table, binding, seeds.seed_vars(ctx, query=expr),
                                 replace={"query": expr}, order_by_time=False, limit=None)
    assert why is None and sql is not None
    assert "|= \"EmptyCart\"" in sql and "service_name" not in sql
    default, _ = seeds.binding_sql(entry.table, binding, seeds.seed_vars(ctx), order_by_time=False, limit=None)
    assert default is not None and 'service_name="checkout"' in default


def test_thresholds_keep_critical_and_warning_apart() -> None:
    th = seeds.parse_thresholds(["Warning threshold: 80%, critical threshold: 95%"], [{"comparator": ">"}])
    words = seeds.threshold_words(th, value="85")
    assert words.startswith("CRITICAL threshold (the paging level): value above 95%")
    assert "past the WARNING threshold only, not the critical one" in words
    grafana = seeds.parse_thresholds([], [{"evaluator": {"type": "gt", "params": [300]}, "for": "5m"}])
    assert seeds.threshold_words(grafana) == ("Firing threshold: value above 300 sustained for 5m (single "
                                              "threshold; no separate warning level)")


def test_window_compare_separates_new_rising_and_chronic() -> None:
    b = RoleBinding(table="t", columns={"resource": "endpoint", "count": "n"})
    now = [{"endpoint": "/cart", "n": 40}, {"endpoint": "/pay", "n": 6}, {"endpoint": "/home", "n": 2}]
    base = [{"endpoint": "/cart", "n": 30}, {"endpoint": "/home", "n": 6}]
    lines, data = seeds.window_compare_digest(now, base, b, ("resource",), window_min=20, base_min=60)
    assert data["new_keys"] == ["/pay"]
    assert data["rising_keys"] == ["/cart"]           # 2.0/min vs 0.5/min
    assert data["chronic_keys"] == ["/home"]
    assert lines[0].startswith("analysis window 48 (2.40/min over 20 min) vs preceding hour 36 (0.60/min)")


def test_service_map_digest_reads_both_directions() -> None:
    from omniops.engine.results import QueryResult

    res = QueryResult(sql="", columns=["parent", "child", "count"], types=["VARCHAR", "VARCHAR", "BIGINT"],
                      rows=[("frontend", "checkout", 120), ("checkout", "redis", 300), ("checkout", "payment", 80)],
                      total_rows=3)
    lines = seeds.service_map_digest("checkout")(res, RoleBinding(table="t"))
    assert "upstream callers of 'checkout': frontend -> checkout (120)" in lines
    assert "downstream dependencies of 'checkout': checkout -> redis (300) | checkout -> payment (80)" in lines


def test_query_language_guess_is_shape_based() -> None:
    assert seeds.guess_query_language('sum(rate(x{a="b"}[5m])) > 1', "unknown") == "promql"
    assert seeds.guess_query_language('sum(count_over_time({app="x"} |= "error" [5m]))', None) == "logql"
    assert seeds.guess_query_language("avg(last_5m):avg:system.cpu.user{*} > 90", "datadog") == "datadog"
    assert seeds.strip_threshold("avg(last_5m):avg:system.cpu.user{*} > 90") == "avg:system.cpu.user{*}"
    assert seeds.onset_time.__name__ == "onset_time"
    assert datetime(2026, 1, 1, tzinfo=UTC) < seeds.as_dt("2026-10-01T11:52:00Z")  # type: ignore[operator]
