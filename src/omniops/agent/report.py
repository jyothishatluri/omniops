"""Report writer: verdict-first Slack mrkdwn report (investigation) or answer (chat), validated by code.

Retrospective lessons encoded here:
- 3.4: every claim cites an evidence id that exists; code strips unknown ids after generation, flags
  entity names that appear in no tool output (logged internally, never shown), and enforces the
  evidence floor with a templated "insufficient data" answer that lists what was tried.
- 3.5: links are data - the model gets a link bucket keyed by evidence id and must copy exact URLs;
  code strips any URL not in the bucket/alert/raw data and appends bucket links it did not place.
  Chat answers share only links backed by a data-bearing query.
- 3.7: calibrated wording by confidence, low-confidence banner below 0.4 with next steps moved above
  the hypotheses, no gap-filling from training data.
- 3.8.5: output budget sized per call (thinking counts against it), "continue from the cut-off"
  rounds when the reply stops on the cap, and a deterministic report when the writer fails: the
  user always gets something.
- 3.9.2: fixed slot order with the stable part first and evidence (pinned first) before hypotheses,
  specialist verdicts and the link bucket; budgets from character estimates; a fixed trim order
  (tool activity first, evidence last) that cuts whole lines, placeholders only when almost nothing
  would remain, and an error when evidence itself is cut; upstream caps (history 1,500 chars per
  item, link bucket 60 entries / 12,000 chars with each URL listed once).
- 3.12: static sectioned system prompts in ``prompts/``; dynamic content only in the user message.
- 4.4: Slack mrkdwn only - Markdown tables become code blocks, links are ``<url|label>``.
- 7.1: report section headings (own-line or inline-labelled) are parsed with aliases so the card
  never loses a section to a heading variant; a mechanism-level leader is labelled "trigger not
  found"; times are UTC.
- D11: staged writes are read from the run's engines and reported as awaiting approval, never as
  applied.
- 3.11: what stayed open (gaps code observed, gaps left at the continuation limit, unfollowed
  artifact leads) gets its own GAPS slot, so loud-zero warnings can never crowd it out; it is
  disclosed in the report, never offered as a next step.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from omniops.agent import language
from omniops.agent.evidence import NOT_MEASURED_KINDS
from omniops.agent.types import Alert, EvidenceRecord, Hypothesis, SpecialistVerdict
from omniops.slack.render import redact, slack_link, to_mrkdwn

if TYPE_CHECKING:
    from omniops.agent.context import RunContext

log = logging.getLogger("omniops.report")

Mode = Literal["investigation", "chat"]

PROMPT_DIR = Path(__file__).parent / "prompts"
REPORT_INPUT_TOKENS = 60_000          # 3.9.1
CHARS_PER_TOKEN = 3.5                 # conservative char estimate; never tokenise on the event loop
REPORT_MAX_OUTPUT_TOKENS = 64_000     # 3.8.5: reasoning shares the cap
CHAT_MAX_OUTPUT_TOKENS = 32_000
CONTINUATION_ROUNDS = 2
CHAT_EVIDENCE_FLOOR = 1               # a direct data question is answered by one data-bearing result
LINK_BUCKET_MAX_ENTRIES = 60
LINK_BUCKET_MAX_CHARS = 12_000
HISTORY_ITEM_CHARS = 1_500
HISTORY_ITEMS = 20
EVIDENCE_SUMMARY_CHARS = 3_000
EVIDENCE_RENDERED_CHARS = 2_000
TOOL_EVENTS_SHOWN = 80
CARD_LINKS_MAX = 10
TIE_GAP = 0.08                        # top-two families closer than this are a deferred decision (3.6.3)

SLOT_ORDER = ("request", "window", "context", "history", "evidence", "hypotheses", "specialists", "gaps",
              "link_bucket", "tool_activity")
TRIM_ORDER = ("tool_activity", "history", "context", "link_bucket", "gaps", "hypotheses", "specialists", "evidence")
GAP_PREFIX = "gap: "                  # run warnings the orchestrator raises for what stayed open
GAPS_SHOWN = 30
REQUIRED_SECTIONS = ("tldr", "root_cause", "evidence", "impact", "timeline", "mitigation", "next_steps", "gaps")
_TRUNCATED = {"max_output_tokens", "length", "max_tokens", "incomplete"}
_CONTINUE = ("Your previous reply was cut off by the output limit. Continue exactly from the last character "
             "you wrote: no repetition, no preamble, same format.")

_EID = re.compile(r"\bE\d+\b")
_EID_GROUP = re.compile(r" ?[\[(]\s*E\d+(?:\s*(?:,|;|/|and|&)\s*E\d+)*\s*[\])]")
_CODE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)
_FENCED = re.compile(r"```.*?```", re.DOTALL)
_SLACK_URL = re.compile(r"<(https?://[^|<>\s]+)(?:\|([^<>]*))?>")
_BARE_URL = re.compile(r"https?://[^\s<>|`\"']+")
_LOCAL_TZ = re.compile(r"\b(CST|CDT|PST|PDT|EST|EDT|MST|MDT|IST|BST|CET|CEST)\b")
_ENTITY = re.compile(r"`([^`\s]{3,80})`|\b([a-z][a-z0-9]*(?:[-_][a-z0-9]+)+|[a-z0-9-]+(?:\.[a-z0-9-]+){2,})\b")
_ENTITY_SKIP = re.compile(r"^(E\d+|H\d+(\.\d+)*|v?\d[\d.]*|\d{4}-\d{2}-\d{2}.*|[\d.:%-]+)$", re.IGNORECASE)
_ENGLISH_HYPHENATED = {
    "follow-up", "follow-ups", "read-only", "well-known", "re-run", "re-check", "end-to-end", "top-level",
    "built-in", "real-time", "long-running", "short-lived", "high-level", "low-level", "in-place", "on-call",
    "self-executable", "out-of-memory", "per-version", "day-over-day", "week-over-week", "non-zero", "time-series",
    "human-only", "low-confidence", "same-day", "up-to-date", "trigger-level", "mechanism-level", "x-axis",
    "y-axis", "client-side", "server-side", "pre-incident", "post-incident", "error-rate", "co-tenant",
}

_SECTION_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tldr", ("tl;dr", "tldr", "summary", "verdict", "bottom line")),
    ("answer", ("answer", "direct answer", "short answer")),
    ("causal_change", ("causal change", "triggering change", "trigger change", "introducing change",
                       "causal commit", "change that caused")),
    ("root_cause", ("root cause", "probable cause", "likely cause", "cause", "diagnosis")),
    ("evidence", ("evidence", "key evidence", "supporting evidence", "findings")),
    ("impact", ("impact", "blast radius", "customer impact", "scope")),
    ("timeline", ("timeline", "sequence of events", "chronology")),
    ("mitigation", ("mitigation", "remediation", "recommended fix", "fix", "recommended actions", "resolution",
                    "immediate actions", "containment")),
    ("next_steps", ("next steps", "follow-ups", "follow ups", "human next steps", "action items")),
    ("gaps", ("gaps", "limits", "limitations", "caveats", "open questions")),
    ("links", ("links", "more links", "references")),
    ("hypotheses", ("hypotheses", "competing hypotheses", "other hypotheses", "alternatives considered")),
    ("tried", ("what was tried",)),
    ("human_checks", ("what a human should check",)),
)
_HEADING = re.compile(r"^\s*(?::[a-z0-9_+-]+:\s*)?\*([^*\n]{1,80})\*\s*:?\s*$")
# "*Mitigation:* text" / "*Mitigation*: text" - a labelled heading with its first line inline (7.1)
_INLINE_HEADING = re.compile(r"^\s*(?::[a-z0-9_+-]+:\s*)?\*([^*\n]{1,80}?)(?::\*|\*:)\s+(\S.*)$")


@cache
def load_prompt(name: str) -> str:
    """A static sectioned system prompt from ``agent/prompts/<name>.md`` (byte-identical per role)."""
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------------
# A read-only view of the run: evidence board, tracker and specialist verdicts
# ---------------------------------------------------------------------------------------------


@dataclass
class LinkEntry:
    url: str
    label: str
    evidence_ids: list[str]

    def line(self) -> str:
        return f"{', '.join(self.evidence_ids)}: {self.label} -> {self.url}"


@dataclass
class RunView:
    run_id: str
    mode: Mode
    records: list[EvidenceRecord]
    hypotheses: list[Hypothesis]
    leader: Hypothesis | None
    verdicts: list[SpecialistVerdict]
    bucket: list[LinkEntry]
    allowed_urls: set[str]
    alert: Alert | None
    question: str | None

    @property
    def ids(self) -> set[str]:
        return {r.id for r in self.records}

    @property
    def top_confidence(self) -> float | None:
        return self.leader.confidence if self.leader else None

    def record(self, eid: str) -> EvidenceRecord | None:
        return next((r for r in self.records if r.id == eid), None)

    def verdict(self, agent: str) -> SpecialistVerdict | None:
        return next((v for v in self.verdicts if v.agent == agent), None)

    def links_for(self, eid: str) -> list[LinkEntry]:
        return [e for e in self.bucket if eid in e.evidence_ids]


def _items(obj: Any, names: tuple[str, ...]) -> list[Any] | None:
    for name in names:
        v = getattr(obj, name, None)
        if v is None:
            continue
        if callable(v):
            try:
                v = v()
            except TypeError:
                continue
        if isinstance(v, dict):
            return list(v.values())
        if isinstance(v, list | tuple):
            return list(v)
    return None


def board_records(board: Any) -> list[EvidenceRecord]:
    """Citable records on the evidence board, in board order (shadow-agent S-ids excluded via
    ``visible()``; falls back to ``records``/``all()``/iteration for simpler boards)."""
    items = _items(board, ("visible", "records", "all", "items"))
    if items is None:
        try:
            items = list(board)
        except TypeError:
            items = []
    return [r for r in items if hasattr(r, "id") and hasattr(r, "summary")]


def tracker_hypotheses(tracker: Any) -> list[Hypothesis]:
    out: list[Hypothesis] = []
    for h in _items(tracker, ("hypotheses", "all", "ranked")) or []:
        if isinstance(h, Hypothesis):
            out.append(h)
        elif isinstance(h, dict):
            try:
                out.append(Hypothesis.model_validate(h))
            except ValueError:
                log.warning("unreadable hypothesis on the tracker: %s", str(h)[:200])
    return out


def tracker_leader(tracker: Any, hyps: list[Hypothesis]) -> Hypothesis | None:
    """The tracker's leader if it exposes one, else the most confident hypothesis not refuted."""
    v = getattr(tracker, "leader", None)
    if callable(v):
        try:
            v = v()
        except TypeError:
            v = None
    if isinstance(v, str):
        v = next((h for h in hyps if h.id == v), None)
    elif isinstance(v, dict):
        v = next((h for h in hyps if h.id == v.get("id")), None)
    if isinstance(v, Hypothesis) and v.status != "refuted":
        return v
    live = [h for h in hyps if h.status != "refuted"]
    return max(live, key=lambda h: h.confidence, default=None)


