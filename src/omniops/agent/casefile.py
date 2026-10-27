"""Per-run case files: one bounded, redacted JSON record of everything a run did, for audits and benchmarks.

Retrospective lessons encoded here:
- 3.18 "per-run case files with every tool call's arguments and raw response": each tool call keeps
  its arguments, its output, the evidence it produced and its own (already redacted) HTTP requests;
  the engines' full request logs are attached too, so an audit can see exactly what was asked of each API.
- 3.18 production audits: run records near 900 KB were truncated by the log agent and only 3 of 25
  parsed. Case files are size-bounded by code: progressively tighter caps, and every truncation is
  marked and sized (4.1 "every truncation must be marked and sized").
- 3.18 "a killed job leaves no case file": callers write one on every exit path (salvage, timeout,
  stop, error, cancellation); the write is atomic so a crash never leaves half a file.
- Never persist secrets: secret-looking keys are masked and every string goes through the redactor.
- Benchmarks read RunResult-shaped JSON (run_id, alert.source_ts, tool_events, staged_writes), so the
  layout mirrors ``RunResult`` field names (3.8.2: one set of names for producer and consumer).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from omniops.slack.render import redact

if TYPE_CHECKING:
    from omniops.agent.context import RunContext
    from omniops.agent.types import EvidenceRecord, RunResult

log = logging.getLogger("omniops.casefile")

CASE_SCHEMA = "omniops-case/1"
MAX_CASE_BYTES = 3_000_000
# Telemetry other modules leave in ctx.extra that an audit needs (3.18: rates, not anecdotes).
EXTRA_KEYS = ("triage", "specialists_skipped", "shadow_verdicts", "shadow_warnings", "open_gaps", "finish",
              "report_validation", "insufficient_data", "deepening", "gap_fill", "memory_refs", "llm_escalations")

_SECRET_KEY = re.compile(r"(?i)^(?:.*[_-])?(?:token|secret|password|passwd|api[_-]?key|apikey|authorization|auth|"
                         r"cookie|credentials?|private[_-]?key|signature|session)$")
_TOKENISH = re.compile(r"\b(?:xox[abposr]-[A-Za-z0-9-]+|xapp-[A-Za-z0-9-]+|glsa_[A-Za-z0-9_]+|"
                       r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})")
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]")


@dataclass(frozen=True)
class Limits:
    """Caps for one serialisation pass; tighter passes run until the file fits ``MAX_CASE_BYTES``."""

    output_chars: int
    summary_chars: int
    rendered_chars: int
    text_chars: int
    list_items: int
    events: int
    evidence: int
    requests: int
    answer_chars: int


PASSES = (
    Limits(6000, 3000, 4000, 8000, 200, 1000, 500, 500, 80_000),
    Limits(1500, 1000, 0, 2000, 60, 400, 200, 150, 30_000),
    Limits(300, 300, 0, 600, 20, 150, 80, 40, 12_000),
)


# ---------------------------------------------------------------------------------------------
# Scrubbing and bounding
# ---------------------------------------------------------------------------------------------


def clip(text: str, limit: int) -> str:
    """Head of the text with an explicit, sized marker."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[clipped: {len(text) - limit} of {len(text)} chars]"


def scrub_text(text: str) -> str:
    return _TOKENISH.sub("[redacted]", redact(text))


def jsonable(obj: Any) -> Any:
    """Plain JSON data from models, dataclasses, datetimes and sets; anything else becomes its str()."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Mapping):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        items = sorted(obj, key=str) if isinstance(obj, (set, frozenset)) else obj
        return [jsonable(v) for v in items]
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: jsonable(getattr(obj, f.name)) for f in fields(obj)}
    return str(obj)


def bound(obj: Any, lim: Limits, *, secrets: bool = True) -> Any:
    """Redact secret-looking keys and strings, clip long strings and long lists (with markers)."""
    obj = jsonable(obj)
    if isinstance(obj, str):
        return clip(scrub_text(obj), lim.text_chars)
    if isinstance(obj, dict):
        return {k: "[redacted]" if secrets and _SECRET_KEY.match(k) else bound(v, lim, secrets=secrets)
                for k, v in obj.items()}
    if isinstance(obj, list):
        out = [bound(v, lim, secrets=secrets) for v in obj[: lim.list_items]]
        if len(obj) > lim.list_items:
            out.append(f"[clipped: {len(obj) - lim.list_items} of {len(obj)} items]")
        return out
    return obj


# ---------------------------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------------------------


def _anchor(ctx: RunContext | None) -> dict[str, Any] | None:
    if ctx is None:
        return None
    a = ctx.anchor
    return jsonable({"source": a.source, "now": a.now, "window_start": a.window_start, "window_end": a.window_end,
                     "trigger": getattr(a, "trigger", None), "display_tz": getattr(a, "display_tz", None),
                     "notes": list(a.notes)})


def _evidence(rec: EvidenceRecord, lim: Limits) -> dict[str, Any]:
    # judge_view repeats ``rendered``; requests sit with the tool call and in the run's request log
    data = {k: v for k, v in rec.data.items() if k not in ("judge_view", "requests")}
    out: dict[str, Any] = {
        "id": rec.id, "kind": rec.kind, "agent": rec.agent, "title": clip(rec.title, 300),
        "summary": clip(scrub_text(rec.summary), lim.summary_chars), "sql": rec.sql, "tables": list(rec.tables),
        "row_count": rec.row_count, "empty_verdict": rec.empty_verdict, "error": rec.error,
        "links": rec.links[:20], "window": list(rec.window) if rec.window else None,
        "confidence": rec.confidence, "meaningful": rec.meaningful, "pinned": rec.pinned, "created": rec.created,
        "data": bound(data, lim),
    }
    if lim.rendered_chars and rec.rendered:
        out["rendered"] = clip(scrub_text(rec.rendered), lim.rendered_chars)
    return out


def _tool_events(result: RunResult, board: dict[str, EvidenceRecord], lim: Limits) -> list[dict[str, Any]]:
    out = []
    for e in result.tool_events[: lim.events]:
        rec = board.get(e.evidence_id or "")
        out.append({
            "agent": e.agent, "name": e.name, "arguments": bound(e.arguments, lim),
            "output": clip(scrub_text(e.output or ""), lim.output_chars), "is_error": e.is_error,
            "evidence_id": e.evidence_id, "elapsed_ms": e.elapsed_ms, "ts": e.ts,
            "result_summary": clip(scrub_text(rec.summary), min(lim.summary_chars, 800)) if rec else None,
            "requests": bound(rec.data.get("requests") or [], lim) if rec else [],
        })
    return out


def _requests(ctx: RunContext | None, lim: Limits) -> tuple[list[Any], int]:
    """Every engine's request log (the engine already masks secret params; this masks again)."""
    if ctx is None:
        return [], 0
    logs: list[Any] = []
    for eng in getattr(ctx, "_engines", None) or [ctx.engine]:
        logs += list(getattr(eng, "request_log", None) or [])
    return [bound(r, lim) for r in logs[: lim.requests]], len(logs)


