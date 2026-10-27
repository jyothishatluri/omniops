"""Long-term memory through MemoryStore: recall ranking, feedback weighting, negative facts, extraction, consolidation."""

from __future__ import annotations

import time
from typing import Any

from omniops.agent.memory import DOWN_FLOOR, MemoryExtraction, MemoryStore, redact
from omniops.agent.types import Alert, ConclusionCard, Hypothesis, RunResult
from omniops.llm.scripted import ScriptedLLM

DAY = 86400.0


def _alert(service: str = "checkout") -> Alert:
    return Alert(platform="prometheus", rule_name="CheckoutErrors", title="CheckoutErrors", service=service)


def _run(run_id: str, *, kind: str = "investigation", status: str = "ok",
         hypotheses: list[Hypothesis] | None = None) -> RunResult:
    card = ConclusionCard(title="checkout 5xx", verdict="Release abc123 raised the batch size; pods OOMKilled",
                          root_cause="batch size 5000 exhausts the 512Mi limit", causal_level="trigger", confidence=0.8)
    return RunResult(run_id=run_id, kind=kind, answer_markdown="## TL;DR\ncheckout pods OOMKilled (E6).",  # type: ignore[arg-type]
                     card=card, hypotheses=hypotheses or [], status=status, alert=_alert())  # type: ignore[arg-type]


def test_recall_prefers_same_service_and_matching_text() -> None:
    mem = MemoryStore(":memory:")
    mem.add_episode(run_id="r1", title="payment latency after card processor slowdown", service="payment",
                    summary="payment p95 rose to 4s", root_cause="processor timeouts")
    mem.add_episode(run_id="r2", title="checkout pods OOMKilled after batch size raise", service="checkout",
                    summary="checkout 5xx at 9/s; pods OOMKilled", root_cause="batch size 5000", causal_level="trigger")
    mem.add_fact(kind="threshold", text="checkout 5xx alert pages at 5 req/s; normal is below 0.5", service="checkout")
    mem.add_fact(kind="gotcha", text="loki label is service_name, not app", service=None)
    recall = mem.search("checkout", "CheckoutErrors 5xx OOMKilled")
    assert recall.incidents[0].ref == "M2"
    assert recall.facts[0].ref == "F1" and recall.facts[0].kind == "threshold"
    lines = mem.recall("checkout", "CheckoutErrors 5xx OOMKilled")
    assert lines[0].startswith("MEMORY RECALL") and any("root cause (trigger): batch size 5000" in x for x in lines)
    assert any("last verified" in x for x in lines)
    assert mem.recall("inventory", "zzz unrelated words") == []


def test_feedback_weighting_and_suppression() -> None:
    mem = MemoryStore(":memory:")
    for run_id in ("up", "flat", "down", "gone"):
        mem.add_fact(kind="behavior", text=f"checkout cache warms in 10 minutes after restart ({run_id})",
                     service="checkout", run_id=run_id)
    mem.vote("up", +1)
    mem.vote("down", -1)
    mem.vote("gone", -1)
    mem.vote("gone", -1)
    recall = mem.search("checkout", "checkout cache warms after restart")
    score = {h.text.split("(")[-1].rstrip(")"): h.score for h in recall.facts + recall.avoid}
    assert abs(score["up"] / score["flat"] - 1.15) < 0.02         # +15% per net positive vote
    assert abs(score["down"] / score["flat"] - 0.70) < 0.02       # -30% per net negative vote
    assert [h.text.split("(")[-1].rstrip(")") for h in recall.facts] == ["up", "flat", "down"]
    assert [h.net_votes for h in recall.avoid] == [-2]           # suppressed at net -2: avoid only
    block = mem.render_recall("checkout", "checkout cache warms after restart")
    assert "rejected by responders (net votes -2)" in block
    for _ in range(3):
        mem.vote("gone", -1)
    floored = mem.search("checkout", "checkout cache warms after restart").avoid[0]
    assert abs(floored.score / score["flat"] - DOWN_FLOOR) < 0.01  # floored at 5% of similarity


async def test_refuted_hypotheses_become_ruled_out_facts() -> None:
    mem = MemoryStore(":memory:")
    hyps = [Hypothesis(id="H2", statement="Redis latency causes checkout 5xx", causal_level="mechanism",
                       confidence=0.1, status="refuted", verdict_notes=["refuted: redis p99 flat at 2ms (E9)"]),
            Hypothesis(id="H1", statement="batch size change", causal_level="trigger", confidence=0.8,
                       status="confirmed")]
    refs = await mem.remember(_run("r1", hypotheses=hyps), llm=None)
    assert refs == ["F1"]
    block = mem.render_recall("checkout", "checkout 5xx redis latency")
    assert "Avoid (previously ruled out" in block
    assert "ruled out: Ruled out for checkout: Redis latency causes checkout 5xx" in block
    assert "refuted" not in block.lower()
    assert mem.search("checkout", "redis latency").facts == []    # never offered as a known fact


