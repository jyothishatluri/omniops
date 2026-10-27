"""ScriptedLLM: a deterministic, offline ``LLMClient`` for every other module's tests.

Retrospective lessons encoded here:
- D27: functionality tests drive real entry points. Only the *model turns* are scripted; the tool
  loop (``drive_tool_loop``) and the structured repair/validate/retry driver (``run_structured``) are
  the production code, so a test exercises the same path production runs.
- 3.8.1: the structured-output canary must test the production path - scripted replies go through
  the same lenient coercion and strict validation, and failures retry with the validation error.

Rules
- ``structured={"HypothesisSet": rule, "verdict": rule}``: keyed by schema class name first, then by
  role. A rule is a static value (dict / pydantic model / raw JSON text / None for an empty reply), a
  callable ``fn(user_text) -> value``, or a list of those consumed one per attempt (the last one
  repeats). No rule -> ``None`` (the caller's deterministic fallback runs).
- ``loops={"decision": [step, ...]}``: one step per model turn, consumed across runs. A step is a
  final text (str), a list of tool-call dicts ``{"name", "arguments", "id"?}``, one tool-call dict,
  ``{"text": ..., "tool_calls": [...]}``, or a callable ``fn(transcript_items) -> step``. When the
  steps run out the model answers ``default_text``.
- ``complete``: a str, a callable ``fn(role, user_text) -> str | dict | LLMResponse``, or a mapping
  role -> (value | ``fn(user_text)``).

Every model turn is recorded in ``calls`` (see ``ScriptedCall``) for assertions.
"""

from __future__ import annotations

import itertools
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from omniops.llm.base import LLMResponse, LoopEvent, LoopResult, Role, T, ToolCall, ToolSpec, Usage
from omniops.llm.meta import ChatTranscript, drive_tool_loop
from omniops.llm.models import OUTPUT_CEILING
from omniops.llm.structured import StructuredTurn, parse_tool_arguments, run_structured

log = logging.getLogger("omniops.llm")


@dataclass
class ScriptedCall:
    kind: Literal["complete", "structured", "loop_turn"]
    role: str
    system: str
    user: str                                  # the user text sent (incl. validation feedback on retries)
    schema_name: str | None = None
    tools: list[str] = field(default_factory=list)          # tool names offered on this turn
    items: list[dict[str, Any]] = field(default_factory=list)  # transcript the turn saw (loop turns)
    response: str = ""
    tool_calls: list[str] = field(default_factory=list)     # tool names the scripted model called


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    return json.dumps(value, default=str)


def _last_user_text(items: list[dict[str, Any]]) -> str:
    for item in reversed(items):
        if item.get("role") == "user":
            content = item.get("content")
            if isinstance(content, list):
                return "".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
            return str(content or "")
    return ""