def _writes(result: RunResult, lim: Limits) -> list[Any]:
    """Staged writes without the live engine object Slack uses for approval."""
    return [bound({k: v for k, v in w.items() if k != "engine"}, lim) for w in result.staged_writes]


def build_case(result: RunResult, ctx: RunContext | None = None, *, extra: Mapping[str, Any] | None = None,
               lim: Limits = PASSES[0]) -> dict[str, Any]:
    """The case record for one run at one level of caps."""
    board = {r.id: r for r in result.evidence}
    requests, n_requests = _requests(ctx, lim)
    alert = result.alert or (ctx.alert if ctx is not None else None)
    telemetry: dict[str, Any] = {}
    if ctx is not None:
        telemetry = {k: bound(ctx.extra[k], lim) for k in EXTRA_KEYS if k in ctx.extra}
    telemetry.update({k: bound(v, lim) for k, v in (extra or {}).items()})
    evidence = result.evidence[: lim.evidence]
    case: dict[str, Any] = {
        "schema": CASE_SCHEMA,
        "run_id": result.run_id,
        "kind": result.kind,
        "status": result.status,
        "written_at": time.time(),
        "source_ts": alert.source_ts if alert is not None else None,
        "channel": getattr(ctx.status, "channel", None) if ctx is not None else None,
        "thread_ts": getattr(ctx.status, "thread_ts", None) if ctx is not None else None,
        "alert": bound(alert, lim) if alert is not None else None,
        "question": clip(scrub_text(ctx.question), lim.text_chars) if ctx is not None and ctx.question else None,
        "anchor": _anchor(ctx),
        "answer_markdown": clip(scrub_text(result.answer_markdown), lim.answer_chars),
        "card": bound(result.card, lim) if result.card is not None else None,
        "tool_events": _tool_events(result, board, lim),
        "tool_events_total": len(result.tool_events),
        "requests": requests,
        "requests_total": n_requests,
        "evidence": [_evidence(r, lim) for r in evidence],
        "evidence_total": len(result.evidence),
        "hypotheses": bound(result.hypotheses, lim),
        "specialist_verdicts": bound(result.specialist_verdicts, lim),
        "staged_writes": _writes(result, lim),
        "stats": jsonable(result.stats.as_dict()),
        "warnings": [clip(scrub_text(w), 2000) for w in result.warnings],
        "telemetry": telemetry,
    }
    return case


def case_json(result: RunResult, ctx: RunContext | None = None, *, extra: Mapping[str, Any] | None = None,
              max_bytes: int = MAX_CASE_BYTES) -> str:
    """The JSON text, tightened pass by pass until it fits ``max_bytes``; the cut is recorded in the file."""
    first_size = 0
    text = ""
    for level, lim in enumerate(PASSES):
        case = build_case(result, ctx, extra=extra, lim=lim)
        if level:
            case["truncation"] = {"level": level, "full_bytes": first_size, "limit_bytes": max_bytes,
                                  "note": "outputs, summaries and lists were clipped to fit; markers show sizes"}
        text = json.dumps(case, default=str, ensure_ascii=False)
        size = len(text.encode("utf-8"))
        first_size = first_size or size
        if size <= max_bytes:
            return text
    log.warning("case %s is %d bytes even at the tightest caps", result.run_id, len(text.encode("utf-8")))
    return text


def case_path(run_id: str, case_dir: str | os.PathLike[str]) -> Path:
    return Path(case_dir) / f"{_SAFE_ID.sub('_', run_id) or 'run'}.json"


def write_case(result: RunResult, ctx: RunContext | None = None, *, case_dir: str | os.PathLike[str] | None = None,
               extra: Mapping[str, Any] | None = None, max_bytes: int = MAX_CASE_BYTES) -> Path | None:
    """Write ``<case_dir>/<run_id>.json`` atomically. Never raises: a failed write is logged and returns None."""
    if case_dir is None:
        case_dir = ctx.deps.settings.case_dir if ctx is not None else ".omniops/cases"
    path = case_path(result.run_id, case_dir)
    try:
        text = case_json(result, ctx, extra=extra, max_bytes=max_bytes)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001 - a case file must never fail the run it records
        log.warning("[%s] case file not written: %s: %s", result.run_id, type(exc).__name__, exc)
        return None
    return path
