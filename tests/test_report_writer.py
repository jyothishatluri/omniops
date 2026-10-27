"""Report writer, conclusion card and calibrated language through their real entry points.

``write_report`` / ``build_card`` / ``early_card`` run against a hand-built run context: a local stub
LLM (no network), a small evidence board and hypothesis tracker with the documented interfaces.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from omniops.agent import language
from omniops.agent.card import build_card, early_card
from omniops.agent.context import Deps, RunContext
from omniops.agent.report import REQUIRED_SECTIONS, get_section, split_sections, write_report
from omniops.agent.types import (
    Alert,
    ChangeRef,
    ConclusionCard,
    EvidenceRecord,
    Hypothesis,
    ImpactSummary,
    SpecialistVerdict,
    ToolEvent,
)
from omniops.engine.engine import StagedWrite
from omniops.engine.timeutil import TimeAnchor
from omniops.llm.base import LLMResponse, Usage
from omniops.settings import Settings
from omniops.slack.render import card_blocks, validate_blocks

TRIGGER = datetime(2026, 10, 1, 14, 10, tzinfo=UTC)
ALERT_URL = "https://grafana.test/alerting/grafana/payment-errors/view"
LOKI_URL = "https://grafana.test/explore?left=%7B%22queries%22%3A%5B%7B%22expr%22%3A%22payment%22%7D%5D%7D"
ARGO_URL = "https://argocd.test/applications/shop-config?revision=4f1c2ab"
CHECKOUT_URL = "https://grafana.test/d/checkout?from=1759327200000&to=1759329000000"
EMPTY_URL = "https://grafana.test/explore?empty=1"
ERROR_URL = "https://grafana.test/explore?failed=1"


# ---------------------------------------------------------------------------------------------
# Local stubs (documented interfaces of EvidenceBoard, HypothesisTracker and LLMClient)
# ---------------------------------------------------------------------------------------------


class StubBoard:
    def __init__(self, records: list[EvidenceRecord]) -> None:
        self.records = records

    def visible(self) -> list[EvidenceRecord]:
        return [r for r in self.records if not r.id.startswith("S")]

    def meaningful_count(self) -> int:
        return sum(1 for r in self.visible() if r.meaningful and not r.error and r.confidence >= 0.3)


class StubTracker:
    def __init__(self, hyps: list[Hypothesis]) -> None:
        self.hypotheses = {h.id: h for h in hyps}

    def leader(self) -> Hypothesis | None:
        live = [h for h in self.hypotheses.values() if h.status != "refuted"]
        return max(live, key=lambda h: h.confidence, default=None)


@dataclass
class StubLLM:
    replies: list[Any] = field(default_factory=list)     # str | (str, stop_reason) | Exception
    card: Any = None                                      # ConclusionCard | None | Exception
    calls: list[dict[str, Any]] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)

    async def complete(self, *, role: str, system: str, input: Any, tools: Any = None, schema: Any = None,
                       schema_name: str = "output", effort: str | None = None,
                       max_output_tokens: int | None = None, timeout_s: float | None = None) -> LLMResponse:
        self.calls.append({"kind": "complete", "role": role, "system": system, "input": input,
                           "max_output_tokens": max_output_tokens})
        reply = self.replies.pop(0) if self.replies else ""
        if isinstance(reply, Exception):
            raise reply
        text, stop = reply if isinstance(reply, tuple) else (reply, "completed")
        return LLMResponse(text=text, tool_calls=[], usage=Usage(), model="stub", stop_reason=stop)

    async def structured(self, *, role: str, system: str, user: str, schema: Any, effort: str | None = None,
                         max_output_tokens: int | None = None, attempts: int = 3,
                         timeout_s: float | None = None) -> Any:
        self.calls.append({"kind": "structured", "role": role, "system": system, "user": user,
                           "schema": schema.__name__})
        if isinstance(self.card, Exception):
            raise self.card
        return self.card.model_copy(deep=True) if self.card is not None else None

    async def run_tool_loop(self, **kwargs: Any) -> Any:
        raise AssertionError("the report writer never runs a tool loop")


# ---------------------------------------------------------------------------------------------
# A payment-failure style run
# ---------------------------------------------------------------------------------------------


def rec(eid: str, title: str, summary: str, *, kind: str = "query", agent: str = "primary", rows: int = 5,
        links: list[str] | None = None, pinned: bool = False, **kw: Any) -> EvidenceRecord:
    return EvidenceRecord(id=eid, kind=kind, agent=agent, title=title, summary=summary, row_count=rows,  # type: ignore[arg-type]
                          links=[{"label": title, "url": u} for u in links or []], pinned=pinned, **kw)


def records() -> list[EvidenceRecord]:
    return [
        rec("E1", "Alert rule PaymentErrors", "payment 5xx share 42.0% > 5% threshold for 5m", kind="alert",
            agent="seed", pinned=True, links=[ALERT_URL]),
        rec("E2", "payment error log patterns", "312 lines 'card token rejected by processor' in payment "
            "from 2026-10-01 14:03 UTC; 0 the day before", agent="seed", pinned=True, links=[LOKI_URL],
            tables=["loki_logs"]),
        rec("E3", "change events 48h", "2026-10-01 14:02 UTC Argo CD sync shop-config revision 4f1c2ab: "
            "ConfigMap shop/flagd-config paymentStrictTokenCheck defaultVariant off -> on", agent="seed",
            pinned=True, links=[ARGO_URL], tables=["argocd_history"]),
        rec("E4", "checkout 5xx share", "checkout 5xx share 38.5% 14:05-14:15 UTC (baseline 0.2%)",
            links=[CHECKOUT_URL], tables=["prometheus_query_range"]),
        rec("E5", "redis errors", "0 rows", rows=0, empty_verdict="inconclusive", meaningful=False,
            links=[EMPTY_URL]),
        rec("E6", "tempo traces", "", rows=0, error="HTTP 503 from tempo", meaningful=False, confidence=0.0,
            links=[ERROR_URL]),
        rec("S1", "shadow agent probe", "shadow-only data", agent="shadow", links=["https://shadow.test/x"]),
    ]


def hypotheses(top: float = 0.82) -> list[Hypothesis]:
    return [
        Hypothesis(id="H1", statement="Enabling paymentStrictTokenCheck in flagd-config makes payment reject "
                   "card tokens", causal_level="trigger", category="config", confidence=top,
                   supporting=["E2", "E3", "E9"], status="confirmed" if top >= 0.7 else "open",
                   missing=["payment charge success rate after reverting the flag"]),
        Hypothesis(id="H2", statement="Redis outage breaks the cart", causal_level="mechanism",
                   category="dependency", confidence=0.31, supporting=["E5"], status="refuted"),
        Hypothesis(id="H1.1", statement="payment rejects all synthetic tokens", causal_level="mechanism",
                   category="config", confidence=round(top * 0.7, 2), supporting=["E2"], parent="H1"),
    ]


def verdicts() -> list[SpecialistVerdict]:
    return [
        SpecialistVerdict(agent="customer_impact", verdict="found", confidence=0.8,
                          headline="Checkout fails for 38.5% of requests since 14:05 UTC; payment charge "
                          "requests are rejected", evidence_ids=["E4", "E77"],
                          details={"level": "high", "affected": ["payment", "checkout"]}),
        SpecialistVerdict(agent="code_change", verdict="found", confidence=0.75,
                          headline="Argo CD sync 4f1c2ab enabled paymentStrictTokenCheck",
                          evidence_ids=["E3"],
                          details={"change": {"title": "Enable strict token check", "url": ARGO_URL,
                                              "author": "ops-bot", "ts": "2026-10-01T14:02:00Z",
                                              "version": "4f1c2ab"}}),
    ]


def make_ctx(llm: StubLLM, *, recs: list[EvidenceRecord] | None = None, hyps: list[Hypothesis] | None = None,
             specs: list[SpecialistVerdict] | None = None, question: str | None = None,
             with_alert: bool = True) -> RunContext:
    alert = Alert(platform="grafana", rule_name="PaymentErrors", title="Payment 5xx share above 5%",
                  service="payment", namespace="shop", triggered_at=TRIGGER, url=ALERT_URL) if with_alert else None
    anchor = TimeAnchor.for_trigger(TRIGGER, wall_now=datetime(2026, 10, 1, 15, 0, tzinfo=UTC))
    board = StubBoard(records() if recs is None else recs)
    ctx = RunContext(run_id="run-42", deps=Deps(catalog=None, llm=llm, settings=Settings()),  # type: ignore[arg-type]
                     anchor=anchor, engine=None, cache=None, board=board,  # type: ignore[arg-type]
                     tracker=StubTracker(hypotheses() if hyps is None else hyps),  # type: ignore[arg-type]
                     deadline=time.monotonic() + 600, alert=alert, question=question)
    ctx.specialist_verdicts = verdicts() if specs is None else specs
    ctx.tool_events = [ToolEvent(agent="primary", name="run_sql", arguments={"sql": "SELECT 1"}, output="ok",
                                 evidence_id="E4")]
    return ctx


GOOD_REPORT = f"""**TL;DR**
The root cause is the flag paymentStrictTokenCheck enabled in `flagd-config` at 14:02 UTC [E3] (trigger, 82%).

