"""investigate() through its real entry point: scripted model turns, the real tools, gates, seeds,
specialists, deepening, report, card, memory and case file, against respx-mocked Prometheus + Loki."""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import httpx
import pytest
import respx
from test_orchestrator_support import (  # noqa: F401 - source_env is a fixture (pytestmark)
    ERROR_SQL,
    LOGS_SQL,
    finish,
    hypotheses_step,
    ids_in,
    loki_logs,
    make_alert,
    make_deps,
    mock_sources,
    pinned_clock,
    source_env,
    sql,
)

from omniops.agent import investigate
from omniops.agent.casefile import case_json, write_case
from omniops.agent.memory import MemoryStore
from omniops.llm.scripted import ScriptedLLM

pytestmark = pytest.mark.usefixtures("source_env")

PAYMENT_SQL = ("SELECT count(*) AS payment_errors FROM loki_logs WHERE service = 'checkout' "
               "AND message LIKE '%payment%'")
CHECK_SQL = "SELECT count(*) AS timeouts FROM loki_logs WHERE service = 'checkout' AND message LIKE '%3000ms%'"
SERIES_SQL = ("SELECT ts, value FROM prometheus_query_range "
              "WHERE query = 'sum(rate(http_requests_total{service=\"checkout\",code=~\"5..\"}[5m]))'")


class StatusStub:
    """The StatusTrail surface the orchestrator uses: run_id, step(), early_card(), stop_requested."""

    def __init__(self, run_id: str = "slackrun01") -> None:
        self.run_id = run_id
        self.stop_requested = False
        self.steps: list[str] = []
        self.early_cards: list[Any] = []

    def step(self, text: str) -> None:
        self.steps.append(text)

    async def early_card(self, card: Any) -> None:
        self.early_cards.append(card)


class HangingLLM(ScriptedLLM):
    """A model whose decision loop never returns: the absolute watchdog must salvage the run."""

    async def run_tool_loop(self, **kwargs: Any) -> Any:
        if kwargs.get("role") == "decision":
            await asyncio.sleep(3600)
        return await super().run_tool_loop(**kwargs)


def _turn_text(llm: ScriptedLLM, role: str) -> str:
    return json.dumps([c.items for c in llm.calls_for("loop_turn", role)])


def _report(user: str) -> str:
    known = ids_in(user.split("## EVIDENCE", 1)[-1])[:3] or ["E1"]
    a, b = known[0], known[-1]
    return (f"*TL;DR*\nThe root cause is payment client timeouts (3000 ms) failing checkout requests [{a}].\n\n"
            f"*Root cause*\nPayment calls time out after 3000 ms [{b}]; details at https://evil.example/steal\n\n"
            f"*Evidence*\n• 5xx peak 9.5 req/s [{a}]\n\n*Impact / blast radius*\n• checkout requests fail [{a}]\n\n"
            "*Timeline (UTC)*\n• 11:55 first timeout\n\n*Mitigation*\n• Raise the payment client timeout\n\n"
            "*Next steps (human-only)*\n• Page the payments team\n\n*Gaps & limits*\n• none [E99]")


