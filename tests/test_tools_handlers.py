"""Model tools through their real entry points (the ToolSpec handlers) against a mocked Prometheus."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from omniops.agent.context import Deps, RunContext
from omniops.agent.evidence import EvidenceBoard
from omniops.agent.leads import get_leads
from omniops.agent.tools import build_tools
from omniops.agent.types import EvidenceRecord
from omniops.engine import catalog as catalog_mod
from omniops.engine import timeutil
from omniops.engine.catalog import Catalog
from omniops.engine.engine import FetchCache, SQLEngine
from omniops.engine.loader import load_all_manifests
from omniops.llm.base import ToolOutput, ToolSpec
from omniops.settings import Settings

TRIGGER = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
PROM = "http://prom.test/api/v1"
RANGE_SQL = "SELECT ts, labels->>'job' AS job, value FROM prometheus_query_range WHERE query = 'up' ORDER BY ts"
_MANIFESTS: dict[str, Any] = {}


def prometheus_catalog(monkeypatch: pytest.MonkeyPatch) -> Catalog:
    """Catalog.from_env with only the prometheus source; manifests are parsed once per session."""
    if not _MANIFESTS:
        _MANIFESTS.update(load_all_manifests(strict=False)[0])
    monkeypatch.setattr(catalog_mod, "load_all_manifests", lambda strict=False: (dict(_MANIFESTS), []))
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom.test")
    monkeypatch.delenv("OMNIOPS_CONFIG", raising=False)
    return Catalog.from_env(only=["prometheus"])


@dataclass
class StubUpdate:
    accepted: list[str]

    def text(self) -> str:
        return f"recorded {len(self.accepted)} hypothesis(es): {', '.join(self.accepted)}"


@dataclass
class StubTracker:
    calls: list[list[dict[str, Any]]] = field(default_factory=list)

    def update(self, items: list[dict[str, Any]]) -> StubUpdate:
        self.calls.append(items)
        return StubUpdate([str(h.get("id")) for h in items if h.get("statement")])


def _matrix(p: dict[str, str], labels: dict[str, str] | None = None, base: float = 1.0) -> dict[str, Any]:
    start, end, step = int(p["start"]), int(p["end"]), int(p["step"])
    values = [[t, str(base * (10 if t >= TRIGGER.timestamp() - 300 else 1))] for t in range(start, end + 1, step)]
    return {"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": {"job": "api", **(labels or {})}, "values": values}]}}


def _empty() -> dict[str, Any]:
    return {"status": "success", "data": {"resultType": "matrix", "result": []}}


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch) -> Iterator[RunContext]:
    cat = prometheus_catalog(monkeypatch)
    anchor = timeutil.TimeAnchor.for_trigger(TRIGGER, wall_now=TRIGGER + timedelta(hours=1))
    cache = FetchCache()
    engine = SQLEngine(cat, anchor, cache=cache)
    settings = Settings(shadow_agents=["shadow_rca"])
    c = RunContext(run_id="t1", deps=Deps(catalog=cat, llm=object(), settings=settings),  # type: ignore[arg-type]
                   anchor=anchor, engine=engine, cache=cache, board=EvidenceBoard(), tracker=StubTracker(),
                   deadline=time.monotonic() + 600)
    c._engines.append(engine)
    yield c
    c.close()


def tools_by_name(tools: list[ToolSpec]) -> dict[str, ToolSpec]:
    return {t.name: t for t in tools}


async def call(tools: dict[str, ToolSpec], name: str, **args: Any) -> ToolOutput:
    return await tools[name].handler(args)


# ---------------------------------------------------------------------------------------------
# run_sql: evidence ids, output, de-dup
# ---------------------------------------------------------------------------------------------


@respx.mock
async def test_run_sql_returns_evidence_id_and_records_everything(ctx: RunContext) -> None:
    route = respx.get(f"{PROM}/query_range").mock(side_effect=lambda r: httpx.Response(200, json=_matrix(dict(r.url.params))))
    t = tools_by_name(build_tools(ctx))
    assert set(t) == {"search_catalog", "describe_table", "run_sql", "record_hypotheses", "finish"}

    out = await call(t, "run_sql", sql=RANGE_SQL, purpose="api up over the incident window")
    assert not out.is_error and out.evidence_id == "E1"
    header, body = out.content.split("\n", 1)
    assert header == f"E1 | purpose: api up over the incident window | sql: {RANGE_SQL}"
    assert body.startswith("[result] ")
    assert "[digest]" in out.content and "onset" in out.content
    rec = ctx.board.get("E1")
    assert rec is not None and rec.meaningful and rec.confidence == 1.0
    assert rec.title == "api up over the incident window" and rec.tables == ["prometheus_query_range"]
    assert rec.window is not None and rec.summary.startswith(f"{rec.row_count} row(s)")
    assert "digest:" in rec.summary and "rows (first 3 of" in rec.summary
    assert route.call_count == 1
    assert ctx.stats.sql_queries == 1 and ctx.stats.api_requests >= 1
    ev = ctx.tool_events[-1]
    assert (ev.agent, ev.name, ev.evidence_id, ev.is_error) == ("primary", "run_sql", "E1", False)
    assert ctx.stats.timings["tool.run_sql"] >= 0

    out2 = await call(t, "run_sql", sql=RANGE_SQL.replace("'up'", "'process_up'"), purpose="second")
    assert out2.evidence_id == "E2" and ctx.board.meaningful_count() == 2


@respx.mock
async def test_run_sql_header_echoes_purpose_and_a_shortened_query(ctx: RunContext) -> None:
    """Results map back to their queries without re-running them: the header line carries the purpose and
    the query (one line, cut to ~160 chars keeping the table and the trailing window)."""
    respx.get(f"{PROM}/query_range").mock(side_effect=lambda r: httpx.Response(200, json=_matrix(dict(r.url.params))))
    t = tools_by_name(build_tools(ctx))
    long_sql = ("SELECT ts, labels->>'job' AS job, value\nFROM prometheus_query_range\n"
                "WHERE query = 'sum by (job) (rate(http_server_requests_seconds_count{status=~\"5..\"}[5m]))'\n"
                "  AND ts >= now() - INTERVAL 2 HOUR AND ts < now() - INTERVAL 1 HOUR\nORDER BY ts DESC")
    purpose = "5xx request rate per job in the hour before the alert, " * 5
    out = await call(t, "run_sql", sql=long_sql, purpose=purpose)
    assert not out.is_error and out.evidence_id == "E1"
    header, body = out.content.split("\n", 1)
    eid, said_purpose, said_sql = header.split(" | ", 2)
    assert eid == "E1" and body.startswith("[result] ")
    assert said_purpose.startswith("purpose: 5xx request rate per job in the hour before the alert,")
    assert said_purpose.endswith(" ...") and len(said_purpose) <= len("purpose: ") + 160
    assert said_sql.startswith("sql: SELECT ts, labels->>'job' AS job, value FROM prometheus_query_range WHERE")
    assert said_sql.endswith("INTERVAL 1 HOUR ORDER BY ts DESC") and " ... " in said_sql
    assert "\n" not in said_sql and len(said_sql) <= len("sql: ") + 160
    rec = ctx.board.get("E1")
    assert rec is not None and rec.title == purpose.strip()    # the evidence record keeps the full purpose

    short = await call(t, "run_sql", sql=RANGE_SQL, purpose="")
    assert short.content.split("\n", 1)[0] == f"{short.evidence_id} | sql: {RANGE_SQL}"


@respx.mock
async def test_duplicates_return_the_earlier_id_and_hard_stop_after_five(ctx: RunContext) -> None:
    route = respx.get(f"{PROM}/query_range").mock(side_effect=lambda r: httpx.Response(200, json=_matrix(dict(r.url.params))))
    t = tools_by_name(build_tools(ctx))
    first = await call(t, "run_sql", sql=RANGE_SQL, purpose="p")
    assert first.evidence_id == "E1"

    # Whitespace-only differences are byte-identical after normalisation.
    dup = await call(t, "run_sql", sql=RANGE_SQL.replace(" FROM", "\n   FROM"), purpose="p again")
    assert dup.content.startswith("duplicate of E1") and dup.evidence_id == "E1" and not dup.stop
    assert len(ctx.board) == 1 and route.call_count == 1

    # Near-duplicate: timestamps inside the same 5-minute buckets.
    base = ("SELECT avg(value) AS v FROM prometheus_query_range WHERE query = 'up' AND ts BETWEEN "
            "TIMESTAMP '2026-10-01 11:{a}:00+00' AND TIMESTAMP '2026-10-01 12:0{b}:00+00'")
    win = await call(t, "run_sql", sql=base.format(a="45", b="5"), purpose="window")
    assert win.evidence_id == "E2"
    near = await call(t, "run_sql", sql=base.format(a="46", b="6"), purpose="shifted 1 min")
    assert near.content.startswith("duplicate of E2") and "near-duplicate" in near.content
    other = await call(t, "run_sql", sql=base.format(a="30", b="5"), purpose="different bucket")
    assert other.evidence_id == "E3"

    for _ in range(2):
        assert not (await call(t, "run_sql", sql=RANGE_SQL, purpose="again")).stop
    stop = await call(t, "run_sql", sql=RANGE_SQL, purpose="fifth duplicate")
    assert stop.stop and stop.content.startswith("STOPPED")
    assert any("duplicate" in w for w in ctx.warnings)


@respx.mock
async def test_same_step_twins_share_one_fetch_and_one_evidence_id(ctx: RunContext) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=_matrix(dict(request.url.params)))

    route = respx.get(f"{PROM}/query_range").mock(side_effect=slow)
    t = tools_by_name(build_tools(ctx))
    first, twin = await asyncio.gather(call(t, "run_sql", sql=RANGE_SQL, purpose="a"),
                                       call(t, "run_sql", sql=RANGE_SQL, purpose="b"))
    assert first.evidence_id == twin.evidence_id == "E1" and twin.content.startswith("duplicate of E1")
    assert route.call_count == 1 and len(ctx.board) == 1 and ctx.stats.sql_queries == 1


@respx.mock
async def test_a_shifted_window_may_repeat_a_result_that_looked_truncated(ctx: RunContext) -> None:
    respx.get(f"{PROM}/query_range").mock(return_value=httpx.Response(200, json=_empty()))
    t = tools_by_name(build_tools(ctx))
    base = ("SELECT * FROM prometheus_query_range WHERE query = 'up' AND ts BETWEEN "
            "TIMESTAMP '2026-10-01 11:{a}:00+00' AND TIMESTAMP '2026-10-01 12:05:00+00'")
    empty = await call(t, "run_sql", sql=base.format(a="45"), purpose="empty series")
    assert ctx.board.get(empty.evidence_id or "").empty_verdict == "inconclusive"  # type: ignore[union-attr]
    shifted = await call(t, "run_sql", sql=base.format(a="46"), purpose="shifted window")
    assert shifted.evidence_id == "E2" and not shifted.content.startswith("duplicate")
    # The byte-identical query is still blocked: the cache would return the same rows.
    assert (await call(t, "run_sql", sql=base.format(a="46"), purpose="same")).content.startswith("duplicate of E2")


@respx.mock
async def test_errors_carry_lessons_keep_stubs_and_cap_retries_per_shape(ctx: RunContext) -> None:
    respx.get(f"{PROM}/query_range").mock(return_value=httpx.Response(500, text="boom"))
    respx.get(f"{PROM}/query").mock(return_value=httpx.Response(429, headers={"Retry-After": "0"}))
    t = tools_by_name(build_tools(ctx))

    unknown = await call(t, "run_sql", sql="SELECT * FROM datadog_metrics WHERE query = 'x'", purpose="guess")
    assert unknown.is_error and unknown.content.startswith("ERROR (UnknownTableError): ")
    assert "\nLesson: search the catalog" in unknown.content
    stub = ctx.board.get(unknown.evidence_id or "")
    assert stub is not None and stub.error and not stub.meaningful and stub.confidence == 0.0

    missing = await call(t, "run_sql", sql="SELECT * FROM prometheus_query_range", purpose="no filter")
    assert missing.content.startswith("ERROR (MissingFilterError)") and "add the required filter" in missing.content

    limited = await call(t, "run_sql", sql="SELECT * FROM prometheus_query WHERE query = 'up'", purpose="rl")
    assert limited.content.startswith("ERROR (RateLimitError)") and "NOT a schema problem" in limited.content

    # An identical repeat of an errored query is a legitimate retry, but each shape gets at most 2 retries.
    shape = "SELECT value FROM prometheus_query_range WHERE query = '{q}'"
    outs = [await call(t, "run_sql", sql=shape.format(q=q), purpose="retry") for q in ("a", "a", "b")]
    assert all(o.content.startswith("ERROR (FetchError)") for o in outs)
    assert "2 retries left" in outs[0].content and "no retries left" in outs[2].content
    refused = await call(t, "run_sql", sql=shape.format(q="c"), purpose="4th try")
    assert refused.content.startswith("ERROR (RetryLimit)")
    assert ctx.stats.sql_errors == 6 and ctx.board.meaningful_count() == 0
    assert all(e.is_error for e in ctx.tool_events)


@respx.mock
async def test_sanitizer_fixes_are_applied_and_reported(ctx: RunContext) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["query"])
        return httpx.Response(200, json=_matrix(dict(request.url.params)))

    respx.get(f"{PROM}/query_range").mock(side_effect=handler)
    t = tools_by_name(build_tools(ctx))
    messy = ("```sql\nSELECT ts, labels->>\"job\" AS job, value FROM prometheus_query_range "
             "WHERE query == \"up\" AND value IS NOT None;\n```")
    out = await call(t, "run_sql", sql=messy, purpose="sanitized")
    assert not out.is_error, out.content
    assert seen == ["up"]
    note = out.content.split("[sanitizer] fixed: ", 1)[1]
    for fix in ("markdown code fence", "trailing semicolon", "'==' -> '='", 'double-quoted literal "up"',
                'JSON key "job"', "Python None -> NULL"):
        assert fix in note
    assert ctx.board.get("E1").sql.startswith("SELECT ts, labels->>'job'")  # type: ignore[union-attr]

    multi = await call(t, "run_sql", sql="SELECT 1; SELECT 2", purpose="two")
    assert multi.is_error and multi.content.startswith("ERROR (SQLSanitizeError)") and "separate run_sql" in multi.content
    blank = await call(t, "run_sql", purpose="nothing")
    assert blank.is_error and "non-empty 'sql'" in blank.content


@respx.mock
async def test_envelopes_and_json_string_arguments_are_unwrapped(ctx: RunContext) -> None:
    respx.get(f"{PROM}/query_range").mock(side_effect=lambda r: httpx.Response(200, json=_matrix(dict(r.url.params))))
    t = tools_by_name(build_tools(ctx))
    out = await t["run_sql"].handler({"arguments": '{"sql": "' + RANGE_SQL + '", "purpose": "wrapped"}'})
    assert out.evidence_id == "E1" and ctx.board.get("E1").title == "wrapped"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------------------------
# Empty results, discovery-only lookups, shadow agents
# ---------------------------------------------------------------------------------------------


@respx.mock
async def test_empty_results_absent_with_control_vs_inconclusive(ctx: RunContext) -> None:
    window_start = int((TRIGGER - timedelta(minutes=15)).timestamp())

    def handler(request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        control = int(float(p["end"])) <= window_start
        if p["query"] == "absent_metric" and control:
            return httpx.Response(200, json=_matrix(p))
        return httpx.Response(200, json=_empty())

    respx.get(f"{PROM}/query_range").mock(side_effect=handler)
    t = tools_by_name(build_tools(ctx))
    absent = await call(t, "run_sql", sql="SELECT * FROM prometheus_query_range WHERE query = 'absent_metric'",
                        purpose="errors in window")
    rec = ctx.board.get(absent.evidence_id or "")
    assert rec is not None and rec.empty_verdict == "absent" and rec.meaningful
    assert "absent: 0 rows in window, positive control returned data elsewhere" in rec.summary

    unknown = await call(t, "run_sql", sql="SELECT * FROM prometheus_query_range WHERE query = 'typo_metric'",
                         purpose="typo")
    rec2 = ctx.board.get(unknown.evidence_id or "")
    assert rec2 is not None and rec2.empty_verdict == "inconclusive" and not rec2.meaningful
    assert "INCONCLUSIVE" in unknown.content
    assert ctx.board.meaningful_count() == 1
    assert any("0 rows" in w for w in ctx.warnings)


@respx.mock
async def test_discovery_lookups_are_kept_but_never_count(ctx: RunContext) -> None:
    respx.get(f"{PROM}/label/job/values").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": ["api", "node"]}))
    t = tools_by_name(build_tools(ctx))
    out = await call(t, "run_sql", sql="SELECT value FROM prometheus_label_values WHERE label = 'job'", purpose="jobs")
    rec = ctx.board.get(out.evidence_id or "")
    assert rec is not None and rec.row_count == 2 and not rec.meaningful and rec.data["discovery_only"]
    assert "discovery-only" in rec.summary and ctx.board.meaningful_count() == 0


@respx.mock
async def test_shadow_agents_get_their_own_id_range(ctx: RunContext) -> None:
    respx.get(f"{PROM}/query_range").mock(side_effect=lambda r: httpx.Response(200, json=_matrix(dict(r.url.params))))
    primary = tools_by_name(build_tools(ctx))
    shadow = tools_by_name(build_tools(ctx, agent="shadow_rca", allow_finish=False, allow_hypotheses=False))
    assert set(shadow) == {"search_catalog", "describe_table", "run_sql"}
    s = await call(shadow, "run_sql", sql=RANGE_SQL, purpose="shadow look")
    p = await call(primary, "run_sql", sql=RANGE_SQL, purpose="primary look")
    assert (s.evidence_id, p.evidence_id) == ("S1", "E1")
    assert ctx.board.valid_ids() == {"E1"} and "S1" not in ctx.board.compact_view()


# ---------------------------------------------------------------------------------------------
# Catalog tools and scope
# ---------------------------------------------------------------------------------------------


async def test_search_and_describe(ctx: RunContext) -> None:
    t = tools_by_name(build_tools(ctx))
    found = await call(t, "search_catalog", query="prometheus range query")
    assert "prometheus_query_range" in found.content and "REQUIRED filters: query" in found.content
    none = await call(t, "search_catalog", query="zzzz qqqq")
    assert none.content.startswith("No tables match") and "CONFIGURED SOURCES" in none.content
    desc = await call(t, "describe_table", table="prometheus_query_range")
    assert desc.content.startswith("TABLE prometheus_query_range") and "VERIFIED QUERY SHAPES" in desc.content
    bad = await call(t, "describe_table", table="datadog_logs")
    assert bad.is_error and bad.content.startswith("ERROR (KeyError): table 'datadog_logs' does not exist")
    assert "Lesson: search_catalog" in bad.content


@respx.mock
async def test_out_of_scope_tables_are_refused_with_the_scope_list(ctx: RunContext) -> None:
    route = respx.get(f"{PROM}/label/job/values").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": ["api"]}))
    t = tools_by_name(build_tools(ctx, agent="infrastructure", allowed_tables={"prometheus_query_range"},
                                  allow_hypotheses=False))
    out = await call(t, "run_sql", sql="SELECT value FROM prometheus_label_values WHERE label = 'job'", purpose="x")
    assert out.is_error and out.content.startswith("ERROR (OutOfScope): prometheus_label_values")
    assert "use only these tables: prometheus_query_range" in out.content
    assert route.call_count == 0 and len(ctx.board) == 0
    desc = await call(t, "describe_table", table="prometheus_rules")
    assert desc.content.startswith("ERROR (OutOfScope)")
    found = await call(t, "search_catalog", query="prometheus")
    assert "prometheus_query_range" in found.content and "prometheus_rules" not in found.content


# ---------------------------------------------------------------------------------------------
# Artifact leads, hypotheses, finish
# ---------------------------------------------------------------------------------------------


@respx.mock
async def test_leads_found_in_raw_output_followed_and_enforced_at_finish(ctx: RunContext) -> None:
    run_url = "https://github.com/acme/shop/actions/runs/987654321"

    def handler(request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        labels = {"deploy_run": run_url, "ticket": "OPS-4521"} if p["query"] == "deploys" else {}
        return httpx.Response(200, json=_matrix(p, labels))

    respx.get(f"{PROM}/query_range").mock(side_effect=handler)
    t = tools_by_name(build_tools(ctx))
    first = await call(t, "run_sql", sql="SELECT ts, labels, value FROM prometheus_query_range WHERE query = 'deploys'",
                       purpose="deploy markers")
    assert "[leads] new cross-platform references" in first.content
    assert run_url in first.content and "OPS-4521" in first.content
    leads = get_leads(ctx)
    # No github/jira source is configured here, so both are unresolvable: reported as gaps, never pushed back.
    assert {x.status for x in leads.all()} == {"unresolvable"}
    assert any("no github source is configured" in g for g in leads.gaps())

    # With a GitHub source available the lead must be followed or dismissed before finish is accepted.
    leads.available = {"github", "jira"}
    for x in leads.all():
        x.status = "unfollowed"
    follow = await call(t, "run_sql", sql="SELECT * FROM prometheus_query_range WHERE query = 'run 987654321'",
                        purpose="follow the CI run")
    assert f"[leads] followed by {follow.evidence_id}: L1" in follow.content
    pushback = await call(t, "finish", summary="done", self_executable_gaps=[])
    assert not pushback.stop and pushback.content.startswith("NOT FINISHED") and "OPS-4521" in pushback.content
    done = await call(t, "finish", summary="CI run E2 broke deploys", self_executable_gaps=[],
                      dismissed_leads=[{"lead": "L2", "reason": "ticket is the alert's own page"}])
    assert done.stop and done.content == "CI run E2 broke deploys"
    assert leads.resolve("L2").status == "dismissed"  # type: ignore[union-attr]
    assert ctx.extra["finish"]["primary"]["dismissed_leads"] == ["L2"]


@respx.mock
async def test_leads_in_seed_evidence_are_surfaced_and_followed(ctx: RunContext) -> None:
    respx.get(f"{PROM}/query_range").mock(side_effect=lambda r: httpx.Response(200, json=_matrix(dict(r.url.params))))
    get_leads(ctx).available = {"github"}
    ctx.board.add(EvidenceRecord(
        id="", kind="seed", agent="seed", title="change events 48h before", summary="1 change event", pinned=True,
        tables=["prometheus_query_range"],
        rendered="| ts | title |\n| 11:57 | deploy via https://github.com/acme/shop/actions/runs/5550001234 |"))
    t = tools_by_name(build_tools(ctx))
    pushback = await call(t, "finish", summary="done", self_executable_gaps=[])
    assert not pushback.stop and "L1 [GitHub Actions run] https://github.com/acme/shop/actions/runs/5550001234 (seen in E1)" \
        in pushback.content
    follow = await call(t, "run_sql", sql="SELECT * FROM prometheus_query_range WHERE query = 'ci_run{id=\"5550001234\"}'",
                        purpose="follow the CI run")
    assert f"[leads] followed by {follow.evidence_id}: L1" in follow.content
    assert get_leads(ctx).gaps() == []
    # Evidence that lands between two model calls (a specialist's seed) is announced with the next result.
    ctx.board.add(EvidenceRecord(id="", kind="seed", agent="code_change", title="recent PRs", summary="1 PR",
                                 tables=["prometheus_query_range"], rendered="merged https://github.com/acme/shop/pull/8"))
    later = await call(t, "run_sql", sql=RANGE_SQL, purpose="unrelated")
    assert "[leads] new cross-platform references" in later.content
    assert "L2 [GitHub pull request] https://github.com/acme/shop/pull/8 (seen in E3)" in later.content


async def test_finish_gaps_pushback_and_on_finish_hook(ctx: RunContext) -> None:
    verdicts: list[str | None] = ["readiness: need a second meaningful item", None]

    async def on_finish(args: dict[str, Any]) -> str | None:
        return verdicts.pop(0)

    t = tools_by_name(build_tools(ctx, on_finish=on_finish))
    gaps = await call(t, "finish", summary="s", self_executable_gaps=[
        {"description": "error rate yesterday", "sql": "SELECT 1"}])
    assert not gaps.stop and gaps.content.startswith("NOT FINISHED: you listed 1 gap(s)")
    assert "SQL: SELECT 1" in gaps.content and ctx.stats.pushbacks["primary.self_executable_gaps"] == 1
    hook = await call(t, "finish", summary="s", self_executable_gaps=[])
    assert not hook.stop and hook.content == "readiness: need a second meaningful item"
    final = await call(t, "finish", summary="final answer", self_executable_gaps=[])
    assert final.stop and final.data["finished"] and final.content == "final answer"
    assert any("0 meaningful evidence" in w for w in ctx.warnings)
    assert any("without running a single query" in w for w in ctx.warnings)


async def test_gap_continuations_are_limited_then_disclosed(ctx: RunContext) -> None:
    t = tools_by_name(build_tools(ctx))
    gap = [{"description": "p95 latency", "sql": "SELECT 2"}]
    for _ in range(2):
        assert not (await call(t, "finish", summary="s", self_executable_gaps=gap)).stop
    final = await call(t, "finish", summary="s", self_executable_gaps=gap)
    assert final.stop and ctx.extra["open_gaps"] == ["p95 latency"]


async def test_record_hypotheses_goes_through_the_tracker(ctx: RunContext) -> None:
    t = tools_by_name(build_tools(ctx))
    hyps = [{"id": "H1", "statement": "deploy 1.4.2 raised error rate", "causal_level": "trigger",
             "confidence": 0.4, "supporting": ["E1"]},
            {"id": "H2", "statement": "db saturation", "causal_level": "mechanism", "confidence": 0.3}]
    out = await call(t, "record_hypotheses", hypotheses=hyps)
    assert out.content == "recorded 2 hypothesis(es): H1, H2" and not out.is_error
    assert ctx.tracker.calls == [hyps]  # type: ignore[attr-defined]
    wrapped = await call(t, "record_hypotheses", hypotheses={"hypotheses": hyps[:1]})
    assert wrapped.content.endswith("H1")
    empty = await call(t, "record_hypotheses", hypotheses=[])
    assert empty.is_error and any("no usable hypotheses" in w for w in ctx.warnings)
    schema = t["record_hypotheses"].parameters["properties"]["hypotheses"]["items"]
    assert schema["properties"]["causal_level"]["enum"] == ["symptom", "mechanism", "trigger"]
    assert "verdict_notes" not in schema["properties"]