## Root cause
Argo CD synced revision 4f1c2ab, which set paymentStrictTokenCheck to on [E3, E42]; payment then rejected
card tokens [E2] [E99]. See https://evil.example/fabricated and [the docs](https://docs.invented.test/page).
The `ledger-svc` was never involved.

*Evidence*
- 312 token rejections since 14:03 UTC [E2] <{LOKI_URL}|payment errors>
- checkout 5xx share 38.5% (E4)

*Impact / blast radius*
payment and checkout fail [E4].

| service | 5xx share |
|---|---|
| checkout | 38.5% E4 |
| payment | 42.0% E17 |

*Timeline (UTC)*
• 2026-10-01 14:02 UTC sync [E3]

*Mitigation*
1. Set paymentStrictTokenCheck defaultVariant back to off in ConfigMap shop/flagd-config [E3].
2. Verify checkout 5xx share falls below 1% [E4].
*Do not* rotate the payment provider credentials: the rejections come from the flag [E2].

*Causal change*
Argo CD sync 4f1c2ab [E3].

*Next steps (human-only)*
• Approve the flag change.

*Gaps & limits*
• Tempo was unavailable [E6].
"""


def _user(llm: StubLLM, n: int = 0) -> str:
    inp = [c for c in llm.calls if c["kind"] == "complete"][n]["input"]
    return inp if isinstance(inp, str) else inp[0]["content"]


# ---------------------------------------------------------------------------------------------
# write_report
# ---------------------------------------------------------------------------------------------


async def test_investigation_report_is_validated_and_links_are_placed() -> None:
    llm = StubLLM(replies=[GOOD_REPORT])
    ctx = make_ctx(llm)
    out = await write_report(ctx)

    v = ctx.extra["report_validation"]
    assert sorted(set(v["stripped_ids"])) == ["E17", "E42", "E99"]
    for bad in ("E42", "E99", "E17"):
        assert bad not in out
    assert "[E3]" in out and "[E2]" in out and "(E4)" in out
    # fabricated URLs are stripped, the markdown link keeps only its label
    assert "evil.example" not in out and "docs.invented.test" not in out
    assert "the docs" in out
    assert any("evil.example" in u for u in v["stripped_urls"])
    # every bucket link is present: placed by the model or appended by code
    for url in (ALERT_URL, LOKI_URL, ARGO_URL, CHECKOUT_URL, EMPTY_URL):
        assert url in out
    assert ERROR_URL not in out and "shadow.test" not in out     # failed query and shadow agent
    assert out.count(LOKI_URL) == 1                               # placed once, not re-appended
    assert out.rstrip().endswith(")") and "*Links*" in out
    # Slack mrkdwn only: no double asterisks, no Markdown headings, the pipe table became a code block
    assert "**" not in out and not re.search(r"^#", out, re.MULTILINE)
    table = re.search(r"```\n(.*?)\n```", out, re.DOTALL)
    assert table and "checkout | 38.5%" in table.group(1) and "E17" not in table.group(1)
    # calibrated: 0.82 keeps "The root cause is"; no banner
    assert "The root cause is" in out and not out.startswith(":warning:")
    assert not v["missing_sections"]
    # grounding: a name that appears in no evidence is flagged internally (not shown as a warning)
    assert any("ledger-svc" in w for w in v["grounding_warnings"])
    assert not any("flagd-config" in w for w in v["grounding_warnings"])


async def test_report_request_uses_fixed_slots_and_static_prompt() -> None:
    llm = StubLLM(replies=[GOOD_REPORT])
    ctx = make_ctx(llm)
    await write_report(ctx)
    call = llm.calls[0]
    assert call["role"] == "report" and call["max_output_tokens"] >= 64_000
    assert call["system"].startswith("# Role and audience") and "{{" not in call["system"]
    user = _user(llm)
    order = ["## REQUEST", "## TIME WINDOW", "## CONTEXT", "## EVIDENCE", "## HYPOTHESES", "## SPECIALIST VERDICTS",
             "## LINK BUCKET", "## TOOL ACTIVITY", "## TASK"]
    positions = [user.index(h) for h in order]
    assert positions == sorted(positions)
    evidence = user[user.index("## EVIDENCE"):user.index("## HYPOTHESES")]
    assert evidence.index("E1 [pinned") < evidence.index("E4 [")          # pinned first
    assert "S1" not in user                                               # shadow ids are not citable
    assert 'required opener: "The root cause is"' in user
    hyps = user[user.index("## HYPOTHESES"):user.index("## SPECIALIST")]
    assert "  H1.1 [" in hyps                                             # sub-hypothesis nested under H1
    assert "refuted -> omit" in hyps
    bucket = user[user.index("## LINK BUCKET"):user.index("## TOOL ACTIVITY")]
    assert bucket.count(LOKI_URL) == 1 and ERROR_URL not in bucket


async def test_staged_writes_reach_the_writer_from_the_engines() -> None:
    llm = StubLLM(replies=[GOOD_REPORT])
    ctx = make_ctx(llm)
    write = StagedWrite("w-9", "grafana_annotations", {}, "Annotate dashboard abc token=sekrit", 0.0, 1e12)
    ctx._engines = [SimpleNamespace(staged_writes={"w-9": write})]  # type: ignore[list-item]
    await write_report(ctx)
    context = _user(llm)[_user(llm).index("## CONTEXT"):_user(llm).index("## EVIDENCE")]
    assert "write w-9 on grafana_annotations" in context and "(executed=no)" in context
    assert "sekrit" not in context


async def test_gaps_have_their_own_slot_and_deepening_reaches_the_writer() -> None:
    """3.11: what stayed open is disclosed; loud-zero warnings cannot crowd it out of the CONTEXT slot."""
    llm = StubLLM(replies=[GOOD_REPORT])
    ctx = make_ctx(llm)
    ctx.warn("gap: the leading hypothesis H1 is at mechanism level; trigger not found")
    ctx.warnings += [f"primary: E{i} returned 0 rows (inconclusive) from loki_logs" for i in range(30)]
    ctx.extra["open_gaps"] = ["did the 14:02 sync touch payment-config?"]
    ctx.extra["deepening"] = "DEEPENING: 1 round(s); focus H1; stopped: exhausted"
    await write_report(ctx)
    user = _user(llm)
    gaps = user[user.index("## GAPS"):user.index("## LINK BUCKET")]
    assert "- the leading hypothesis H1 is at mechanism level; trigger not found" in gaps
    assert "- left open at the continuation limit: did the 14:02 sync touch payment-config?" in gaps
    context = user[user.index("## CONTEXT"):user.index("## EVIDENCE")]
    assert "trigger not found" not in context and "E29 returned 0 rows" in context
    assert "DEEPENING: 1 round(s)" in user[user.index("## HYPOTHESES"):user.index("## SPECIALIST")]


async def test_ties_are_flagged_between_families_not_within_one() -> None:
    def hyp(hid: str, conf: float, parent: str | None = None, status: str = "open") -> Hypothesis:
        return Hypothesis(id=hid, statement=f"claim {hid}", causal_level="mechanism", confidence=conf,
                          parent=parent, status=status)  # type: ignore[arg-type]

    llm = StubLLM(replies=[GOOD_REPORT, GOOD_REPORT, GOOD_REPORT])
    await write_report(make_ctx(llm, hyps=[hyp("H1", 0.62), hyp("H1.1", 0.6, "H1"), hyp("H2", 0.3)]))
    await write_report(make_ctx(llm, hyps=[hyp("H1", 0.4), hyp("H1.1", 0.62, "H1"), hyp("H2", 0.58)]))
    await write_report(make_ctx(llm, hyps=[hyp("H1", 0.62), hyp("H2", 0.6, status="refuted")]))
    assert "TIE:" not in _user(llm, 0)
    assert "TIE: H1.1 and H2 are within 0.08" in _user(llm, 1)
    assert "TIE:" not in _user(llm, 2)


async def test_report_input_trims_noise_before_evidence() -> None:
    llm = StubLLM(replies=[GOOD_REPORT])
    ctx = make_ctx(llm)
    ctx.thread_context = "\n\n".join(f"user{i}: " + "chatter " * 400 for i in range(30))
    ctx.tool_events = [ToolEvent(agent="primary", name="run_sql", arguments={"sql": "SELECT " + "x" * 300},
                                 output="ok") for _ in range(200)]
    await write_report(ctx, budget_chars=12_000)
    user = _user(llm)
    assert len(user) < 13_000
    assert "312 lines 'card token rejected by processor'" in user        # evidence survives
    assert "## TOOL ACTIVITY" in user and ("omitted" in user or "trimmed" in user)
    assert not any("evidence was trimmed" in w for w in ctx.warnings)


async def test_low_confidence_banner_moves_next_steps_and_downgrades_wording() -> None:
    llm = StubLLM(replies=[("*TL;DR*\nThe root cause is definitely the flag [E3].\n\n*Root cause*\nIt proves "
                           "that the flag broke payment [E2].\n\n*Mitigation*\n1. Turn the flag off [E3].\n\n"
                           "*Next steps*\n• Confirm with the payments team.\n")])
    ctx = make_ctx(llm, hyps=hypotheses(top=0.35))
    out = await write_report(ctx)
    assert out.startswith(":warning: *Low confidence:*")
    assert "H1" in out.split("\n")[0] and "35%" in out.split("\n")[0]
    assert "The root cause is" not in out and "definitely" not in out and "proves" not in out
    assert "A possible factor (unconfirmed) is" in out
    assert out.index("*Next steps*") < out.index("*Root cause*")
    assert ctx.extra["report_validation"]["banner"] is True
    assert ctx.extra["report_validation"]["calibration_changes"]


async def test_no_hypotheses_means_no_root_cause_wording() -> None:
    llm = StubLLM(replies=["*TL;DR*\nThe root cause is the flag [E3]."])
    ctx = make_ctx(llm, hyps=[])
    out = await write_report(ctx)
    assert out.startswith(":warning: *Low confidence:* no hypothesis")
    assert "The root cause is" not in out
    assert "zero hypotheses at report time" in ctx.warnings


async def test_insufficient_data_template_below_the_floor() -> None:
    llm = StubLLM(replies=["should never be used"])
    recs = [records()[0], records()[4], records()[5]]                   # alert + empty + error
    ctx = make_ctx(llm, recs=recs, hyps=[], specs=[])
    out = await write_report(ctx)
    assert not llm.calls                                                 # no speculation call
    assert out.startswith(":grey_question: *Insufficient data to determine a cause*")
    assert "*What was tried*" in out and "*What a human should check*" in out
    assert "E5 redis errors: no rows (inconclusive)" in out
    assert "E6 tempo traces: error: HTTP 503 from tempo" in out
    assert ALERT_URL in out and "1 tool calls" in out
    assert any("below the evidence floor" in w for w in ctx.warnings)
    card = await build_card(ctx, out)
    assert card.status == "insufficient_data" and card.confidence == 0.0 and card.run_id == "run-42"
    assert card.gaps and not llm.calls
    blocks = card_blocks(card)
    assert validate_blocks(blocks) == []
    assert any("What a human should check" in str(b) for b in blocks)


async def test_writer_failure_ships_a_deterministic_report() -> None:
    llm = StubLLM(replies=[TimeoutError("report call timed out")])
    ctx = make_ctx(llm)
    out = await write_report(ctx)
    keys = {s.key for s in split_sections(out)}
    assert set(REQUIRED_SECTIONS) <= keys
    assert "The root cause is enabling paymentStrictTokenCheck" in get_section(out, "tldr")
    assert "Checkout fails for 38.5%" in get_section(out, "impact")
    assert "Argo CD sync 4f1c2ab" in get_section(out, "causal_change")
    assert "E9" not in out and "Redis outage" not in out                  # dangling id, refuted hypothesis
    assert any("writer failed" in w for w in ctx.warnings)


async def test_truncated_reply_is_continued_and_spliced() -> None:
    head = "*TL;DR*\nThe root cause is the flag [E3].\n\n*Root cause*\nArgo CD synced revision 4f1c2ab"
    tail = "CD synced revision 4f1c2ab at 14:02 UTC [E3].\n\n*Mitigation*\n1. Turn the flag off [E3]."
    llm = StubLLM(replies=[(head, "max_output_tokens"), (tail, "completed")])
    ctx = make_ctx(llm)
    out = await write_report(ctx)
    assert len(llm.calls) == 2
    assert llm.calls[1]["input"][-1]["content"].startswith("Your previous reply was cut off")
    assert "Argo CD synced revision 4f1c2ab at 14:02 UTC [E3]." in out
    assert out.count("revision 4f1c2ab") == 1


async def test_chat_answer_gives_the_value_first_and_only_data_bearing_links() -> None:
    llm = StubLLM(replies=[("*Answer*\n`checkout` has the highest 5xx share: 38.5% over 14:05-14:15 UTC [E4] "
                           "[E12].\n\n*Evidence*\n• checkout 5xx share 38.5% [E4]")])
    ctx = make_ctx(llm, hyps=[], specs=[], question="Which service has the highest 5xx share?", with_alert=False)
    out = await write_report(ctx, mode="chat")
    assert llm.calls[0]["role"] == "chat"
    assert "OmniOps answering a question" in llm.calls[0]["system"]
    assert out.startswith("*Answer*\n`checkout` has the highest 5xx share: 38.5%")
    assert "E12" not in out and not out.startswith(":warning:")
    assert CHECKOUT_URL in out and LOKI_URL in out
    assert EMPTY_URL not in out                                           # empty result: no link in chat


async def test_chat_without_any_data_returns_insufficient_answer() -> None:
    llm = StubLLM()
    ctx = make_ctx(llm, recs=[], hyps=[], specs=[], question="What is the p99 of checkout?", with_alert=False)
    ctx.tool_events = []
    out = await write_report(ctx, mode="chat")
    assert out.startswith(":grey_question: *Insufficient data to answer*")
    assert "No queries ran" in out and not llm.calls
    assert "insufficient data: zero tool calls and zero evidence" in ctx.warnings


# ---------------------------------------------------------------------------------------------
# build_card / early_card
# ---------------------------------------------------------------------------------------------


def _formatter_card() -> ConclusionCard:
    return ConclusionCard(
        run_id="wrong-run", title="Payment errors", verdict="The root cause is definitely the strict token flag.",
        root_cause="paymentStrictTokenCheck was enabled [E3] [E55].", causal_level="symptom", confidence=0.99,
        impact=ImpactSummary(level="critical", summary="All users are down", affected=["everything"]),
        mitigation=["Set paymentStrictTokenCheck back to off in shop/flagd-config <https://made.up/x|here>"],
        hypotheses=[{"id": "H1", "statement": "flag", "confidence": 0.99, "status": "open", "evidence_ids": []},
                    {"id": "H7", "statement": "invented", "confidence": 0.5, "status": "open",
                     "evidence_ids": ["E1"]}],
        evidence_links=[{"label": "made up", "url": "https://made.up/link"}], gaps=["Tempo unavailable"])


async def test_card_overrides_win_over_the_formatter() -> None:
    llm = StubLLM(replies=[GOOD_REPORT], card=_formatter_card())
    ctx = make_ctx(llm)
    report = await write_report(ctx)
    card = await build_card(ctx, report)

    fmt = next(c for c in llm.calls if c["kind"] == "structured")
    assert fmt["role"] == "formatter" and fmt["schema"] == "ConclusionCard"
    assert "## FINAL REPORT (authoritative)" in fmt["user"] and "*Mitigation*" in fmt["user"]
    assert card.run_id == "run-42" and card.status == "final"
    assert card.confidence == 0.82 and card.causal_level == "trigger"
    assert "definitely" not in card.verdict or card.confidence >= 0.7
    assert "E55" not in card.root_cause
    rows = {h["id"]: h for h in card.hypotheses}
    assert set(rows) == {"H1", "H1.1"}                                    # tracker rows; refuted H2 dropped
    assert rows["H1"]["evidence_ids"] == ["E2", "E3"]                     # restored, dangling E9 dropped
    assert card.impact.summary.startswith("Checkout fails for 38.5%") and card.impact.level == "high"
    assert card.impact.affected == ["payment", "checkout"] and card.impact.evidence_ids == ["E4"]
    assert card.trigger_change is not None and card.trigger_change.url == ARGO_URL
    assert card.trigger_change.version == "4f1c2ab" and card.trigger_change.author == "ops-bot"
    urls = [lk["url"] for lk in card.evidence_links]
    assert "https://made.up/link" not in urls and LOKI_URL in urls and len(urls) <= 10
    assert all("made.up" not in m for m in card.mitigation)
    blocks = card_blocks(card)
    assert validate_blocks(blocks) == []
    assert ARGO_URL in str(blocks)


async def test_card_impact_flagged_when_its_hypothesis_was_refuted() -> None:
    specs = verdicts()
    specs[0].details["hypothesis_id"] = "H2"
    llm = StubLLM(replies=[GOOD_REPORT], card=_formatter_card())
    ctx = make_ctx(llm, specs=specs)
    card = await build_card(ctx, await write_report(ctx))
    assert "scoped to H2, which deepening later refuted" in card.impact.summary


async def test_card_drops_a_trigger_change_the_report_does_not_have() -> None:
    specs = verdicts()
    specs[1] = SpecialistVerdict(agent="code_change", verdict="not_found", headline="No change in 48h")
    formatted = _formatter_card()
    formatted.trigger_change = ChangeRef(title="Invented PR #12", url="https://github.com/acme/shop/pull/12")
    llm = StubLLM(replies=[GOOD_REPORT.replace("Argo CD sync 4f1c2ab [E3].", "Not identified: no deploys.")],
                  card=formatted)
    ctx = make_ctx(llm, specs=specs)
    card = await build_card(ctx, await write_report(ctx))
    assert card.trigger_change is None


async def test_deterministic_card_when_the_formatter_fails() -> None:
    llm = StubLLM(replies=[GOOD_REPORT], card=RuntimeError("formatter down"))
    ctx = make_ctx(llm)
    report = await write_report(ctx)
    card = await build_card(ctx, report)
    assert card.status == "final" and card.run_id == "run-42"
    assert card.verdict.startswith("The root cause is the flag paymentStrictTokenCheck")
    assert card.mitigation[0].startswith("Set paymentStrictTokenCheck defaultVariant back to off")
    assert any(m.startswith("*Do not* rotate") for m in card.mitigation)
    assert card.confidence == 0.82 and card.trigger_change is not None
    assert card.gaps == ["Tempo was unavailable [E6]."]
    assert "card formatter produced no valid card; deterministic card used" in ctx.warnings
    assert validate_blocks(card_blocks(card)) == []


async def test_inline_labelled_headings_still_fill_the_card() -> None:
    report = ("*TL;DR:* The most likely contributing factor is the flag [E3].\n"
              "*Mitigation:* Turn paymentStrictTokenCheck off in shop/flagd-config [E3].\n"
              "*Gaps & limits*: Tempo was down.")
    llm = StubLLM(card=None)
    ctx = make_ctx(llm, hyps=hypotheses(top=0.6))
    card = await build_card(ctx, report)
    assert card.mitigation == ["Turn paymentStrictTokenCheck off in shop/flagd-config [E3]."]
    assert card.verdict.startswith("The most likely contributing factor is the flag")
    assert card.gaps == ["Tempo was down."]


async def test_early_card_is_preliminary_and_calibrated() -> None:
    llm = StubLLM()
    ctx = make_ctx(llm, hyps=hypotheses(top=0.55))
    card = early_card(ctx)
    assert card.status == "preliminary" and card.run_id == "run-42" and not llm.calls
    assert card.verdict.startswith("Preliminary: The most likely contributing factor is enabling")
    assert card.confidence == 0.55 and card.causal_level == "trigger"
    blocks = card_blocks(card)
    assert validate_blocks(blocks) == []
    actions = next(b for b in blocks if b["type"] == "actions")["elements"]
    assert [a["action_id"] for a in actions] == ["omniops_stop"]


async def test_real_board_and_tracker_feed_the_report() -> None:
    try:
        from omniops.agent.evidence import EvidenceBoard
        from omniops.agent.hypotheses import HypothesisTracker
    except ImportError as e:                                              # sibling module mid-rewrite
        pytest.skip(f"evidence board/tracker not importable: {e}")
    board = EvidenceBoard(shadow_agents=["shadow"])
    for r in records():
        board.add(r, pinned=r.pinned)
    tracker = HypothesisTracker(board)
    tracker.hypotheses = {h.id: h for h in hypotheses()}
    llm = StubLLM(replies=[GOOD_REPORT])
    ctx = make_ctx(llm)
    ctx.board, ctx.tracker = board, tracker
    out = await write_report(ctx)
    assert "[E3]" in out and "E99" not in out and "shadow.test" not in out
    card = await build_card(ctx, out)
    assert card.confidence == 0.82 and {h["id"] for h in card.hypotheses} >= {"H1"}


# ---------------------------------------------------------------------------------------------
# language
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("conf", "phrase"), [
    (0.95, "The root cause is"), (0.7, "The root cause is"), (0.69, "The most likely contributing factor is"),
    (0.5, "The most likely contributing factor is"), (0.45, "A possible factor (unconfirmed) is"),
    (0.3, "A possible factor (unconfirmed) is"), (0.29, None),
])
def test_wording_tiers(conf: float, phrase: str | None) -> None:
    assert language.lead_phrase(conf) == phrase
    assert language.shown(conf) is (phrase is not None)
    assert language.needs_banner(conf) is (conf < 0.4)


def test_overclaims_are_downgraded_outside_code_and_links() -> None:
    text = ("The root cause is a bad flag. The flag is the root cause. This proves that payment broke "
            "definitely. `The root cause is` <https://x.test|The root cause is>")
    cal = language.calibrate(text, 0.55)
    assert cal.text.startswith("The most likely contributing factor is a bad flag.")
    assert "The flag is the most likely contributing factor." in cal.text
    assert "This suggests that payment broke likely." in cal.text
    assert cal.text.endswith("`The root cause is` <https://x.test|The root cause is>")
    assert len(cal.changes) == 4
    assert language.calibrate(text, 0.8).text == text
    assert language.calibrate(text, None).text == text
    assert language.level_label("mechanism") == "mechanism (trigger not found)"
