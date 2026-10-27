"""Regressions in the investigation and chat flows, through their real entry points (scripted model turns,
real tools and gates, respx-mocked Prometheus + Loki): the evidence floor, stray cancellations, shadow
agents, the chat answer after a model error, the memory write after the report, private runs and how-to
triage."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest
import respx
from test_orchestrator_support import (  # noqa: F401 - source_env is a fixture (pytestmark)
    ALERTS,
    ERROR_SQL,
    LOGS_SQL,
    finish,
    hypotheses_step,
    make_alert,
    make_deps,
    mock_sources,
    pinned_clock,
    source_env,
    sql,
)

from omniops.agent import DefaultRunner, answer, investigate, orchestrator
from omniops.agent.chat import triage_question
from omniops.agent.context import RunContext
from omniops.agent.memory import MemoryStore
from omniops.llm.meta import LLMError
from omniops.llm.scripted import ScriptedLLM

pytestmark = pytest.mark.usefixtures("source_env")

SERIES_SQL = ("SELECT ts, value FROM prometheus_query_range "
              "WHERE query = 'sum(rate(http_requests_total{service=\"checkout\",code=~\"5..\"}[5m]))'")
REPORT = "*TL;DR*\nThe checkout 5xx peak was 9.5 req/s [E1]."
MERGE = {"hypotheses": [{"id": "H1", "statement": "payments dependency timeouts cause checkout 5xx",
                         "causal_level": "mechanism", "category": "dependency", "confidence": 0.5}]}
EXTRACTION = {"worth_storing": True, "reason": "a confirmed trigger",
              "incident": {"title": "checkout 5xx from payment timeouts", "summary": "Payment calls timed out.",
                           "root_cause": "payment client timeout", "causal_level": "trigger", "confidence": 0.7}}


class StatusStub:
    """The StatusTrail surface the flows use: run_id, step(), done(), early_card(), stop_requested."""

    def __init__(self, run_id: str = "regrun") -> None:
        self.run_id = run_id
        self.stop_requested = False
        self.steps: list[str] = []

    def step(self, text: str) -> None:
        self.steps.append(text)

    def done(self, text: str) -> None:
        self.steps.append(text)

    async def early_card(self, card: Any) -> None:
        return None


def _good_run(**structured: Any) -> ScriptedLLM:
    return ScriptedLLM(loops={"decision": [[sql(ERROR_SQL, "checkout 5xx rate peak"), sql(LOGS_SQL, "error logs")],
                                           hypotheses_step(), finish("H1 leads.")]},
                       structured=structured or None, complete={"report": REPORT})


# -- F1: the evidence floor counts measurements of the incident, not the alert restating itself --------------


async def test_alert_and_its_own_active_alert_listing_stay_below_the_floor(tmp_path: Any) -> None:
    """Logs fail, metrics and rules come back empty; only the alert list answers, and it lists just this alert."""
    llm = ScriptedLLM(loops={"decision": [
        [sql(SERIES_SQL, "checkout 5xx rate"), sql(LOGS_SQL, "checkout error logs")],
        finish("Nothing conclusive."),
    ]}, complete={"report": "*TL;DR*\nAn unconfirmed lead is the database [E1]."})
    with respx.mock(assert_all_called=False) as m:
        routes = mock_sources(m, broken=True)
        routes["alerts"].respond(json=ALERTS)
        result = await investigate(make_alert(), make_deps(llm, tmp_path=tmp_path), anchor=pinned_clock())

    listing = next(r for r in result.evidence if r.title.startswith("Other active alerts"))
    assert "0 besides this one" in listing.summary and not listing.meaningful
    assert result.status == "insufficient_data"
    assert result.answer_markdown.startswith(":grey_question: *Insufficient data")
    assert "database" not in result.answer_markdown and not llm.calls_for("complete", "report")
    assert "seeds produced zero meaningful evidence items" in result.warnings     # a loud zero, not the alert


# -- F2: a CancelledError nobody asked for is a failure to salvage, never a silent 'done' --------------------


class StrayCancelDecision(ScriptedLLM):
    """The decision loop awaits a fetch another agent owned and cancelled: CancelledError arrives in a task that
    nobody cancelled (``Task.cancelling() == 0``)."""

    async def run_tool_loop(self, **kwargs: Any) -> Any:
        if kwargs.get("role") == "decision":
            raise asyncio.CancelledError()
        return await super().run_tool_loop(**kwargs)


async def test_supervise_reports_a_self_cancelled_body_as_an_error(tmp_path: Any) -> None:
    ctx = RunContext.create(make_deps(ScriptedLLM(), tmp_path=tmp_path), pinned_clock(), 60.0, alert=make_alert())

    async def body() -> None:
        await asyncio.sleep(0)
        raise asyncio.CancelledError()

    outcome, error = await orchestrator.supervise(ctx, body(), hard_s=5.0)
    ctx.close()
    assert outcome == "error" and isinstance(error, asyncio.CancelledError)


async def test_stray_cancel_in_the_decision_loop_does_not_skip_the_rest_of_the_flow(tmp_path: Any) -> None:
    llm = StrayCancelDecision(structured={"HypothesisSet": MERGE}, complete={"report": REPORT})
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        result = await investigate(make_alert(), make_deps(llm, tmp_path=tmp_path), anchor=pinned_clock())

    assert any("cancelled by a shared operation" in w for w in result.warnings)
    assert llm.calls_for("structured", "hypotheses")                 # the merge still ran
    assert result.status == "ok" and "9.5 req/s" in result.answer_markdown


async def test_join_warns_about_a_specialist_that_ended_cancelled_on_its_own(tmp_path: Any) -> None:
    ctx = RunContext.create(make_deps(ScriptedLLM(), tmp_path=tmp_path), pinned_clock(), 60.0, alert=make_alert())
    st = orchestrator._Run(ctx, hard_deadline=time.monotonic() + 60)

    async def gone() -> Any:
        raise asyncio.CancelledError()

    st.children["dependency"] = asyncio.create_task(gone())
    await asyncio.wait({st.children["dependency"]})
    await orchestrator._join_specialists(st)
    ctx.close()
    assert any(w.startswith("dependency: ended cancelled unexpectedly") for w in ctx.warnings)


# -- F3: shadow agents (D8) run and are traced, but nothing about them reaches Slack, the primary or the writer -


async def test_shadow_specialist_never_reaches_the_trail_the_warnings_or_the_primary(tmp_path: Any) -> None:
    def verdict(user: str) -> dict[str, Any]:
        return {"verdict": "inconclusive", "headline": "SHADOW-SECRET-HEADLINE 4 timeouts", "confidence": 0.3,
                "evidence_ids": [], "details": {}}

    llm = ScriptedLLM(loops={"decision": [[sql(ERROR_SQL, "5xx"), sql(LOGS_SQL, "logs")], hypotheses_step(),
                                          finish("done")], "specialist": ["notes"]},
                      structured={"SpecialistVerdict": verdict}, complete={"report": REPORT})
    status = StatusStub("shadowrun")
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        deps = make_deps(llm, tmp_path=tmp_path, specialists=["customer_impact"], shadow_agents=["customer_impact"])
        result = await investigate(make_alert(), deps, status=status, anchor=pinned_clock())

    assert not [s for s in status.steps if "customer_impact" in s or "SHADOW-SECRET" in s]
    assert not [w for w in result.warnings if "customer_impact" in w]
    turns = json.dumps([c.items for c in llm.calls_for("loop_turn", "decision")])
    assert "SPECIALISTS RUNNING CONCURRENTLY" not in turns and result.specialist_verdicts == []
    telemetry = json.loads((tmp_path / "cases" / "shadowrun.json").read_text())["telemetry"]
    assert "customer_impact: ran 0 queries" in telemetry["shadow_warnings"]   # kept for traces only
    assert telemetry["shadow_verdicts"][0]["agent"] == "customer_impact"


# -- F4: a chat loop that dies on a model error has no answer of its own --------------------------------------


async def test_chat_loop_error_does_not_post_the_last_draft_as_the_answer(tmp_path: Any) -> None:
    def broken(items: list[dict[str, Any]]) -> Any:
        raise LLMError("bad request", status=400, model_failure=False)

    llm = ScriptedLLM(loops={"chat": [
        [sql(ERROR_SQL, "checkout 5xx peak")],
        "Let me also check the error logs.",                         # a draft, pushed back for the attestation
        broken,
    ]}, complete={"chat": "*Answer*\nThe peak checkout 5xx rate was 9.5 req/s."})
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        result = await answer("What was the peak checkout 5xx rate in the last hour?",
                              make_deps(llm, tmp_path=tmp_path), anchor=pinned_clock(), run_id="chaterr")

    assert "Let me also check" not in result.answer_markdown
    assert "9.5 req/s" in result.answer_markdown and llm.calls_for("complete")


# -- F5: the memory write never holds or loses the finished report and the case file -------------------------


class SlowMemory(MemoryStore):
    def __init__(self) -> None:
        super().__init__(":memory:")
        self.entered = asyncio.Event()

    async def remember(self, run_result: Any, llm: Any = None) -> list[str]:
        self.entered.set()
        await asyncio.sleep(30)
        return []


async def test_stop_during_the_memory_write_delivers_the_finished_report(tmp_path: Any) -> None:
    memory = SlowMemory()
    status = StatusStub("memstop")
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        deps = make_deps(_good_run(), tmp_path=tmp_path)
        deps.memory = memory
        run = asyncio.create_task(investigate(make_alert(), deps, status=status, anchor=pinned_clock()))
        await asyncio.wait_for(memory.entered.wait(), 60)
        status.stop_requested = True                                  # the Slack Stop button, still live
        result = await asyncio.wait_for(run, 15)                      # well inside Slack's 20 s stop grace

    assert result.status == "ok" and "9.5 req/s" in result.answer_markdown and result.card is not None
    assert any("memory write cancelled" in w for w in result.warnings)
    case = json.loads((tmp_path / "cases" / "memstop.json").read_text())
    assert case["status"] == "ok" and any("memory write cancelled" in w for w in case["warnings"])


async def test_cancel_during_the_memory_write_keeps_the_case_file(tmp_path: Any) -> None:
    memory = SlowMemory()
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        deps = make_deps(_good_run(), tmp_path=tmp_path)
        deps.memory = memory
        run = asyncio.create_task(investigate(make_alert(), deps, anchor=pinned_clock(), run_id="memcancel"))
        await asyncio.wait_for(memory.entered.wait(), 60)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

    case = json.loads((tmp_path / "cases" / "memcancel.json").read_text())
    assert case["status"] == "ok" and "9.5 req/s" in case["answer_markdown"]


# -- F8: a private /investigate run writes nothing to the shared memory -------------------------------------


@pytest.mark.parametrize("private", [True, False])
async def test_private_run_writes_no_shared_memory(tmp_path: Any, private: bool) -> None:
    memory = MemoryStore(":memory:")
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        deps = make_deps(_good_run(MemoryExtraction=EXTRACTION), tmp_path=tmp_path)
        deps.memory = memory
        result = await DefaultRunner(anchor=pinned_clock()).investigate(make_alert(), deps, private=private)

    assert result.status == "ok"
    assert bool(memory.search("checkout", "payment timeouts").incidents) is not private


# -- F7: how-to questions are not held to the live-data gate and the evidence floor -------------------------


HOWTO = "How do I write a PromQL query for p99 request latency?"


@pytest.mark.parametrize(("question", "intent"), [
    (HOWTO, "howto"),
    ("How can I build a LogQL query that counts checkout errors?", "howto"),
    ("What is the syntax for a TraceQL span filter?", "howto"),
    ("What does rate() do in PromQL?", "howto"),
    ("What does this query return for checkout?", "data"),
    ("How do I fix the checkout 5xx errors?", "data"),                 # not about writing a query: measure it
    ("How can I see the current checkout error rate?", "data"),        # live state
    ("What was the peak checkout 5xx rate in the last hour?", "data"),
])
async def test_code_triage_tells_how_to_from_data_questions(tmp_path: Any, question: str, intent: str) -> None:
    ctx = RunContext.create(make_deps(ScriptedLLM(), tmp_path=tmp_path), pinned_clock(), 60.0, question=question)
    triage = await triage_question(ctx)
    ctx.close()
    assert triage.intent == intent


async def test_how_to_question_is_answered_not_floored(tmp_path: Any) -> None:
    text = ("Use histogram_quantile(0.99, sum by (le) (rate(http_request_duration_seconds_bucket[5m]))) for the "
            "p99 over 5-minute windows.")
    llm = ScriptedLLM(loops={"chat": [finish(text)] * 4}, complete={"chat": "*Answer*\nWRITER"})
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        result = await answer(HOWTO, make_deps(llm, tmp_path=tmp_path), anchor=pinned_clock(), run_id="howto")

    assert result.status == "ok" and "histogram_quantile(0.99" in result.answer_markdown
    assert "Insufficient data" not in result.answer_markdown and "chat.live_state" not in result.stats.pushbacks

