"""Slack Block Kit rendering: conclusion card, paged report, approvals, and a faithful text view.

Retrospective lessons encoded here:
- D10: the card's interactive structure is rendered by code from a validated ``ConclusionCard``;
  the LLM never authors blocks. Buttons carry stable action ids and the run/write id in their value.
- 4.4 (Slack is a constrained rendering target), which is this module's spec:
  50 blocks per message (pages use at most 45), about 3,000 characters per section, a hidden total
  size limit (a message failed at 39 blocks, so pages also have a character budget), ``chat.update``
  fails above 4,000 text characters (the notification text stays <= 3,000), Markdown tables do not
  render (they degrade to monospace code blocks), action ids are unique within an actions block,
  one invalid block rejects the whole message (``validate_blocks`` checks before posting).
- 4.4 rules: content goes in blocks and the text field is a short notification; never clip - page
  long answers, buttons only on the last page; each interactive card gets its own message.
- 3.5: links render only as ``<url|label>`` built from data; tables cannot carry links.
- D11: staged writes render with Approve/Cancel buttons carrying the write id.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from omniops.agent.language import confidence_word, level_label, map_prose, shown
from omniops.agent.types import ConclusionCard

SECTION_TEXT_MAX = 3000
PACK_LIMIT = 2900                     # margin under the section limit for multi-unit characters
HEADER_TEXT_MAX = 150
FIELD_TEXT_MAX = 2000
MAX_FIELDS = 10
MESSAGE_MAX_BLOCKS = 50
PAGE_MAX_BLOCKS = 45
PAGE_TEXT_BUDGET = 12_000             # hidden total-size limit (4.4): pages stay well under it
MESSAGE_TEXT_RISK = 16_000            # validate_blocks flags messages above this
NOTIFICATION_MAX = 3000
BUTTON_TEXT_MAX = 75
BUTTON_VALUE_MAX = 2000
CONTEXT_MAX_ELEMENTS = 10
CARD_EVIDENCE_LINKS = 10
CARD_HYPOTHESES = 3
APPROVAL_PREVIEW_CHARS = 2500

ACTION_FEEDBACK_UP = "omniops_feedback_up"
ACTION_FEEDBACK_DOWN = "omniops_feedback_down"
ACTION_SHOW_REPORT = "omniops_show_report"
ACTION_STOP = "omniops_stop"
ACTION_APPROVE_WRITE = "omniops_write_approve"
ACTION_CANCEL_WRITE = "omniops_write_cancel"

_STATUS_EMOJI = {"final": ":mag:", "preliminary": ":hourglass_flowing_sand:",
                 "insufficient_data": ":grey_question:", "error": ":warning:"}
_STATUS_LABEL = {"final": "Final", "preliminary": "Preliminary (report in progress)",
                 "insufficient_data": "Insufficient data", "error": "Error"}

# Only links survive escaping. Mentions and broadcasts (<!here>, <!channel>, <!subteam^..>, <@U..>, <#C..>) in
# model or tool text are untrusted data (3.16): kept live they would ping people. Code adds its own mentions
# ("Shared by <@U..>") outside escape_mrkdwn.
_SLACK_TOKEN = re.compile(r"<(?:https?://|mailto:)[^<>\n]*>")
_USER_MENTION = re.compile(r"<@[UW][A-Z0-9]+>")      # a single user mention set by code (requested_by)
_MD_LINK = re.compile(r"!?\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_FENCE_OPEN = re.compile(r"^\s*```[A-Za-z0-9_+-]*\s*$")
_SEPARATOR_CELL = re.compile(r"^:?-{2,}:?$")
_SECRET_KEY = re.compile(r"(?i)token|secret|passw|api[_-]?key|authori[sz]ation|cookie|credential|private[_-]?key")
_SECRET_INLINE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|authorization)(\s*[=:]\s*)\S+")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")


# ---------------------------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------------------------


def escape_mrkdwn(text: str) -> str:
    """Escape &, <, > for Slack while keeping real links (``<https://..|label>``, ``<mailto:..>``); mentions
    and broadcasts in the text are escaped, so quoted data can never ping anyone."""
    def esc(s: str) -> str:
        s = re.sub(r"&(?!(?:amp|lt|gt);)", "&amp;", s)
        return s.replace("<", "&lt;").replace(">", "&gt;")

    out, pos = [], 0
    for m in _SLACK_TOKEN.finditer(text):
        out.append(esc(text[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(esc(text[pos:]))
    return "".join(out)


def unescape_mrkdwn(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def slack_link(url: str, label: str) -> str:
    """``<url|label>`` with characters that would break Slack's link syntax neutralised."""
    safe_url = url.strip().replace("|", "%7C").replace(">", "%3E").replace("<", "%3C").replace(" ", "%20")
    safe_label = " ".join(label.replace("|", "/").replace("<", "(").replace(">", ")").split()) or safe_url
    return f"<{safe_url}|{safe_label}>"


def redact(text: str) -> str:
    """Mask credential-looking values before anything reaches Slack."""
    return _BEARER.sub("Bearer [redacted]", _SECRET_INLINE.sub(r"\1\2[redacted]", text))


def _redact_obj(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: "[redacted]" if _SECRET_KEY.search(str(k)) else _redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_obj(v) for v in obj]
    return redact(obj) if isinstance(obj, str) else obj


def fit(text: str, limit: int) -> str:
    """Shorten a summary field at a word boundary with an explicit ellipsis (cards only; reports page)."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    cut = cut[:space] if space > limit // 2 else cut
    lt = cut.rfind("<")
    if lt > cut.rfind(">"):                     # never cut inside a <url|label> link
        cut = cut[:lt]
    amp = cut.rfind("&")
    if amp >= 0 and ";" not in cut[amp:]:       # nor inside an &amp; entity
        cut = cut[:amp]
    return cut.rstrip() + "…"


def _strip_cell(cell: str) -> str:
    cell = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2", cell)
    cell = _MD_LINK.sub(r"\1", cell)
    return cell.replace("**", "").replace("`", "").strip()


def table_to_code(lines: list[str]) -> str:
    """A Markdown pipe table as an aligned monospace code block (Slack cannot render pipe tables)."""
    rows: list[list[str]] = []
    for ln in lines:
        cells = [_strip_cell(c) for c in ln.strip().strip("|").split("|")]
        if cells and all(_SEPARATOR_CELL.match(c.replace(" ", "")) for c in cells if c):
            continue
        rows.append(cells)
    if not rows:
        return ""
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    widths = [max(len(r[i]) for r in rows) for i in range(ncol)]
    fmt = [" | ".join(c.ljust(widths[i]) for i, c in enumerate(r)).rstrip() for r in rows]
    if len(fmt) > 1:
        fmt.insert(1, "-+-".join("-" * w for w in widths))
    return "```\n" + "\n".join(fmt) + "\n```"


def _inline_mrkdwn(seg: str) -> str:
    seg = _MD_LINK.sub(lambda m: slack_link(m.group(2), m.group(1)), seg)
    seg = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"*\1*", seg)
    seg = re.sub(r"(?<![A-Za-z0-9])__(?=\S)(.+?)(?<=\S)__(?![A-Za-z0-9])", r"_\1_", seg)
    return re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"~\1~", seg)


def _is_table_line(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


def to_mrkdwn(markdown: str) -> str:
    """Normalise Markdown to Slack mrkdwn (lenient in, strict out).

    ``**bold**`` -> ``*bold*``, ``# Heading`` -> ``*Heading*``, ``[label](url)`` -> ``<url|label>``,
    ``- item`` -> ``• item``, pipe tables -> monospace code blocks. Code fences are left untouched.
    """
    lines = markdown.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i, in_code = 0, False
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if in_code:
            out.append(line)
            in_code = not s.endswith("```")
            i += 1
            continue
        if s.startswith("```"):
            if s.endswith("```") and len(s) > 6:
                out.append(line)                     # single-line ```code``` stays inline
            else:
                in_code = True
                out.append("```" if _FENCE_OPEN.match(line) else line)
            i += 1
            continue
        if _is_table_line(line):
            j = i
            while j < len(lines) and _is_table_line(lines[j]):
                j += 1
            if j - i >= 2:
                out.append(table_to_code(lines[i:j]))
                i = j
                continue
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            title = heading.group(1).replace("**", "").strip().strip("*").strip()
            out.append(f"*{map_prose(title, _inline_mrkdwn)}*")
        elif re.match(r"^\s*([-*_])\s*\1\s*\1[-*_\s]*$", line):
            out.append("")
        else:
            line = re.sub(r"^(\s*)[-*+]\s+(?=\S)", r"\1• ", line)
            out.append(map_prose(line, _inline_mrkdwn))
        i += 1
    if in_code:
        out.append("```")
    return "\n".join(out)


def split_text(text: str, limit: int) -> list[str]:
    """Split at line boundaries, then at spaces outside ``<...>`` links; never drops content."""
    chunks: list[str] = []
    buf = ""
    for line in text.split("\n"):
        if len(line) > limit:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.extend(_split_line(line, limit))
            continue
        cand = f"{buf}\n{line}" if buf else line
        if len(cand) <= limit:
            buf = cand
        else:
            chunks.append(buf)
            buf = line
    if buf:
        chunks.append(buf)
    return [c for c in chunks if c.strip()]


def _split_line(line: str, limit: int) -> list[str]:
    tokens = re.findall(r"<[^<>\n]*>\S*|[^\s<]+(?:<[^<>\n]*>\S*)?|<|\s+", line)
    pieces: list[str] = []
    buf = ""
    for tok in tokens:
        while len(tok) > limit:                 # a single token longer than a section: hard split
            if buf:
                pieces.append(buf)
                buf = ""
            pieces.append(tok[:limit])
            tok = tok[limit:]
        if len(buf) + len(tok) <= limit:
            buf += tok
        else:
            pieces.append(buf)
            buf = tok.lstrip()
    if buf:
        pieces.append(buf)
    return [p for p in pieces if p.strip()]


def _units(mrkdwn: str) -> list[tuple[str, str]]:
    """("code", body) and ("prose", paragraph) units in order."""
    units: list[tuple[str, str]] = []
    para: list[str] = []
    code: list[str] | None = None

    def flush() -> None:
        if para and "\n".join(para).strip():
            units.append(("prose", "\n".join(para).strip("\n")))
        para.clear()

    for line in mrkdwn.split("\n"):
        s = line.strip()
        if code is not None:
            if s.endswith("```"):
                tail = s[:-3]
                if tail:
                    code.append(tail)
                units.append(("code", "\n".join(code)))
                code = None
            else:
                code.append(line)
            continue
        if s.startswith("```") and not (s.endswith("```") and len(s) > 6):
            flush()
            head = s[3:]
            code = [] if _FENCE_OPEN.match(s) or not head else [head]
            continue
        if not s:
            flush()
        else:
            para.append(line)
    if code is not None:
        units.append(("code", "\n".join(code)))
    flush()
    return units


def _pieces(markdown: str, limit: int) -> list[str]:
    """Escaped section texts, each <= ``limit``; code blocks are re-fenced when split."""
    pieces: list[str] = []
    for kind, body in _units(to_mrkdwn(markdown)):
        body = escape_mrkdwn(body)
        if kind == "code":
            if not body.strip():
                continue
            for part in split_text(body, limit - 8):
                pieces.append(f"```\n{part}\n```")
        else:
            pieces.extend(split_text(body, limit))
    return pieces


def _pack(pieces: list[str], limit: int) -> list[str]:
    sections: list[str] = []
    buf = ""
    for p in pieces:
        cand = f"{buf}\n\n{p}" if buf else p
        if len(cand) <= limit:
            buf = cand
        else:
            if buf:
                sections.append(buf)
            buf = p
    if buf:
        sections.append(buf)
    return sections


def text_chunks(markdown: str, limit: int = SECTION_TEXT_MAX) -> list[str]:
    """Last-resort degradation: plain mrkdwn chunks <= ``limit`` (no blocks)."""
    return _pack(_pieces(markdown, limit), limit)


# ---------------------------------------------------------------------------------------------
# Block builders
# ---------------------------------------------------------------------------------------------


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context(*texts: str) -> dict[str, Any]:
    els = [{"type": "mrkdwn", "text": fit(t, SECTION_TEXT_MAX)} for t in texts if t.strip()]
    return {"type": "context", "elements": els[:CONTEXT_MAX_ELEMENTS]}


def _button(text: str, action_id: str, value: str, style: str | None = None,
            confirm: dict[str, Any] | None = None) -> dict[str, Any]:
    b: dict[str, Any] = {"type": "button", "text": {"type": "plain_text", "text": fit(text, BUTTON_TEXT_MAX),
                                                    "emoji": True},
                         "action_id": action_id, "value": value[:BUTTON_VALUE_MAX]}
    if style:
        b["style"] = style
    if confirm:
        b["confirm"] = confirm
    return b


def _feedback_buttons(run_id: str) -> list[dict[str, Any]]:
    return [_button(":thumbsup: Helpful", ACTION_FEEDBACK_UP, run_id),
            _button(":thumbsdown: Not helpful", ACTION_FEEDBACK_DOWN, run_id)]


def _pct(c: float) -> str:
    return f"{round(c * 100)}%"


def _bullets(items: list[str], limit: int) -> str:
    return fit("\n".join(f"• {escape_mrkdwn(i.strip())}" for i in items if i.strip()), limit)


def card_blocks(card: ConclusionCard, *, show_report: bool = True) -> list[dict[str, Any]]:
    """Block Kit for the conclusion card (one message, well under the 50-block limit)."""
    status = card.status
    run_id = card.run_id or "unknown"
    title = f"{_STATUS_EMOJI.get(status, ':mag:')} {card.title or 'Investigation'}"
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": fit(title, HEADER_TEXT_MAX), "emoji": True}},
        _section(fit(f"*Verdict:* {escape_mrkdwn(card.verdict)}", SECTION_TEXT_MAX)),
    ]
    if status != "insufficient_data":
        fields = [f"*Confidence*\n{_pct(card.confidence)} ({confidence_word(card.confidence)})",
                  f"*Causal level*\n{level_label(card.causal_level)}",
                  f"*Impact*\n{card.impact.level}",
                  f"*Status*\n{_STATUS_LABEL.get(status, status)}"]
        blocks.append({"type": "section", "fields": [{"type": "mrkdwn", "text": fit(f, FIELD_TEXT_MAX)}
                                                     for f in fields[:MAX_FIELDS]]})
        if card.root_cause.strip():
            blocks.append(_section(fit(f"*Root cause* · {level_label(card.causal_level)}\n"
                                       f"{escape_mrkdwn(card.root_cause)}", SECTION_TEXT_MAX)))
    if card.trigger_change is not None:
        tc = card.trigger_change
        name = slack_link(tc.url, tc.title) if tc.url else escape_mrkdwn(tc.title)
        meta = " · ".join(escape_mrkdwn(x) for x in (tc.version, tc.author, tc.ts) if x)
        blocks.append(_section(fit(f"*Triggering change*\n{name}" + (f"\n{meta}" if meta else ""),
                                   SECTION_TEXT_MAX)))
    if card.impact.summary.strip() or card.impact.affected:
        txt = f"*Impact* ({card.impact.level})\n{escape_mrkdwn(card.impact.summary)}"
        if card.impact.affected:
            txt += "\nAffected: " + ", ".join(escape_mrkdwn(a) for a in card.impact.affected[:15])
        blocks.append(_section(fit(txt, SECTION_TEXT_MAX)))
    if card.mitigation:
        blocks.append(_section("*Mitigation*\n" + _bullets(card.mitigation[:6], SECTION_TEXT_MAX - 20)))
    hyp_lines = []
    for h in card.hypotheses:
        conf = float(h.get("confidence") or 0.0)
        h_status = str(h.get("status") or "open")
        if not shown(conf, h_status):
            continue
        ids = ", ".join(str(e) for e in (h.get("evidence_ids") or [])[:6])
        hyp_lines.append(f"`{h.get('id', '?')}` {_pct(conf)} · {h_status} · "
                         f"{escape_mrkdwn(fit(str(h.get('statement', '')), 400))}" + (f" ({ids})" if ids else ""))
        if len(hyp_lines) == CARD_HYPOTHESES:
            break
    if hyp_lines:
        blocks.append(_section(fit("*Top hypotheses*\n" + "\n".join(f"• {x}" for x in hyp_lines),
                                   SECTION_TEXT_MAX)))
    ev = "*Evidence*\n"
    for lk in [x for x in card.evidence_links if x.get("url")][:CARD_EVIDENCE_LINKS]:
        item = slack_link(lk["url"], fit(lk.get("label") or lk["url"], 80))
        if len(ev) + len(item) + 3 > SECTION_TEXT_MAX:
            break
        ev += ("" if ev.endswith("\n") else " · ") + item
    if not ev.endswith("\n"):
        blocks.append(_section(ev))
    if card.gaps:
        head = "*What a human should check*" if status == "insufficient_data" else "*Gaps & limits*"
        blocks.append(_section(head + "\n" + _bullets(card.gaps[:5], SECTION_TEXT_MAX - 40)))
    buttons: list[dict[str, Any]] = []
    if status == "preliminary":
        buttons.append(_button(":octagonal_sign: Stop", ACTION_STOP, run_id))
    elif show_report:
        label = "Show details" if status == "insufficient_data" else "Show full report"
        buttons.append(_button(label, ACTION_SHOW_REPORT, run_id))
    if status != "preliminary":
        buttons.extend(_feedback_buttons(run_id))
    if buttons:
        blocks.append({"type": "actions", "block_id": "omniops_card_actions", "elements": buttons})
    blocks.append(_context(f"OmniOps run `{escape_mrkdwn(run_id)}` · {_STATUS_LABEL.get(status, status)}"))
    return blocks


def report_pages(markdown: str, *, run_id: str | None = None, buttons: bool = True,
                 header: str | None = None) -> list[list[dict[str, Any]]]:
    """Page a long report into messages; never clip.

    Every section text is <= 3,000 characters, every page <= 45 blocks and ~12,000 characters, tables
    are monospace code blocks, and feedback buttons (when ``run_id`` is given) sit on the last page.
    """
    sections = _pack(_pieces(markdown, PACK_LIMIT), PACK_LIMIT) or ["_(empty)_"]
    content_max = PAGE_MAX_BLOCKS - 3              # room for header, footer and actions
    pages: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    size = 0
    for s in sections:
        if cur and (len(cur) >= content_max or size + len(s) > PAGE_TEXT_BUDGET):
            pages.append(cur)
            cur, size = [], 0
        cur.append(_section(s))
        size += len(s)
    pages.append(cur)
    n = len(pages)
    if header:
        pages[0].insert(0, {"type": "header", "text": {"type": "plain_text", "text": fit(header, HEADER_TEXT_MAX),
                                                       "emoji": True}})
    for i, page in enumerate(pages, 1):
        if n > 1:
            page.append(_context(f"Page {i} of {n}" + (" · continued below" if i < n else "")))
        if i == n and buttons and run_id:
            page.append({"type": "actions", "block_id": "omniops_report_actions", "elements":
                         _feedback_buttons(run_id)})
    return pages


def insufficient_data_pages(markdown: str, *, run_id: str | None = None) -> list[list[dict[str, Any]]]:
    """The templated insufficient-data answer: a header, the template, feedback on the last page."""
    return report_pages(markdown, run_id=run_id, header=":grey_question: Insufficient data to determine a cause")


def notification_text(card: ConclusionCard | None = None, markdown: str | None = None, *,
                      prefix: str = "") -> str:
    """The message ``text`` field: a short notification snippet (<= 3,000 chars; content is in blocks)."""
    if card is not None:
        body = f"{_STATUS_EMOJI.get(card.status, ':mag:')} {card.title}: {card.verdict}"
        if card.status == "final" and card.confidence:
            body += f" (confidence {_pct(card.confidence)})"
    else:
        units = [b for k, b in _units(to_mrkdwn(markdown or "")) if k == "prose"]
        body = units[0] if units else (markdown or "").strip()
    text = f"{prefix}{escape_mrkdwn(' '.join(body.split()))}"
    return fit(text, NOTIFICATION_MAX) or "OmniOps"


def _write_fields(write: Any) -> dict[str, Any]:
    get = write.get if isinstance(write, dict) else (lambda k, d=None: getattr(write, k, d))
    return {"id": str(get("id", "") or ""), "table": str(get("table", "") or ""),
            "summary": str(get("summary", "") or ""), "filters": get("filters", None) or {},
            "expires": get("expires", None), "requested_by": get("requested_by", None)}


def approval_blocks(write: Any) -> list[dict[str, Any]]:
    """Approve/Cancel blocks for a staged write (``StagedWrite`` or dict with id/table/summary/filters)."""
    w = _write_fields(write)
    wid = w["id"] or "unknown"
    preview = json.dumps(_redact_obj(w["filters"]), indent=2, default=str, sort_keys=True)
    if len(preview) > APPROVAL_PREVIEW_CHARS:
        preview = preview[:APPROVAL_PREVIEW_CHARS] + f"\n… ({len(preview) - APPROVAL_PREVIEW_CHARS} more chars)"
    expires = ""
    if isinstance(w["expires"], int | float):
        expires = datetime.fromtimestamp(float(w["expires"]), UTC).strftime("%Y-%m-%d %H:%M UTC")
    meta = [f"Write `{escape_mrkdwn(wid)}`", "runs once, only after approval"]
    if expires:
        meta.insert(1, f"expires {expires}")
    if w["requested_by"]:
        who = str(w["requested_by"])
        meta.append(f"requested by {who if _USER_MENTION.fullmatch(who) else escape_mrkdwn(who)}")
    confirm = {"title": {"type": "plain_text", "text": "Run this write?"},
               "text": {"type": "mrkdwn", "text": fit(escape_mrkdwn(redact(w["summary"])) or wid, 300)},
               "confirm": {"type": "plain_text", "text": "Approve"},
               "deny": {"type": "plain_text", "text": "Back"}}
    return [
        {"type": "header", "text": {"type": "plain_text", "text": ":lock: Approval needed: staged write",
                                    "emoji": True}},
        _section(fit(f"*{escape_mrkdwn(redact(w['summary'])) or 'Staged write'}*\n"
                     f"Target: `{escape_mrkdwn(w['table'] or 'unknown')}`", SECTION_TEXT_MAX)),
        _section(f"```\n{escape_mrkdwn(preview)}\n```"),
        _context(" · ".join(meta)),
        {"type": "actions", "block_id": f"omniops_write:{wid}"[:255], "elements": [
            _button("Approve", ACTION_APPROVE_WRITE, wid, style="primary", confirm=confirm),
            _button("Cancel", ACTION_CANCEL_WRITE, wid, style="danger")]},
    ]


# ---------------------------------------------------------------------------------------------
# Validation and the text view used by simulators and benchmarks
# ---------------------------------------------------------------------------------------------


def validate_blocks(blocks: list[dict[str, Any]]) -> list[str]:
    """Problems that would make Slack reject the message (one bad block rejects all of them)."""
    problems: list[str] = []
    if len(blocks) > MESSAGE_MAX_BLOCKS:
        problems.append(f"{len(blocks)} blocks > {MESSAGE_MAX_BLOCKS}")
    try:
        json.dumps(blocks)
    except (TypeError, ValueError) as e:
        problems.append(f"not JSON-serialisable: {e}")
    block_ids: set[str] = set()
    total = 0
    for n, b in enumerate(blocks):
        t = b.get("type")
        bid = b.get("block_id")
        if bid:
            if bid in block_ids:
                problems.append(f"block {n}: duplicate block_id {bid}")
            block_ids.add(bid)
        if t == "section":
            text = (b.get("text") or {}).get("text")
            fields = b.get("fields") or []
            if text is None and not fields:
                problems.append(f"block {n}: section without text or fields")
            if text is not None and not (0 < len(text) <= SECTION_TEXT_MAX):
                problems.append(f"block {n}: section text length {len(text)}")
            if len(fields) > MAX_FIELDS or any(not (0 < len(f.get("text", "")) <= FIELD_TEXT_MAX) for f in fields):
                problems.append(f"block {n}: invalid fields")
            total += len(text or "") + sum(len(f.get("text", "")) for f in fields)
        elif t == "header":
            text = (b.get("text") or {}).get("text", "")
            if not (0 < len(text) <= HEADER_TEXT_MAX):
                problems.append(f"block {n}: header length {len(text)}")
            total += len(text)
        elif t == "context":
            els = b.get("elements") or []
            if not (0 < len(els) <= CONTEXT_MAX_ELEMENTS):
                problems.append(f"block {n}: context with {len(els)} elements")
            total += sum(len(e.get("text", "")) for e in els)
        elif t == "actions":
            ids = [e.get("action_id") for e in b.get("elements") or []]
            if not ids or len(ids) != len(set(ids)):
                problems.append(f"block {n}: action_ids not unique: {ids}")
            for e in b.get("elements") or []:
                if len(e.get("text", {}).get("text", "")) > BUTTON_TEXT_MAX or \
                        len(e.get("value", "")) > BUTTON_VALUE_MAX:
                    problems.append(f"block {n}: button too long")
        elif t not in {"divider", "image", "rich_text", "table"}:
            problems.append(f"block {n}: unknown type {t}")
    if total > MESSAGE_TEXT_RISK:
        problems.append(f"total text {total} chars risks Slack's hidden message size limit")
    return problems


def _mrkdwn_to_text(text: str) -> str:
    text = re.sub(r"<(https?://[^|<>]+)\|([^<>]+)>", r"[\2](\1)", text)
    text = re.sub(r"<(https?://[^|<>]+)>", r"\1", text)
    text = re.sub(r"<[@#]([A-Z0-9]+)\|([^<>]+)>", r"@\2", text)
    text = re.sub(r"<([@#])([A-Z0-9]+)>", r"\1\2", text)
    text = re.sub(r"<!([a-z]+)(?:\|[^<>]*)?>", r"@\1", text)
    return unescape_mrkdwn(text)


def _block_text(b: dict[str, Any]) -> str:
    t = b.get("type")
    if t == "header":
        return (b.get("text") or {}).get("text", "")
    if t == "section":
        parts = []
        if b.get("text"):
            parts.append(_mrkdwn_to_text(b["text"].get("text", "")))
        parts.extend(_mrkdwn_to_text(f.get("text", "")) for f in b.get("fields") or [])
        acc = b.get("accessory") or {}
        if acc.get("type") == "button":
            parts.append(f"[{acc.get('text', {}).get('text', '')}]")
        return "\n".join(p for p in parts if p)
    if t == "context":
        return " · ".join(_mrkdwn_to_text(e.get("text", "")) for e in b.get("elements") or [] if e.get("text"))
    if t == "divider":
        return "---"
    if t == "actions":
        return " ".join(f"[{e.get('text', {}).get('text', '')}]" for e in b.get("elements") or [])
    if t == "image":
        return f"[image: {b.get('alt_text', '')}]"
    if t == "rich_text":
        return "".join(el.get("text", "") for sec in b.get("elements") or [] for el in sec.get("elements") or [])
    return ""


def blocks_to_text(blocks: list[dict[str, Any]] | list[list[dict[str, Any]]]) -> str:
    """What a user sees, as plain text/Markdown: links become ``[label](url)``, entities are unescaped.

    Accepts one message's blocks or a list of pages (pages are joined in order).
    """
    if blocks and isinstance(blocks[0], list):
        return "\n\n".join(blocks_to_text(p) for p in blocks)  # type: ignore[arg-type]
    return "\n\n".join(t for t in (_block_text(b) for b in blocks) if t)  # type: ignore[arg-type]
