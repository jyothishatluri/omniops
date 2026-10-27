"""ATIF-v1.7 trajectories for o11y-bench: build, flush, validate, and read back the final answer.

The o11y-bench grader parses ``/logs/agent/*.json`` and judges the *final response*: the last agent
message with text after the last user message, unless that message also calls tools (then there is
no answer); a short closing note (< 200 chars) gets the nearest earlier long agent text prepended
(``grading.models.Transcript.final_answer_indexes``). So:

- the final step is an agent step carrying the answer and no tool calls;
- tool steps carry no message text, so a short answer is never merged with planning text;
- nothing else is written as JSON next to the trajectory (the grader would parse it too);
- custom data lives in ``extra`` only: Harbor's ATIF models reject undeclared fields.

Retrospective lessons encoded (OpsIntelligence):
- 3.18 judge calibration: feed the judge the real tool activity (one agent step per tool call with
  its observation) and clip head + tail with an explicit, sized elision marker.
- 3.18 / 4.8 "a killed job leaves no case file": the file is rewritten atomically after every step,
  so a Harbor timeout still leaves everything done so far.
- 4.1 silent failure: every truncation is marked and sized; warnings (zero tool calls, empty
  answer, ...) travel inside the trajectory instead of disappearing.
- 3.5 links are data: Slack link markup is converted to markdown, never rewritten or invented.
"""

from __future__ import annotations

import html
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "ATIF-v1.7"
CLOSING_NOTE_MAX_CHARS = 200          # grading/models.py: shorter final texts get earlier text prepended
OBSERVATION_MAX_CHARS = 20_000        # per tool result; the judge reads ~400 chars, humans read the rest
_SOURCES = ("system", "user", "agent")
_AGENT_ONLY = ("tool_calls", "metrics", "reasoning_content", "reasoning_effort", "model_name")
# Harbor's ATIF models reject undeclared fields: custom data goes in ``extra`` only.
_ROOT_KEYS = frozenset({"schema_version", "session_id", "trajectory_id", "agent", "steps", "notes", "final_metrics",
                        "continued_trajectory_ref", "subagent_trajectories", "extra"})
_AGENT_KEYS = frozenset({"name", "version", "model_name", "tool_definitions", "extra"})
_STEP_KEYS = frozenset({"step_id", "timestamp", "source", "message", "observation", "extra", "llm_call_count",
                        "is_copied_context", *_AGENT_ONLY})
_FINAL_METRICS_KEYS = frozenset({"total_prompt_tokens", "total_completion_tokens", "total_cached_tokens",
                                 "total_cost_usd", "total_steps", "extra"})
_SLACK_LINK = re.compile(r"<((?:https?|mailto):[^|>\s]+)(?:\|([^>]*))?>")


def clip(text: str, limit: int) -> str:
    """Head and tail with an explicit, sized elision marker (never a silent cut)."""
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    return f"{text[:head]}\n...[{len(text) - limit} characters omitted]...\n{text[-tail:]}"


def slack_to_markdown(text: str) -> str:
    """Slack mrkdwn -> plain markdown for the grader: ``<url|label>`` links become ``[label](url)``
    and Slack's entity escapes (``&lt; &gt; &amp;``) are undone so PromQL/LogQL reads as typed."""
    def link(m: re.Match[str]) -> str:
        url, label = m.group(1), m.group(2)
        return f"[{label}]({url})" if label and label != url else url

    return html.unescape(_SLACK_LINK.sub(link, text)).strip()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


@dataclass
class ToolStep:
    """One tool call and its observation, as one ATIF agent step."""

    call_id: str
    name: str
    arguments: dict[str, Any]
    output: str
    is_error: bool = False
    ts: float = field(default_factory=time.time)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Totals:
    prompt_tokens: int = 0            # includes cached tokens (ATIF convention)
    completion_tokens: int = 0        # includes reasoning tokens
    cached_tokens: int = 0
    cost_usd: float = 0.0


