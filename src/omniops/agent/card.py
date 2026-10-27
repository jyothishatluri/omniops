"""Conclusion card: formatted from the finished report, then corrected by deterministic overrides.

Retrospective lessons encoded here:
- D10: the card is the primary result. It is built after the report (an early card built from raw
  hypotheses once named the wrong cause), an early card is posted first and the final card replaces
  it in place (same run id). The LLM fills a data object; code renders the blocks.
- 3.8.4: the formatter is schema-bound with up to three attempts (the client re-asks with the exact
  validation error); after that a fully deterministic card is built. A card always posts.
- 3.15: the report is authoritative and structured verdicts win over the formatter's copy: run id,
  confidence and causal level come from the tracker's leader; each hypothesis' evidence ids are
  restored from the tracker with dangling ids dropped; impact is copied verbatim from the
  customer-impact verdict (flagged when deepening refuted the hypothesis it was scoped to); the
  triggering change comes from the code-change verdict.
- 3.5 / 3.7: card links come only from the evidence link bucket, and the verdict wording is
  calibrated to the leader's confidence.
- 7.1: mitigation and gaps fall back to the report's sections via heading aliases, so a heading
  variant never empties the card's mitigation.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from omniops.agent import language
from omniops.agent.report import (
    CARD_LINKS_MAX,
    RunView,
    bullets,
    get_section,
    load_prompt,
    run_view,
    split_sections,
    strip_disallowed_urls,
    strip_unknown_ids,
)
from omniops.agent.types import ChangeRef, ConclusionCard, ImpactSummary, SpecialistVerdict
from omniops.slack.render import redact

if TYPE_CHECKING:
    from omniops.agent.context import RunContext

log = logging.getLogger("omniops.card")

CARD_REPORT_CHARS = 40_000
FORMATTER_MAX_OUTPUT_TOKENS = 8_000      # reasoning shares the cap (3.8.5)
CARD_HYPOTHESES_MAX = 5
CARD_MITIGATION_MAX = 6
CARD_GAPS_MAX = 5
_IMPACT_LEVELS = {"none", "low", "medium", "high", "critical", "unknown"}
_SECTION_PRIORITY = ("tldr", "answer", "root_cause", "impact", "mitigation", "causal_change", "gaps", "timeline",
                     "next_steps", "evidence")
_DO_NOT = re.compile(r"^[*_]*(?:do not|don't|avoid)\b", re.IGNORECASE)
_CHANGE_URL = re.compile(r"/(commit|commits|pull|merge_requests|compare|applications)/|github|gitlab|argocd",
                         re.IGNORECASE)
_NO_CHANGE = re.compile(r"^\W*(not identified|none identified|no (causal|introducing|triggering) change)",
                        re.IGNORECASE)


def _title(ctx: RunContext) -> str:
    a = ctx.alert
    if a is not None and (a.title or a.rule_name):
        return (a.title or a.rule_name or "")[:150]
    if a is not None and a.service:
        return f"{a.service} alert"
    if ctx.question:
        return " ".join(ctx.question.split())[:120]
    return f"Investigation {ctx.run_id}"


def _first_sentence(text: str) -> str:
    text = " ".join(re.sub(r"^\s*(?:[•\-*+]|\d+[.)])\s+", "", ln) for ln in text.split("\n") if ln.strip())
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z:*_`<])", text, maxsplit=1)
    return parts[0].strip() if parts else ""


def _first_paragraph(text: str) -> str:
    return next((p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()), "")


def _clean(text: str, view: RunView, confidence: float) -> str:
    """Strip unknown evidence ids and ungrounded URLs, then calibrate the wording."""
    text, _ = strip_unknown_ids(text or "", view.ids)
    text, _ = strip_disallowed_urls(text, view.allowed_urls)
    return language.calibrate(text, confidence).text.strip()


def _default_verdict(view: RunView) -> str:
    lead = view.leader
    if lead is not None and language.shown(lead.confidence, lead.status):
        return language.calibrated_sentence(lead.statement, lead.confidence)
    return "The cause is not established; see the report for what was checked."


def report_mitigation(report_md: str) -> list[str]:
    """Mitigation items from the report: its bullets/numbered steps plus any "Do not" line, else its
    first paragraph (so a formatting variant never empties the card's mitigation, 7.1)."""
    body = get_section(report_md, "mitigation")
    items = bullets(body)
    items += [ln.strip() for ln in body.split("\n") if _DO_NOT.match(ln.strip())]   # never a bullet line
    return items or _items_or_paragraph(body)


def _items_or_paragraph(body: str) -> list[str]:
    return bullets(body) or [p for p in [_first_paragraph(body)] if p]


def _evidence_links(view: RunView, report_md: str) -> list[dict[str, str]]:
    """Up to 10 bucket links: evidence in report-citation order, then the leader's support, then pinned."""
    order: list[str] = list(dict.fromkeys(re.findall(r"\bE\d+\b", report_md)))
    for h in sorted(view.hypotheses, key=lambda h: -h.confidence):
        order.extend(h.supporting)
    order.extend(r.id for r in view.records if r.pinned)
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for eid in dict.fromkeys(order):
        for e in view.links_for(eid):
            if e.url not in seen:
                seen.add(e.url)
                links.append({"label": f"{eid}: {e.label}", "url": e.url, "evidence_id": eid})
    if not links and view.alert is not None and view.alert.url:
        links.append({"label": "Alert", "url": view.alert.url, "evidence_id": ""})
    return links[:CARD_LINKS_MAX]


def _text(v: Any) -> str | None:
    if isinstance(v, dict):
        v = v.get("login") or v.get("name") or v.get("email")
    return str(v) if v not in (None, "") else None


def _utc(ts: str | None) -> str | None:
    """ISO-8601 timestamps as "YYYY-MM-DD HH:MM UTC" (every user-facing time is UTC, 7.1)."""
    if not ts:
        return ts
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return ts
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC") if dt.tzinfo else dt.strftime("%Y-%m-%d %H:%M UTC")


def _change_from_verdict(cc: SpecialistVerdict, view: RunView) -> ChangeRef:
    d = cc.details or {}
    src = next((d[k] for k in ("change", "commit", "pr", "trigger_change") if isinstance(d.get(k), dict)), d)
    allowed = {u.rstrip("/") for u in view.allowed_urls}
    url = _text(src.get("url") or src.get("html_url") or src.get("link"))
    if url and url.rstrip("/") not in allowed:
        url = None
    if url is None:                                   # a change link from the verdict's own evidence
        url = next((e.url for eid in cc.evidence_ids for e in view.links_for(eid) if _CHANGE_URL.search(e.url)),
                   None)
    version = src.get("version") or src.get("sha") or src.get("revision")
    if version is None and isinstance(src.get("commit"), str):
        version = src.get("commit")
    return ChangeRef(
        title=str(src.get("title") or src.get("message") or src.get("summary") or cc.headline)[:200],
        ts=_utc(_text(src.get("ts") or src.get("time") or src.get("timestamp") or src.get("merged_at")
                      or src.get("date"))),
        url=url, author=_text(src.get("author") or src.get("user")), version=_text(version))


def _change_refuted(cc: SpecialistVerdict, view: RunView) -> bool:
    """True when every hypothesis that cites the code-change verdict (or its evidence) was refuted: a
    disproved theory must not stay the triggering change on the card (3.15)."""
    ids = set(cc.evidence_ids) | {r.id for r in view.records if r.kind == "specialist" and r.agent == cc.agent}
    citing = [h for h in view.hypotheses if ids & set(h.supporting)]
    return bool(citing) and all(h.status == "refuted" for h in citing)


def _impact_from_verdict(ci: SpecialistVerdict, card: ConclusionCard, view: RunView) -> ImpactSummary:
    d = ci.details or {}
    level = str(d.get("level") or d.get("impact_level") or d.get("severity") or "").lower()
    if level not in _IMPACT_LEVELS:
        level = "none" if ci.verdict == "not_found" else card.impact.level
    affected = d.get("affected") or d.get("affected_workloads") or card.impact.affected
    summary = ci.headline
    scoped = d.get("hypothesis_id") or d.get("scoped_to")
    if scoped and any(h.id == scoped and h.status == "refuted" for h in view.hypotheses):
        summary += f" (note: this assessment was scoped to {scoped}, which deepening later refuted)"
    return ImpactSummary(level=level, summary=summary,  # type: ignore[arg-type]
                         affected=[str(a) for a in affected][:20] if isinstance(affected, list) else [],
                         evidence_ids=[i for i in ci.evidence_ids if i in view.ids])


def apply_overrides(card: ConclusionCard, ctx: RunContext, report_md: str, *,
                    view: RunView | None = None) -> ConclusionCard:
    """Deterministic fields win over the formatter's copy (3.15). ``card.status`` is left to the caller."""
    view = view or run_view(ctx)
    lead = view.leader
    card.run_id = ctx.run_id
    card.title = (card.title or "").strip() or _title(ctx)
    card.confidence = round(lead.confidence, 2) if lead else 0.0
    card.causal_level = lead.causal_level if lead else "symptom"
    conf = card.confidence
    card.verdict = _clean(card.verdict, view, conf) or _default_verdict(view)
    card.root_cause = _clean(card.root_cause, view, conf) if language.shown(conf) else ""
    card.hypotheses = [
        {"id": h.id, "statement": h.statement, "confidence": round(h.confidence, 2), "status": h.status,
         "causal_level": h.causal_level, "evidence_ids": [e for e in h.supporting if e in view.ids]}
        for h in sorted(view.hypotheses, key=lambda h: -h.confidence) if h.status != "refuted"
    ][:CARD_HYPOTHESES_MAX]
    card.evidence_links = _evidence_links(view, report_md)
    ci = view.verdict("customer_impact")
    if ci is not None and ci.verdict in {"found", "not_found"}:   # an inconclusive verdict never overwrites
        card.impact = _impact_from_verdict(ci, card, view)        # the report's measured impact
    else:
        card.impact.summary = _clean(card.impact.summary, view, conf)
        card.impact.evidence_ids = [i for i in card.impact.evidence_ids if i in view.ids]
    cc = view.verdict("code_change")
    causal = get_section(report_md, "causal_change")
    # The report is authoritative (3.15): when it names no causal change, neither does the card, even if
    # the code-change specialist found a candidate (deepening may have refuted it after the verdict).
    no_change = bool(report_md.strip()) and (not causal.strip() or bool(_NO_CHANGE.match(causal)))
    if no_change or (cc is not None and cc.verdict == "found" and _change_refuted(cc, view)):
        card.trigger_change = None
    elif cc is not None and cc.verdict == "found":
        card.trigger_change = _change_from_verdict(cc, view)
    elif card.trigger_change is not None and card.trigger_change.url and \
            card.trigger_change.url.rstrip("/") not in {u.rstrip("/") for u in view.allowed_urls}:
        card.trigger_change.url = None
    mitigation = [m for m in card.mitigation if m.strip()] or report_mitigation(report_md)
    card.mitigation = [c for c in (_clean(m, view, conf) for m in mitigation) if c][:CARD_MITIGATION_MAX]
    gaps = [g for g in card.gaps if g.strip()] or _items_or_paragraph(get_section(report_md, "gaps"))
    card.gaps = [c for c in (_clean(g, view, conf) for g in gaps) if c][:CARD_GAPS_MAX]
    return card


def _report_for_formatter(report_md: str) -> str:
    if len(report_md) <= CARD_REPORT_CHARS:
        return report_md
    sections = {s.key: s for s in split_sections(report_md) if s.key}
    out, size = [], 0
    for key in _SECTION_PRIORITY:
        s = sections.get(key)
        if s is None:
            continue
        block = f"{s.heading}\n{s.body}"
        if size + len(block) > CARD_REPORT_CHARS:
            block = block[: max(0, CARD_REPORT_CHARS - size)] + "\n[... section clipped for the card formatter]"
        out.append(block)
        size += len(block)
        if size >= CARD_REPORT_CHARS:
            break
    return "\n\n".join(out)


def _id_order(eid: str) -> int:
    return int(eid[1:]) if eid[1:].isdigit() else 0


def formatter_input(ctx: RunContext, view: RunView, report_md: str) -> str:
    lead = view.leader
    if lead is not None:
        opener = language.lead_phrase(lead.confidence) or "do not present a cause as established"
        leader = (f"{lead.id} confidence {lead.confidence:.2f} ({language.confidence_word(lead.confidence)}), "
                  f"causal level {language.level_label(lead.causal_level)}; verdict wording: \"{opener}\"")
    else:
        leader = "none (no hypothesis recorded): the verdict must say the cause is not established"
    hyps = "\n".join(f"- {h.id} {h.confidence:.2f} {h.status} [{h.causal_level}]: {h.statement}"
                     for h in sorted(view.hypotheses, key=lambda h: -h.confidence)) or "- none"
    return "\n\n".join([
        f"## RUN\nrun_id: {ctx.run_id}\ntitle hint: {_title(ctx)}",
        (f"## FACTS (computed by code; the card must agree with them)\nleader: {leader}\nhypotheses:\n{hyps}\n"
         f"evidence ids that exist: {', '.join(sorted(view.ids, key=_id_order))}"),
        f"## FINAL REPORT (authoritative)\n{_report_for_formatter(report_md)}",
        "## TASK\nFill the conclusion card from the final report.",
    ])


def insufficient_data_card(ctx: RunContext, report_md: str, *, view: RunView | None = None) -> ConclusionCard:
    """The card for a run below the evidence floor: no cause, what a human should check."""
    view = view or run_view(ctx)
    checks = bullets(get_section(report_md, "human_checks"))
    return ConclusionCard(
        run_id=ctx.run_id, title=_title(ctx), status="insufficient_data", confidence=0.0, causal_level="symptom",
        verdict="Insufficient data: too little meaningful evidence to propose a cause. Nothing here is a guess.",
        gaps=checks[:CARD_GAPS_MAX], evidence_links=_evidence_links(view, report_md))


def fallback_card(ctx: RunContext, report_md: str, *, view: RunView | None = None) -> ConclusionCard:
    """A fully deterministic card from the report's sections and the tracker (3.8.4)."""
    view = view or run_view(ctx)
    tldr = get_section(report_md, "tldr") or get_section(report_md, "answer")
    card = ConclusionCard(
        title=_title(ctx), verdict=_first_sentence(tldr) or _default_verdict(view),
        root_cause=_first_paragraph(get_section(report_md, "root_cause")),
        impact=ImpactSummary(summary=_first_paragraph(get_section(report_md, "impact"))), status="final")
    return apply_overrides(card, ctx, report_md, view=view)


def _is_insufficient(ctx: RunContext, report_md: str) -> bool:
    return bool(ctx.extra.get("insufficient_data")) or report_md.lstrip().startswith(
        ":grey_question: *Insufficient data")


async def build_card(ctx: RunContext, report_md: str) -> ConclusionCard:
    """The final card: formatter (schema-bound, 3 attempts) from the finished report, then overrides;
    a deterministic card when the formatter fails."""
    view = run_view(ctx)
    if _is_insufficient(ctx, report_md):
        return insufficient_data_card(ctx, report_md, view=view)
    settings = ctx.deps.settings
    card: ConclusionCard | None = None
    try:
        card = await ctx.deps.llm.structured(
            role="formatter", system=load_prompt("card"), user=formatter_input(ctx, view, report_md),
            schema=ConclusionCard, max_output_tokens=FORMATTER_MAX_OUTPUT_TOKENS, attempts=3,
            timeout_s=max(30.0, min(180.0, ctx.remaining() + settings.watchdog_grace_s)))
    except Exception as e:  # noqa: BLE001 - a card always posts (3.8.4)
        log.error("[%s] card formatter raised %s: %s", ctx.run_id, type(e).__name__, redact(str(e))[:200])
    if card is None:
        ctx.warn("card formatter produced no valid card; deterministic card used")
        return fallback_card(ctx, report_md, view=view)
    card.status = "final"
    return apply_overrides(card, ctx, report_md, view=view)


def early_card(ctx: RunContext) -> ConclusionCard:
    """The preliminary card posted when the agents finish (no LLM call); the final card replaces it."""
    view = run_view(ctx)
    lead = view.leader
    shown = lead is not None and language.shown(lead.confidence, lead.status)
    verdict = ("Preliminary: " + language.calibrated_sentence(lead.statement, lead.confidence)) if shown and lead \
        else "Preliminary: evidence gathered; no hypothesis is established yet. The full report is being written."
    card = ConclusionCard(title=_title(ctx), verdict=verdict, root_cause=lead.statement if shown and lead else "",
                          status="preliminary")
    return apply_overrides(card, ctx, "", view=view)
