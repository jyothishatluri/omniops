"""Provider-neutral LLM interface used by every agent module.

Default provider: Meta Model API (https://api.meta.ai/v1, OpenAI-compatible) via the Responses API,
which replays reasoning across tool turns (``include: ["reasoning.encrypted_content"]``). Facts that
shape this interface:
- ``tool_choice`` only supports "auto": structured output uses ``text.format`` JSON schemas, never a
  forced tool call; tool loops must handle both a tool call and a plain-text reply.
- ``max_output_tokens`` covers reasoning + visible output: size it per call.
- Prefix caching is automatic: static system prompt first, dynamic content last, messages only grow,
  stable ``prompt_cache_key`` per role.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

Role = Literal[
    "decision", "evidence", "hypotheses", "verdict", "deepening", "report", "chat", "specialist",
    "formatter", "triage", "alert_parse", "memory", "judge",
]


@dataclass
class ToolOutput:
    content: str
    is_error: bool = False
    evidence_id: str | None = None
    stop: bool = False                    # the loop ends after this tool result (e.g. finish accepted)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolSpec:
    name: str                             # ^[a-zA-Z0-9_-]+$
    description: str
    parameters: dict[str, Any]            # JSON schema (root type object)
    handler: Callable[[dict[str, Any]], Awaitable[ToolOutput]]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""


@dataclass
class Usage:
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.cached_tokens += other.cached_tokens
        self.output_tokens += other.output_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.cost_usd += other.cost_usd


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    usage: Usage
    model: str
    output_items: list[dict[str, Any]] = field(default_factory=list)   # replayable items (Responses API)
    stop_reason: str | None = None        # completed | max_output_tokens | ...
    elapsed_s: float = 0.0


@dataclass
class LoopEvent:
    step: int
    kind: Literal["assistant_text", "tool_call", "tool_result", "pushback", "error", "final"]
    payload: dict[str, Any]
    ts: float = field(default_factory=time.time)


@dataclass
class LoopResult:
    final_text: str
    steps: int
    stopped_by: Literal["final_text", "tool_stop", "deadline", "max_steps", "stop_signal", "error"]
    usage: Usage
    events: list[LoopEvent]


class LLMClient(Protocol):
    """What agent code may call. Implementations: MetaResponsesClient, ChatCompletionsClient, ScriptedLLM,
    and the per-run view ``RunScopedLLM`` that ``for_run`` returns."""

    usage: Usage

    def for_run(self, run_id: str | None) -> LLMClient:
        """A view for one run: escalation stays sticky within the run and usage is counted per run (3.13).
        Share one view across a run's agents; calling it again starts a fresh scope."""
        ...

    def stats(self) -> dict[str, Any]:
        """Usage totals keyed like ``RunStats`` (llm_calls, input_tokens, cached_tokens, output_tokens,
        reasoning_tokens, cost_usd, escalations)."""
        ...

    async def complete(
        self,
        *,
        role: Role,
        system: str,
        input: list[dict[str, Any]] | str,
        tools: list[ToolSpec] | None = None,
        schema: dict[str, Any] | None = None,
        schema_name: str = "output",
        effort: str | None = None,
        max_output_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> LLMResponse: ...

    async def structured(
        self,
        *,
        role: Role,
        system: str,
        user: str,
        schema: type[T],
        effort: str | None = None,
        max_output_tokens: int | None = None,
        attempts: int = 3,
        timeout_s: float | None = None,
    ) -> T | None:
        """Schema-bound output. Lenient in (coerce shape variants), strict out (pydantic). Retries with
        the exact validation error appended; returns None after ``attempts`` so callers fall back
        deterministically. ``timeout_s`` is the budget for the whole call, every attempt included
        (default: the role's outer wait); when it is spent the call returns None."""
        ...

    async def run_tool_loop(
        self,
        *,
        role: Role,
        system: str,
        user: str,
        tools: list[ToolSpec],
        deadline: float,
        max_steps: int = 40,
        effort: str | None = None,
        on_event: Callable[[LoopEvent], Awaitable[None] | None] | None = None,
        before_final: Callable[[str, int], Awaitable[str | None]] | None = None,
        inject: Callable[[], list[str]] | None = None,
        stop_check: Callable[[], bool] | None = None,
    ) -> LoopResult:
        """Native tool calling loop.

        - ``before_final(text, step)``: called when the model replies without tool calls; return a
          pushback message (readiness/depth gate, self-executable gaps) to continue the loop, or None
          to accept the text as final.
        - ``inject()``: messages to append before the next turn (e.g. specialist evidence that just
          landed on the evidence board).
        - Tool handlers' exceptions become error tool results with the exception's ``lesson``.
        - ``final_text`` is answer text only (never the commentary sent with a tool call); it is empty
          when the loop ended without an answer. An answer cut off at the output limit is re-asked.
        - At the deadline (or after a failed turn) one tool-less "answer now" turn is sent; it ends at
          most ``meta.WRAP_UP_OVERRUN_S`` after ``deadline``, so an outer wait must allow more than that.
        """
        ...
