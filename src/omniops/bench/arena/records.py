"""Arena investigation records: render final / intermediate / actions text and validate archive-v1.

What an investigation delivered is read back from the Slack thread, the way the user saw it, and the
tool activity from the run's ``ToolEvent`` list. Lessons encoded (OpsIntelligence retrospective):

- 3.18 judge calibration: feed the judge the real tool activity (``render_actions`` lists every tool
  call with its arguments and a result digest); collapse long URLs (a report of URL hashes read as
  garbled); show head *and* tail of long results with an explicit, sized elision marker.
- 4.1 silent failure: zero tool calls, an empty final answer or an archive whose final answer differs
  from ``final.txt`` are reported as problems, never passed through quietly. Verify the effect (the
  archive the Arena wrote), not the config.
- D10 / 4.4: the final answer is the final state of the latest run's conclusion card and report
  pages; the status trail, superseded versions (early card, placeholders) and other bot posts
  (notices, approval cards, an earlier run) are intermediate advice. Slack content lives in blocks,
  so text is rendered from blocks (buttons are controls, not content), not from the ``text`` field.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

STATUS_EVENT_TYPE = "omniops_status"         # slack/status.py marks the live status trail with this
STATUS_BLOCK_ID = "omniops_status"
ANSWER_EVENT_TYPES = ("omniops_card", "omniops_report")   # slack/app.py META_CARD / META_REPORT
DETECTION_STATES = ("detected", "not_detected", "not_measured")
LONG_URL = 120                               # longer links keep only their label (3.18: collapse URLs)
FINAL_SELECTION = (
    "Final state of the latest OmniOps run's conclusion card and report pages in the launch thread, in "
    "thread order, rendered from Slack blocks as displayed (buttons omitted); without a card or report, "
    "every OmniOps post except the live status trail. Links longer than "
    f"{LONG_URL} characters are reduced to their label."
)

_MD_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_BARE_URL = re.compile(r"(?<![(\[])\bhttps?://[^\s)>\]]+")
_SLACK_LINK = re.compile(r"<(https?://[^|<>]+)(?:\|([^<>]+))?>")


class ArchiveError(ValueError):
    """An archive-v1 record that the Arena would reject, or that does not match the case files."""


# ---------------------------------------------------------------------------------------------
# Slack transcript model
# ---------------------------------------------------------------------------------------------


@dataclass
class MessageVersion:
    text: str = ""
    blocks: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] | None = None
    at: float | None = None                  # epoch seconds when posted / edited / first seen

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MessageVersion:
        at = d.get("edited_at", d.get("seen_at", d.get("at")))
        return cls(text=str(d.get("text") or ""), blocks=list(d.get("blocks") or []),
                   attachments=list(d.get("attachments") or []), metadata=d.get("metadata"),
                   at=float(at) if at is not None else None)


@dataclass
class SlackMessage:
    """One message in the launch thread with every version that was observed (oldest first)."""

    ts: str
    user: str | None
    bot_id: str | None
    versions: list[MessageVersion]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SlackMessage:
        current = MessageVersion.from_dict(d)
        if current.at is None:
            current.at = _ts_float(d.get("ts"))
        earlier = [MessageVersion.from_dict(e) for e in d.get("edits") or []]
        if earlier and earlier[0].at is None:
            earlier[0].at = _ts_float(d.get("ts"))
        return cls(ts=str(d.get("ts") or ""), user=d.get("user"), bot_id=d.get("bot_id"),
                   versions=[*earlier, current])

    @property
    def final(self) -> MessageVersion:
        return self.versions[-1]

    def is_status(self) -> bool:
        for v in self.versions:
            if (v.metadata or {}).get("event_type") == STATUS_EVENT_TYPE:
                return True
            if any(b.get("block_id") == STATUS_BLOCK_ID for b in v.blocks):
                return True
        return False

    def answer_run(self) -> str | None:
        """The run id when the final version is a conclusion card or report page, else None."""
        meta = self.final.metadata or {}
        if meta.get("event_type") not in ANSWER_EVENT_TYPES:
            return None
        return str((meta.get("event_payload") or {}).get("run_id") or "")


@dataclass
class SlackTranscript:
    """The launch thread: the launch message (root) plus replies, as seen by the harness."""

    channel: str
    thread_ts: str
    bot_user_id: str | None
    bot_id: str | None
    messages: list[SlackMessage]
    capture: str = "simulator"               # simulator (every edit recorded) | polling (edits may be missed)

    @classmethod
    def from_dicts(cls, channel: str, thread_ts: str, messages: Iterable[dict[str, Any]], *,
                   bot_user_id: str | None = None, bot_id: str | None = None,
                   capture: str = "simulator") -> SlackTranscript:
        msgs = sorted((SlackMessage.from_dict(m) for m in messages), key=lambda m: _ts_float(m.ts) or 0.0)
        return cls(channel, thread_ts, bot_user_id, bot_id, msgs, capture)

    def bot_messages(self) -> list[SlackMessage]:
        """OmniOps' replies in the thread (never the launch message or another integration's posts)."""
        out = []
        for m in self.messages:
            if m.ts == self.thread_ts:
                continue
            mine = ((self.bot_user_id and m.user == self.bot_user_id) or (self.bot_id and m.bot_id == self.bot_id)
                    or (not self.bot_user_id and not self.bot_id and m.bot_id))
            if mine:
                out.append(m)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel, "thread_ts": self.thread_ts, "bot_user_id": self.bot_user_id,
            "bot_id": self.bot_id, "capture": self.capture,
            "messages": [{"ts": m.ts, "user": m.user, "bot_id": m.bot_id,
                          "versions": [v.__dict__ for v in m.versions]} for m in self.messages],
        }


# ---------------------------------------------------------------------------------------------
# Rendering what the user saw
# ---------------------------------------------------------------------------------------------


def version_text(v: MessageVersion) -> str:
    """A message version as displayed: blocks when present (content lives there), else ``text``."""
    parts: list[str] = []
    blocks = [b for b in v.blocks if b.get("type") != "actions"]
    if blocks:
        parts.append(_blocks_to_text(blocks))
    elif v.text:
        parts.append(_slack_mrkdwn_to_text(v.text))
    for a in v.attachments:
        parts.append(_attachment_text(a))
    return collapse_links("\n\n".join(p for p in parts if p.strip()).strip())


def collapse_links(text: str) -> str:
    """Keep short links; reduce long ones to their label (or host) so the judge reads prose, not hashes."""
    def md(m: re.Match[str]) -> str:
        label, url = m.group(1), m.group(2)
        return m.group(0) if len(url) <= LONG_URL else label

    def bare(m: re.Match[str]) -> str:
        url = m.group(0)
        if len(url) <= LONG_URL:
            return url
        host = re.sub(r"^https?://", "", url).split("/", 1)[0]
        return f"<link: {host}>"

    return _BARE_URL.sub(bare, _MD_LINK.sub(md, text))


def render_final(transcript: SlackTranscript | None, results: Sequence[Any] = ()) -> tuple[str, str]:
    """The verbatim final delivered answer and where it came from (``slack`` | ``run_result`` | ``none``).

    Slack is authoritative. The RunResult fallback exists for a thread that could not be read back
    and is recorded as such, because it is not what the user saw.
    """
    if transcript is not None:
        texts = [t for t in (version_text(m.final) for m in final_messages(transcript)) if t]
        if texts:
            return "\n\n".join(texts) + "\n", "slack"
    for r in reversed(list(results)):
        text = _result_text(r)
        if text:
            return text + "\n", "run_result"
    return "", "none"


def final_messages(transcript: SlackTranscript) -> list[SlackMessage]:
    """The latest run's card + report pages; without any, every non-status bot post (an error notice)."""
    content = [m for m in transcript.bot_messages() if not m.is_status()]
    answers = [m for m in content if m.answer_run() is not None]
    if not answers:
        return content
    latest = answers[-1].answer_run()
    return [m for m in answers if m.answer_run() == latest]