async def test_investigation_end_to_end(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMETHEUS_TOKEN", "glsa_SECRETSECRETSECRET1234")
    verdict_asked = asyncio.Event()

    async def loki(request: httpx.Request) -> httpx.Response:
        if "payment" in request.url.params.get("query", ""):
            # Hold the primary's second step until the specialist has published (deterministic injection).
            await asyncio.wait_for(verdict_asked.wait(), timeout=10)
            await asyncio.sleep(0.2)
        return loki_logs(request)

    def specialist_verdict(user: str) -> dict[str, Any]:
        verdict_asked.set()
        ids = re.findall(r"^(E\d+) \(", user, flags=re.MULTILINE)[:2]
        return {"verdict": "found", "headline": "4 checkout payment timeouts (3000 ms) in 5 min",
                "confidence": 0.6, "evidence_ids": ids,
                "details": {"level": "medium", "affected": ["checkout"], "hypothesis_id": "H1"}}

    def merge(user: str) -> dict[str, Any]:
        spec = re.search(r"(E\d+) \(customer_impact; customer_impact specialist verdict\)", user)
        ids = ids_in(user.split("EVIDENCE:", 1)[-1])
        supporting = ids[:6] + ([spec.group(1)] if spec else [])
        return {"hypotheses": [{"id": "H1", "statement": "payments dependency timeouts (3000 ms client timeout) cause "
                                "checkout 5xx", "causal_level": "trigger", "category": "dependency",
                                "confidence": 0.65, "supporting": supporting}]}

    llm = ScriptedLLM(
        loops={"decision": [
            [sql(ERROR_SQL, "checkout 5xx rate peak"), sql(LOGS_SQL, "checkout error logs")],
            [sql(PAYMENT_SQL, "payment errors in checkout logs")],
            hypotheses_step(),
            finish("H1: payment client timeouts (3000 ms) cause the checkout 5xx."),
        ], "specialist": ["4 payment timeouts in 5 minutes; checkout requests fail."]},
        structured={
            "SpecialistVerdict": specialist_verdict,
            "HypothesisSet": merge,
            "Decomposition": {"checks": [{"id": "H1.1", "statement": "payment calls hit the 3000 ms client timeout",
                                          "causal_level": "trigger", "sql": CHECK_SQL,
                                          "confirms_if": "timeouts > 0 in the window",
                                          "refutes_if": "no timeout lines while errors persist"}]},
            "CheckVerdict": {"verdict": "confirmed", "rationale": "4 lines show 'timeout after 3000ms'",
                             "terminal": True},
            "ConclusionCard": {"title": "Checkout 5xx from payment timeouts", "verdict": "Payment timeouts",
                               "root_cause": "payment client timeout 3000 ms", "causal_level": "trigger",
                               "confidence": 0.7, "mitigation": ["Raise the payment client timeout"]},
            "MemoryExtraction": {"worth_storing": True, "reason": "a confirmed trigger",
                                 "incident": {"title": "checkout 5xx from payment timeouts",
                                              "summary": "Payment calls timed out after 3000 ms.",
                                              "root_cause": "payment client timeout", "causal_level": "trigger",
                                              "confidence": 0.7}},
        },
        complete={"report": _report})
    memory = MemoryStore(":memory:")
    status = StatusStub()
    with respx.mock(assert_all_called=False) as m:
        routes = mock_sources(m, loki=loki)
        deps = make_deps(llm, tmp_path=tmp_path, specialists=["customer_impact"], deepening_rounds=1)
        deps.memory = memory
        result = await investigate(make_alert(), deps, status=status, anchor=pinned_clock())

    assert result.status == "ok" and result.kind == "investigation"
    assert result.run_id == "slackrun01"                                # the Slack trail's run id
    ids = {r.id for r in result.evidence}
    agents = {r.agent for r in result.evidence}
    assert {"seed", "primary", "customer_impact", "deepening"} <= agents
    assert result.evidence[0].id == "E1" and result.evidence[0].kind == "alert"
    # Specialist evidence reached the primary loop while it ran (inject), and the merge made it a hypothesis.
    spec = next(r for r in result.evidence if r.kind == "specialist")
    turns = _turn_text(llm, "decision")
    assert "SPECIALIST FINDINGS JUST PUBLISHED" in turns and spec.id in turns
    h1 = next(h for h in result.hypotheses if h.id == "H1")
    assert spec.id in h1.supporting and llm.calls_for("structured", "hypotheses")
    assert [v.agent for v in result.specialist_verdicts] == ["customer_impact"]
    # Deepening judged one sub-check against real data.
    sub = next(h for h in result.hypotheses if h.id == "H1.1")
    assert sub.status == "confirmed"
    assert any(e.agent == "deepening" and e.name == "run_sql" for e in result.tool_events)
    # Report: citations and links validated by code.
    assert "evil.example" not in result.answer_markdown and "E99" not in result.answer_markdown
    assert set(re.findall(r"\bE\d+\b", result.answer_markdown)) <= ids
    assert "prom.test/graph" in result.answer_markdown                   # bucket links appended from data
    # Card first (D10), then the final card from the report, sharing the run id (3.15).
    assert status.early_cards and status.early_cards[0].status == "preliminary"
    assert status.early_cards[0].run_id == result.run_id == result.card.run_id
    assert result.card.status == "final" and result.card.mitigation
    assert any(s.startswith("Seeds:") for s in status.steps) and "Writing the report" in status.steps
    # Memory: one gated extraction stored the incident.
    assert memory.search("checkout", "payment timeouts").incidents
    # Stats come from the run-scoped LLM view and the tools.
    assert result.stats.llm_calls > 0 and result.stats.sql_queries >= 4 and result.stats.finished
    assert routes["range"].call_count >= 2
    # Case file: every tool call with its arguments, redacted request logs, evidence, verdicts, no secrets.
    path = tmp_path / "cases" / "slackrun01.json"
    text = path.read_text()
    case = json.loads(text)
    assert case["run_id"] == "slackrun01" and case["source_ts"] == "1790856000.000100"
    assert any(e["name"] == "run_sql" and e["arguments"].get("sql") == ERROR_SQL for e in case["tool_events"])
    assert case["requests"] and any("prom.test" in r["url"] for r in case["requests"])
    assert {e["id"] for e in case["evidence"]} == ids and case["hypotheses"] and case["specialist_verdicts"]
    assert "DEEPENING" in case["telemetry"]["deepening"] and case["anchor"]["source"] == "trigger"
    assert "SECRETSECRET" not in text


async def test_readiness_gate_code_fills_coverage_the_model_refused(tmp_path: Any) -> None:
    """The log seed fails, the model never queries logs and refuses the readiness pushback: code runs the
    gap-filling logs query itself (3.6.3) and the loop finishes with it on the board."""
    def loki(request: httpx.Request) -> httpx.Response:
        if "level=~" in request.url.params.get("query", ""):       # the seed's error-only pattern query
            return httpx.Response(503, text="overloaded")
        return loki_logs(request)

    llm = ScriptedLLM(loops={"decision": [
        [sql(ERROR_SQL, "checkout 5xx rate peak")],
        hypotheses_step(),
        finish("H1 leads."),
        "I am confident in H1; nothing else to check.",              # refuses the readiness pushback
        finish("H1 leads; logs confirm payment timeouts."),
    ]}, complete={"report": _report})
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m, loki=loki)
        result = await investigate(make_alert(), make_deps(llm, tmp_path=tmp_path), anchor=pinned_clock())

    filled = [r for r in result.evidence if r.agent == "gap_fill"]
    assert filled and filled[0].pinned and any(r.meaningful for r in filled)
    assert filled[0].tables == ["loki_logs"]
    turns = _turn_text(llm, "decision")
    assert "READINESS GATE (1/3)" in turns and "CODE-ISSUED GAP-FILLING QUERIES" in turns
    assert result.stats.pushbacks["readiness"] == 1 and result.stats.pushbacks["primary.gap_fill"] == 1
    assert result.status == "ok" and len(llm.calls_for("loop_turn", "decision")) == 5


