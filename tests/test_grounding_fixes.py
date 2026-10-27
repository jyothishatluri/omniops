"""Regression tests for grounding defects F1-F11 (evidence, corroboration, card, wording, sanitizer, leads)."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx
from test_report_writer import ARGO_URL, GOOD_REPORT, _formatter_card, hypotheses, make_ctx
from test_report_writer import StubLLM as ReportLLM

from omniops.agent import gates, language
from omniops.agent.card import build_card, early_card, fallback_card
from omniops.agent.context import Deps, RunContext
from omniops.agent.deepening import _deterministic_reason, _result_text, deepen
from omniops.agent.evidence import EvidenceBoard, record_error
from omniops.agent.hypotheses import UNCORROBORATED_CAP, HypothesisTracker
from omniops.agent.leads import LeadTracker
from omniops.agent.report import write_report
from omniops.agent.sanitize import sanitize_sql
from omniops.agent.tools import build_tools
from omniops.agent.types import Alert, EvidenceRecord, HypothesisSet, SpecialistVerdict
from omniops.engine.catalog import Catalog
from omniops.engine.timeutil import TimeAnchor
from omniops.settings import Settings

TRIGGER = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
PROM = "http://prom.test/api/v1/query_range"
PROMPTS = Path(__file__).resolve().parents[1] / "src" / "omniops" / "agent" / "prompts"


# ---------------------------------------------------------------------------------------------
# A real engine over a respx-mocked Prometheus
# ---------------------------------------------------------------------------------------------


def _matrix(p: dict[str, str], value: float = 1.0) -> httpx.Response:
    start, end, step = int(p["start"]), int(p["end"]), int(p["step"])
    return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": {"job": "api"}, "values": [[t, str(value)] for t in range(start, end + 1, step)]}]}})


def _empty() -> httpx.Response:
    return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": []}})


def prometheus(request: httpx.Request) -> httpx.Response:
    q = dict(request.url.params)["query"]
    if "throttled" in q:
        return httpx.Response(429, headers={"Retry-After": "0"}, text="slow down")
    if "chekout" in q or "nonexistent" in q:
        return _empty()
    return _matrix(dict(request.url.params), 10.0 if "errors" in q else 1.0)


@pytest.fixture
def prom_ctx(monkeypatch: pytest.MonkeyPatch) -> Iterator[RunContext]:
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom.test")
    monkeypatch.delenv("OMNIOPS_CONFIG", raising=False)
    deps = Deps(catalog=Catalog.from_env(only=["prometheus"]), llm=object(), settings=Settings())  # type: ignore[arg-type]
    anchor = TimeAnchor.for_trigger(TRIGGER, wall_now=TRIGGER + timedelta(hours=1))
    with respx.mock(assert_all_called=False) as router:
        router.get(PROM).mock(side_effect=prometheus)
        c = RunContext.create(deps, anchor, 600, alert=Alert(platform="grafana", title="Checkout errors",
                                                             service="checkout"))
        yield c
        c.close()


async def _sql(ctx: RunContext, sql: str, purpose: str = "p") -> Any:
    tools = {t.name: t for t in build_tools(ctx)}
    return await tools["run_sql"].handler({"sql": sql, "purpose": purpose})


# ---------------------------------------------------------------------------------------------
# F1: an aggregate over zero input rows is an inconclusive empty, not a measurement
# ---------------------------------------------------------------------------------------------


async def test_count_over_an_empty_fetch_is_inconclusive_not_a_measured_zero(prom_ctx: RunContext) -> None:
    out = await _sql(prom_ctx, "SELECT count(*) AS errors FROM prometheus_query_range "
                               "WHERE query = 'errors_total{service=\"chekout\"}'", "errors for the typo'd service")
    rec = prom_ctx.board.get(out.evidence_id or "")
    assert rec is not None and rec.data["fetched_rows"] == {"prometheus_query_range": 0}
    assert not rec.meaningful and rec.empty_verdict == "inconclusive" and rec.row_count == 0
    assert rec.confidence <= 0.4 and "no positive control confirmed the filter" in rec.summary
    assert "[empty]" in out.content and "INCONCLUSIVE" in out.content
    assert prom_ctx.board.meaningful_count() == 0
    # Deepening treats it as an empty: inconclusive by code, and the judge is told so.
    assert _deterministic_reason(rec) is not None
    assert "EMPTY WITHOUT POSITIVE CONTROL" in _result_text(rec)


async def test_all_null_aggregate_after_predicates_removed_every_fetched_row(prom_ctx: RunContext) -> None:
    out = await _sql(prom_ctx, "SELECT max(value) AS peak FROM prometheus_query_range "
                               "WHERE query = 'up' AND value > 1000000")
    rec = prom_ctx.board.get(out.evidence_id or "")
    assert rec is not None and rec.data["fetched_rows"]["prometheus_query_range"] > 0
    assert not rec.meaningful and rec.empty_verdict == "inconclusive" and rec.row_count == 0
    assert "the source returned" in rec.summary and "[empty]" in out.content
    real = await _sql(prom_ctx, "SELECT count(*) AS n FROM prometheus_query_range WHERE query = 'up'")
    rec2 = prom_ctx.board.get(real.evidence_id or "")
    assert rec2 is not None and rec2.meaningful and rec2.row_count == 1 and rec2.empty_verdict is None


# ---------------------------------------------------------------------------------------------
# F2: a specialist verdict is not an independent source next to the evidence it cites
# ---------------------------------------------------------------------------------------------


def _measure(board: EvidenceBoard, kind: str, agent: str, tables: list[str]) -> EvidenceRecord:
    return board.add(EvidenceRecord(id="", kind=kind, agent=agent, title=f"{agent} {kind}",  # type: ignore[arg-type]
                                    summary="data", sql="SELECT 1", tables=tables, row_count=3))


def test_a_verdict_citing_a_seed_does_not_corroborate_that_seed() -> None:
    board = EvidenceBoard()
    seed = _measure(board, "seed", "seed", ["omni_change_events"])
    verdict = board.publish_specialist(SpecialistVerdict(
        agent="code_change", verdict="found", headline="deploy v2.4 at 11:52 precedes the onset", confidence=0.8,
        evidence_ids=[seed.id]))
    tr = HypothesisTracker(board)
    tr.update([{"id": "H1", "statement": "Deploy v2.4 broke checkout", "causal_level": "trigger",
                "category": "code_deploy", "confidence": 0.8, "supporting": [seed.id, verdict.id]}])
    h1 = tr.get("H1")
    assert tr.independent_support(h1) == 1 and h1.confidence == UNCORROBORATED_CAP
    # The specialist's own query of another table is a second source; a verdict citing it adds it.
    own = _measure(board, "query", "code_change", ["github_commits"])
    second = board.publish_specialist(SpecialistVerdict(
        agent="code_change", verdict="found", headline="commit abc1234 changed the pool size", confidence=0.8,
        evidence_ids=[seed.id, own.id]))
    tr.update([{"id": "H1", "confidence": 0.8, "supporting": [second.id]}])
    assert tr.independent_support(tr.get("H1")) == 2 and tr.get("H1").confidence == 0.8


# ---------------------------------------------------------------------------------------------
# F3: the card never shows a triggering change the report or deepening rejected
# ---------------------------------------------------------------------------------------------


def _refuted_h1() -> list[Any]:
    hyps = hypotheses()
    hyps[0].status, hyps[0].confidence = "refuted", 0.1
    return hyps


async def test_card_drops_the_verdict_change_when_the_report_says_not_identified() -> None:
    report = GOOD_REPORT.replace("Argo CD sync 4f1c2ab [E3].", "Not identified: the Argo CD sync landed after onset.")
    ctx = make_ctx(ReportLLM(card=_formatter_card()), hyps=_refuted_h1())
    assert (await build_card(ctx, report)).trigger_change is None
    assert fallback_card(ctx, report).trigger_change is None


async def test_card_drops_the_verdict_change_when_every_hypothesis_citing_it_was_refuted() -> None:
    ctx = make_ctx(ReportLLM(card=_formatter_card()), hyps=_refuted_h1())
    assert (await build_card(ctx, GOOD_REPORT)).trigger_change is None
    assert early_card(ctx).trigger_change is None
    live = make_ctx(ReportLLM())
    change = early_card(live).trigger_change              # no report yet: the verdict still names it
    assert change is not None and change.url == ARGO_URL


# ---------------------------------------------------------------------------------------------
# F4: the evidence floor counts measured items only
# ---------------------------------------------------------------------------------------------


async def test_alert_and_verdicts_do_not_lift_the_evidence_floor() -> None:
    board = EvidenceBoard()
    board.add(EvidenceRecord(id="", kind="alert", agent="seed", title="Alert rule", summary="5xx > 5%"), pinned=True)
    seed = _measure(board, "seed", "seed", ["omni_change_events"])
    failed = board.add(record_error("HTTP 503 from loki", agent="seed", purpose="error logs", kind="seed"))
    assert board.meaningful_count() == 1 and gates.meaningful_count(SimpleNamespace(board=board)) == 1  # type: ignore[arg-type]
    vague = board.publish_specialist(SpecialistVerdict(agent="dependency", verdict="not_found", confidence=0.5,
                                                       headline="no dependency errors", evidence_ids=[failed.id]))
    assert not vague.meaningful and "cites no meaningful measurement" in vague.summary
    restated = board.publish_specialist(SpecialistVerdict(agent="code_change", verdict="found", confidence=0.7,
                                                          headline="deploy at 11:52", evidence_ids=[seed.id]))
    assert restated.meaningful and board.meaningful_count() == 1
    assert gates.meaningful_count(SimpleNamespace(board=board)) == 1  # type: ignore[arg-type]
    llm = ReportLLM(replies=[GOOD_REPORT])
    ctx = make_ctx(llm)
    ctx.board = board
    out = await write_report(ctx)
    assert out.startswith(":grey_question: *Insufficient data") and not [c for c in llm.calls
                                                                         if c["kind"] == "complete"]


# ---------------------------------------------------------------------------------------------
# F5: deepening check SQL and corrected SQL go through the sanitizer
# ---------------------------------------------------------------------------------------------


class DeepLLM:
    def __init__(self, **handlers: Callable[[str], Any]) -> None:
        self.handlers = handlers

    async def structured(self, *, role: str, system: str, user: str, schema: type, **_: Any) -> Any:
        value = self.handlers[role](user) if role in self.handlers else None
        return schema.model_validate(value) if isinstance(value, dict) else value


def _check(hid: str, sql: str) -> dict[str, str]:
    return {"id": hid, "statement": f"{hid}: a deeper sub-claim", "causal_level": "trigger", "sql": sql,
            "confirms_if": "rises > 5x before the onset", "refutes_if": "flat vs baseline"}


async def test_deepening_sanitizes_decomposition_and_corrected_sql(prom_ctx: RunContext) -> None:
    ctx = prom_ctx
    ctx.board.add_result(await ctx.engine.query("SELECT ts, value FROM prometheus_query_range WHERE query = "
                                                "'sum(rate(http_errors_total[5m]))'"), agent="seed",
                         purpose="error rate", kind="seed", pinned=True)
    ctx.tracker.update([{"id": "H1", "statement": "A cache dependency failure drove the errors",
                         "causal_level": "mechanism", "category": "dependency", "confidence": 0.5,
                         "supporting": ["E1"]}])
    smart = ("SELECT labels->>\"job\" AS job, max(value) AS peak FROM prometheus_query_range "
             "WHERE query == “sum(rate(cache_errors_total[5m]))” GROUP BY 1")
    empty = "SELECT labels->>'job' AS job, max(value) AS peak FROM prometheus_query_range " \
            "WHERE query = 'nonexistent_misses' GROUP BY 1"
    corrected = ("SELECT labels->>'job' AS job, max(value) AS peak FROM prometheus_query_range "
                 "WHERE query = \"sum(rate(cache_misses_total[5m]))\" AND value != None GROUP BY 1")

    def judge(user: str) -> dict[str, Any]:
        hid = re.search(r"CLAIM UNDER TEST: (H[\d.]+)", user).group(1)  # type: ignore[union-attr]
        if hid == "H1.2" and "MODE: CORRECTION ONLY" in user:
            return {"verdict": "inconclusive", "rationale": "wrong metric name", "corrected_sql": corrected}
        return {"verdict": "confirmed", "rationale": "cache errors 1.0/s on api"}

    ctx.deps.llm = DeepLLM(deepening=lambda _u: {"checks": [_check("H1.1", smart), _check("H1.2", empty)]},
                           verdict=judge)
    report = await deepen(ctx, max_rounds=1)

    o1, o2 = report.outcomes
    assert (o1.verdict.verdict, o1.decided_by) == ("confirmed", "judge")
    assert "== " not in ctx.tracker.checks["H1.1"].sql and "'sum(rate(cache_errors_total[5m]))'" in o1.sql
    first = ctx.board.get(o1.evidence_ids[0])
    assert first is not None and not first.error and first.row_count > 0
    assert o2.rechecked and (o2.verdict.verdict, o2.decided_by) == ("confirmed", "judge")
    recheck = ctx.board.get(o2.evidence_ids[-1])
    assert recheck is not None and not recheck.error and recheck.row_count > 0
    assert any(n.startswith("H1.1: sanitizer fixed") for n in report.notes)
    assert any(n.startswith("H1.2 re-check: sanitizer fixed") for n in report.notes)


# ---------------------------------------------------------------------------------------------
# F6: rate limits and timeouts never use up a query shape's retries
# ---------------------------------------------------------------------------------------------


async def test_rate_limits_do_not_block_the_table_for_the_rest_of_the_run(prom_ctx: RunContext) -> None:
    shape = "SELECT ts, value FROM prometheus_query_range WHERE query = '{q}'"
    for q in ("throttled_a", "throttled_b", "throttled_c"):
        out = await _sql(prom_ctx, shape.format(q=q))
        assert out.content.startswith("ERROR (RateLimitError)") and "not counted against" in out.content
    ok = await _sql(prom_ctx, shape.format(q="node_cpu_seconds_total"))
    assert not ok.is_error and ok.content.startswith("E4 | ")


# ---------------------------------------------------------------------------------------------
# F7: PR/MR leads are followed only by queries that read their platform
# ---------------------------------------------------------------------------------------------


def test_pr_and_mr_leads_are_not_followed_by_unrelated_limits() -> None:
    t = LeadTracker()
    t.scan("deploy https://github.com/acme/checkout/pull/20 and https://gitlab.com/acme/payments/-/merge_requests/5",
           origin=["loki_logs"], evidence_id="E1")
    assert t.observe(sql="SELECT ts, line FROM loki_logs WHERE service='checkout' ORDER BY ts DESC LIMIT 20",
                     origin={"loki_logs"}, evidence_id="E9") == []
    assert t.observe(sql="SELECT * FROM loki_logs WHERE app='payments' LIMIT 5", origin={"loki_logs"},
                     evidence_id="E10") == []
    assert {x.status for x in t.all()} == {"unfollowed"}
    pr = t.observe(sql="SELECT * FROM github_pull_files WHERE repo = 'acme/checkout' AND number = 20",
                   origin={"github_pull_files"}, evidence_id="E11")
    assert [x.ref for x in pr] == ["github:acme/checkout#20"]
    mr = t.observe(sql="SELECT * FROM gitlab_merge_requests WHERE project = 'acme/payments' AND iid = 5",
                   evidence_id="E12")                       # platform read from the SQL's own tables
    assert [x.ref for x in mr] == ["gitlab:acme/payments!5"]


# ---------------------------------------------------------------------------------------------
# F8: a validated partial HypothesisSet keeps the fields it omits
# ---------------------------------------------------------------------------------------------


def test_validated_partial_update_keeps_category_and_refuted_status() -> None:
    board = EvidenceBoard()
    tr = HypothesisTracker(board)
    tr.update([{"id": "H1", "statement": "Deploy 2.4 broke checkout", "causal_level": "trigger",
                "category": "code_deploy", "confidence": 0.3},
               {"id": "H2", "statement": "Redis latency drove the errors", "causal_level": "mechanism",
                "category": "dependency", "confidence": 0.4, "status": "refuted"}])
    assert (tr.get("H2").category, tr.get("H2").status) == ("dependency", "refuted")
    hs = HypothesisSet.model_validate({"hypotheses": [
        {"id": "H2", "statement": "Redis latency drove the errors", "causal_level": "mechanism", "confidence": 0.35}]})
    tr.update(hs, origin="formation")
    h2 = tr.get("H2")
    assert (h2.category, h2.status, h2.confidence) == ("dependency", "refuted", 0.1)
    assert tr.leader().id == "H1"


# ---------------------------------------------------------------------------------------------
# F9: negated root-cause sentences are not rewritten into contradictions
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("conf", [0.2, 0.45, 0.6])
def test_calibrate_leaves_negated_cause_statements_alone(conf: float) -> None:
    for text in ("The root cause is not established: two hypotheses remain open.", "The root cause is unknown.",
                 "The root cause of the outage is still unclear.", "We cannot definitely say which deploy it was."):
        assert language.calibrate(text, conf).text == text
    assert language.calibrate("The root cause is the flag.", conf).text != "The root cause is the flag."
    assert "likely" in language.calibrate("It was definitely the flag.", conf).text


# ---------------------------------------------------------------------------------------------
# F10: a double-quoted table qualifier stays an identifier
# ---------------------------------------------------------------------------------------------


def test_sanitizer_keeps_quoted_qualifiers_as_identifiers() -> None:
    joined = 'SELECT l.line FROM loki_logs l JOIN k8s_pods "p" ON l.pod = "p".pod'
    assert sanitize_sql(joined, ["pod", "id", "line"]) == (joined, [])
    qualified = 'SELECT * FROM a JOIN k8s_pods ON a.id = "k8s_pods".id'
    assert sanitize_sql(qualified, ["pod"]) == (qualified, [])
    fixed, fixes = sanitize_sql('SELECT * FROM loki_logs l WHERE l.level = "error"', ["level"])
    assert fixed == "SELECT * FROM loki_logs l WHERE l.level = 'error'" and len(fixes) == 1


# ---------------------------------------------------------------------------------------------
# F11: the decision prompt agrees that a controlled empty is a real measurement
# ---------------------------------------------------------------------------------------------


def test_decision_prompt_keeps_the_genuine_absence_exception() -> None:
    text = (PROMPTS / "decision.md").read_text()
    trigger = next(ln for ln in text.split("\n- ") if ln.startswith("A query returns 0 rows"))
    assert "genuine absence" in trigger and "real" in trigger