def render_intermediate(transcript: SlackTranscript | None) -> str:
    """Earlier advice the user could see, in time order: the status trail, superseded versions of the final
    messages, and every other bot post (notices, approval cards, an earlier run's answer)."""
    if transcript is None:
        return ""
    final = {m.ts for m in final_messages(transcript)}
    items: list[tuple[float, str, str]] = []
    for m in transcript.bot_messages():
        status = m.is_status()
        versions = m.versions[:-1] if m.ts in final else m.versions
        label = "status" if status else ("superseded" if m.ts in final else "other post")
        last = None
        for v in versions:
            text = version_text(v)
            if not text or text == last:
                continue
            last = text
            items.append((v.at or _ts_float(m.ts) or 0.0, label, text))
    items.sort(key=lambda x: x[0])
    out = [f"[{_fmt_time(at)}] ({label})\n{text}" for at, label, text in items]
    return ("\n\n".join(out) + "\n") if out else ""


def render_actions(results: Sequence[Any], *, result_chars: int = 900) -> str:
    """Every tool call (SQL text in full, arguments, a head+tail result digest) plus staged writes."""
    events: list[Any] = []
    writes: list[dict[str, Any]] = []
    run_ids: list[str] = []
    for r in results:
        run_ids.append(str(_get(r, "run_id", "?")))
        events.extend(_get(r, "tool_events", []) or [])
        writes.extend(_get(r, "staged_writes", []) or [])
    events.sort(key=lambda e: float(_get(e, "ts", 0.0) or 0.0))
    sql = [e for e in events if _get(e, "name") == "run_sql"]
    errors = [e for e in events if _get(e, "is_error", False)]
    agents = sorted({str(_get(e, "agent", "?")) for e in events})
    lines = [(f"OmniOps tool activity (runs: {', '.join(run_ids) or 'none'}): {len(events)} tool calls, "
              f"{len(sql)} SQL queries, {len(errors)} errors; agents: {', '.join(agents) or 'none'}.")]
    if not events:
        lines.append("WARNING: zero tool calls were recorded for this investigation.")
    if writes:
        lines.append(f"Staged writes (require human approval in Slack; none executed by the harness): {len(writes)}")
        for w in writes:
            lines.append("  - " + _clip(json.dumps(w, default=str, sort_keys=True), 600))
    else:
        lines.append("Staged writes: none.")
    for i, e in enumerate(events, 1):
        args = dict(_get(e, "arguments", {}) or {})
        head = (f"#{i} {_fmt_time(float(_get(e, 'ts', 0.0) or 0.0))} {_get(e, 'agent', '?')} {_get(e, 'name', '?')}"
                + (f" -> {_get(e, 'evidence_id')}" if _get(e, "evidence_id") else "")
                + f" ({int(_get(e, 'elapsed_ms', 0) or 0)} ms)" + (" ERROR" if _get(e, "is_error", False) else ""))
        block = [head]
        query = args.pop("sql", None) if _get(e, "name") == "run_sql" else None
        if query:
            block.append("SQL:\n" + _clip(str(query).strip(), 4000))
        if args:
            block.append("Arguments: " + _clip(json.dumps(args, default=str, sort_keys=True), 1500))
        block.append("Result: " + head_tail(str(_get(e, "output", "") or ""), result_chars))
        lines.append("\n".join(block))
    return "\n\n".join(lines) + "\n"


