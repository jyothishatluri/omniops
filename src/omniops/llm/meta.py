"""Meta Model API clients (Responses + Chat Completions) and the native tool loop every agent uses.

Retrospective lessons encoded here:
- D2: native tool calling with the control (validation, budgets, audit) in a wrapper around it:
  argument repair, unknown-tool and bad-argument error results, exceptions turned into error tool
  results that carry the exception's ``lesson``, ``before_final`` gates, deadlines and step caps.
- D16 / 4.3: the Responses API with *stateless replay*. The full input item list (messages,
  reasoning items with ``encrypted_content``, function calls, function call outputs) is kept and
  only appended to; text is joined across ALL output blocks (empty first blocks happen); content
  is never stringified from "the first block". Replay follows the provider's ordering rules: a
  reasoning item is followed by an assistant message or call, text before a call keeps ``phase``.
- 3.8.5 / 4.3: ``max_output_tokens`` includes reasoning, so a reply that stops on it is logged
  loudly; the loop raises the budget for later turns and asks again instead of accepting a cut-off
  answer, and tool calls whose arguments were cut off are answered with an error, never run with
  "repaired" arguments. Structured calls retry once with a larger budget. Ceilings differ per
  deployment: when a rejection states one, it is remembered and the call is retried once at it (a
  context-window rejection depends on the input size, so it only resizes that one request).
- 3.13: nested timeouts (HTTP timeout > outer wait, so nothing is silently re-sent), retries for
  429/5xx/529 honouring ``Retry-After`` with jitter, and a sticky per-run escalation ladder: two
  same-model retries on exception/timeout/validator rejection, then the next tier for the rest of
  the run (flapping models re-bills cached prefixes); every escalation is logged and counted. The
  failure streak belongs to one logical call, so a concurrent call's success never resets it. Long
  roles (the report) stream, with a stall guard that retries a stream gone quiet once.
- 3.14: prefix caching - static instructions first, messages only grow, a stable
  ``prompt_cache_key`` per role and 24h retention on the Responses API.
- 3.9.1: honest budgets - usage including cached and reasoning tokens, cost per model, and a loud
  warning when a call's real input exceeds its role's input budget.
- 4.1 (loud zeros): zero tool calls, empty answers and cost-less models are logged as warnings.
"""

from __future__ import annotations

import asyncio
import email.utils
import inspect
import json
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx

from omniops.llm.base import (
    LLMResponse,
    LoopEvent,
    LoopResult,
    Role,
    T,
    ToolCall,
    ToolOutput,
    ToolSpec,
    Usage,
)
from omniops.llm.models import ModelConfig, cost, http_timeout_for, normalize_effort
from omniops.llm.structured import (
    ARG_PARSE_ERROR_KEY,
    StructuredTurn,
    fit_arguments,
    parse_tool_arguments,
    run_structured,
    to_json_schema,
)
from omniops.llm.structured import schema_name as safe_schema_name

log = logging.getLogger("omniops.llm")

TIME_UP = ("Time is up: answer now with what you have. Do not call any more tools. Give your best-supported "
           "conclusion, cite the evidence you gathered, and disclose the gaps you could not close.")
STEP_LIMIT = ("Step limit reached: answer now with what you have. Do not call any more tools. Give your "
              "best-supported conclusion, cite the evidence you gathered, and disclose the gaps.")
EMPTY_REPLY = ("Your last reply was empty{why}. Continue: call a tool if you still need evidence, otherwise "
               "write your answer now, concisely.")
CUT_OFF = ("Your last reply was cut off at the output limit (reasoning counts against it), so it is incomplete. "
           "Write your answer again, complete and more concisely.")
CUT_OFF_ARGUMENTS = "they were cut off at the output limit, so the tool was not run"
NO_TEXT = "(no visible reply)"
WRAP_UP_MIN_S = 45.0          # the time-up turn may run this long even when the deadline is closer ...
WRAP_UP_OVERRUN_S = 25.0      # ... but it ends at most this long after the deadline: callers' outer waits allow more
EMPTY_PUSHBACKS = 2
CUT_OFF_PUSHBACKS = 2
STRUCTURED_RETRY_MIN_S = 5.0  # a structured re-ask starts only with at least this much of its budget left
CONTEXT_MARGIN_TOKENS = 64


# ---------------------------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------------------------


