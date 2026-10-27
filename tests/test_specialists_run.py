"""run_specialist through its real entry point: scripted model turns, real tools, respx-mocked sources."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
import respx
from test_specialists_support import (  # noqa: F401 - source_env is a fixture (pytestmark)
    CHANGE_URL,
    close_ctx,
    ids_in,
    make_ctx,
    mock_sources,
    source_env,
)

from omniops.agent import seeds
from omniops.agent.memory import MemoryStore
from omniops.agent.specialists import base, get_spec, run_specialist, run_specialists
from omniops.agent.types import SpecialistVerdict
from omniops.llm.base import LoopResult, Usage
from omniops.llm.scripted import ScriptedLLM
from omniops.settings import Settings

pytestmark = pytest.mark.usefixtures("source_env")

PREV_LOGS_SQL = ("SELECT message FROM testkube_pod_logs WHERE namespace = 'shop' AND pod = 'checkout-7f9c-abc' "
                 "AND previous = true AND message LIKE '%Memory%'")


def _sql(sql: str, purpose: str) -> dict[str, Any]:
    return {"name": "run_sql", "arguments": {"sql": sql, "purpose": purpose}}


def _found(agent: str, *, confidence: float = 0.8,
           details: dict[str, Any] | None = None) -> Callable[[str], dict[str, Any]]:
    def rule(user: str) -> dict[str, Any]:
        gathered = ids_in(user, "EVIDENCE YOU GATHERED")
        return {"verdict": "found", "headline": f"{agent}: checkout-7f9c-abc OOMKilled (exit 137) 4 restarts",
                "confidence": confidence, "evidence_ids": gathered[:3], "details": details or {},
                "suggested_hypothesis": "checkout pods exceed their memory limit after the batch size change"}
    return rule


def _loop_user(llm: ScriptedLLM) -> str:
    return llm.calls_for("loop_turn", "specialist")[0].user


async def test_infrastructure_specialist_end_to_end() -> None:
    bad = {"verdict": "found", "headline": "OOM", "confidence": 0.7, "evidence_ids": ["E999"]}
    llm = ScriptedLLM(
        loops={"specialist": [
            [_sql(PREV_LOGS_SQL, "previous container logs mentioning memory")],
            "checkout-7f9c-abc was OOMKilled (exit 137, 4 restarts) after 'starting batch of 5000 items'.",
        ]},
        structured={"SpecialistVerdict": [bad, _found("infrastructure", details={"failure_class": "oom"})]})
    with respx.mock(assert_all_called=False) as m:
        routes = mock_sources(m)
        ctx = make_ctx(llm=llm)
        seeded = await seeds.run_seeds(ctx)
        verdict = await run_specialist("infrastructure", ctx)
        await close_ctx(ctx)

    assert verdict.agent == "infrastructure" and verdict.verdict == "found"
    assert verdict.details == {"failure_class": "oom"}
    assert set(verdict.evidence_ids) <= ctx.board.valid_ids() and verdict.evidence_ids
    # The invalid first answer was re-asked once with the exact validation error.
    asks = llm.calls_for("structured", "specialist")
    assert len(asks) == 2 and "do not exist" in asks[1].user and "E999" in asks[1].user
    # Published: pinned, citable, and on the run's verdict list.
    rec = next(r for r in ctx.board.all() if r.kind == "specialist")
    assert rec.agent == "infrastructure" and rec.pinned and rec.meaningful
    assert ctx.specialist_verdicts == [verdict]

    # Seeds: the shared Kubernetes seed was reused (fetched once); previous container logs were seeded.
    k8s = next(r for r in seeded if r.title.startswith("Kubernetes state"))
    prev = next(r for r in ctx.board.all() if r.title.startswith("Previous container logs"))
    assert prev.agent == "infrastructure" and not prev.pinned and "OutOfMemoryError" in prev.rendered
    assert routes["pods"].call_count == 1
    user = _loop_user(llm)
    assert f"{k8s.id} (seed; Kubernetes state" in user and f"{prev.id} (infrastructure; Previous" in user
    scope = user.split("SCOPE:")[1].splitlines()[0]
    assert "testkube_pods" in scope and "prometheus_query_range" in scope and "omni_log_patterns" in scope
    assert "loki_logs" in scope                                     # the table omni_log_patterns reads
    assert "TIME RULE: Anchor (trigger)" in user

    # Tools: the query became evidence attributed to this agent.
    query = next(e for e in ctx.tool_events if e.agent == "infrastructure" and e.name == "run_sql")
    assert query.evidence_id and not query.is_error and ctx.board.get(query.evidence_id).row_count == 1
    assert "specialist.infrastructure" in ctx.stats.timings


async def test_code_change_alone_publishes_shared_seed_and_caps_confidence() -> None:
    change = {"change": {"title": "sync checkout to rev abc123", "ts": "2026-10-01T11:52:00Z", "url": CHANGE_URL,
                         "version": "abc123", "kind": "sync", "minutes_before_onset": 8}}

    def one_id(user: str) -> dict[str, Any]:
        v = _found("code_change", confidence=0.9, details=change)(user)
        return {**v, "evidence_ids": v["evidence_ids"][:1]}

    llm = ScriptedLLM(loops={"specialist": [
        [_sql("SELECT ts, kind, service, title, version, url FROM omni_change_events WHERE service = 'checkout'",
              "checkout changes"),
         _sql("SELECT message FROM loki_logs WHERE service = 'checkout'", "logs (outside this agent's scope)")],
        "abc123 synced 8 min before onset (E1).",
    ]}, structured={"SpecialistVerdict": one_id})
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        ctx = make_ctx(llm=llm)
        verdict = await run_specialist("code_change", ctx)
        seeded = await seeds.run_seeds(ctx)                  # the primary's seed pack runs later
        await close_ctx(ctx)

    changes = ctx.board.get("E1")
    assert changes is not None and changes.agent == "seed" and changes.pinned
    assert changes.title.startswith("Change events 48h")
    assert [r.id for r in seeded if r.title.startswith("Change events 48h")] == ["E1"]   # not fetched twice
    assert verdict.details["change"]["url"] == CHANGE_URL
    assert verdict.confidence == 0.55 and "confidence_capped" in verdict.details   # one item cannot carry 0.9
    scope = _loop_user(llm).split("SCOPE:")[1].splitlines()[0]
    assert "omni_change_events" in scope and "testdeploy_changes" in scope and "loki_logs" not in scope
    events = {e.arguments["sql"]: e for e in ctx.tool_events if e.agent == "code_change" and e.name == "run_sql"}
    changes_q = next(e for sql, e in events.items() if "omni_change_events" in sql)
    outside = next(e for sql, e in events.items() if "loki_logs" in sql)
    assert not changes_q.is_error and ctx.board.get(changes_q.evidence_id).row_count == 1   # scoped engine sees it
    assert outside.is_error and "OutOfScope" in outside.output and "omni_change_events" in outside.output


async def test_not_applicable_without_roles() -> None:
    llm = ScriptedLLM()
    with respx.mock(assert_all_called=False):
        ctx = make_ctx(llm=llm, sources=["prometheus", "loki"])
        dep = await run_specialist("dependency", ctx)
        prior = await run_specialist("prior_incidents", ctx)
        await close_ctx(ctx)
    assert dep.verdict == "not_applicable" and "service_map / traces" in dep.headline
    assert prior.verdict == "not_applicable"
    assert llm.calls == [] and len(ctx.board) == 0 and ctx.specialist_verdicts == []
    assert set(ctx.extra["specialists_skipped"]) == {"dependency", "prior_incidents"}


async def test_prior_incidents_applies_with_memory_only() -> None:
    mem = MemoryStore(":memory:")
    mem.add_episode(run_id="old", title="checkout 5xx after batch size change", service="checkout",
                    summary="checkout OOMKilled", root_cause="batch size 5000", causal_level="trigger")
    llm = ScriptedLLM(loops={"specialist": [
        [_sql("SELECT template, count FROM omni_log_patterns WHERE service = 'checkout' AND error_only = true",
              "recurrence probe: heap errors before onset")],
        "M1 matches; OOM recurred."]},
        structured={"SpecialistVerdict": _found("prior_incidents")})
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        ctx = make_ctx(llm=llm, sources=["prometheus", "loki"], memory=mem)
        verdict = await run_specialist("prior_incidents", ctx)
        await close_ctx(ctx)
    assert verdict.verdict == "found"
    memory = next(r for r in ctx.board.all() if r.kind == "memory")
    assert "M1" in memory.summary and memory.id in _loop_user(llm)


async def test_shadow_specialist_is_private() -> None:
    def cite_shadow(user: str) -> dict[str, Any]:
        return {"verdict": "found", "headline": "OOMKilled", "confidence": 0.5,
                "evidence_ids": ids_in(user, "EVIDENCE YOU GATHERED")[:2]}

    llm = ScriptedLLM(loops={"specialist": [[_sql(PREV_LOGS_SQL, "previous logs")], "OOM (S1)."]},
                      structured={"SpecialistVerdict": cite_shadow})
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        ctx = make_ctx(llm=llm, settings=Settings(shadow_agents=["infrastructure"]))
        verdict = await run_specialist("infrastructure", ctx)
        await close_ctx(ctx)
    assert verdict.verdict == "found" and verdict.evidence_ids and all(e.startswith("S") for e in verdict.evidence_ids)
    assert {r.id[0] for r in ctx.board.all()} == {"S"}              # seeds and queries in the S range
    assert ctx.board.visible() == [] and ctx.specialist_verdicts == []
    [rec] = [r for r in ctx.board.all() if r.kind == "specialist"]  # kept for traces as an S-record only
    assert rec.id.startswith("S") and not rec.pinned
    assert ctx.extra["shadow_verdicts"][0]["agent"] == "infrastructure"
    assert ctx.extra["shadow_verdicts"][0]["record_id"] == rec.id
    assert "(shadow run)" in _loop_user(llm)


async def test_invalid_verdicts_fall_back_to_inconclusive() -> None:
    llm = ScriptedLLM(loops={"specialist": [[_sql(PREV_LOGS_SQL, "previous logs")], "done"]},
                      structured={"SpecialistVerdict": {"verdict": "found", "headline": "x", "evidence_ids": ["E999"]}})
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        ctx = make_ctx(llm=llm)
        verdict = await run_specialist("infrastructure", ctx)
        await close_ctx(ctx)
    assert len(llm.calls_for("structured")) == base.VERDICT_ATTEMPTS
    assert verdict.verdict == "inconclusive" and verdict.confidence == 0.0
    assert verdict.evidence_ids and all(ctx.board.get(e).meaningful for e in verdict.evidence_ids)  # salvaged
    assert verdict.details["fallback"].startswith("no valid structured verdict")
    assert any("no valid structured verdict" in w for w in ctx.warnings)
    rec = next(r for r in ctx.board.all() if r.kind == "specialist")
    assert not rec.meaningful                                         # inconclusive never counts as evidence


async def test_zero_queries_are_pushed_back_once() -> None:
    llm = ScriptedLLM(loops={"specialist": [
        "Looks like an OOM from the seed alone.",
        [_sql(PREV_LOGS_SQL, "previous logs")],
        "Confirmed from the previous container logs.",
    ]}, structured={"SpecialistVerdict": _found("infrastructure", confidence=0.5)})
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        ctx = make_ctx(llm=llm)
        verdict = await run_specialist("infrastructure", ctx)
        await close_ctx(ctx)
    assert ctx.stats.pushbacks["infrastructure.no_queries"] == 1
    turns = llm.calls_for("loop_turn", "specialist")
    assert len(turns) == 3 and "NOT DONE" in str(turns[1].items)
    assert verdict.verdict == "found"


class HangingLLM:
    """A model whose tool loop never returns (the D4 runaway deep dive)."""

    def __init__(self) -> None:
        self.usage = Usage()

    async def run_tool_loop(self, **kw: Any) -> LoopResult:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    async def structured(self, **kw: Any) -> None:
        raise AssertionError("the watchdog fires before the verdict call")


async def test_watchdog_salvages_partial_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "MIN_BUDGET_S", 0.0)
    monkeypatch.setattr(base, "MIN_LOOP_S", 0.0)
    monkeypatch.setattr(base, "VERDICT_RESERVE_S", (0.05, 0.05))
    settings = Settings(specialist_budget_s=1.0, watchdog_grace_s=0.3)
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        ctx = make_ctx(llm=HangingLLM(), settings=settings)
        verdict = await asyncio.wait_for(run_specialist("infrastructure", ctx), timeout=10)
        await close_ctx(ctx)
    assert verdict.verdict == "inconclusive"
    assert verdict.details["fallback"].startswith("watchdog after") and verdict.details["stage"] == "tool loop"
    seeded = [r.id for r in ctx.board.all() if r.meaningful and r.kind == "seed"]
    assert seeded and set(verdict.evidence_ids) == set(seeded)       # partial evidence salvaged and cited
    assert any("watchdog fired" in w for w in ctx.warnings)
    assert ctx.specialist_verdicts == [verdict]


class StrayCancelLLM(ScriptedLLM):
    """The tool loop awaits a fetch another agent owned and cancelled: CancelledError arrives in a task that
    nobody cancelled (``Task.cancelling() == 0``)."""

    async def run_tool_loop(self, **kw: Any) -> LoopResult:
        raise asyncio.CancelledError()


async def test_stray_cancellation_is_salvaged_not_a_silent_zero() -> None:
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        ctx = make_ctx(llm=StrayCancelLLM())
        verdict = await asyncio.create_task(run_specialist("infrastructure", ctx))
        await close_ctx(ctx)
    assert verdict.verdict == "inconclusive" and "shared operation" in verdict.details["fallback"]
    seeded = {r.id for r in ctx.board.all() if r.meaningful and r.kind == "seed"}
    assert set(verdict.evidence_ids) == seeded and ctx.specialist_verdicts == [verdict]   # evidence handed over
    assert any(w.startswith("infrastructure: cancelled during tool loop") for w in ctx.warnings)


async def test_cancelling_a_specialist_still_cancels_it() -> None:
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        ctx = make_ctx(llm=HangingLLM())
        task = asyncio.create_task(run_specialist("infrastructure", ctx))
        await asyncio.sleep(0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await close_ctx(ctx)
    assert ctx.specialist_verdicts == []


async def test_shadow_specialist_warnings_stay_in_its_traces() -> None:
    llm = ScriptedLLM(loops={"specialist": ["Looks like an OOM from the seed alone.", "Still an OOM."]},
                      structured={"SpecialistVerdict": {"verdict": "inconclusive", "headline": "unclear"}})
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        ctx = make_ctx(llm=llm, settings=Settings(shadow_agents=["infrastructure"]))
        await run_specialist("infrastructure", ctx)
        await close_ctx(ctx)
    assert not [w for w in ctx.warnings if "infrastructure" in w]
    assert "infrastructure: ran 0 queries" in ctx.extra["shadow_warnings"]


async def test_run_specialists_runs_the_configured_set() -> None:
    llm = ScriptedLLM(default_text="nothing more to add")
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        ctx = make_ctx(llm=llm, sources=["prometheus", "loki"],
                       settings=Settings(specialists=["dependency", "customer_impact", "bogus"]))
        verdicts = await run_specialists(ctx)
        await close_ctx(ctx)
    by_agent = {v.agent: v for v in verdicts}
    assert set(by_agent) == {"dependency", "customer_impact"}
    assert by_agent["dependency"].verdict == "not_applicable"
    # No structured rule scripted: the deterministic fallback still publishes a verdict.
    assert by_agent["customer_impact"].verdict == "inconclusive"
    assert isinstance(by_agent["customer_impact"], SpecialistVerdict)
    assert get_spec("customer_impact").roles[0] == "rum"