class SlowReportLLM(ScriptedLLM):
    """A report call that takes longer than the agents' whole watchdog budget."""

    async def complete(self, **kwargs: Any) -> Any:
        if kwargs.get("role") == "report":
            await asyncio.sleep(3.5)
        return await super().complete(**kwargs)


async def test_report_call_gets_its_own_allowance_past_the_agents_watchdog(tmp_path: Any) -> None:
    """D25: the report has a separate, longer budget; it is not cut to whatever the agents left."""
    llm = SlowReportLLM(loops={"decision": [[sql(ERROR_SQL, "checkout 5xx rate peak"), sql(LOGS_SQL, "error logs")],
                                            hypotheses_step(), *[finish("H1 leads.")] * 6]},
                        complete={"report": _report})
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        deps = make_deps(llm, tmp_path=tmp_path, investigation_budget_s=2.0, watchdog_grace_s=0.5,
                         report_reserve_s=1.0, report_call_s=12.0)
        assert deps.settings.investigation_guard_s == 2.0 + 11.0 + 0.5
        t0 = time.monotonic()
        result = await investigate(make_alert(), deps, anchor=pinned_clock())

    assert result.status == "ok" and 3.5 <= time.monotonic() - t0 < deps.settings.investigation_guard_s
    assert "payment client timeouts (3000 ms)" in result.answer_markdown           # the model's report, not salvage
    assert not any("watchdog" in w for w in result.warnings)