class LLMError(RuntimeError):
    """A failed model call. Messages never contain credentials; ``lesson`` says what it means."""

    def __init__(self, message: str, *, status: int | None = None, kind: str | None = None,
                 param: str | None = None, retryable: bool = False, model_failure: bool = True,
                 same_model: bool = True, retry_after: float | None = None, lesson: str = "",
                 detail: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.kind = kind
        self.param = param
        self.detail = detail                  # the provider's own error message
        self.retryable = retryable            # the HTTP layer may re-send the same request
        self.model_failure = model_failure    # counts toward the escalation ladder
        self.same_model = same_model          # retrying the same model can help
        self.retry_after = retry_after
        self.lesson = lesson


class LLMTimeout(LLMError):
    """The outer wait expired. Never re-sent by the transport: the request would be paid for twice (3.13).

    Only a tool-loop turn is asked again, explicitly and logged, while its loop deadline allows (the
    escalation ladder's "retry on timeout").
    """


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _error_from_response(r: httpx.Response, *, role: str, model: str) -> LLMError:
    try:
        body = r.json()
    except ValueError:
        body = {}
    err = body.get("error") if isinstance(body, dict) else None
    err = err if isinstance(err, dict) else {}
    status = r.status_code
    msg = " ".join(str(err.get("message") or r.text or r.reason_phrase).split())[:400]
    text = f"{role} on {model}: HTTP {status} {err.get('type') or ''}: {msg}".replace("  ", " ")
    common: dict[str, Any] = {"status": status, "kind": err.get("type"), "param": err.get("param"),
                              "retry_after": _retry_after(r.headers.get("retry-after")), "detail": msg}
    if status in (429, 500, 502, 503, 529):
        return LLMError(text, retryable=True, lesson="provider rate limit or overload: back off and retry; "
                        "not a prompt or schema problem", **common)
    if status == 504:
        return LLMError(text, same_model=False, lesson="the non-streaming call ran too long: send less work per "
                        "call; never re-sent unchanged", **common)
    if status in (403, 404):
        return LLMError(text, same_model=False, lesson="this model is not available to this key or endpoint: "
                        "use the next tier", **common)
    return LLMError(text, model_failure=False, lesson="the request is invalid (parameters, conversation order or "
                    "input size): fix the request; retrying will not help", **common)


_TOKEN_PARAM = re.compile(r"max_(?:output|completion)_tokens|max_tokens|output tokens", re.IGNORECASE)
_NUM = r"(\d[\d,]{2,})"
_LIMIT_BEFORE = re.compile(r"(?:<=|less than or equal to|at most|no more than|cannot exceed|must not exceed|"
                           r"maximum(?: allowed| value)?(?: is| of)?|limit(?: is| of)?)\s*:?\s*" + _NUM, re.IGNORECASE)
_LIMIT_AFTER = re.compile(_NUM + r"[^\d.;]{0,40}?(?:is|which is) the (?:maximum|limit)", re.IGNORECASE)
_CONTEXT_ERROR = re.compile(r"context (?:length|window)", re.IGNORECASE)
_CONTEXT_SIZE = re.compile(r"context (?:length|window)(?: is| of)?\s*:?\s*" + _NUM, re.IGNORECASE)
_INPUT_SIZE = re.compile(_NUM + r"\s*(?:input tokens|prompt tokens|tokens? in the messages|in the messages)",
                         re.IGNORECASE)


def _ints(matches: list[Any]) -> list[int]:
    flat = [m for g in matches for m in (g if isinstance(g, tuple) else (g,)) if m]
    return [int(n.replace(",", "")) for n in flat]


def reported_ceiling(err: LLMError, requested: int) -> int | None:
    """The output ceiling a rejection states for this deployment, if it is below ``requested``.

    Output ceilings differ per deployment and the error message states them (4.3); the answer is to
    retry once at that ceiling, not to fail the call (3.8.5). Only a number the message ties to the
    limit counts, and a context-window rejection is not an output ceiling: its numbers are the input
    size and the window, which depend on the request (see ``context_room``).
    """
    if err.status != 400 or not (_TOKEN_PARAM.search(err.param or "") or _TOKEN_PARAM.search(err.detail)):
        return None
    if _CONTEXT_ERROR.search(err.detail):
        return None
    numbers = _ints(_LIMIT_BEFORE.findall(err.detail) + _LIMIT_AFTER.findall(err.detail))
    below = [n for n in numbers if 256 <= n < requested]
    return max(below) if below else None


def context_room(err: LLMError, requested: int) -> int | None:
    """Output tokens a context-window rejection leaves for THIS request (window - input - margin).

    Never remembered as the model's ceiling: the next request's input differs.
    """
    if err.status != 400 or not _CONTEXT_ERROR.search(err.detail):
        return None
    window, used = _ints(_CONTEXT_SIZE.findall(err.detail)), _ints(_INPUT_SIZE.findall(err.detail))
    if not window or not used:
        return None
    room = window[0] - used[0] - CONTEXT_MARGIN_TOKENS
    return room if 256 <= room < requested else None


# ---------------------------------------------------------------------------------------------
# Transcripts: append-only conversations in each endpoint's native item format
# ---------------------------------------------------------------------------------------------


def _tool_text(out: ToolOutput) -> str:
    text = out.content if out.content.strip() else "(the tool returned no output)"
    if out.is_error and not text.lstrip().upper().startswith("ERROR"):
        text = f"ERROR: {text}"
    return text


class Transcript:
    """Messages only grow (3.14); the model that produced the last turn is remembered."""

    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []
        self.model: str | None = None

    def items(self) -> list[dict[str, Any]]:
        return list(self._items)

    def add_raw(self, items: list[dict[str, Any]]) -> None:
        self._items.extend(items)

    def add_response(self, resp: LLMResponse) -> None:
        self._items.extend(resp.output_items)

    def add_user(self, text: str) -> None:
        raise NotImplementedError

    def add_tool_results(self, calls: list[ToolCall], outputs: list[ToolOutput]) -> None:
        raise NotImplementedError

    def drop_reasoning(self) -> None:
        """Called when the model changes mid-conversation (escalation)."""


class ResponsesTranscript(Transcript):
    def add_user(self, text: str) -> None:
        # A reasoning item must be followed by an assistant message or function call before a user turn.
        if self._items and self._items[-1].get("type") == "reasoning":
            self._items.append({"type": "message", "role": "assistant",
                                "content": [{"type": "output_text", "text": NO_TEXT}]})
        self._items.append({"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]})

    def add_tool_results(self, calls: list[ToolCall], outputs: list[ToolOutput]) -> None:
        for call, out in zip(calls, outputs, strict=True):
            self._items.append({"type": "function_call_output", "call_id": call.id, "output": _tool_text(out)})

    def drop_reasoning(self) -> None:
        # Encrypted reasoning belongs to the model that produced it.
        self._items = [i for i in self._items if i.get("type") != "reasoning"]


class ChatTranscript(Transcript):
    def add_user(self, text: str) -> None:
        self._items.append({"role": "user", "content": text})

    def add_tool_results(self, calls: list[ToolCall], outputs: list[ToolOutput]) -> None:
        for call, out in zip(calls, outputs, strict=True):
            self._items.append({"role": "tool", "tool_call_id": call.id, "content": _tool_text(out)})


# ---------------------------------------------------------------------------------------------
# HTTP clients
# ---------------------------------------------------------------------------------------------


def _usage(input_tokens: Any, cached: Any, output_tokens: Any, reasoning: Any) -> Usage:
    return Usage(input_tokens=int(input_tokens or 0), cached_tokens=int(cached or 0),
                 output_tokens=int(output_tokens or 0), reasoning_tokens=int(reasoning or 0))


def _raw_arguments(raw: Any) -> str:
    return raw if isinstance(raw, str) else json.dumps(raw or {}, ensure_ascii=False)


def _is_complete_json(raw: Any) -> bool:
    if isinstance(raw, dict) or raw is None or (isinstance(raw, str) and not raw.strip()):
        return True
    try:
        json.loads(raw)
    except (TypeError, ValueError):
        return False
    return True


def _call_arguments(raw: Any, *, cut_off: bool) -> dict[str, Any]:
    """Lenient arguments, except that arguments cut off at the output limit are never repaired (3.8.5).

    Truncation recovery would run the tool with part of what the model meant (a dropped hypothesis);
    an error tool result makes the model call it again with the larger budget instead.
    """
    if cut_off:
        return {ARG_PARSE_ERROR_KEY: CUT_OFF_ARGUMENTS}
    return parse_tool_arguments(raw)


def _json_body(r: httpx.Response, *, role: str, model: str) -> dict[str, Any] | LLMError:
    try:
        data = r.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        return data
    return LLMError(f"{role} on {model}: malformed response body", retryable=True,
                    lesson="the endpoint returned a non-JSON body")


def _http_timeout(wait_s: float) -> httpx.Timeout:
    return httpx.Timeout(http_timeout_for(wait_s), connect=min(10.0, wait_s))


_TERMINAL_EVENTS = ("response.completed", "response.incomplete", "response.failed")


class _HTTPClient:
    """Shared transport (retries, nested timeouts, accounting). Subclasses define the wire format."""

    protocol: ClassVar[str]
    path: ClassVar[str]
    streams: ClassVar[bool] = False       # the wire format has a streaming mode (``config.stream_roles``)

    def __init__(self, config: ModelConfig | None = None) -> None:
        self.config = config or ModelConfig.from_env()
        self.usage = Usage()
        self.llm_calls = 0
        self.ceilings: dict[str, int] = {}    # model -> output ceiling the deployment reported (probed)
        self._http: httpx.AsyncClient | None = None

    # -- wire format hooks ------------------------------------------------------------------

    def new_transcript(self) -> Transcript:
        raise NotImplementedError

    def _body(self, *, role: str, model: str, system: str, items: list[dict[str, Any]],
              tools: list[ToolSpec] | None, schema: dict[str, Any] | None, schema_name: str,
              effort: str | None, max_output_tokens: int) -> dict[str, Any]:
        raise NotImplementedError

    def _parse(self, data: dict[str, Any], *, role: str, model: str) -> LLMResponse:
        raise NotImplementedError

    # -- per-run scopes and the LLMClient interface -----------------------------------------

    def for_run(self, run_id: str | None) -> RunScopedLLM:
        """A view with sticky escalation state and run-local usage; share it across a run's agents."""
        return RunScopedLLM(self, run_id)

    async def complete(self, *, role: Role, system: str, input: list[dict[str, Any]] | str,
                       tools: list[ToolSpec] | None = None, schema: dict[str, Any] | None = None,
                       schema_name: str = "output", effort: str | None = None,
                       max_output_tokens: int | None = None, timeout_s: float | None = None) -> LLMResponse:
        return await self.for_run(None).complete(
            role=role, system=system, input=input, tools=tools, schema=schema, schema_name=schema_name,
            effort=effort, max_output_tokens=max_output_tokens, timeout_s=timeout_s)

    async def structured(self, *, role: Role, system: str, user: str, schema: type[T], effort: str | None = None,
                         max_output_tokens: int | None = None, attempts: int = 3,
                         timeout_s: float | None = None) -> T | None:
        return await self.for_run(None).structured(
            role=role, system=system, user=user, schema=schema, effort=effort,
            max_output_tokens=max_output_tokens, attempts=attempts, timeout_s=timeout_s)

    async def run_tool_loop(self, *, role: Role, system: str, user: str, tools: list[ToolSpec], deadline: float,
                            max_steps: int = 40, effort: str | None = None,
                            on_event: Callable[[LoopEvent], Awaitable[None] | None] | None = None,
                            before_final: Callable[[str, int], Awaitable[str | None]] | None = None,
                            inject: Callable[[], list[str]] | None = None,
                            stop_check: Callable[[], bool] | None = None) -> LoopResult:
        return await self.for_run(None).run_tool_loop(
            role=role, system=system, user=user, tools=tools, deadline=deadline, max_steps=max_steps,
            effort=effort, on_event=on_event, before_final=before_final, inject=inject, stop_check=stop_check)

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # -- one request ------------------------------------------------------------------------

    async def request(self, *, role: Role, model: str, system: str, items: list[dict[str, Any]],
                      tools: list[ToolSpec] | None, schema: dict[str, Any] | None, schema_name: str,
                      effort: str | None, max_output_tokens: int, timeout_s: float) -> LLMResponse:
        """One model call with transport retries; ``timeout_s`` is the outer wait for all of it.

        Roles in ``config.stream_roles`` stream (3.13): a long non-streamed generation is cut by the
        provider's time limit, and a stream shows whether the model is still producing.
        """
        body = self._body(role=role, model=model, system=system, items=items, tools=tools, schema=schema,
                          schema_name=schema_name, effort=effort, max_output_tokens=max_output_tokens)
        stream = self.streams and role in self.config.stream_roles
        if stream:
            body["stream"] = True
        t0 = time.monotonic()
        data = await self._post(body, role=role, model=model, timeout_s=timeout_s, stream=stream)
        resp = self._parse(data, role=role, model=model)
        resp.elapsed_s = time.monotonic() - t0
        resp.usage.cost_usd = cost(model, resp.usage)
        if resp.stop_reason == "max_output_tokens":
            log.warning("%s on %s STOPPED AT max_output_tokens=%d (reasoning %d of %d output tokens): the visible "
                        "answer may be cut short", role, model, max_output_tokens, resp.usage.reasoning_tokens,
                        resp.usage.output_tokens)
        budget = self.config.role(role).input_budget_tokens
        if resp.usage.input_tokens > budget:
            log.warning("%s on %s used %d input tokens, over its %d-token budget", role, model,
                        resp.usage.input_tokens, budget)
        return resp

    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient()
        return self._http

    async def _post(self, body: dict[str, Any], *, role: str, model: str, timeout_s: float,
                    stream: bool = False) -> dict[str, Any]:
        key = self.config.api_key
        if not key:
            raise LLMError(f"{role}: the API key env var {self.config.api_key_env} is not set", status=401,
                           model_failure=False, lesson="configure the model API key")
        url = f"{self.config.base_url}{self.path}"
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        deadline = time.monotonic() + timeout_s
        attempt = stalls = 0
        while True:
            attempt += 1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LLMTimeout(f"{role} on {model}: no time left for the call", lesson="the outer wait expired")
            result: dict[str, Any] | LLMError
            try:
                async with asyncio.timeout(remaining):
                    if stream:
                        result = await self._send_stream(url, body, headers, remaining, role=role, model=model)
                    else:
                        r = await self._client().post(url, json=body, headers=headers,
                                                      timeout=_http_timeout(remaining))
                        result = _json_body(r, role=role, model=model) if r.status_code < 300 \
                            else _error_from_response(r, role=role, model=model)
            except TimeoutError:
                raise LLMTimeout(f"{role} on {model}: no reply within {timeout_s:.0f}s",
                                 lesson="the outer wait expired; the request is not re-sent") from None
            except httpx.TransportError as e:
                result = LLMError(f"{role} on {model}: transport error {type(e).__name__}", retryable=True,
                                  lesson="network problem reaching the model endpoint")
            if isinstance(result, dict):
                return result
            err = result
            stalls += err.kind == "stall"
            if not err.retryable or attempt >= self.config.max_http_attempts or stalls > 1:
                raise err
            delay = err.retry_after if err.retry_after is not None \
                else self.config.retry_base_s * 2 ** (attempt - 1)
            delay = min(delay, self.config.retry_max_delay_s) + random.uniform(0, self.config.retry_jitter_s)
            if delay >= deadline - time.monotonic():
                raise err
            log.warning("%s; retry %d/%d in %.1fs", err, attempt, self.config.max_http_attempts - 1, delay)
            await asyncio.sleep(delay)

    async def _send_stream(self, url: str, body: dict[str, Any], headers: dict[str, str], wait_s: float, *,
                           role: str, model: str) -> dict[str, Any] | LLMError:
        async with self._client().stream("POST", url, json=body, headers=headers,
                                         timeout=_http_timeout(wait_s)) as r:
            if r.status_code >= 300:
                await r.aread()
                return _error_from_response(r, role=role, model=model)
            if "text/event-stream" not in r.headers.get("content-type", ""):
                await r.aread()                     # the endpoint answered in one piece after all
                return _json_body(r, role=role, model=model)
            return await self._read_stream(r, role=role, model=model)

    async def _read_stream(self, r: httpx.Response, *, role: str, model: str) -> dict[str, Any] | LLMError:
        """Assemble a streamed Responses reply (3.13) into the same dict a non-streamed call returns.

        The terminal event carries the whole response (output items seen on the way are the fallback).
        Once output has started, a stream silent for ``stream_stall_s`` is abandoned and the caller
        retries it once. Silence while the model reasons is not a stall (endpoints without keep-alive
        pings send nothing then): the outer wait bounds that part.
        """
        stall_s = self.config.stream_stall_s
        lines = r.aiter_lines()
        data: list[str] = []
        items: list[dict[str, Any]] = []
        armed = ended = False
        while not ended:
            try:
                async with asyncio.timeout(stall_s if armed else None):
                    line = await anext(lines)
            except StopAsyncIteration:
                ended, line = True, ""
            except TimeoutError:
                return LLMError(f"{role} on {model}: the stream stalled (no event for {stall_s:.0f}s)", kind="stall",
                                retryable=True, lesson="the stream went quiet mid-reply; it is retried once")
            if line.startswith("data:"):
                data.append(line[5:].removeprefix(" "))
                continue
            if line.strip() or not data:            # event:/id: fields, keep-alive comments
                continue
            payload, data = "\n".join(data), []
            if payload.strip() == "[DONE]":
                break
            try:
                event = json.loads(payload)
            except ValueError:
                continue
            kind = str(event.get("type") or "") if isinstance(event, dict) else ""
            item = event.get("item") if isinstance(event, dict) else None
            if kind.endswith(".delta"):
                armed = True
            elif kind == "response.output_item.added" and isinstance(item, dict) and item.get("type") == "reasoning":
                armed = False                       # reasoning again: silent until its result
            elif kind == "response.output_item.done" and isinstance(item, dict):
                items.append(item)
            elif kind in _TERMINAL_EVENTS and isinstance(event.get("response"), dict):
                final = event["response"]
                return final if final.get("output") or not items else {**final, "output": items}
            elif kind == "error":
                e = event["error"] if isinstance(event.get("error"), dict) else event
                return LLMError(f"{role} on {model}: stream error: {e.get('code')}: {e.get('message')}",
                                kind=e.get("code"), lesson="the provider failed this generation")
        return LLMError(f"{role} on {model}: the stream ended before the response was complete", retryable=True,
                        lesson="the stream was cut short")


class MetaResponsesClient(_HTTPClient):
    """``POST /responses`` with stateless encrypted-reasoning replay (default for the Meta Model API)."""

    protocol = "responses"
    path = "/responses"
    streams = True

    def new_transcript(self) -> Transcript:
        return ResponsesTranscript()

    def _body(self, *, role: str, model: str, system: str, items: list[dict[str, Any]],
              tools: list[ToolSpec] | None, schema: dict[str, Any] | None, schema_name: str,
              effort: str | None, max_output_tokens: int) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model, "input": items, "store": False, "include": ["reasoning.encrypted_content"],
            "max_output_tokens": max(16, int(max_output_tokens)), "prompt_cache_key": f"omniops-{role}",
        }
        if system:
            body["instructions"] = system
        if self.config.cache_retention:
            body["prompt_cache_retention"] = self.config.cache_retention
        if eff := normalize_effort(effort, "responses"):
            body["reasoning"] = {"effort": eff}
        if tools:
            body["tools"] = [{"type": "function", "name": t.name, "description": t.description,
                              "parameters": t.parameters} for t in tools]
            body["tool_choice"] = "auto"           # the only value Muse accepts
            body["parallel_tool_calls"] = True
        if schema is not None:
            body["text"] = {"format": {"type": "json_schema", "name": schema_name, "schema": schema}}
        return body

    def _parse(self, data: dict[str, Any], *, role: str, model: str) -> LLMResponse:
        status = data.get("status") or "completed"
        if status == "failed":
            e = data.get("error") or {}
            raise LLMError(f"{role} on {model}: generation failed: {e.get('code')}: {e.get('message')}",
                           kind=e.get("code"), lesson="the provider failed this generation")
        texts: list[str] = []
        calls: list[ToolCall] = []
        replay: list[dict[str, Any]] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind == "message":
                parts = item.get("content")
                parts = [{"type": "output_text", "text": parts}] if isinstance(parts, str) else parts or []
                blocks = [str(p.get("text") or p.get("refusal") or "") for p in parts
                          if isinstance(p, dict) and p.get("type") in ("output_text", "text", "refusal")]
                text = "".join(blocks)              # ALL blocks: the first one is sometimes empty
                if text.strip():
                    texts.append(text.strip())
                msg: dict[str, Any] = {"type": "message", "role": "assistant", "content": [
                    {"type": "output_text", "text": b} for b in blocks if b] or [{"type": "output_text",
                                                                                   "text": NO_TEXT}]}
                if item.get("phase"):
                    msg["phase"] = item["phase"]   # commentary before a call must stay tagged
                replay.append(msg)
            elif kind == "reasoning":
                if not item.get("encrypted_content"):
                    continue                        # a bare rs_ id cannot be replayed with store=false (HTTP 400)
                r: dict[str, Any] = {"type": "reasoning", "summary": item.get("summary") or [],
                                     "encrypted_content": item["encrypted_content"]}
                if item.get("id"):
                    r["id"] = item["id"]
                replay.append(r)
            elif kind == "function_call":
                raw = item.get("arguments")
                call_id = str(item.get("call_id") or item.get("id") or f"call_{len(calls) + 1}")[:64]
                name = str(item.get("name") or "")
                cut_off = item.get("status") not in (None, "completed") or (
                    status == "incomplete" and not _is_complete_json(raw))
                calls.append(ToolCall(id=call_id, name=name, arguments=_call_arguments(raw, cut_off=cut_off),
                                      raw_arguments=_raw_arguments(raw)))
                if replay and replay[-1].get("type") == "message" and not replay[-1].get("phase"):
                    replay[-1]["phase"] = "commentary"   # final-answer text right before a call is a 400
                replay.append({"type": "function_call", "call_id": call_id, "name": name,
                               "arguments": _raw_arguments(raw)})
            else:
                replay.append(item)
        u = data.get("usage") or {}
        usage = _usage(u.get("input_tokens"), (u.get("input_tokens_details") or {}).get("cached_tokens"),
                       u.get("output_tokens"), (u.get("output_tokens_details") or {}).get("reasoning_tokens"))
        stop = status
        if status == "incomplete":
            stop = (data.get("incomplete_details") or {}).get("reason") or "incomplete"
        return LLMResponse(text="\n\n".join(texts), tool_calls=calls, usage=usage, model=model,
                           output_items=replay, stop_reason=stop)


class ChatCompletionsClient(_HTTPClient):
    """``POST /chat/completions`` for other OpenAI-compatible endpoints (no reasoning replay)."""

    protocol = "chat"
    path = "/chat/completions"

    def new_transcript(self) -> Transcript:
        return ChatTranscript()

    def _body(self, *, role: str, model: str, system: str, items: list[dict[str, Any]],
              tools: list[ToolSpec] | None, schema: dict[str, Any] | None, schema_name: str,
              effort: str | None, max_output_tokens: int) -> dict[str, Any]:
        messages = ([{"role": "system", "content": system}] if system else []) + items
        body: dict[str, Any] = {"model": model, "messages": messages,
                                "max_completion_tokens": max(16, int(max_output_tokens)),
                                "prompt_cache_key": f"omniops-{role}"}
        if eff := normalize_effort(effort, "chat"):
            body["reasoning_effort"] = eff
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                               "parameters": t.parameters}} for t in tools]
            body["tool_choice"] = "auto"
            body["parallel_tool_calls"] = True
        if schema is not None:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": schema_name, "schema": schema}}
        return body

    def _parse(self, data: dict[str, Any], *, role: str, model: str) -> LLMResponse:
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"{role} on {model}: response has no choices", lesson="empty completion")
        choice = choices[0]
        msg = choice.get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            text = "".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
        else:
            text = str(content or "")
        finish = choice.get("finish_reason")
        calls: list[ToolCall] = []
        wire_calls: list[dict[str, Any]] = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            raw = fn.get("arguments")
            call_id = str(tc.get("id") or f"call_{i + 1}")
            name = str(fn.get("name") or "")
            cut_off = finish == "length" and not _is_complete_json(raw)
            calls.append(ToolCall(id=call_id, name=name, arguments=_call_arguments(raw, cut_off=cut_off),
                                  raw_arguments=_raw_arguments(raw)))
            wire_calls.append({"id": call_id, "type": "function",
                               "function": {"name": name, "arguments": _raw_arguments(raw)}})
        assistant: dict[str, Any] = {"role": "assistant", "content": text or (None if wire_calls else NO_TEXT)}
        if wire_calls:
            assistant["tool_calls"] = wire_calls
        stop = {"length": "max_output_tokens", "stop": "completed", "tool_calls": "completed"}.get(
            finish, finish or "completed")
        u = data.get("usage") or {}
        usage = _usage(u.get("prompt_tokens"), (u.get("prompt_tokens_details") or {}).get("cached_tokens"),
                       u.get("completion_tokens"),
                       (u.get("completion_tokens_details") or {}).get("reasoning_tokens"))
        return LLMResponse(text=text.strip(), tool_calls=calls, usage=usage, model=model,
                           output_items=[assistant], stop_reason=stop)