def head_tail(text: str, limit: int) -> str:
    """Head and tail with an explicit, sized elision marker (3.18: a tail-only cut lost the tables)."""
    text = text.strip()
    if len(text) <= limit:
        return text
    head = int(limit * 0.7)
    tail = limit - head
    return f"{text[:head]}\n[... {len(text) - head - tail} characters elided ...]\n{text[-tail:]}"


# ---------------------------------------------------------------------------------------------
# archive-v1 validation (mirrors the Arena's validate_archive, plus case-file consistency)
# ---------------------------------------------------------------------------------------------


def validate_archive(record: dict[str, Any], *, scenarios: Iterable[str] | None = None,
                     final_text: str | None = None) -> dict[str, Any]:
    """Raise ``ArchiveError`` for any rule the Arena enforces, or when the archived final answer is not
    byte-identical to ``final_text`` (the case's ``final.txt``). Returns the record."""
    problems: list[str] = []
    if record.get("schema_version") != "archive-v1":
        problems.append("schema_version must be archive-v1")
    for key in ("run_id", "case_id", "product", "final_selection"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            problems.append(f"missing {key}")
    if scenarios is not None and record.get("scenario") not in set(scenarios):
        problems.append(f"unknown scenario {record.get('scenario')!r}")
    if (record.get("detection") or {}).get("status") not in DETECTION_STATES:
        problems.append("explicit detection status required")
    sources = record.get("sources")
    if not isinstance(sources, dict):
        problems.append("sources must be a map")
    else:
        for key in ("final_answer", "intermediate", "actions"):
            if not isinstance(sources.get(key), str):
                problems.append(f"missing text source {key}")
        if final_text is not None and isinstance(sources.get("final_answer"), str) \
                and sources["final_answer"] != final_text:
            problems.append("archived final_answer differs from final.txt")
    lim = record.get("limitations")
    if not isinstance(lim, list) or not all(isinstance(x, str) for x in lim):
        problems.append("limitations must be a string array")
    if problems:
        raise ArchiveError("; ".join(problems))
    return record


def record_warnings(record: dict[str, Any]) -> list[str]:
    """Loud zeros for a structurally valid record that will not score as a completed investigation."""
    src = record.get("sources") or {}
    out = []
    if not str(src.get("final_answer", "")).strip():
        out.append("final answer is empty: this case is not a completed investigation")
    if not str(src.get("actions", "")).strip():
        out.append("no action trace: the judge must return insufficient_evidence for action safety")
    if not str(src.get("intermediate", "")).strip():
        out.append("no intermediate record: the judge must return insufficient_evidence for intermediate advice")
    return out


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _ts_float(ts: Any) -> float | None:
    try:
        return float(ts)
    except (TypeError, ValueError):
        return None


def _fmt_time(at: float) -> str:
    if not at:
        return "unknown time"
    return datetime.fromtimestamp(at, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]} [... {len(text) - limit} characters clipped]"


