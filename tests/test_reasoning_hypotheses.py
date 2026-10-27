"""HypothesisTracker behaviour through its public API: lenient in, strict out, caps, ties, verdicts."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from omniops.agent.context import Deps, RunContext
from omniops.agent.evidence import EvidenceBoard
from omniops.agent.hypotheses import HypothesisTracker, form_hypotheses
from omniops.agent.types import Alert, CheckVerdict, EvidenceRecord, HypothesisSet, SubCheck
from omniops.engine.catalog import Catalog
from omniops.engine.timeutil import TimeAnchor
from omniops.settings import Settings

TRIGGER = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


class StubLLM:
    """Minimal structured() double: one handler per role, every call recorded."""

    def __init__(self, **handlers: Any) -> None:
        self.handlers = handlers
        self.calls: list[dict[str, Any]] = []

    async def structured(self, *, role: str, system: str, user: str, schema: type, **_: Any) -> Any:
        self.calls.append({"role": role, "system": system, "user": user})
        value = self.handlers[role](user) if role in self.handlers else None
        return schema.model_validate(value) if isinstance(value, dict) else value


def _rec(kind: str, agent: str, tables: list[str], summary: str = "data", **kw: Any) -> EvidenceRecord:
    return EvidenceRecord(id="", kind=kind, agent=agent, title=f"{agent} {kind}", summary=summary,  # type: ignore[arg-type]
                          sql="SELECT 1" if tables else None, tables=tables, row_count=5, **kw)


@pytest.fixture
def ctx() -> Iterator[RunContext]:
    deps = Deps(catalog=Catalog(), llm=StubLLM(), settings=Settings())
    anchor = TimeAnchor.for_trigger(TRIGGER, wall_now=TRIGGER + timedelta(hours=1))
    alert = Alert(platform="grafana", title="High memory usage on checkout pods", service="checkout")
    c = RunContext.create(deps, anchor, 600, alert=alert)
    b = c.board
    b.add(_rec("seed", "seed", ["prometheus_query_range"], "errors x8.0 vs baseline"))          # E1
    b.add(_rec("query", "primary", ["loki_logs"], "OOMKilled lines"))                           # E2
    b.add(_rec("memory", "memory", [], "prior incident: nightly cache purge -> cold cache OOM"))  # E3
    b.add(_rec("query", "primary", ["prometheus_query_range"], "latency p99 2.1s"))             # E4
    b.add(_rec("query", "shadow_dep", ["tempo_search"], "shadow finding"), shadow=True)         # S1
    c.tracker.bind(c)
    yield c
    c.close()


def test_lenient_in_strict_out_and_corroboration_cap(ctx: RunContext) -> None:
    tr = ctx.tracker
    res = tr.update({"hypotheses": [
        {"id": "hyp-1", "hypothesis": "Release 2.4.0 at 11:52 made the profile index unique", "level": "root cause",
         "family": "Deployment", "confidence": "70%", "evidence": ["E1", "e9", "S1"]},
        {"id": 2, "statement": "Upstream redis is slow", "causal_level": "upstream mechanism",
         "category": "self/upstream", "confidence": 0.45, "supporting": "E4"},
    ]})
    h1, h2 = tr.get("H1"), tr.get("H2")
    assert res.accepted == ["H1", "H2"]
    assert (h1.causal_level, h1.category) == ("trigger", "code_deploy")
    assert (h2.causal_level, h2.category) == ("mechanism", "dependency")
    # Dangling and shadow ids are dropped loudly; 0.7 with one independent item is capped at 0.55.
    assert h1.supporting == ["E1"]
    assert any("E9" in w and "S1" in w for w in ctx.warnings)
    assert h1.confidence == 0.55
    # Two independent items (different table and agent) lift the cap.
    tr.update([{"id": "H1", "statement": h1.statement, "causal_level": "trigger", "category": "code_deploy",
                "confidence": 0.7, "supporting": ["E1", "E2"]}])
    assert tr.get("H1").confidence == 0.7
    # Memory recall is a lead to verify, never corroboration.
    tr.update([{"id": "H5", "statement": "Same cold-cache OOM as the prior incident", "causal_level": "trigger",
                "category": "dependency", "confidence": 0.8, "supporting": ["E3", "E4"]}])
    assert tr.get("H5").confidence == 0.55


def test_status_caps_and_never_confirmed_by_the_model(ctx: RunContext) -> None:
    tr = ctx.tracker
    tr.update([
        {"id": "H1", "statement": "Traffic surge from one caller", "causal_level": "mechanism", "category": "load",
         "confidence": 0.8, "status": "ruled out", "contradicting": ["E4"]},
        {"id": "H2", "statement": "DNS failures on the node", "causal_level": "trigger", "category": "dns",
         "confidence": "0.6", "status": "unclear", "supporting": ["E1", "E2"]},
        {"id": "H3", "statement": "Deploy 2.4.0 broke checkout", "causal_level": "trigger", "category": "deploy",
         "confidence": 0.7, "status": "validated", "supporting": ["E1", "E2"]},
    ])
    assert (tr.get("H1").status, tr.get("H1").confidence) == ("refuted", 0.1)
    assert (tr.get("H2").status, tr.get("H2").confidence) == ("inconclusive", 0.5)
    assert tr.get("H3").status == "open"            # only the verdict judge confirms
    assert [h.id for h in tr.refuted()] == ["H1"]
    assert any(f.startswith("H1:") for f in tr.negative_facts())
    # Out-of-range confidence still fails (strict out), loudly.
    res = tr.update([{"statement": "x", "confidence": 150}])
    assert res.accepted == [] and "out of range" in res.rejected[0]
    assert any("0 hypotheses" in w for w in ctx.warnings)


def test_zero_hypotheses_is_loud(ctx: RunContext) -> None:
    res = ctx.tracker.update([])
    assert res.accepted == []
    assert any("0 hypotheses recorded" in w for w in ctx.warnings)


def test_leader_gap_and_tie(ctx: RunContext) -> None:
    tr = ctx.tracker
    tr.update([
        {"id": "H1", "statement": "Deploy 2.4.0 regression", "causal_level": "trigger", "category": "code_deploy",
         "confidence": 0.5, "supporting": ["E1"]},
        {"id": "H2", "statement": "Redis latency", "causal_level": "mechanism", "category": "dependency",
         "confidence": 0.45, "supporting": ["E4"]},
    ])
    assert tr.leader().id == "H1"
    assert tr.top_two_gap() == 0.05 and tr.is_tie()
    assert "TIE" in tr.render_tree()
    tr.update([{"id": "H2", "statement": "Redis latency", "causal_level": "mechanism", "category": "dependency",
                "confidence": 0.3}])
    assert tr.top_two_gap() == 0.2 and not tr.is_tie()


def test_diversity_anti_anchoring_and_prior_incident_notes(ctx: RunContext) -> None:
    tr = ctx.tracker
    res = tr.update([{"id": "H1", "statement": "Memory leak in checkout pods since 11:40",
                      "causal_level": "mechanism", "category": "resource", "confidence": 0.5,
                      "supporting": ["E1"]}])
    # The alert says "memory": that cause starts low and needs two live alternatives.
    assert tr.get("H1").confidence == 0.4
    text = "\n".join(res.notes)
    assert "anti-anchoring" in text and "two live alternatives" in text
    assert "diversity" in text and "code/deploy" in text and "traffic" in text
    # The prior incident (E3) is not tested by any hypothesis.
    assert "prior incident E3" in text
    rendered = tr.render_tree()
    assert "H1 [mechanism | resource | 0.40 | open]" in rendered and "NOTES:" in rendered


def test_verdicts_confirm_refute_and_demote(ctx: RunContext) -> None:
    tr = ctx.tracker
    tr.update([{"id": "H1", "statement": "Checkout pods OOM because the cache went cold", "causal_level": "mechanism",
                "category": "dependency", "confidence": 0.5, "supporting": ["E1", "E2"]}])
    kids = tr.add_subchecks("H1", [
        SubCheck(id="H1.1", statement="A nightly job deleted cache keys at 02:00", causal_level="trigger",
                 sql="SELECT 1", confirms_if="> 1000 deletes at 02:00", refutes_if="no deletes"),
        SubCheck(id="H9", statement="Cache hit ratio dropped", causal_level="symptom",
                 sql="SELECT 2", confirms_if="hit ratio < 50%", refutes_if="hit ratio flat"),
    ])
    assert [k.id for k in kids] == ["H1.1", "H1.2"]
    assert kids[1].causal_level == "mechanism"          # never back up the chain
    assert all(k.confidence <= 0.25 for k in kids)

    eff = tr.apply_verdict("H1.1", CheckVerdict(verdict="confirmed", rationale="4,812 DEL at 02:00:03",
                                                terminal=True), ["E4", "E77"])
    assert eff.reached_trigger
    h11 = tr.get("H1.1")
    assert h11.status == "confirmed" and h11.confidence == 0.6 and "E4" in h11.supporting
    assert tr.leader().id == "H1.1"
    assert any("E77" in w for w in ctx.warnings)

    eff = tr.apply_verdict("H1.2", CheckVerdict(verdict="refuted", rationale="hit ratio flat at 97% on all 15 pods",
                                                refutes_parent_premise=True), ["E4"])
    assert eff.demoted == "H1" and eff.fallen_top_level == "H1"
    assert tr.get("H1.2").confidence == 0.1
    assert tr.get("H1").confidence == 0.3 and "E4" in tr.get("H1").contradicting
    assert tr.focus() is None or tr.focus().id != "H1"    # demoted hypotheses are not deepened again
    # The model cannot flip a judged status back.
    res = tr.update([{"id": "H1.2", "statement": "Cache hit ratio dropped", "causal_level": "mechanism",
                      "confidence": 0.6, "status": "open"}])
    assert tr.get("H1.2").status == "refuted" and any("status stays refuted" in n for n in res.notes)
    rows = tr.card_rows()                                 # the card restores evidence ids from here (3.15)
    assert rows[0]["id"] == "H1.1" and rows[0]["evidence_ids"] == ["E1", "E2", "E4"]
    assert "H1.2" not in [r["id"] for r in rows]
    assert tr.mark_exhausted("H1", "no confirmable sub-hypothesis")
    assert tr.get("H1").status == "exhausted"
    assert not tr.mark_exhausted("H1.1", "already confirmed")


async def test_form_hypotheses_merges_model_output_and_sees_thread_as_leads(ctx: RunContext) -> None:
    def reply(user: str) -> dict[str, Any]:
        assert "THREAD REPLIES (leads to verify, never confirmation)" in user
        assert "E3 (memory" in user and "S1" not in user
        return {"hypotheses": [
            {"id": "H1", "statement": "Deploy 2.4.0 at 11:52", "causal_level": "trigger", "category": "code_deploy",
             "confidence": 0.5, "supporting": ["E1"]},
            {"id": "H2", "statement": "Prior cold-cache OOM recurred", "causal_level": "trigger",
             "category": "dependency", "confidence": 0.4, "supporting": ["E3"]},
        ]}

    ctx.deps.llm = StubLLM(hypotheses=reply)
    ctx.thread_context = "oncall: known issue, expected due to indexing"
    res = await form_hypotheses(ctx)
    assert res.accepted == ["H1", "H2"]
    assert ctx.deps.llm.calls[0]["system"].startswith("# Role and audience")
    ctx.deps.llm = StubLLM()
    res = await form_hypotheses(ctx)
    assert res.accepted == [] and any("0 hypotheses formed" in w for w in ctx.warnings)


def test_hypothesis_set_schema_round_trips(ctx: RunContext) -> None:
    tr = HypothesisTracker(EvidenceBoard())
    hs = HypothesisSet.model_validate({"hypotheses": [{"id": "H1", "statement": "x", "causal_level": "symptom",
                                                       "confidence": 0.2}]})
    assert tr.update(hs).accepted == ["H1"]