async def test_remember_extracts_once_and_is_gated() -> None:
    extraction = {"worth_storing": True, "reason": "trigger-level cause",
                  "incident": {"title": "checkout OOM after batch raise", "summary": "5xx at 9/s",
                               "root_cause": "batch size 5000", "causal_level": "trigger", "mitigation": "rollback",
                               "confidence": 0.8},
                  "facts": [{"kind": "gotcha", "text": "testkube pod logs need previous=true for crashloops",
                             "confidence": 0.7},
                            {"kind": "ruled_out", "text": "DNS was refuted for checkout 5xx", "confidence": 0.6}]}
    llm = ScriptedLLM(structured={"MemoryExtraction": extraction})
    mem = MemoryStore(":memory:")
    refs = await mem.remember(_run("r1"), llm)
    assert refs == ["M1", "F1", "F2"]
    calls = llm.calls_for("structured", "memory")
    assert len(calls) == 1 and calls[0].system.startswith("# Role and audience")
    assert "CARD VERDICT: Release abc123" in calls[0].user        # conclusions first
    assert "ruled out" in mem.render_recall("checkout", "DNS checkout 5xx")
    # At most one incident memory per investigation, even when asked again.
    await mem.remember(_run("r1"), llm)
    assert [h.ref for h in mem.search("checkout", "checkout OOM batch").incidents] == ["M1"]
    # Chat never produces an incident memory (facts still allowed).
    chat_refs = await mem.remember(_run("c1", kind="chat"), llm)
    assert not any(r.startswith("M") for r in chat_refs)
    # Runs without evidence store nothing and make no call.
    before = len(llm.calls)
    assert await mem.remember(_run("x1", status="insufficient_data"), llm) == []
    assert len(llm.calls) == before
    # The model's own gate.
    no = ScriptedLLM(structured={"MemoryExtraction": {"worth_storing": False, "reason": "routine check"}})
    assert await MemoryStore(":memory:").remember(_run("r9"), no) == []


def test_consolidate_merges_duplicates_and_decays() -> None:
    mem = MemoryStore(":memory:")
    now = time.time()
    old = now - 400 * DAY
    a = mem.add_fact(kind="threshold", text="checkout p95 alert fires at 300 ms", service="checkout-api",
                     run_id="r1", confidence=0.8, now=old)
    b = mem.add_fact(kind="threshold", text="checkout p95 alert fires at 450 ms", service="Checkout-API",
                     run_id="r2", now=now - 10 * DAY)
    mem.vote("r2", +1, now=now - 10 * DAY)
    stale = mem.add_fact(kind="gotcha", text="the legacy exporter drops labels", service="legacy", run_id="r3",
                         now=old)
    mem.vote("r3", -1, now=old)
    liked = mem.add_fact(kind="procedure", text="restart the legacy exporter with --keep-labels", service="legacy",
                         run_id="r4", now=old)
    mem.vote("r4", +1, now=old)
    out = mem.consolidate(now=now)
    assert out == {"merged": 1, "archived": 1}
    hits = {h.ref: h for h in mem.search("checkout-api", "checkout p95 alert fires", now=now).facts}
    assert a in hits and b not in hits                           # the older fact wins
    assert hits[a].net_votes == 1                                # counters merged into the survivor
    recall = mem.search("legacy", "legacy exporter labels", now=now)
    refs = {h.ref for h in recall.facts + recall.avoid}
    assert stale not in refs and liked in refs                   # old + disliked + never reinforced -> archived
    decayed = next(h for h in recall.facts if h.ref == liked)
    assert decayed.confidence < 0.6 * 0.5                        # 400 days > two 180-day half-lives


def test_extraction_schema_accepts_minimal_reply() -> None:
    out: Any = MemoryExtraction.model_validate({"worth_storing": False})
    assert out.incident is None and out.facts == []


def test_secrets_never_reach_disk(tmp_path: Any) -> None:
    path = tmp_path / "memory.db"
    mem = MemoryStore(path)
    mem.add_fact(kind="procedure", service="catalog",
                 text="catalog reconnects with DB_CONNECTION_STRING=postgres://shop_user:s3cret@pg:5432/shop_db; "
                      "header Authorization: Bearer abcdef123456789 and password=hunter2")
    mem.add_episode(run_id="r1", title="catalog auth failures", summary="token: ghp_abcdefghijklmnop1234", service="catalog")
    mem.close()
    raw = path.read_bytes()
    for secret in (b"s3cret", b"abcdef123456789", b"hunter2", b"ghp_abcdefghijklmnop1234"):
        assert secret not in raw
    assert redact("card token rejected by processor") == "card token rejected by processor"
    assert "postgres://[REDACTED]@pg:5432" in redact("postgres://u:p@pg:5432/db")