async def test_watchdog_salvages_partial_results(tmp_path: Any) -> None:
    llm = HangingLLM()
    t0 = time.monotonic()
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        deps = make_deps(llm, tmp_path=tmp_path, investigation_budget_s=4.0, watchdog_grace_s=1.0)
        result = await investigate(make_alert(), deps, anchor=pinned_clock())

    assert time.monotonic() - t0 < 10
    assert result.status == "timeout"
    assert any("watchdog" in w for w in result.warnings)
    seeds = [r for r in result.evidence if r.agent == "seed"]
    assert seeds and result.answer_markdown.strip()                   # the seeds were reported, not lost
    assert "watchdog" in result.answer_markdown and result.card is not None
    case = json.loads((tmp_path / "cases" / f"{result.run_id}.json").read_text())
    assert case["status"] == "timeout" and case["telemetry"]["stage"] == "agents"


async def test_stop_request_is_honoured(tmp_path: Any) -> None:
    llm = HangingLLM()
    status = StatusStub("stoprun")
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        asyncio.get_running_loop().call_later(0.5, setattr, status, "stop_requested", True)
        t0 = time.monotonic()
        result = await investigate(make_alert(), make_deps(llm, tmp_path=tmp_path), status=status,
                                   anchor=pinned_clock())
    assert time.monotonic() - t0 < 10                                 # well inside Slack's 20 s stop grace
    assert result.status == "stopped" and result.run_id == "stoprun"
    assert result.answer_markdown.strip() and result.card is not None
    assert (tmp_path / "cases" / "stoprun.json").exists()


async def test_evidence_floor_returns_insufficient_data(tmp_path: Any) -> None:
    llm = ScriptedLLM(loops={"decision": [
        [sql(SERIES_SQL, "checkout 5xx rate"), sql(LOGS_SQL, "checkout error logs")],
        finish("Nothing conclusive."),
    ]}, complete={"report": "*TL;DR*\nThe root cause is the database [E1]."})
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m, broken=True)
        result = await investigate(make_alert(), make_deps(llm, tmp_path=tmp_path), anchor=pinned_clock())

    assert result.status == "insufficient_data"
    assert result.answer_markdown.startswith(":grey_question: *Insufficient data")
    assert "What was tried" in result.answer_markdown and "database" not in result.answer_markdown
    assert not llm.calls_for("complete", "report")                    # never asked the model to speculate
    assert result.card is not None and result.card.status == "insufficient_data"
    assert any("evidence floor" in w for w in result.warnings)
    assert any("0 rows" in w for w in result.warnings)                 # loud zeros from the tools


async def test_case_file_is_bounded_and_marks_truncation(tmp_path: Any) -> None:
    llm = ScriptedLLM(loops={"decision": [[sql(LOGS_SQL, "checkout error logs")], finish("done")]},
                      complete={"report": _report})
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        result = await investigate(make_alert(), make_deps(llm, tmp_path=tmp_path), anchor=pinned_clock())
    full = len(case_json(result).encode())
    path = write_case(result, None, case_dir=tmp_path / "small", max_bytes=full // 3)
    assert path is not None
    case = json.loads(path.read_text())
    assert case["truncation"]["level"] >= 1 and case["truncation"]["full_bytes"] > full // 3
    assert "[clipped:" in path.read_text()