# ---------------------------------------------------------------------------------------------
# Per-run scope: sticky escalation ladder + run-local usage
# ---------------------------------------------------------------------------------------------


@dataclass
class _Streak:
    """Consecutive failures of ONE logical call (a structured call, a tool loop's turns).

    Never shared between concurrent calls of a role (five specialists run at once), so another call's
    success cannot keep a failing call from escalating (3.13).
    """

    fails: int = 0


class RunScopedLLM:
    """An ``LLMClient`` for one run. Escalation is sticky per role for the rest of the run (3.13)."""

    def __init__(self, client: _HTTPClient, run_id: str | None) -> None:
        self.client = client
        self.run_id = run_id
        self.usage = Usage()
        self.llm_calls = 0
        self.escalations: list[dict[str, str]] = []
        self._rung: dict[str, int] = {}

    def for_run(self, run_id: str | None) -> RunScopedLLM:
        return self.client.for_run(run_id)

    def model_for(self, role: Role) -> str:
        ladder = self.client.config.ladder(role)
        return ladder[min(self._rung.get(role, 0), len(ladder) - 1)]

    def stats(self) -> dict[str, Any]:
        """Totals keyed like ``RunStats`` fields."""
        u = self.usage
        return {"llm_calls": self.llm_calls, "input_tokens": u.input_tokens, "cached_tokens": u.cached_tokens,
                "output_tokens": u.output_tokens, "reasoning_tokens": u.reasoning_tokens,
                "cost_usd": round(u.cost_usd, 6), "escalations": len(self.escalations)}

    def _fail(self, role: Role, model: str, reason: str, *, streak: _Streak, now: bool = False) -> bool:
        """Count a failure of one logical call; escalate after ``escalate_after`` in a row (or ``now``).

        True when the next try runs on another model: this call escalated the role, or a concurrent
        call of the same role already did. The streak starts over on the new model.
        """
        streak.fails += 1
        if self.model_for(role) != model:
            streak.fails = 0
            return True
        if not now and streak.fails < self.client.config.escalate_after:
            return False
        ladder = self.client.config.ladder(role)
        rung = self._rung.get(role, 0)
        if rung + 1 >= len(ladder):
            return False
        self._rung[role] = rung + 1
        streak.fails = 0
        self.escalations.append({"role": role, "from": model, "to": ladder[rung + 1], "reason": reason[:300]})
        log.warning("[%s] escalating %s from %s to %s for the rest of the run: %s", self.run_id or "-", role,
                    model, ladder[rung + 1], reason[:300])
        return True

    def _account(self, resp: LLMResponse) -> None:
        for scope in (self, self.client):
            scope.usage.add(resp.usage)
            scope.llm_calls += 1

    async def _call(self, *, role: Role, system: str, transcript: Transcript, tools: list[ToolSpec] | None,
                    schema: dict[str, Any] | None, schema_name: str, effort: str | None,
                    max_output_tokens: int | None, timeout_s: float | None, retry_until: float | None = None,
                    streak: _Streak | None = None, settle: bool = True) -> LLMResponse:
        """One logical call: same-model retries, then sticky escalation.

        ``timeout_s`` is the outer wait for one request. Retries (a timeout included) and escalated
        tries start while ``retry_until`` - by default the end of the first wait - leaves time for
        them. ``streak`` counts this call's consecutive failures; a success resets it unless
        ``settle`` is False (a structured reply still has to pass the validator).
        """
        cfg = self.client.config
        rd = cfg.role(role)
        wait = timeout_s if timeout_s is not None else rd.timeout_s
        end = retry_until if retry_until is not None else time.monotonic() + wait
        streak = streak if streak is not None else _Streak()
        requested = max_output_tokens or rd.max_output_tokens
        rooms: dict[str, int] = {}               # what the context window leaves this request (not remembered)
        ceiling_retried: set[str] = set()
        tries = 0
        while True:
            model = self.model_for(role)
            if transcript.model not in (None, model):
                transcript.drop_reasoning()
            budget = min(requested, self.client.ceilings.get(model, requested), rooms.get(model, requested))
            tries += 1
            try:
                resp = await self.client.request(
                    role=role, model=model, system=system, items=transcript.items(), tools=tools, schema=schema,
                    schema_name=schema_name, effort=effort or rd.effort, max_output_tokens=budget,
                    timeout_s=max(0.0, min(wait, end - time.monotonic())))
            except LLMError as e:
                ceiling = reported_ceiling(e, budget)
                if ceiling is not None and model not in ceiling_retried:
                    # A concurrent call may have stored a ceiling meanwhile: keep the lower one, retry anyway.
                    known = self.client.ceilings.get(model)
                    self.client.ceilings[model] = min(ceiling, known) if known else ceiling
                    ceiling_retried.add(model)
                    log.warning("[%s] %s: %s caps output at %d tokens (asked %d): retrying once at the ceiling",
                                self.run_id or "-", role, model, self.client.ceilings[model], budget)
                    continue
                fit = context_room(e, budget)
                if fit is not None and model not in rooms:
                    rooms[model] = fit
                    log.warning("[%s] %s on %s: the input leaves room for %d output tokens (asked %d): retrying "
                                "this request at that budget", self.run_id or "-", role, model, fit, budget)
                    continue
                if not e.model_failure:
                    raise
                moved = self._fail(role, model, str(e), streak=streak, now=not e.same_model)
                time_left = end - time.monotonic() > 1.0
                if moved and time_left:
                    tries = 0
                    continue
                if e.same_model and tries < cfg.escalate_after and time_left:
                    log.warning("[%s] %s retrying on %s after: %s", self.run_id or "-", role, model, e)
                    continue
                raise
            transcript.model = model
            self._account(resp)
            if settle:
                streak.fails = 0
            return resp

    async def complete(self, *, role: Role, system: str, input: list[dict[str, Any]] | str,
                       tools: list[ToolSpec] | None = None, schema: dict[str, Any] | None = None,
                       schema_name: str = "output", effort: str | None = None,
                       max_output_tokens: int | None = None, timeout_s: float | None = None) -> LLMResponse:
        transcript = self.client.new_transcript()
        if isinstance(input, str):
            transcript.add_user(input)
        else:
            transcript.add_raw(input)
        return await self._call(role=role, system=system, transcript=transcript, tools=tools, schema=schema,
                                schema_name=safe_schema_name(schema_name), effort=effort,
                                max_output_tokens=max_output_tokens, timeout_s=timeout_s)

    async def structured(self, *, role: Role, system: str, user: str, schema: type[T], effort: str | None = None,
                         max_output_tokens: int | None = None, attempts: int = 3,
                         timeout_s: float | None = None) -> T | None:
        """Schema-bound output; ``None`` means fall back.

        ``timeout_s`` (default: the role's outer wait) is the budget for the whole call, every attempt
        included: callers pass the time they have left. A re-ask starts only with at least
        ``STRUCTURED_RETRY_MIN_S`` left.
        """
        cfg = self.client.config
        json_schema = to_json_schema(schema)
        name = safe_schema_name(schema.__name__)
        total = timeout_s if timeout_s is not None else cfg.role(role).timeout_s
        end = time.monotonic() + total
        streak = _Streak()                       # validation rejections and failed requests of THIS call
        sent = 0

        async def turn(text: str, budget: int) -> StructuredTurn:
            nonlocal sent
            left = end - time.monotonic()
            if sent and left < STRUCTURED_RETRY_MIN_S:
                raise LLMTimeout(f"{role}: {max(0.0, left):.1f}s left of the {total:.0f}s budget: no time to ask "
                                 "again", lesson="the call's time budget is spent")
            sent += 1
            transcript = self.client.new_transcript()
            transcript.add_user(text)
            resp = await self._call(role=role, system=system, transcript=transcript, tools=None, schema=json_schema,
                                    schema_name=name, effort=effort, max_output_tokens=budget,
                                    timeout_s=max(0.0, left), streak=streak, settle=False)
            return StructuredTurn(resp.text, resp.stop_reason, resp.model)

        try:
            return await run_structured(
                turn, user=user, schema=schema, attempts=attempts,
                max_output_tokens=max_output_tokens or cfg.role(role).max_output_tokens, ceiling=cfg.output_ceiling,
                on_reject=lambda model, error: self._fail(role, model, f"validation: {error}", streak=streak),
                label=f"{role}/{schema.__name__}")
        except LLMError as e:
            log.warning("[%s] structured %s/%s failed: %s", self.run_id or "-", role, schema.__name__, e)
            return None

    async def run_tool_loop(self, *, role: Role, system: str, user: str, tools: list[ToolSpec], deadline: float,
                            max_steps: int = 40, effort: str | None = None,
                            on_event: Callable[[LoopEvent], Awaitable[None] | None] | None = None,
                            before_final: Callable[[str, int], Awaitable[str | None]] | None = None,
                            inject: Callable[[], list[str]] | None = None,
                            stop_check: Callable[[], bool] | None = None) -> LoopResult:
        cfg = self.client.config
        rd = cfg.role(role)
        transcript = self.client.new_transcript()
        streak = _Streak()

        async def turn(wrap_up: bool, timeout_s: float, budget: int) -> LLMResponse:
            # The time-up turn has no tools and low effort: it must answer, and fast. A normal turn that
            # fails (a timeout included) is retried and escalated while the loop's deadline allows.
            return await self._call(role=role, system=system, transcript=transcript,
                                    tools=None if wrap_up else tools, schema=None, schema_name="output",
                                    effort="low" if wrap_up else effort, max_output_tokens=budget,
                                    timeout_s=timeout_s, retry_until=None if wrap_up else deadline, streak=streak)

        return await drive_tool_loop(
            turn=turn, transcript=transcript, user=user, tools=tools, deadline=deadline, max_steps=max_steps,
            turn_timeout_s=rd.timeout_s, max_output_tokens=rd.max_output_tokens, ceiling=cfg.output_ceiling,
            on_event=on_event, before_final=before_final, inject=inject, stop_check=stop_check,
            label=f"{self.run_id or '-'}:{role}")


