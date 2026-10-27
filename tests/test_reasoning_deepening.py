"""Recursive deepening through deepen(): real SQLEngine over a respx-mocked Prometheus, stub model turns."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from omniops.agent.context import Deps, RunContext
from omniops.agent.deepening import deepen
from omniops.agent.types import Alert
from omniops.engine.catalog import Catalog
from omniops.engine.timeutil import TimeAnchor
from omniops.settings import Settings

TRIGGER = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
WINDOW_START = int((TRIGGER - timedelta(minutes=15)).timestamp())
PROM = "http://prom.test/api/v1/query_range"

ERRORS_SQL = ("SELECT ts, value FROM prometheus_query_range "
              "WHERE query = 'sum(rate(http_errors_total[5m]))' ORDER BY ts")
TRAFFIC_SQL = ("SELECT labels->>'job' AS job, avg(value) AS rps FROM prometheus_query_range "
               "WHERE query = 'sum by (job) (rate(http_requests_total[5m]))' GROUP BY 1")


def _q(promql: str, select: str = "labels->>'job' AS job, max(value) AS peak", tail: str = " GROUP BY 1") -> str:
    return f"SELECT {select} FROM prometheus_query_range WHERE query = '{promql}'{tail}"


def _matrix(p: dict[str, str], series: list[dict[str, str]], value: Callable[[dict[str, str], int], float]
            ) -> httpx.Response:
    start, end, step = int(p["start"]), int(p["end"]), int(p["step"])
    result = [{"metric": labels, "values": [[t, str(value(labels, t))] for t in range(start, end + 1, step)]}
              for labels in series]
    return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": result}})


def _empty() -> httpx.Response:
    return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": []}})


def prometheus(request: httpx.Request) -> httpx.Response:
    """A small fake Prometheus: one behaviour per metric name in the PromQL."""
    p = dict(request.url.params)
    q = p["query"]
    if "((" in q:
        return httpx.Response(200, json={"status": "error", "errorType": "bad_data", "error": "parse error at char 5"})
    if "nonexistent" in q:
        return _empty()
    if "pool_timeouts" in q:     # absent in the trigger window, present before it (positive control)
        if int(p["end"]) > WINDOW_START:
            return _empty()
        return _matrix(p, [{"job": "api"}], lambda _l, _t: 2.0)
    if "container_memory" in q:  # 20 pods: a raw dump is far bigger than the judge's view
        pods = [{"pod": f"checkout-{i:02d}"} for i in range(20)]
        return _matrix(p, pods, lambda lab, t: 4e8 + int(lab["pod"][-2:]) * 1e6 + (t % 7))
    spike = int(TRIGGER.timestamp()) - 300
    return _matrix(p, [{"job": "api"}], lambda _l, t: 10.0 if "errors" in q and t >= spike else 1.0)


class StubLLM:
    """structured() double: handlers per role, each called with the user text; every call recorded."""

    def __init__(self, **handlers: Callable[[str], Any]) -> None:
        self.handlers = handlers
        self.calls: list[dict[str, str]] = []

    async def structured(self, *, role: str, system: str, user: str, schema: type, **_: Any) -> Any:
        self.calls.append({"role": role, "system": system, "user": user})
        value = self.handlers[role](user) if role in self.handlers else None
        return schema.model_validate(value) if isinstance(value, dict) else value

    def of(self, role: str, needle: str = "") -> list[str]:
        return [c["user"] for c in self.calls if c["role"] == role and needle in c["user"]]


def claim(user: str) -> str:
    m = re.search(r"CLAIM UNDER TEST: (H[\d.]+)", user)
    assert m, user[:300]
    return m.group(1)


def focus(user: str) -> str:
    m = re.search(r"FOCUS HYPOTHESIS: (H[\d.]+)", user)
    assert m, user[:300]
    return m.group(1)


def check(hid: str, sql: str, level: str = "trigger", statement: str = "") -> dict[str, str]:
    return {"id": hid, "statement": statement or f"{hid}: a deeper sub-claim", "causal_level": level, "sql": sql,
            "confirms_if": "the value rises > 5x within 10 min before the onset", "refutes_if": "flat vs baseline"}


@pytest.fixture(scope="module")
def catalog() -> Iterator[Catalog]:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("PROMETHEUS_URL", "http://prom.test")
        yield Catalog.from_env(only=["prometheus"])


@pytest.fixture
async def ctx(catalog: Catalog) -> Any:
    with respx.mock(assert_all_called=False) as router:
        route = router.get(PROM).mock(side_effect=prometheus)
        deps = Deps(catalog=catalog, llm=StubLLM(), settings=Settings())
        anchor = TimeAnchor.for_trigger(TRIGGER, wall_now=TRIGGER + timedelta(hours=1))
        alert = Alert(platform="grafana", title="Checkout pods OOMKilled", service="checkout")
        c = RunContext.create(deps, anchor, 600, alert=alert)
        c.extra["route"] = route
        e1 = c.board.add_result(await c.engine.query(ERRORS_SQL), agent="seed", purpose="error rate", kind="seed",
                                pinned=True)
        e2 = c.board.add_result(await c.engine.query(TRAFFIC_SQL), agent="primary", purpose="traffic")
        assert (e1.id, e2.id) == ("E1", "E2") and e1.row_count > 0 and "onset" in e1.summary
        yield c
        c.close()


def _hyp(ctx: RunContext, hid: str, statement: str, level: str, category: str, conf: float,
         supporting: list[str]) -> None:
    res = ctx.tracker.update([{"id": hid, "statement": statement, "causal_level": level, "category": category,
                               "confidence": conf, "supporting": supporting}])
    assert res.accepted == [hid], res.text()


async def test_refuted_premise_demotes_parent_and_reforms_once(ctx: RunContext) -> None:
    _hyp(ctx, "H1", "A shared-cache dependency failure drove the 10x error rate", "mechanism", "dependency", 0.5,
         ["E1", "E2"])
    _hyp(ctx, "H2", "Release 2.4.0 introduced the failing code path", "trigger", "code_deploy", 0.35, ["E1"])
    plans = {
        "H1": [check("H1.1", _q("sum by (job) (rate(cache_deletes_total[5m]))")),
               check("H1.2", _q("sum by (job) (rate(cache_misses_total[5m]))"), "mechanism")],
        "H3": [check("H3.1", _q("sum by (job) (rate(publisher_invalidations_total[5m]))"))],
        "H2": [check("H2.1", _q("sum by (job) (rate(release_errors_total[5m]))"))],
    }
    verdicts = {
        "H1.1": {"verdict": "refuted", "rationale": "cache deletes flat at 1.0/s on the only job, all samples",
                 "refutes_parent_premise": True},
        "H1.2": {"verdict": "inconclusive", "rationale": "misses flat; does not decide"},
        "H3.1": {"verdict": "refuted", "rationale": "no invalidations above 1.0/s", "refutes_parent_premise": True},
        "H2.1": {"verdict": "confirmed", "rationale": "release errors 10.0/s from 11:55, 1.0/s before",
                 "terminal": True},
    }
    reform = {"hypotheses": [
        {"id": "H9", "statement": "The publisher's nightly invalidation hit the shared cache before the onset",
         "causal_level": "trigger", "category": "dependency", "confidence": 0.25, "supporting": ["E1"]},
        {"id": "H10", "statement": "A shared-cache dependency failure drove the 10x error rate",
         "causal_level": "mechanism", "category": "dependency", "confidence": 0.5},
    ]}
    llm = StubLLM(deepening=lambda u: {"checks": plans[focus(u)]}, verdict=lambda u: verdicts[claim(u)],
                  hypotheses=lambda _u: reform)
    ctx.deps.llm = llm

    report = await deepen(ctx, max_rounds=3)

    tr = ctx.tracker
    assert report.focus_path == ["H1", "H3", "H2"]           # re-formed H3 deepened below the 0.3 floor
    assert report.demoted == ["H1", "H3"]
    assert tr.get("H1").confidence == 0.3 and tr.get("H1").status == "exhausted"
    assert report.reformed and report.reformed_ids == ["H3"]  # the restated H1 was ignored
    assert len(llm.of("hypotheses")) == 1                     # re-formed once, not after H3 fell
    reform_call = next(c for c in llm.calls if c["role"] == "hypotheses")
    assert "WHAT WAS REFUTED: top-level hypothesis H1" in reform_call["user"]
    assert "EARLIEST MEASURED ONSET: 2026-10-01T11:55" in reform_call["user"]
    assert "earliest onset" in reform_call["system"].lower()
    # The judge sees the parent's established facts and the checks already judged.
    h11 = llm.of("verdict", "CLAIM UNDER TEST: H1.1")[0]
    assert "ESTABLISHED FACTS" in h11 and "- E1 (seed; error rate)" in h11
    h21 = llm.of("verdict", "CLAIM UNDER TEST: H2.1")[0]
    assert "H1.1 [refuted]" in h21 and "H3.1 [refuted]" in h21
    assert report.reached_trigger == "H2.1" and report.stopped == "trigger_reached"
    assert tr.leader().id == "H2.1" and tr.get("H2.1").confidence == 0.6
    kinds = [r.kind for r in ctx.board.all()]
    assert kinds.count("deepening") == 4
    summary = report.summary()
    assert "demoted (premise refuted): H1, H3" in summary and "trigger reached at H2.1" in summary


async def test_empty_without_positive_control_stays_inconclusive(ctx: RunContext) -> None:
    _hyp(ctx, "H1", "Checkout heap exhaustion from a leak", "mechanism", "resource", 0.5, ["E1", "E2"])
    llm = StubLLM(
        deepening=lambda _u: {"checks": [
            check("H1.1", "SELECT ts, value FROM prometheus_query_range WHERE query = 'nonexistent_heap_bytes'"),
            check("H1.2", "SELECT ts, value FROM prometheus_query_range WHERE query = 'sum(('")]},
        # Even a confident "confirmed" cannot turn an empty or failed result into a confirmation.
        verdict=lambda u: {"verdict": "confirmed", "rationale": "clean, exact data", "terminal": True,
                           "corrected_sql": ("SELECT ts, value FROM prometheus_query_range "
                                             "WHERE query = 'nonexistent_heap_bytes_v2'")
                           if "H1.1" in claim(u) else None},
    )
    ctx.deps.llm = llm

    report = await deepen(ctx, max_rounds=1)

    o1, o2 = report.outcomes
    assert (o1.verdict.verdict, o1.decided_by, o1.rechecked) == ("inconclusive", "code", True)
    assert len(o1.evidence_ids) == 2                          # original + the one corrected re-check
    assert (o2.verdict.verdict, o2.decided_by, o2.rechecked) == ("inconclusive", "code", False)
    assert "error is not evidence" in o2.verdict.rationale
    first = llm.of("verdict", "CLAIM UNDER TEST: H1.1")
    assert len(first) == 1 and "MODE: CORRECTION ONLY" in first[0] and "EMPTY WITHOUT POSITIVE CONTROL" in first[0]
    assert "ERROR: " in llm.of("verdict", "CLAIM UNDER TEST: H1.2")[0]
    assert ctx.tracker.get("H1.1").status == "inconclusive" and ctx.tracker.get("H1.1").confidence <= 0.5
    assert ctx.tracker.get("H1").status == "exhausted" and report.exhausted == ["H1"]
    assert not any(o.verdict.verdict == "confirmed" for o in report.outcomes)
    assert any("returned 0 rows" in w for w in ctx.warnings)
    assert any("query failed" in w for w in ctx.warnings)
    empties = [r for r in ctx.board.all() if r.kind == "deepening" and r.row_count == 0 and not r.error]
    assert empties and all(r.empty_verdict == "inconclusive" and not r.meaningful for r in empties)


async def test_positive_control_absence_reaches_the_judge(ctx: RunContext) -> None:
    _hyp(ctx, "H1", "Release 2.4.0 exhausted the DB connection pool", "trigger", "code_deploy", 0.5, ["E1", "E2"])
    sql = _q("sum by (job) (rate(pool_timeouts_total[5m]))")
    seen: list[str] = []

    def judge(user: str) -> dict[str, Any]:
        seen.append(user)
        assert "MODE: CORRECTION ONLY" not in user
        if "REAL ABSENCE" not in user:
            return {"verdict": "inconclusive", "rationale": "no positive control"}
        return {"verdict": "confirmed", "rationale": "pool timeouts absent in the window, present before",
                "terminal": True}

    ctx.deps.llm = StubLLM(deepening=lambda _u: {"checks": [check(
        "H1.1", sql, statement="Pool timeouts were absent in the incident window")]}, verdict=judge)

    report = await deepen(ctx, max_rounds=2)

    rec = ctx.board.get(report.outcomes[0].evidence_ids[0])
    assert rec.row_count == 0 and rec.empty_verdict == "absent" and rec.meaningful
    assert len(seen) == 1 and "POSITIVE CONTROL" in seen[0]
    assert report.outcomes[0].decided_by == "judge" and report.outcomes[0].verdict.verdict == "confirmed"
    assert report.reached_trigger == "H1.1" and report.rounds == 1


async def test_partial_coverage_refutation_gets_one_corrected_recheck(ctx: RunContext) -> None:
    _hyp(ctx, "H1", "Checkout pods hit their 512Mi memory limit", "mechanism", "resource", 0.5, ["E1", "E2"])
    raw = "SELECT ts, labels->>'pod' AS pod, value FROM prometheus_query_range WHERE query = 'container_memory_ws'"
    agg = _q("container_memory_ws", "labels->>'pod' AS pod, max(value) AS peak", " GROUP BY 1 ORDER BY peak DESC")
    dup = "  select ts, value FROM prometheus_query_range\n WHERE query = 'sum(rate(http_errors_total[5m]))' ORDER BY ts;"

    def judge(user: str) -> dict[str, Any]:
        if claim(user) == "H1.2":
            return {"verdict": "inconclusive", "rationale": "rate only"}
        if "MODE: CORRECTION ONLY" in user:
            return {"verdict": "inconclusive", "rationale": "aggregate per pod", "corrected_sql": agg}
        if "CORRECTED RESULT" in user:
            assert "ORIGINAL RESULT" in user and "FIRST VERDICT: inconclusive" in user
            return {"verdict": "refuted", "rationale": "all 20 pods peak at 0.42 GB, below 512Mi",
                    "refutes_parent_premise": True}
        assert "[truncated] showing" in user
        return {"verdict": "refuted", "rationale": "max below the limit", "refutes_parent_premise": True}

    llm = StubLLM(deepening=lambda _u: {"checks": [check("H1.1", raw, "mechanism"), check("H1.2", dup, "mechanism")]},
                  verdict=judge)
    ctx.deps.llm = llm
    route = ctx.extra["route"]
    calls_before = route.call_count

    report = await deepen(ctx, max_rounds=1)

    o1, o2 = report.outcomes
    assert o1.rechecked and len(o1.evidence_ids) == 2
    assert o1.verdict.verdict == "refuted" and o1.decided_by == "judge"
    assert len(llm.of("verdict", "CLAIM UNDER TEST: H1.1")) == 3   # judged, correction, re-check
    assert report.demoted == ["H1"] and ctx.tracker.get("H1").confidence == 0.3
    # Duplicate guard: the check identical to E1 reuses E1 instead of querying again.
    assert o2.evidence_ids == ["E1"] and any("duplicate guard" in n for n in report.notes)
    assert route.call_count - calls_before == 1     # one fetch: the re-check is served by the shared cache
    # H1 fell: re-formation ran once, and its empty answer is loud.
    assert report.reformed and any("re-forming hypotheses" in w for w in ctx.warnings)


async def test_unverified_tables_are_rejected_and_no_verdict_is_never_a_confirmation(ctx: RunContext) -> None:
    _hyp(ctx, "H1", "Release 2.4.0 raised the error rate", "trigger", "code_deploy", 0.5, ["E1", "E2"])
    attempts: list[str] = []

    def decompose(user: str) -> dict[str, Any]:
        attempts.append(user)
        if len(attempts) == 1:
            return {"checks": [check("H1.1", "SELECT * FROM tempo_search WHERE service = 'checkout'")]}
        assert "PREVIOUS ATTEMPT REJECTED" in user and "tempo_search" in user
        return {"checks": [check("H1.1", _q("sum by (job) (rate(http_errors_total{version=\"2.4.0\"}[5m]))"))]}

    ctx.deps.llm = StubLLM(deepening=decompose)   # no verdict handler: the judge returns nothing

    report = await deepen(ctx, max_rounds=1)

    assert len(attempts) == 2 and "TABLES YOU MAY USE" in attempts[0] and "prometheus_query_range" in attempts[0]
    (o,) = report.outcomes
    assert o.verdict.verdict == "inconclusive" and o.decided_by == "code"
    assert ctx.tracker.get("H1.1").status == "inconclusive"
    assert any("no valid verdict" in w for w in ctx.warnings)


async def test_nothing_to_deepen_is_loud(ctx: RunContext) -> None:
    report = await deepen(ctx, max_rounds=3)
    assert report.stopped == "no_hypotheses" and report.rounds == 0
    assert any("0 hypotheses to deepen" in w for w in ctx.warnings)