class TrajectoryBuilder:
    """Accumulates the run and rewrites ``path`` atomically on every ``flush``.

    Steps are numbered when serialised, so tool steps recorded live can be replaced by the run's
    authoritative tool events at the end without renumbering by hand.
    """

    def __init__(self, path: Path | None, *, agent_name: str, agent_version: str, model_name: str,
                 session_id: str | None = None) -> None:
        self.path = path
        self.agent_name = agent_name
        self.agent_version = agent_version
        self.model_name = model_name
        self.session_id = session_id or str(uuid.uuid4())
        self.trajectory_id = str(uuid.uuid4())
        self.started = time.time()
        self.system_message = ""
        self.user_message = ""
        self.tool_steps: list[ToolStep] = []
        self.user_events: list[tuple[float, str]] = []   # e.g. the simulated user's write approvals
        self.final_answer: str | None = None
        self.final_ts: float | None = None
        self.tool_definitions: list[dict[str, Any]] = []
        self.totals = Totals()
        self.metrics_extra: dict[str, Any] = {}
        self.extra: dict[str, Any] = {}
        self.warnings: list[str] = []

    # -- recording ----------------------------------------------------------------------------

    def add_tool_step(self, step: ToolStep) -> None:
        self.tool_steps.append(step)
        self.flush()

    def replace_tool_steps(self, steps: list[ToolStep]) -> None:
        self.tool_steps = list(steps)

    def add_user_event(self, message: str, ts: float | None = None) -> None:
        self.user_events.append((ts or time.time(), message))

    def add_tool_definitions(self, defs: list[dict[str, Any]]) -> None:
        known = {d.get("name") for d in self.tool_definitions}
        self.tool_definitions += [d for d in defs if d.get("name") not in known]

    def set_final(self, answer: str) -> None:
        self.final_answer = answer
        self.final_ts = time.time()

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    # -- serialisation ------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        steps: list[dict[str, Any]] = []

        def add(source: str, message: str, ts: float, **fields: Any) -> None:
            step: dict[str, Any] = {"step_id": len(steps) + 1, "timestamp": _iso(ts), "source": source,
                                    "message": message}
            step.update({k: v for k, v in fields.items() if v})
            steps.append(step)

        add("system", self.system_message, self.started)
        if self.user_message:
            add("user", self.user_message, self.started)
        for t in self.tool_steps:
            add("agent", "", t.ts,
                tool_calls=[{"tool_call_id": t.call_id, "function_name": t.name,
                             "arguments": _json_safe(t.arguments)}],
                observation={"results": [{"source_call_id": t.call_id,
                                          "content": clip(t.output, OBSERVATION_MAX_CHARS),
                                          **({"extra": {"is_error": True}} if t.is_error else {})}]},
                extra=_json_safe(t.extra))
        for ts, message in self.user_events:
            add("user", message, ts)
        if self.final_answer is not None:
            add("agent", self.final_answer, self.final_ts or time.time())
        return {
            "schema_version": SCHEMA_VERSION,
            "session_id": self.session_id,
            "trajectory_id": self.trajectory_id,
            "agent": {"name": self.agent_name, "version": self.agent_version, "model_name": self.model_name,
                      "tool_definitions": self.tool_definitions},
            "steps": steps,
            "final_metrics": {
                "total_prompt_tokens": self.totals.prompt_tokens,
                "total_completion_tokens": self.totals.completion_tokens,
                "total_cached_tokens": self.totals.cached_tokens,
                "total_cost_usd": round(self.totals.cost_usd, 6),
                "total_steps": len(steps),
                "extra": {"total_tool_calls": len(self.tool_steps),
                          "elapsed_seconds": round(time.time() - self.started, 1), **self.metrics_extra},
            },
            "extra": _json_safe({**self.extra, "warnings": self.warnings}),
        }

    def flush(self) -> None:
        """Atomic rewrite (temp file + rename) so a kill mid-write never leaves broken JSON."""
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False))
        os.replace(tmp, self.path)


# ---------------------------------------------------------------------------------------------
# Reading back: the grader's final-answer rule and structural validation
# ---------------------------------------------------------------------------------------------


def _messages(data: dict[str, Any]) -> list[tuple[str, str | None, bool]]:
    """(role, content, has_tool_calls) in the order o11y-bench's transcript parser produces."""
    out: list[tuple[str, str | None, bool]] = []
    for step in data.get("steps", []):
        source = step.get("source", "")
        text = step.get("message", "")
        if source == "user":
            out.append(("user", text or "", False))
        elif source in ("agent", "system"):
            content = text if text and text != "(tool use)" else None
            out.append(("assistant" if source == "agent" else "system", content, bool(step.get("tool_calls"))))
            for _ in (step.get("observation") or {}).get("results", []):
                out.append(("tool", None, False))
    return out