# ---------------------------------------------------------------------------------------------
# The tool loop (shared with ScriptedLLM so tests exercise the production loop)
# ---------------------------------------------------------------------------------------------

TurnFn = Callable[[bool, float, int], Awaitable[LLMResponse]]   # (wrap_up, timeout_s, max_output_tokens)


async def _run_tool(spec: ToolSpec | None, call: ToolCall, available: list[str]) -> tuple[ToolOutput, int]:
    t0 = time.monotonic()
    if spec is None:
        out = ToolOutput(f"ERROR: unknown tool {call.name!r}. Available tools: {', '.join(available)}",
                         is_error=True)
    elif ARG_PARSE_ERROR_KEY in call.arguments:
        out = ToolOutput(f"ERROR: the arguments for {call.name} are not valid JSON "
                         f"({call.arguments[ARG_PARSE_ERROR_KEY]}). Call it again with one JSON object.",
                         is_error=True)
    else:
        try:
            out = await spec.handler(call.arguments)
        except Exception as exc:  # noqa: BLE001 - every tool failure becomes a lesson for the model
            lesson = getattr(exc, "lesson", None)
            log.warning("tool %s raised %s: %s", call.name, type(exc).__name__, exc)
            out = ToolOutput(f"ERROR ({type(exc).__name__}): {exc}" + (f"\nLesson: {lesson}" if lesson else ""),
                             is_error=True)
    return out, int((time.monotonic() - t0) * 1000)