class ScriptedLLM:
    """Implements ``LLMClient`` (plus ``for_run``) without any network access."""

    def __init__(self, *, structured: Mapping[str, Any] | None = None,
                 loops: Mapping[str, Sequence[Any]] | None = None, complete: Any = None,
                 default_text: str = "(scripted: no more steps)", model: str = "scripted") -> None:
        self.usage = Usage()
        self.llm_calls = 0
        self.escalations: list[dict[str, str]] = []           # parity with RunScopedLLM; never escalates
        self.calls: list[ScriptedCall] = []
        self.model = model
        self.default_text = default_text
        self._structured = dict(structured or {})
        self._loops: dict[str, list[Any]] = {k: list(v) for k, v in (loops or {}).items()}
        self._complete = complete
        self._attempt: dict[str, int] = {}
        self._ids = itertools.count(1)

    def for_run(self, run_id: str | None) -> ScriptedLLM:
        return self

    async def aclose(self) -> None:
        return None

    def calls_for(self, kind: str | None = None, role: str | None = None) -> list[ScriptedCall]:
        return [c for c in self.calls if (kind is None or c.kind == kind) and (role is None or c.role == role)]

    def stats(self) -> dict[str, Any]:
        """Totals keyed like ``RunStats`` fields (same shape as ``RunScopedLLM.stats``)."""
        u = self.usage
        return {"llm_calls": self.llm_calls, "input_tokens": u.input_tokens, "cached_tokens": u.cached_tokens,
                "output_tokens": u.output_tokens, "reasoning_tokens": u.reasoning_tokens,
                "cost_usd": u.cost_usd, "escalations": 0}

    def _record(self, call: ScriptedCall) -> None:
        self.calls.append(call)
        self.llm_calls += 1

    # -- complete ------------------------------------------------------------------------------

    async def complete(self, *, role: Role, system: str, input: list[dict[str, Any]] | str,
                       tools: list[ToolSpec] | None = None, schema: dict[str, Any] | None = None,
                       schema_name: str = "output", effort: str | None = None,
                       max_output_tokens: int | None = None, timeout_s: float | None = None) -> LLMResponse:
        user = input if isinstance(input, str) else _last_user_text(input)
        rule = self._complete
        if isinstance(rule, Mapping):
            value = rule.get(role, self.default_text)
            value = value(user) if callable(value) else value
        elif callable(rule):
            value = rule(role, user)
        else:
            value = self.default_text if rule is None else rule
        resp = value if isinstance(value, LLMResponse) else LLMResponse(
            text=_as_text(value), tool_calls=[], usage=Usage(), model=self.model, stop_reason="completed")
        self._record(ScriptedCall("complete", role, system, user, schema_name=schema_name if schema else None,
                                  tools=[t.name for t in tools or []], response=resp.text))
        return resp

    # -- structured ----------------------------------------------------------------------------

    def _next_value(self, key: str, user: str) -> Any:
        rule = self._structured[key]
        if isinstance(rule, list):
            i = self._attempt.get(key, 0)
            self._attempt[key] = i + 1
            rule = rule[min(i, len(rule) - 1)] if rule else None
        return rule(user) if callable(rule) else rule

    async def structured(self, *, role: Role, system: str, user: str, schema: type[T], effort: str | None = None,
                         max_output_tokens: int | None = None, attempts: int = 3,
                         timeout_s: float | None = None) -> T | None:
        name = schema.__name__
        key = name if name in self._structured else role if role in self._structured else None
        if key is None:
            log.warning("ScriptedLLM: no structured rule for %s/%s: returning None", role, name)
            self._record(ScriptedCall("structured", role, system, user, schema_name=name))
            return None

        async def turn(text: str, budget: int) -> StructuredTurn:
            raw = _as_text(self._next_value(key, text))
            self._record(ScriptedCall("structured", role, system, text, schema_name=name, response=raw))
            return StructuredTurn(raw, "completed", self.model)

        return await run_structured(turn, user=user, schema=schema, attempts=attempts,
                                    max_output_tokens=max_output_tokens or 8_000, ceiling=OUTPUT_CEILING,
                                    label=f"scripted {role}/{name}")

    # -- tool loop -----------------------------------------------------------------------------

    def _tool_call(self, spec: Mapping[str, Any]) -> ToolCall:
        raw = spec.get("arguments", spec.get("args", {}))
        return ToolCall(id=str(spec.get("id") or f"call_{next(self._ids)}"), name=str(spec["name"]),
                        arguments=parse_tool_arguments(raw), raw_arguments=raw if isinstance(raw, str)
                        else json.dumps(raw))

    def _parse_step(self, step: Any) -> tuple[str, list[ToolCall]]:
        if step is None or isinstance(step, str):
            return (self.default_text if step is None else step), []
        if isinstance(step, Mapping) and "name" in step:
            return "", [self._tool_call(step)]
        if isinstance(step, Mapping):
            return str(step.get("text") or ""), [self._tool_call(c) for c in step.get("tool_calls") or []]
        return "", [self._tool_call(c) for c in step]

    async def run_tool_loop(self, *, role: Role, system: str, user: str, tools: list[ToolSpec], deadline: float,
                            max_steps: int = 40, effort: str | None = None,
                            on_event: Callable[[LoopEvent], Awaitable[None] | None] | None = None,
                            before_final: Callable[[str, int], Awaitable[str | None]] | None = None,
                            inject: Callable[[], list[str]] | None = None,
                            stop_check: Callable[[], bool] | None = None) -> LoopResult:
        transcript = ChatTranscript()

        async def turn(wrap_up: bool, timeout_s: float, budget: int) -> LLMResponse:
            queue = self._loops.get(role)
            step = queue.pop(0) if queue else self.default_text
            if callable(step):
                step = step(transcript.items())
            text, calls = self._parse_step(step)
            if wrap_up:                                 # a tool-less turn cannot call tools
                calls, text = [], text or self.default_text
            assistant: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                assistant["tool_calls"] = [{"id": c.id, "type": "function", "function": {
                    "name": c.name, "arguments": c.raw_arguments}} for c in calls]
            self._record(ScriptedCall("loop_turn", role, system, user, tools=[] if wrap_up else [t.name for t in tools],
                                      items=transcript.items(), response=text, tool_calls=[c.name for c in calls]))
            return LLMResponse(text=text, tool_calls=calls, usage=Usage(), model=self.model,
                               output_items=[assistant], stop_reason="completed")

        return await drive_tool_loop(
            turn=turn, transcript=transcript, user=user, tools=tools, deadline=deadline, max_steps=max_steps,
            turn_timeout_s=60.0, max_output_tokens=16_000, ceiling=OUTPUT_CEILING, on_event=on_event,
            before_final=before_final, inject=inject, stop_check=stop_check, label=f"scripted:{role}")