def _blocks_to_text(blocks: list[dict[str, Any]]) -> str:
    try:
        from omniops.slack.render import blocks_to_text
    except ImportError:                                   # renderer not importable: fall back to section text
        return "\n\n".join(_slack_mrkdwn_to_text(str((b.get("text") or {}).get("text", ""))) for b in blocks)
    return blocks_to_text(blocks)


def _slack_mrkdwn_to_text(text: str) -> str:
    text = _SLACK_LINK.sub(lambda m: f"[{m.group(2)}]({m.group(1)})" if m.group(2) else m.group(1), text)
    text = re.sub(r"<[@#]([A-Z0-9]+)\|([^<>]+)>", r"@\2", text)
    text = re.sub(r"<([@#])([A-Z0-9]+)>", r"\1\2", text)
    text = re.sub(r"<!([a-z]+)(?:\|[^<>]*)?>", r"@\1", text)
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _attachment_text(a: dict[str, Any]) -> str:
    parts = [str(a.get(k) or "") for k in ("pretext", "title", "text")]
    parts += [f"{f.get('title', '')}: {f.get('value', '')}" for f in a.get("fields") or []]
    if a.get("blocks"):
        parts.append(_blocks_to_text(a["blocks"]))
    if a.get("footer"):
        parts.append(str(a["footer"]))
    return _slack_mrkdwn_to_text("\n".join(p for p in parts if p.strip()))


def _result_text(r: Any) -> str:
    """Card summary + report from a RunResult (fallback only; recorded as not-as-displayed)."""
    md = str(_get(r, "answer_markdown", "") or "").strip()
    card = _get(r, "card")
    head = ""
    if card is not None:
        title = _get(card, "title", "")
        verdict = _get(card, "verdict", "")
        mitig = _get(card, "mitigation", []) or []
        head = "\n".join(x for x in [f"{title}", f"Verdict: {verdict}"] if x.strip())
        if mitig:
            head += "\nMitigation:\n" + "\n".join(f"- {m}" for m in mitig)
    return collapse_links("\n\n".join(x for x in (head.strip(), md) if x))