def final_answer(data: dict[str, Any]) -> str:
    """The text o11y-bench's judge grades as the final response (``Transcript.final_answer``)."""
    msgs = _messages(data)
    last_user = max((i for i, m in enumerate(msgs) if m[0] == "user"), default=-1)
    texts = [i for i, (role, content, _) in enumerate(msgs)
             if i > last_user and role == "assistant" and isinstance(content, str) and content.strip()]
    if not texts or msgs[texts[-1]][2]:
        return ""
    final = texts[-1]
    chosen = [final]
    if len(msgs[final][1] or "") < CLOSING_NOTE_MAX_CHARS:
        earlier = next((i for i in reversed(texts[:-1]) if len(msgs[i][1] or "") >= CLOSING_NOTE_MAX_CHARS), None)
        if earlier is not None:
            chosen = [earlier, final]
    return "\n\n".join((msgs[i][1] or "").strip() for i in chosen)


def validate_trajectory(data: dict[str, Any]) -> list[str]:
    """Structural ATIF-v1.7 checks (schema, sequential ids, agent-only fields, call references,
    timestamps). Returns problems; empty means valid."""
    problems: list[str] = []
    if data.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version must be {SCHEMA_VERSION}, got {data.get('schema_version')!r}")
    problems += _undeclared("trajectory", data, _ROOT_KEYS)
    agent = data.get("agent")
    if not isinstance(agent, dict) or not agent.get("name") or not agent.get("version"):
        problems.append("agent.name and agent.version are required")
    else:
        problems += _undeclared("agent", agent, _AGENT_KEYS)
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        return problems + ["steps must be a non-empty list"]
    for n, step in enumerate(steps, start=1):
        where = f"step {n}"
        problems += _undeclared(where, step, _STEP_KEYS)
        if step.get("step_id") != n:
            problems.append(f"{where}: step_id {step.get('step_id')!r} is not sequential (expected {n})")
        source = step.get("source")
        if source not in _SOURCES:
            problems.append(f"{where}: source {source!r} not in {_SOURCES}")
        if not isinstance(step.get("message"), str | list):
            problems.append(f"{where}: message must be a string or a list of content parts")
        if source != "agent":
            problems += [f"{where}: {k} is only allowed on agent steps" for k in _AGENT_ONLY if k in step]
        if "timestamp" in step:
            try:
                datetime.fromisoformat(str(step["timestamp"]))
            except ValueError:
                problems.append(f"{where}: timestamp {step['timestamp']!r} is not ISO 8601")
        call_ids = set()
        for call in step.get("tool_calls") or []:
            if not call.get("tool_call_id") or not call.get("function_name"):
                problems.append(f"{where}: tool call needs tool_call_id and function_name")
            if not isinstance(call.get("arguments"), dict):
                problems.append(f"{where}: tool call arguments must be a JSON object")
            call_ids.add(call.get("tool_call_id"))
        for result in (step.get("observation") or {}).get("results", []):
            ref = result.get("source_call_id")
            if ref is not None and ref not in call_ids:
                problems.append(f"{where}: observation refers to unknown tool call {ref!r}")
    fm = data.get("final_metrics")
    if fm is not None:
        problems += _undeclared("final_metrics", fm, _FINAL_METRICS_KEYS)
        if fm.get("total_steps") not in (None, len(steps)):
            problems.append(f"final_metrics.total_steps {fm.get('total_steps')} != {len(steps)} steps")
        cached, prompt = fm.get("total_cached_tokens") or 0, fm.get("total_prompt_tokens") or 0
        if cached > prompt:
            problems.append(f"final_metrics: cached tokens ({cached}) must be a subset of prompt tokens ({prompt})")
    return problems


def _undeclared(where: str, obj: dict[str, Any], allowed: frozenset[str]) -> list[str]:
    return [f"{where}: undeclared field {k!r} (put custom data in extra)" for k in sorted(set(obj) - allowed)]