def _coerce_verdict(v: Any) -> SpecialistVerdict | None:
    if isinstance(v, SpecialistVerdict):
        return v
    if isinstance(v, dict) and v.get("agent") and "headline" in v:
        try:
            return SpecialistVerdict.model_validate(v)
        except ValueError:
            return None
    return None


def specialist_verdicts(ctx: RunContext, records: list[EvidenceRecord]) -> list[SpecialistVerdict]:
    """Verdicts from ``ctx.specialist_verdicts`` plus any published only as specialist evidence."""
    out: dict[str, SpecialistVerdict] = {}
    for v in getattr(ctx, "specialist_verdicts", None) or []:
        sv = _coerce_verdict(v)
        if sv:
            out[sv.agent] = sv
    for r in records:
        if r.kind != "specialist":
            continue
        payload = r.data.get("verdict") if isinstance(r.data.get("verdict"), dict) else r.data
        sv = _coerce_verdict(payload)
        if sv and sv.agent not in out:
            if not sv.evidence_ids:
                sv.evidence_ids = [r.id]
            out[sv.agent] = sv
    return list(out.values())


def _data_bearing(r: EvidenceRecord) -> bool:
    return not r.error and r.meaningful and (r.row_count > 0 or r.kind in {"alert", "specialist"})


def _norm_url(u: str) -> str:
    return u.replace("&amp;", "&").strip().rstrip(".,;:!?)]}'\"").rstrip("/")