async def drive_tool_loop(
    *,
    turn: TurnFn,
    transcript: Transcript,
    user: str,
    tools: list[ToolSpec],
    deadline: float,
    max_steps: int,
    turn_timeout_s: float,
    max_output_tokens: int,
    ceiling: int,
    on_event: Callable[[LoopEvent], Awaitable[None] | None] | None = None,
    before_final: Callable[[str, int], Awaitable[str | None]] | None = None,
    inject: Callable[[], list[str]] | None = None,
    stop_check: Callable[[], bool] | None = None,
    label: str = "",
) -> LoopResult:
    """The native tool loop, exactly as ``LLMClient.run_tool_loop`` documents it.

    Parallel calls run concurrently; results are appended in call order. When the time left is below
    the slowest healthy step so far (or the step cap is reached), one tool-less "answer now, disclose
    gaps" turn is sent instead of another step; it gets at least ``WRAP_UP_MIN_S`` but ends no later
    than ``WRAP_UP_OVERRUN_S`` after the deadline. A turn that fails as a model failure (``turn``
    already retried it) gets that answer-now turn too. An answer cut off at the output limit is
    never accepted as final while a re-ask with the raised budget is left. ``final_text`` is only
    ever answer text, never the commentary that came with a tool call.
    """
    by_name = {t.name: t for t in tools}
    available = sorted(by_name)
    events: list[LoopEvent] = []
    usage = Usage()
    budget = max_output_tokens
    slowest = 0.0
    steps = calls_made = empty_pushbacks = cut_off_pushbacks = 0
    last_text = ""                       # the latest answer draft (a reply without tool calls)

    async def emit(kind: Any, payload: dict[str, Any]) -> None:
        ev = LoopEvent(step=steps, kind=kind, payload=payload)
        events.append(ev)
        if on_event is None:
            return
        try:
            r = on_event(ev)
            if inspect.isawaitable(r):
                await r
        except Exception:
            log.exception("[%s] on_event handler failed (ignored)", label)

    async def finish(text: str, stopped_by: Any) -> LoopResult:
        if calls_made == 0:
            log.warning("[%s] tool loop ended with zero tool calls", label)
        if not text.strip():
            log.warning("[%s] tool loop ended with an empty answer (%s)", label, stopped_by)
        await emit("final", {"text": text, "stopped_by": stopped_by})
        return LoopResult(final_text=text, steps=steps, stopped_by=stopped_by, usage=usage, events=events)

    async def wrap_up(message: str, reason: str) -> LoopResult:
        nonlocal steps
        remaining = deadline - time.monotonic()
        # Callers wrap the loop in an outer wait that allows WRAP_UP_OVERRUN_S past the deadline (and a
        # margin): a paid answer turn cancelled by it would be lost with the whole result.
        room = remaining + WRAP_UP_OVERRUN_S
        if room < 1.0:
            log.warning("[%s] %.0fs past the deadline: no time for the answer-now turn", label, -remaining)
            return await finish(last_text, reason)
        allowance = min(turn_timeout_s, max(remaining, WRAP_UP_MIN_S), room)
        transcript.add_user(message)
        await emit("pushback", {"text": message, "reason": reason})
        steps += 1
        try:
            resp = await turn(True, allowance, budget)
        except LLMError as e:
            await emit("error", {"error": str(e), "lesson": e.lesson})
            return await finish(last_text, "error")
        usage.add(resp.usage)
        transcript.add_response(resp)
        return await finish(resp.text or last_text, reason)

    transcript.add_user(user)
    while True:
        if stop_check is not None and stop_check():
            return await finish(last_text, "stop_signal")
        for msg in (inject() if inject is not None else []):
            transcript.add_user(msg)
            await emit("pushback", {"text": msg, "reason": "inject"})
        remaining = deadline - time.monotonic()
        if remaining <= 0 or remaining < slowest:
            log.warning("[%s] %.0fs left (slowest step %.0fs): asking for the answer now", label, remaining, slowest)
            return await wrap_up(TIME_UP, "deadline")
        if steps >= max_steps:
            return await wrap_up(STEP_LIMIT, "max_steps")
        steps += 1
        t0 = time.monotonic()
        try:
            resp = await turn(False, min(turn_timeout_s, remaining), budget)
        except LLMError as e:
            await emit("error", {"error": str(e), "lesson": e.lesson})
            if not e.model_failure:              # an invalid request: asking again will not help
                return await finish(last_text, "error")
            # The turn's retries (and escalation) are spent: stop exploring, but still ask for the answer.
            log.warning("[%s] turn %d failed (%s): asking for the answer now", label, steps, e)
            return await wrap_up(TIME_UP, "deadline" if time.monotonic() >= deadline else "error")
        usage.add(resp.usage)
        transcript.add_response(resp)
        cut_off = resp.stop_reason == "max_output_tokens"
        if cut_off and budget < ceiling:
            budget = min(ceiling, budget * 2)
            log.warning("[%s] raising max_output_tokens to %d for the next turns", label, budget)

        if resp.tool_calls:
            if resp.text.strip():
                await emit("assistant_text", {"text": resp.text})
            for c in resp.tool_calls:
                spec = by_name.get(c.name)
                if spec is not None and ARG_PARSE_ERROR_KEY not in c.arguments:
                    c.arguments = fit_arguments(c.arguments, spec.parameters)
                await emit("tool_call", {"id": c.id, "name": c.name, "arguments": c.arguments})
            calls_made += len(resp.tool_calls)
            results = await asyncio.gather(*(_run_tool(by_name.get(c.name), c, available) for c in resp.tool_calls))
            outputs = [out for out, _ in results]
            transcript.add_tool_results(resp.tool_calls, outputs)
            for c, (out, ms) in zip(resp.tool_calls, results, strict=True):
                await emit("tool_result", {"id": c.id, "name": c.name, "content": out.content,
                                           "is_error": out.is_error, "evidence_id": out.evidence_id,
                                           "stop": out.stop, "elapsed_ms": ms, "data": out.data})
            slowest = max(slowest, time.monotonic() - t0)
            stopper = next((o for o in outputs if o.stop), None)
            if stopper is not None:
                return await finish(stopper.content or last_text, "tool_stop")
            continue

        slowest = max(slowest, time.monotonic() - t0)
        text = resp.text
        if not text.strip() and empty_pushbacks < EMPTY_PUSHBACKS:
            empty_pushbacks += 1
            why = " (cut off at the output limit)" if cut_off else ""
            msg = EMPTY_REPLY.format(why=why)
            transcript.add_user(msg)
            await emit("pushback", {"text": msg, "reason": "empty"})
            continue
        if text.strip() and cut_off:
            if cut_off_pushbacks < CUT_OFF_PUSHBACKS:
                cut_off_pushbacks += 1
                transcript.add_user(CUT_OFF)
                await emit("pushback", {"text": CUT_OFF, "reason": "cut_off"})
                continue
            log.warning("[%s] accepting an answer cut off at the output limit after %d re-asks", label,
                        CUT_OFF_PUSHBACKS)
        if text.strip():
            last_text = text
        if before_final is not None:
            push = await before_final(text, steps)
            if push:
                transcript.add_user(push)
                await emit("pushback", {"text": push, "reason": "gate"})
                continue
        return await finish(text or last_text, "final_text")