def link_bucket(records: list[EvidenceRecord], *, data_bearing_only: bool = False) -> list[LinkEntry]:
    """Deterministic links keyed by evidence id, each URL once (pinned records first), capped.

    Failed queries contribute no links; chat answers keep only links of data-bearing results (3.5).
    """
    by_url: dict[str, LinkEntry] = {}
    for r in sorted(records, key=lambda r: not r.pinned):
        if r.error or (data_bearing_only and not _data_bearing(r)):
            continue
        for lk in r.links or []:
            url = str(lk.get("url") or lk.get("href") or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            entry = by_url.get(url)
            if entry:
                if r.id not in entry.evidence_ids:
                    entry.evidence_ids.append(r.id)
            else:
                label = str(lk.get("label") or lk.get("title") or lk.get("name") or r.title or url)
                by_url[url] = LinkEntry(url, " ".join(label.split())[:120], [r.id])
    out: list[LinkEntry] = []
    size = 0
    for e in by_url.values():
        size += len(e.line()) + 1
        if len(out) >= LINK_BUCKET_MAX_ENTRIES or size > LINK_BUCKET_MAX_CHARS:
            log.warning("link bucket capped at %d entries (%d available)", len(out), len(by_url))
            break
        out.append(e)
    return out


def run_view(ctx: RunContext, mode: Mode = "investigation") -> RunView:
    records = board_records(ctx.board)
    hyps = tracker_hypotheses(ctx.tracker)
    chat = mode == "chat"
    bucket = link_bucket(records, data_bearing_only=chat)
    allowed = {e.url for e in bucket}
    if ctx.alert is not None:
        allowed.update(u for u in [ctx.alert.url, *ctx.alert.links] if u)
    for r in records:                       # URLs in raw responses are data too (3.5)
        if chat and not _data_bearing(r):
            continue
        for text in (r.summary, r.rendered):
            allowed.update(_norm_url(u) for u in _BARE_URL.findall(text or ""))
    return RunView(run_id=ctx.run_id, mode=mode, records=records, hypotheses=hyps,
                   leader=tracker_leader(ctx.tracker, hyps), verdicts=specialist_verdicts(ctx, records),
                   bucket=bucket, allowed_urls=allowed, alert=ctx.alert, question=ctx.question)


def _is_meaningful(r: EvidenceRecord) -> bool:
    return r.meaningful and not r.error and r.confidence >= 0.3 and r.kind != "lead"


def _is_measured(r: EvidenceRecord) -> bool:
    """Counts toward the evidence floor (3.4): measured items, not the alert, memory, leads or verdicts."""
    return _is_meaningful(r) and r.kind not in NOT_MEASURED_KINDS


def meaningful_count(ctx: RunContext, view: RunView | None = None) -> int:
    """The board's own count when it has one, else measured records that are not empty/errors/discovery-only."""
    fn = getattr(ctx.board, "meaningful_count", None)
    if callable(fn):
        try:
            return int(fn())
        except (TypeError, ValueError):
            pass
    records = view.records if view else board_records(ctx.board)
    return sum(1 for r in records if _is_measured(r))


# ---------------------------------------------------------------------------------------------
# Report request: fixed slot order, char-estimated budget, fixed trim order
# ---------------------------------------------------------------------------------------------


@dataclass
class Slot:
    name: str
    title: str
    body: str


def _clip_lines(text: str, limit: int) -> str:
    """Cut back to a whole line and say how much was dropped (every truncation is explicit)."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    nl = cut.rfind("\n")
    cut = cut[:nl] if nl > limit // 3 else cut
    return f"{cut}\n[... {len(text) - len(cut)} more chars not shown of {len(text)}]"


def _fmt_dt(v: Any) -> str:
    return v.strftime("%Y-%m-%d %H:%M:%S UTC") if hasattr(v, "strftime") else str(v)


def _effective_confidence(view: RunView, mode: Mode) -> float | None:
    """The leader's confidence; 0.0 for an investigation without hypotheses; None for a plain question."""
    conf = view.top_confidence
    return 0.0 if conf is None and mode == "investigation" else conf


def _calibration_line(view: RunView, mode: Mode) -> str:
    lead = view.leader
    if lead is None:
        if mode == "chat":
            return "calibration: no hypotheses recorded (plain data question): answer with the measured values."
        return ("calibration: no hypothesis was recorded -> do not name a root cause; say the cause is not "
                "established.")
    opener = language.lead_phrase(lead.confidence)
    rule = f'required opener: "{opener}"' if opener else "below 0.3: do not present any cause as established"
    return (f"calibration: leader {lead.id} confidence {lead.confidence:.2f} ({language.confidence_word(lead.confidence)}), "
            f"causal level {language.level_label(lead.causal_level)} -> {rule}")


def _slot_request(ctx: RunContext, view: RunView, mode: Mode) -> str:
    lines = [f"mode: {mode}", f"run_id: {ctx.run_id}"]
    if view.question:
        lines.append(f"question: {view.question}")
    a = view.alert
    if a is not None:
        fields = {"platform": a.platform, "rule_id": a.rule_id, "rule": a.rule_name, "title": a.title,
                  "status": a.status, "severity": a.severity, "service": a.service, "env": a.env,
                  "namespace": a.namespace, "host": a.host, "group": a.group, "query": a.query,
                  "threshold": a.threshold, "value": a.value,
                  "triggered_at": _fmt_dt(a.triggered_at) if a.triggered_at else None}
        lines.append("alert: " + "; ".join(f"{k}={v}" for k, v in fields.items() if v))
        if a.customer_report:
            lines.append("alert kind: a human/customer report, not a monitor")
    lines.append(_calibration_line(view, mode))
    banner = language.needs_banner(_effective_confidence(view, mode))
    lines.append(f"low-confidence banner: {'yes (code adds it; do not write one)' if banner else 'no'}")
    lines.append(f"meaningful evidence items: {meaningful_count(ctx, view)}")
    return "\n".join(lines)


def _slot_window(ctx: RunContext) -> str:
    anchor = ctx.anchor
    lines = [anchor.describe()] if hasattr(anchor, "describe") else []
    lines.extend(f"note: {n}" for n in getattr(anchor, "notes", []) or [])
    return "\n".join(lines) + "\nAll times in the report are UTC."


def _slot_context(ctx: RunContext, view: RunView) -> str:
    lines: list[str] = []
    a = view.alert
    if a is not None and a.text.strip():
        lines.append("alert body (untrusted text):\n" + _clip_lines(a.text, 3000))
    if a is not None and (a.url or a.links):
        lines.append("alert links: " + " ; ".join(u for u in [a.url, *a.links] if u))
    warnings = [w for w in ctx.warnings if not w.startswith(GAP_PREFIX)]    # gaps have their own slot
    if warnings:
        lines.append("run warnings (observed by code):\n" + "\n".join(f"- {w}" for w in warnings[-20:]))
    staged = staged_writes(ctx)
    if staged:
        lines.append("staged writes (approval happens in Slack; say applied only if executed=yes):\n" + "\n".join(
            f"- write {w['id']} on {w['table']}: {redact(w['summary'])[:300]} (executed={w['executed']})"
            for w in staged))
    return "\n\n".join(lines)


def staged_writes(ctx: RunContext) -> list[dict[str, Any]]:
    """Writes staged by any of the run's engines (D11), read from the engines rather than tool text."""
    engines = getattr(ctx, "_engines", None) or [ctx.engine]
    out: dict[str, dict[str, Any]] = {}
    for eng in engines:
        for w in (getattr(eng, "staged_writes", None) or {}).values():
            out[w.id] = {"id": w.id, "table": w.table, "summary": w.summary,
                         "executed": "yes" if getattr(w, "claimed", False) else "no"}
    return list(out.values())


def _slot_history(ctx: RunContext) -> str:
    raw = (ctx.thread_context or "").strip()
    if not raw:
        return ""
    items = [i for i in re.split(r"\n\s*\n|\n(?=\S+:)", raw) if i.strip()][-HISTORY_ITEMS:]
    return "\n".join(_clip_lines(i, HISTORY_ITEM_CHARS) for i in items) + \
        "\n(reference only: prior messages are not evidence unless this run's data corroborates them)"


def _record_block(r: EvidenceRecord) -> str:
    flags = [r.kind, r.agent]
    if r.pinned:
        flags.insert(0, "pinned")
    if r.error:
        flags.append("ERROR")
    elif r.empty_verdict:
        flags.append(f"EMPTY ({r.empty_verdict})")
    elif not r.meaningful:
        flags.append("not meaningful")
    head = f"{r.id} [{', '.join(flags)}] {r.title}"
    meta = [f"rows={r.row_count}", f"measurement confidence={r.confidence:.1f}"]
    if r.window:
        meta.insert(0, f"window={r.window[0]} -> {r.window[1]}")
    if r.tables:
        meta.append("tables=" + ",".join(r.tables[:6]))
    parts = [head, "  " + "; ".join(meta)]
    if r.error:
        parts.append("  error: " + redact(r.error[:400]))
    if r.summary.strip():
        parts.append(_clip_lines(r.summary, EVIDENCE_SUMMARY_CHARS))
    if r.rendered.strip() and r.row_count and r.rendered.strip() not in r.summary:
        parts.append("data:\n" + _clip_lines(r.rendered, EVIDENCE_RENDERED_CHARS))
    return "\n".join(parts)


def _slot_evidence(view: RunView) -> str:
    ordered = sorted(view.records, key=lambda r: not r.pinned)
    return "\n\n".join(_record_block(r) for r in ordered)


def _known(ids: list[str], known: set[str]) -> str:
    """Only citable ids reach the writer (dangling ones would be stripped from its output anyway)."""
    return ", ".join(i for i in ids if i in known) or "-"


def _hyp_line(h: Hypothesis, depth: int, known: set[str]) -> str:
    if h.status == "refuted":
        wording = "refuted -> omit from the report"
    else:
        wording = f'wording "{language.lead_phrase(h.confidence)}"' if language.shown(h.confidence) else \
            "below 0.3 -> do not show"
    pad = "  " * depth
    lines = [(f"{pad}{h.id} [{h.causal_level} | {h.category} | {h.status} | {h.confidence:.2f} -> {wording}] "
              f"{h.statement}"),
             f"{pad}  supporting: {_known(h.supporting, known)}; contradicting: {_known(h.contradicting, known)}"]
    if h.missing:
        lines.append(f"{pad}  missing: {'; '.join(h.missing[:4])}")
    if h.verdict_notes:
        lines.append(f"{pad}  verdicts: {' | '.join(n[:300] for n in h.verdict_notes[-3:])}")
    return "\n".join(lines)


def _slot_hypotheses(view: RunView) -> str:
    hyps = sorted(view.hypotheses, key=lambda h: -h.confidence)
    if not hyps:
        return "(none recorded)"
    ids = {h.id for h in hyps}
    children: dict[str, list[Hypothesis]] = {}
    for h in hyps:
        if h.parent and h.parent in ids:
            children.setdefault(h.parent, []).append(h)
    lines: list[str] = []

    def walk(h: Hypothesis, depth: int) -> None:
        lines.append(_hyp_line(h, depth, view.ids))
        for c in children.get(h.id, []):
            walk(c, depth + 1)

    roots = [h for h in hyps if not (h.parent and h.parent in ids)]
    for h in roots:
        walk(h, 0)

    def family(h: Hypothesis) -> list[Hypothesis]:
        return [h] + [d for c in children.get(h.id, []) for d in family(c)]

    def family_best(root: Hypothesis) -> Hypothesis:
        return max((h for h in family(root) if h.status != "refuted"), key=lambda x: x.confidence)

    fams = sorted((family_best(r) for r in roots if r.status != "refuted"), key=lambda x: -x.confidence)
    if len(fams) >= 2 and fams[0].confidence - fams[1].confidence < TIE_GAP:   # competing families (3.6.3)
        lines.append(f"TIE: {fams[0].id} and {fams[1].id} are within {TIE_GAP} -> present both as competing "
                     "explanations")
    return "\n".join(lines)


def _slot_specialists(view: RunView) -> str:
    out = []
    for v in view.verdicts:
        line = f"{v.agent}: {v.verdict} ({v.confidence:.2f}) {v.headline}"
        if any(i in view.ids for i in v.evidence_ids):
            line += f" [{_known(v.evidence_ids, view.ids)}]"
        if v.details:
            line += "\n  details: " + _clip_lines(json.dumps(v.details, default=str), 1200)
        out.append(line)
    return "\n".join(out)


def open_gaps(ctx: RunContext) -> list[str]:
    """What stayed open, de-duplicated: gap warnings, gaps left at the continuation limit, unfollowed leads."""
    lines = [w[len(GAP_PREFIX):] for w in ctx.warnings if w.startswith(GAP_PREFIX)]
    lines += [f"left open at the continuation limit: {g}" for g in ctx.extra.get("open_gaps") or []]
    try:
        from omniops.agent.leads import sync_leads

        lines += sync_leads(ctx).gaps()
    except Exception as exc:  # noqa: BLE001 - a lead scan must never block the report
        log.warning("[%s] lead gaps unavailable: %s", ctx.run_id, exc)
    return list(dict.fromkeys(" ".join(x.split()) for x in lines if x.strip()))


def _slot_gaps(ctx: RunContext) -> str:
    gaps = open_gaps(ctx)
    shown = [f"- {g}" for g in gaps[:GAPS_SHOWN]]
    if len(gaps) > GAPS_SHOWN:
        shown.append(f"[{len(gaps) - GAPS_SHOWN} more gap(s) not shown]")
    return "\n".join(shown)


def _slot_links(view: RunView) -> str:
    return "\n".join(e.line() for e in view.bucket)


def _slot_tools(ctx: RunContext) -> str:
    events = ctx.tool_events
    if not events:
        return "(no tool calls recorded)"
    counts: dict[str, list[int]] = {}
    for e in events:
        c = counts.setdefault(f"{e.agent}.{e.name}", [0, 0])
        c[0] += 1
        c[1] += int(e.is_error)
    lines = ["calls: " + ", ".join(f"{k} x{n}" + (f" ({err} errors)" if err else "")
                                   for k, (n, err) in counts.items())]
    for e in events[-TOOL_EVENTS_SHOWN:]:
        args = redact(json.dumps(e.arguments, default=str))[:200]
        res = f"-> {e.evidence_id}" if e.evidence_id else ""
        lines.append(f"- {e.agent}.{e.name}({args}) {res}{' ERROR' if e.is_error else ''}".rstrip())
    return "\n".join(lines)


def _assemble(slots: list[Slot]) -> str:
    return "\n\n".join(f"## {s.title}\n{s.body}" for s in slots if s.body.strip())


def fit_slots(slots: list[Slot], budget_chars: int, ctx: RunContext | None = None) -> list[str]:
    """Trim slots in ``TRIM_ORDER`` until the request fits; returns the names trimmed."""
    trimmed: list[str] = []
    total = len(_assemble(slots))
    for name in TRIM_ORDER:
        if total <= budget_chars:
            break
        slot = next((s for s in slots if s.name == name and s.body.strip()), None)
        if slot is None:
            continue
        orig = len(slot.body)
        keep = orig - (total - budget_chars) - 120
        if keep < max(200, orig // 10):
            slot.body = f"[{slot.title} omitted: {orig} chars did not fit the report budget]"
        else:
            cut = slot.body[:keep]
            nl = cut.rfind("\n")
            cut = cut[:nl] if nl > 0 else cut
            slot.body = f"{cut}\n[... trimmed {orig - len(cut)} of {orig} chars to fit the report budget]"
        trimmed.append(name)
        total = len(_assemble(slots))
        if name == "evidence":
            log.error("report input over budget: evidence trimmed (%d chars)", orig)
            if ctx is not None:
                ctx.warn("report input over budget: evidence was trimmed")
    return trimmed


def build_report_input(ctx: RunContext, *, mode: Mode = "investigation", budget_chars: int | None = None,
                       view: RunView | None = None) -> tuple[str, list[str]]:
    """The report's user message in fixed slot order, and the slots trimmed to fit the budget."""
    view = view or run_view(ctx, mode)
    titles = {"request": "REQUEST", "window": "TIME WINDOW (UTC)", "context": "CONTEXT",
              "history": "THREAD HISTORY", "evidence": "EVIDENCE (pinned first)",
              "hypotheses": "HYPOTHESES (tree, with verdicts)", "specialists": "SPECIALIST VERDICTS",
              "gaps": "GAPS (what stayed open; disclose under Gaps & limits)",
              "link_bucket": "LINK BUCKET (evidence ids: label -> exact URL)",
              "tool_activity": "TOOL ACTIVITY (what was tried)"}
    hypotheses = _slot_hypotheses(view)
    deepening = ctx.extra.get("deepening")
    if isinstance(deepening, str) and deepening.strip():
        hypotheses += "\n\n" + deepening.strip()
    bodies = {"request": _slot_request(ctx, view, mode), "window": _slot_window(ctx),
              "context": _slot_context(ctx, view), "history": _slot_history(ctx),
              "evidence": _slot_evidence(view), "hypotheses": hypotheses,
              "specialists": _slot_specialists(view), "gaps": _slot_gaps(ctx), "link_bucket": _slot_links(view),
              "tool_activity": _slot_tools(ctx)}
    slots = [Slot(n, titles[n], bodies[n]) for n in SLOT_ORDER]
    budget = budget_chars or int(REPORT_INPUT_TOKENS * CHARS_PER_TOKEN)
    trimmed = fit_slots(slots, budget, ctx)
    task = "Write the incident report now." if mode == "investigation" else "Answer the question now."
    return _assemble(slots) + f"\n\n## TASK\n{task}", trimmed


# ---------------------------------------------------------------------------------------------
# Generation with continuation rounds
# ---------------------------------------------------------------------------------------------


def _splice(head: str, tail: str) -> str:
    """Join a continuation, trimming any overlap the model repeated."""
    for k in range(min(400, len(head), len(tail)), 19, -1):
        if head.endswith(tail[:k]):
            return head + tail[k:]
    return head + tail


async def _generate(ctx: RunContext, *, role: str, system: str, user: str, max_tokens: int,
                    timeout_s: float | None = None) -> str:
    llm = ctx.deps.llm
    settings = ctx.deps.settings
    timeout = timeout_s or max(30.0, min(settings.report_call_s, ctx.remaining() + settings.watchdog_grace_s))
    resp = await llm.complete(role=role, system=system, input=user,  # type: ignore[arg-type]
                              max_output_tokens=max_tokens, timeout_s=timeout)
    text = resp.text or ""
    for n in range(CONTINUATION_ROUNDS):
        if resp.stop_reason not in _TRUNCATED:
            break
        log.warning("[%s] %s reply stopped on the output cap (%d chars); continuation round %d",
                    ctx.run_id, role, len(text), n + 1)
        msgs = [{"role": "user", "content": user}, {"role": "assistant", "content": text},
                {"role": "user", "content": _CONTINUE}]
        resp = await llm.complete(role=role, system=system, input=msgs,  # type: ignore[arg-type]
                                  max_output_tokens=max_tokens, timeout_s=timeout)
        text = _splice(text, resp.text or "")
    if resp.stop_reason in _TRUNCATED:
        ctx.warn(f"{role} reply still truncated after {CONTINUATION_ROUNDS} continuation rounds")
    return text


# ---------------------------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------------------------


@dataclass
class Section:
    heading: str          # the raw heading line ("" for the preamble)
    key: str              # canonical key ("" for the preamble)
    lines: list[str] = field(default_factory=list)
    inline: bool = False  # the heading shared its line with the first body line

    @property
    def body(self) -> str:
        return "\n".join(self.lines).strip()


def section_key(heading: str) -> str | None:
    """Canonical key for a heading such as "*Impact / blast radius*" or "Remediation"; None if unknown."""
    h = re.sub(r":[a-z0-9_+-]+:", "", heading.lower()).strip(" *_:#\t")
    for key, aliases in _SECTION_ALIASES:
        if any(h.startswith(a) for a in aliases):
            return key
    return None


def split_sections(text: str) -> list[Section]:
    """Lossless split on recognised headings, including inline-labelled ones ("*Fix:* do X");
    unknown bold lines stay inside the current section."""
    sections = [Section("", "")]
    in_code = False
    for line in text.split("\n"):
        if line.strip().startswith("```") and not (line.strip().endswith("```") and len(line.strip()) > 6):
            in_code = not in_code
            sections[-1].lines.append(line)
            continue
        m = None if in_code else (_HEADING.match(line) or re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line))
        key = section_key(m.group(1)) if m else None
        if key:
            sections.append(Section(line, key))
            continue
        inline = None if in_code else _INLINE_HEADING.match(line)
        key = section_key(inline.group(1)) if inline else None
        if inline and key:
            head = line[: inline.start(2)].rstrip()
            sections.append(Section(head, key, [inline.group(2)], inline=True))
        else:
            sections[-1].lines.append(line)
    return sections


def join_sections(sections: list[Section]) -> str:
    out: list[str] = []
    for s in sections:
        if s.inline and s.lines:
            out.append(f"{s.heading} {s.lines[0]}")
            out.extend(s.lines[1:])
            continue
        if s.heading:
            out.append(s.heading)
        out.extend(s.lines)
    return "\n".join(out)


def get_section(text: str, key: str) -> str:
    return "\n\n".join(s.body for s in split_sections(text) if s.key == key and s.body)


def bullets(body: str) -> list[str]:
    """Top-level bullet/numbered items of a section body (continuation lines folded in)."""
    items: list[str] = []
    for line in body.split("\n"):
        m = re.match(r"^\s{0,2}(?:[•\-*+]|\d+[.)])\s+(.*)$", line)
        if m:
            items.append(m.group(1).strip())
        elif items and line.startswith((" ", "\t")) and line.strip():
            items[-1] += " " + line.strip()
    return [i for i in items if i]


# ---------------------------------------------------------------------------------------------
# Post-validation (code backs up the prompt)
# ---------------------------------------------------------------------------------------------


@dataclass
class ReportValidation:
    stripped_ids: list[str] = field(default_factory=list)
    stripped_urls: list[str] = field(default_factory=list)
    appended_links: list[str] = field(default_factory=list)
    grounding_warnings: list[str] = field(default_factory=list)
    calibration_changes: list[str] = field(default_factory=list)
    missing_sections: list[str] = field(default_factory=list)
    banner: bool = False


def _map_non_code(text: str, fn: Any) -> str:
    out, pos = [], 0
    for m in _CODE.finditer(text):
        out.append(fn(text[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(fn(text[pos:]))
    return "".join(out)


def _tidy(seg: str) -> str:
    seg = re.sub(r"[ \t]+([,.;])(?=\s|$)", r"\1", seg)
    seg = re.sub(r",\s*,", ",", seg)
    seg = re.sub(r"\(\s*\)|\[\s*\]", "", seg)
    return re.sub(r"(?<=\S)[ \t]{2,}", " ", seg)


def strip_unknown_ids(text: str, known: set[str]) -> tuple[str, list[str]]:
    """Remove citations of evidence ids that do not exist (dashes keep code-block columns aligned)."""
    removed: list[str] = []

    def group(m: re.Match[str]) -> str:
        ids = _EID.findall(m.group(0))
        keep = [i for i in ids if i in known]
        removed.extend(i for i in ids if i not in known)
        if len(keep) == len(ids):
            return m.group(0)
        if not keep:
            return ""
        lead = " " if m.group(0).startswith(" ") else ""
        opener = "[" if "[" in m.group(0) else "("
        return f"{lead}{opener}{', '.join(keep)}{']' if opener == '[' else ')'}"

    def bare(m: re.Match[str]) -> str:
        if m.group(0) in known:
            return m.group(0)
        removed.append(m.group(0))
        return ""

    def prose(seg: str) -> str:
        before = len(removed)
        seg = _EID.sub(bare, _EID_GROUP.sub(group, seg))
        return _tidy(seg) if len(removed) > before else seg

    def code(m: re.Match[str]) -> str:
        def dash(t: re.Match[str]) -> str:
            if t.group(0) in known:
                return t.group(0)
            removed.append(t.group(0))
            return "-".ljust(len(t.group(0)))
        return _EID.sub(dash, m.group(0))

    text = _FENCED.sub(code, text)
    return language.map_prose(text, prose), removed


def strip_disallowed_urls(text: str, allowed: set[str]) -> tuple[str, list[str]]:
    """Drop links/URLs that are not in the link bucket, the alert or raw data (prose only)."""
    ok = {_norm_url(u) for u in allowed}
    removed: list[str] = []

    def bare(seg: str) -> str:
        def rep(m: re.Match[str]) -> str:
            url = _norm_url(m.group(0))
            if url in ok:
                return m.group(0)
            removed.append(url)
            return m.group(0)[len(m.group(0).rstrip(".,;:!?)]}'\"")):]
        new = _BARE_URL.sub(rep, seg)
        return _tidy(new) if new != seg else seg

    def seg_fn(seg: str) -> str:
        out, pos = [], 0
        for m in _SLACK_URL.finditer(seg):
            out.append(bare(seg[pos:m.start()]))
            if _norm_url(m.group(1)) in ok:
                out.append(m.group(0))
            else:
                removed.append(m.group(1))
                out.append(m.group(2) or "")
            pos = m.end()
        out.append(bare(seg[pos:]))
        return "".join(out)

    return _map_non_code(text, seg_fn), removed


def _urls_in(text: str) -> set[str]:
    found = {_norm_url(m.group(1)) for m in _SLACK_URL.finditer(text)}
    found.update(_norm_url(u) for u in _BARE_URL.findall(text))
    return found


def append_unplaced_links(text: str, bucket: list[LinkEntry]) -> tuple[str, list[str]]:
    """Code appends every bucket link the model did not place (3.5)."""
    present = _urls_in(text)
    missing = [e for e in bucket if _norm_url(e.url) not in present]
    if not missing:
        return text, []
    items = [f"• {slack_link(e.url, e.label)} ({', '.join(e.evidence_ids)})" for e in missing]
    sections = split_sections(text)
    links = next((s for s in sections if s.key == "links"), None)
    if links is None:
        return text.rstrip() + "\n\n*Links*\n" + "\n".join(items), [e.url for e in missing]
    while links.lines and not links.lines[-1].strip():
        links.lines.pop()
    links.lines.extend([*items, ""])
    return join_sections(sections), [e.url for e in missing]


def _corpus(ctx: RunContext, view: RunView) -> str:
    parts: list[str] = [view.question or "", ctx.thread_context or ""]
    if view.alert is not None:
        parts.append(view.alert.model_dump_json())
    for r in view.records:
        parts.extend([r.title, r.summary, r.rendered, r.sql or "", " ".join(r.tables),
                      json.dumps(r.links, default=str), json.dumps(r.data, default=str)])
    for v in view.verdicts:
        parts.append(v.model_dump_json())
    for e in ctx.tool_events:
        parts.extend([json.dumps(e.arguments, default=str), e.output or ""])
    return "\n".join(parts).lower()


def grounding_warnings(text: str, ctx: RunContext, view: RunView) -> list[str]:
    """Entity-like names in the report that appear in no evidence or tool output (logged, not shown)."""
    corpus = _corpus(ctx, view)
    seen: set[str] = set()
    out: list[str] = []
    prose_only = _FENCED.sub("", text)
    prose_only = _SLACK_URL.sub(lambda m: m.group(2) or "", prose_only)
    prose_only = _BARE_URL.sub("", re.sub(r":[a-z0-9_+-]+:", " ", prose_only))
    for m in _ENTITY.finditer(prose_only):
        name = (m.group(1) or m.group(2) or "").strip(".,:;()[]")
        low = name.lower()
        if not name or low in seen or _ENTITY_SKIP.match(name) or low in _ENGLISH_HYPHENATED:
            continue
        seen.add(low)
        if low not in corpus:
            out.append(f"entity '{name}' appears in no evidence or tool output")
    if _LOCAL_TZ.search(prose_only):
        out.append("report uses a local time-zone abbreviation; times must be UTC")
    return out[:20]


def _banner(view: RunView) -> str:
    lead = view.leader
    if lead is None:
        return (":warning: *Low confidence:* no hypothesis is supported by enough evidence. Treat everything "
                "below as unconfirmed; next steps are listed first.")
    return (f":warning: *Low confidence:* the leading hypothesis ({lead.id}) is at {round(lead.confidence * 100)}% "
            f"({language.level_label(lead.causal_level)}). Treat the cause below as unconfirmed; next steps are "
            "listed first.")


def _apply_banner(text: str, view: RunView) -> str:
    sections = split_sections(text)
    nxt = next((s for s in sections if s.key == "next_steps"), None)
    if nxt is not None:
        sections.remove(nxt)
        if nxt.lines and nxt.lines[-1].strip():
            nxt.lines.append("")
        anchor = next((i for i, s in enumerate(sections) if s.key in {"tldr", "answer"}), 0)
        sections.insert(anchor + 1, nxt)
    return f"{_banner(view)}\n\n{join_sections(sections).lstrip()}"


def validate_report(markdown: str, ctx: RunContext, *, mode: Mode = "investigation",
                    view: RunView | None = None) -> tuple[str, ReportValidation]:
    """Normalise to Slack mrkdwn and enforce citations, links, wording and the low-confidence banner."""
    view = view or run_view(ctx, mode)
    v = ReportValidation()
    text = to_mrkdwn(markdown.strip())
    text, v.stripped_ids = strip_unknown_ids(text, view.ids)
    text, v.stripped_urls = strip_disallowed_urls(text, view.allowed_urls)
    conf = _effective_confidence(view, mode)          # no hypotheses: nothing may be called a root cause
    cal = language.calibrate(text, conf)
    text, v.calibration_changes = cal.text, cal.changes
    v.grounding_warnings = grounding_warnings(text, ctx, view)
    if mode == "investigation":
        keys = {s.key for s in split_sections(text)}
        v.missing_sections = [k for k in REQUIRED_SECTIONS if k not in keys]
    if language.needs_banner(conf) and not text.lstrip().startswith(":warning: *Low confidence"):
        text = _apply_banner(text, view)
        v.banner = True
    text, v.appended_links = append_unplaced_links(text, view.bucket)
    for label, items in (("stripped unknown evidence ids", v.stripped_ids), ("stripped URLs", v.stripped_urls),
                         ("grounding", v.grounding_warnings), ("calibration", v.calibration_changes),
                         ("missing sections", v.missing_sections)):
        if items:
            log.warning("[%s] report %s: %s", ctx.run_id, label, items[:10])
    ctx.extra["report_validation"] = asdict(v)
    return text.strip(), v


# ---------------------------------------------------------------------------------------------
# Deterministic reports: insufficient data, and the writer-failure fallback
# ---------------------------------------------------------------------------------------------


def _subject(ctx: RunContext) -> str:
    a = ctx.alert
    if a is not None:
        return a.title or a.rule_name or "the alert"
    return " ".join((ctx.question or "the question").split())[:200]


def _window_text(ctx: RunContext) -> str:
    s, e = getattr(ctx.anchor, "window_start", None), getattr(ctx.anchor, "window_end", None)
    if s and e:
        return f"{_fmt_dt(s)} to {_fmt_dt(e)}"
    return f"up to {_fmt_dt(ctx.anchor.now)}"


def _status_of(r: EvidenceRecord) -> str:
    if r.error:
        return "error: " + redact(" ".join(r.error.split())[:160])
    if r.empty_verdict:
        return f"no rows ({r.empty_verdict})"
    if not r.meaningful:
        return "not meaningful (discovery only)"
    return f"{r.row_count} rows"


def insufficient_data_report(ctx: RunContext, *, mode: Mode | None = None) -> str:
    """The templated answer below the evidence floor: what was tried and what a human should check."""
    mode = mode or ("investigation" if ctx.alert is not None else "chat")
    view = run_view(ctx, mode)
    ctx.extra["insufficient_data"] = True
    n = meaningful_count(ctx, view)
    floor = ctx.deps.settings.evidence_floor if mode == "investigation" else CHAT_EVIDENCE_FLOOR
    a = view.alert
    lines = [":grey_question: *Insufficient data to determine a cause*" if mode == "investigation"
             else ":grey_question: *Insufficient data to answer*",
             f"*{_subject(ctx)}* · window {_window_text(ctx)}",
             (f"Only {n} meaningful evidence item(s) were found (the floor is {floor}), so OmniOps is not "
              "proposing an answer. Nothing below is a guess."), "", "*What was tried*"]
    if not ctx.tool_events and not view.records:
        lines.append("• No queries ran. The data sources may not be configured or reachable.")
        ctx.warn("insufficient data: zero tool calls and zero evidence")
    for r in view.records[:25]:
        lines.append(f"• {r.id} {r.title}: {_status_of(r)}")
    if len(view.records) > 25:
        lines.append(f"• ... and {len(view.records) - 25} more evidence records")
    if ctx.tool_events:
        errors = sum(e.is_error for e in ctx.tool_events)
        lines.append(f"• {len(ctx.tool_events)} tool calls ({errors} failed)")
    lines += ["", "*What a human should check*"]
    if a is not None and a.url:
        lines.append(f"• Open the alert and confirm the signal reflects real impact: {slack_link(a.url, 'alert')}")
    failed = sorted({t for r in view.records if r.error for t in (r.tables or [r.title])})
    if failed:
        lines.append("• Check access to the sources whose queries failed: " + ", ".join(f"`{t}`" for t in failed[:8]))
    empty = [r for r in view.records if r.empty_verdict and not r.error]
    if empty:
        lines.append("• Confirm the names and scope used by queries that returned nothing: "
                     + "; ".join(r.title for r in empty[:5]))
    target = (a.service or a.namespace or a.host) if a is not None else None
    if target:
        lines.append(f"• Check deploys and configuration changes to `{target}` in the 48 hours before "
                     f"{_fmt_dt(a.triggered_at) if a and a.triggered_at else 'the alert'}.")
        lines.append(f"• Check error logs and restarts for `{target}` during {_window_text(ctx)}.")
    if lines[-1] == "*What a human should check*":
        lines.append("• Re-ask with the service, environment and time range, or check the source system directly.")
    if ctx.warnings:
        lines += ["", "*Gaps & limits*"] + [f"• {redact(w)}" for w in ctx.warnings[-8:]]
    links = view.bucket[:CARD_LINKS_MAX]
    if links:
        lines += ["", "*Links*"] + [f"• {slack_link(e.url, e.label)} ({', '.join(e.evidence_ids)})" for e in links]
    return "\n".join(lines)


def _cite(ids: list[str], known: set[str]) -> str:
    keep = [i for i in ids if i in known]
    return f" [{', '.join(keep[:6])}]" if keep else ""


def fallback_report(ctx: RunContext, view: RunView, *, mode: Mode, reason: str) -> str:
    """Deterministic report from the board and tracker when the writer fails (3.8.5)."""
    known = view.ids
    evidence = [f"• {r.id} {r.title}: {' '.join(r.summary.split())[:300]}"
                for r in sorted(view.records, key=lambda r: not r.pinned) if _is_meaningful(r)][:15]
    gaps = [f"• The writer was unavailable ({reason}); this text was assembled by code from the evidence board."]
    gaps += [f"• {redact(w)}" for w in ctx.warnings[-8:]]
    if mode == "chat":
        return "\n".join(["*Answer*", ("The answer could not be written; these are the results gathered for "
                                        "the question:"), *evidence, "", "*Gaps & limits*", *gaps])
    lead = view.leader
    shown = [h for h in sorted(view.hypotheses, key=lambda h: -h.confidence) if language.shown(h.confidence, h.status)]
    if lead is not None and language.shown(lead.confidence, lead.status):
        tldr = (f"{language.calibrated_sentence(lead.statement, lead.confidence)} (confidence "
                f"{round(lead.confidence * 100)}%, {language.level_label(lead.causal_level)}){_cite(lead.supporting, known)}")
    else:
        tldr = "The cause is not established: no hypothesis reached 0.3 confidence."
    root = [f"• {language.calibrated_sentence(h.statement, h.confidence)} ({round(h.confidence * 100)}%, "
            f"{h.causal_level}, {h.status}){_cite(h.supporting, known)}" for h in shown[:4]] or \
        ["• Not established."]
    impact = view.verdict("customer_impact")
    change = view.verdict("code_change")
    next_steps = [f"• Confirm: {m}" for m in (lead.missing[:4] if lead else [])] or \
        ["• Review the evidence above before acting on any hypothesis."]
    return "\n".join([
        "*TL;DR*", tldr, "", "*Root cause*", *root, "", "*Evidence*", *(evidence or ["• None."]), "",
        "*Impact / blast radius*",
        (impact.headline + _cite(impact.evidence_ids, known)) if impact else "Not assessed: no customer-impact "
        "verdict was produced.", "",
        "*Timeline (UTC)*", f"• Analysis window: {_window_text(ctx)}", "",
        "*Mitigation*", ("Not generated: the writer failed. Do not act on the hypotheses above until a human "
                         "confirms them against the evidence."), "",
        "*Causal change*",
        (change.headline + _cite(change.evidence_ids, known)) if change and change.verdict == "found"
        else "Not identified.", "",
        "*Next steps (human-only)*", *next_steps, "", "*Gaps & limits*", *gaps])


# ---------------------------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------------------------


async def write_report(ctx: RunContext, *, mode: Mode = "investigation", floor: int | None = None,
                       budget_chars: int | None = None, timeout_s: float | None = None) -> str:
    """The final report (investigation) or answer (chat) as validated Slack mrkdwn. Never raises for
    model failures: below the evidence floor it returns the insufficient-data template, and when the
    writer fails it returns a deterministic report. ``timeout_s`` is the writer call's outer wait (the
    orchestrator passes the report's own allowance, D25); by default it follows the run's deadline."""
    t0 = time.monotonic()
    view = run_view(ctx, mode)
    need = floor if floor is not None else (ctx.deps.settings.evidence_floor if mode == "investigation"
                                            else CHAT_EVIDENCE_FLOOR)
    have = meaningful_count(ctx, view)
    if have < need:
        ctx.warn(f"below the evidence floor ({have} < {need}): insufficient-data answer")
        return insufficient_data_report(ctx, mode=mode)
    if mode == "investigation" and not view.hypotheses:
        ctx.warn("zero hypotheses at report time")
    system = load_prompt("report" if mode == "investigation" else "chat_answer")
    budget = budget_chars or int(REPORT_INPUT_TOKENS * CHARS_PER_TOKEN) - len(system)
    user, trimmed = build_report_input(ctx, mode=mode, budget_chars=budget, view=view)
    if trimmed:
        log.warning("[%s] report input trimmed: %s", ctx.run_id, trimmed)
    role = "report" if mode == "investigation" else "chat"
    max_tokens = REPORT_MAX_OUTPUT_TOKENS if mode == "investigation" else CHAT_MAX_OUTPUT_TOKENS
    try:
        raw = await _generate(ctx, role=role, system=system, user=user, max_tokens=max_tokens, timeout_s=timeout_s)
        if not raw.strip():
            raise ValueError("the writer returned an empty reply")
    except Exception as e:  # noqa: BLE001 - the report always ships (3.8.5)
        reason = f"{type(e).__name__}: {redact(str(e))[:200]}"
        log.error("[%s] %s writer failed: %s", ctx.run_id, role, reason)
        ctx.warn(f"{role} writer failed ({type(e).__name__}); deterministic report used")
        raw = fallback_report(ctx, view, mode=mode, reason=type(e).__name__)
    text, _ = validate_report(raw, ctx, mode=mode, view=view)
    ctx.stats.timings[f"{role}_s"] = round(time.monotonic() - t0, 1)
    return text
