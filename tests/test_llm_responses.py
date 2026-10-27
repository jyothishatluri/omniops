"""MetaResponsesClient through its real entry points against a mocked https://api.meta.ai/v1/responses."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
from typing import Any

import httpx
import pytest
import respx

from omniops.agent.types import SpecialistVerdict
from omniops.llm import (
    LLMError,
    LLMResponse,
    MetaResponsesClient,
    ModelConfig,
    ToolCall,
    ToolOutput,
    ToolSpec,
    Usage,
)
from omniops.llm.meta import ChatTranscript, LLMTimeout, drive_tool_loop, reported_ceiling
from omniops.llm.models import http_timeout_for

URL = "https://api.meta.ai/v1/responses"
SQL_PARAMS = {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"],
              "additionalProperties": False}


def cfg(**kw: Any) -> ModelConfig:
    return ModelConfig(api_key="test-key", retry_base_s=0.01, retry_jitter_s=0.0, **kw)


def reply(*output: dict[str, Any], status: str = "completed", reason: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "resp_1", "object": "response", "status": status, "output": list(output),
        "usage": {"input_tokens": 100, "input_tokens_details": {"cached_tokens": 40}, "output_tokens": 50,
                  "output_tokens_details": {"reasoning_tokens": 30}, "total_tokens": 150},
        "incomplete_details": {"reason": reason} if reason else None,
    }
    return body


def reasoning(n: int) -> dict[str, Any]:
    return {"type": "reasoning", "id": f"rs_{n}", "summary": [], "encrypted_content": f"enc-{n}"}


def call(call_id: str, name: str, arguments: str) -> dict[str, Any]:
    return {"type": "function_call", "id": f"fc_{call_id}", "call_id": call_id, "name": name,
            "arguments": arguments, "status": "completed"}


def message(*texts: str) -> dict[str, Any]:
    return {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": t, "annotations": []} for t in texts]}


class FakeAPI:
    """Serves canned replies in order and records every request body."""

    def __init__(self, *replies: dict[str, Any] | httpx.Response) -> None:
        self.replies = list(replies)
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        self.bodies.append(json.loads(request.content))
        r = self.replies.pop(0)
        return r if isinstance(r, httpx.Response) else httpx.Response(200, json=r)

    @property
    def models(self) -> list[str]:
        return [b["model"] for b in self.bodies]


class AsyncAPI(FakeAPI):
    """Like FakeAPI, but a reply may also be an async ``fn(request)`` (a slow or hanging endpoint)."""

    async def __call__(self, request: httpx.Request) -> httpx.Response:  # type: ignore[override]
        self.bodies.append(json.loads(request.content))
        r: Any = self.replies.pop(0)
        if callable(r):
            return await r(request)
        return r if isinstance(r, httpx.Response) else httpx.Response(200, json=r)


async def hang(request: httpx.Request) -> httpx.Response:
    await asyncio.sleep(2)
    return httpx.Response(200, json=reply(message("too late")))


def user_text(item: dict[str, Any]) -> str:
    assert item["role"] == "user"
    return item["content"][0]["text"]


# ---------------------------------------------------------------------------------------------
# Tool loop
# ---------------------------------------------------------------------------------------------


@respx.mock
async def test_parallel_tool_calls_then_final_answer_replays_reasoning_and_outputs_in_order() -> None:
    api = FakeAPI(
        reply(reasoning(1), call("call_a", "slow_sql", '{"sql": "select 1"}'),
              call("call_b", "describe", '```json\n{"table": "k8s_pods",}\n```'),
              call("call_c", "broken", "{}")),
        reply(reasoning(2), message("", "Root cause: deploy v42 (E1).")),
    )
    respx.post(URL).mock(side_effect=api)
    finished: list[str] = []

    async def slow_sql(args: dict[str, Any]) -> ToolOutput:
        await asyncio.sleep(0.05)
        finished.append("slow_sql")
        return ToolOutput(f"rows for {args['sql']}", evidence_id="E1")

    async def describe(args: dict[str, Any]) -> ToolOutput:
        finished.append("describe")
        return ToolOutput(f"columns of {args['table']}")

    class FetchError(RuntimeError):
        lesson = "describe the table and fix the query; do not repeat the same query"

    async def broken(args: dict[str, Any]) -> ToolOutput:
        raise FetchError("unknown column foo")

    tools = [ToolSpec("slow_sql", "Run SQL", SQL_PARAMS, slow_sql),
             ToolSpec("describe", "Describe a table", {"type": "object", "properties": {"table": {"type": "string"}}},
                      describe),
             ToolSpec("broken", "Always fails", {"type": "object", "properties": {}}, broken)]
    events: list[Any] = []
    client = MetaResponsesClient(cfg())

    result = await client.run_tool_loop(role="decision", system="STATIC SYSTEM PROMPT", user="Why is checkout failing?",
                                        tools=tools, deadline=time.monotonic() + 60, on_event=events.append)

    assert result.final_text == "Root cause: deploy v42 (E1)."   # empty first block is not the answer
    assert result.stopped_by == "final_text" and result.steps == 2
    assert finished == ["describe", "slow_sql"]                  # executed concurrently

    first, second = api.bodies
    assert first["model"] == "muse-wonder-1.0"
    assert first["store"] is False and first["include"] == ["reasoning.encrypted_content"]
    assert first["instructions"] == "STATIC SYSTEM PROMPT"
    assert first["tool_choice"] == "auto" and first["parallel_tool_calls"] is True
    assert first["reasoning"] == {"effort": "medium"} and first["max_output_tokens"] == 16_000
    assert first["prompt_cache_key"] == "omniops-decision" and first["prompt_cache_retention"] == "24h"
    assert [t["name"] for t in first["tools"]] == ["slow_sql", "describe", "broken"]

    replay = second["input"]
    assert replay[: len(first["input"])] == first["input"]       # messages only grow
    assert [i["type"] for i in replay] == ["message", "reasoning", "function_call", "function_call", "function_call",
                                           "function_call_output", "function_call_output", "function_call_output"]
    assert replay[1] == {"type": "reasoning", "summary": [], "id": "rs_1", "encrypted_content": "enc-1"}
    assert [i["call_id"] for i in replay[2:5]] == [i["call_id"] for i in replay[5:]] == ["call_a", "call_b", "call_c"]
    assert replay[5]["output"] == "rows for select 1"
    assert replay[6]["output"] == "columns of k8s_pods"
    assert replay[7]["output"].startswith("ERROR (FetchError): unknown column foo")
    assert "Lesson: describe the table and fix the query" in replay[7]["output"]

    assert result.usage.input_tokens == 200 and result.usage.cached_tokens == 80
    assert result.usage.reasoning_tokens == 60 and result.usage.output_tokens == 100
    assert result.usage.cost_usd == pytest.approx(2 * (60 * 4.0 + 40 * 0.2 + 50 * 20.0) / 1e6)
    assert client.llm_calls == 2 and client.usage.input_tokens == 200
    assert [e.kind for e in result.events] == ["tool_call"] * 3 + ["tool_result"] * 3 + ["final"]
    assert events == result.events
    assert [e.payload["is_error"] for e in result.events if e.kind == "tool_result"] == [False, False, True]


@respx.mock
async def test_gate_pushback_injected_evidence_and_reasoning_only_turn_keep_valid_order(
        caplog: pytest.LogCaptureFixture) -> None:
    api = FakeAPI(
        reply(reasoning(1), message("Draft: probably the deploy.")),
        reply(reasoning(2), status="incomplete", reason="max_output_tokens"),
        reply(message("Final: deploy v42 caused it (E1, E2).")),
    )
    respx.post(URL).mock(side_effect=api)
    gate = iter(["Readiness gate: cite at least two evidence items.", None])
    inject_calls = 0

    async def before_final(text: str, step: int) -> str | None:
        return next(gate)

    def inject() -> list[str]:
        nonlocal inject_calls
        inject_calls += 1
        return ["Specialist code_change: deploy v42 at 11:58 (E2)."] if inject_calls == 2 else []

    client = MetaResponsesClient(cfg())
    with caplog.at_level(logging.WARNING, logger="omniops.llm"):
        result = await client.run_tool_loop(role="chat", system="S", user="What broke?", tools=[],
                                            deadline=time.monotonic() + 60, before_final=before_final, inject=inject)

    assert result.final_text == "Final: deploy v42 caused it (E1, E2)."
    assert result.stopped_by == "final_text"
    second, third = api.bodies[1]["input"], api.bodies[2]["input"]
    assert second[1]["type"] == "reasoning" and second[2]["content"][0]["text"] == "Draft: probably the deploy."
    assert user_text(second[3]) == "Readiness gate: cite at least two evidence items."
    assert user_text(second[4]) == "Specialist code_change: deploy v42 at 11:58 (E2)."
    # reasoning-only turn: a minimal assistant message keeps "reasoning -> assistant -> user" valid
    assert third[5]["type"] == "reasoning" and third[6]["role"] == "assistant"
    assert "cut off at the output limit" in user_text(third[7])
    assert api.bodies[2]["max_output_tokens"] == 2 * api.bodies[1]["max_output_tokens"]
    assert "STOPPED AT max_output_tokens" in caplog.text
    assert [e.payload["reason"] for e in result.events if e.kind == "pushback"] == ["gate", "inject", "empty"]


@respx.mock
async def test_deadline_sends_one_toolless_answer_now_turn() -> None:
    api = FakeAPI(reply(call("c1", "slow_sql", '{"sql": "select 1"}')),
                  reply(message("Best supported answer; gaps: no trace data.")))
    respx.post(URL).mock(side_effect=api)

    async def slow_sql(args: dict[str, Any]) -> ToolOutput:
        await asyncio.sleep(0.3)
        return ToolOutput("rows")

    client = MetaResponsesClient(cfg())
    result = await client.run_tool_loop(role="decision", system="S", user="Investigate.",
                                        tools=[ToolSpec("slow_sql", "Run SQL", SQL_PARAMS, slow_sql)],
                                        deadline=time.monotonic() + 0.5)

    assert result.stopped_by == "deadline"
    assert result.final_text == "Best supported answer; gaps: no trace data."
    wrap = api.bodies[1]
    assert "tools" not in wrap and wrap["reasoning"] == {"effort": "low"}
    assert user_text(wrap["input"][-1]).startswith("Time is up: answer now with what you have")


@respx.mock
async def test_step_cap_stop_signal_and_stopping_tool() -> None:
    async def ok(args: dict[str, Any]) -> ToolOutput:
        return ToolOutput("rows")

    async def finish(args: dict[str, Any]) -> ToolOutput:
        return ToolOutput(args["answer"], stop=True)

    tools = [ToolSpec("slow_sql", "Run SQL", SQL_PARAMS, ok),
             ToolSpec("finish", "Finish", {"type": "object", "properties": {"answer": {"type": "string"}}}, finish)]
    client = MetaResponsesClient(cfg())

    api = FakeAPI(reply(call("c1", "slow_sql", '{"sql": "select 1"}')), reply(message("Answer at the cap.")))
    respx.post(URL).mock(side_effect=api)
    capped = await client.run_tool_loop(role="decision", system="S", user="Go.", tools=tools,
                                        deadline=time.monotonic() + 60, max_steps=1)
    assert capped.stopped_by == "max_steps" and capped.final_text == "Answer at the cap."
    assert user_text(api.bodies[1]["input"][-1]).startswith("Step limit reached")

    api = FakeAPI(reply(call("c1", "slow_sql", '{"sql": "select 1"}')))
    respx.post(URL).mock(side_effect=api)
    stopped = await client.run_tool_loop(role="decision", system="S", user="Go.", tools=tools,
                                         deadline=time.monotonic() + 60, stop_check=lambda: len(api.bodies) >= 1)
    assert stopped.stopped_by == "stop_signal" and len(api.bodies) == 1

    api = FakeAPI(reply(message("calling finish"), call("c9", "finish", '{"answer": "Report text."}')))
    respx.post(URL).mock(side_effect=api)
    done = await client.run_tool_loop(role="decision", system="S", user="Go.", tools=tools,
                                      deadline=time.monotonic() + 60)
    assert done.stopped_by == "tool_stop" and done.final_text == "Report text."
    assert [e.kind for e in done.events] == ["assistant_text", "tool_call", "tool_result", "final"]


# ---------------------------------------------------------------------------------------------
# Structured output
# ---------------------------------------------------------------------------------------------


@respx.mock
async def test_structured_feeds_the_exact_validation_error_back_then_succeeds() -> None:
    api = FakeAPI(
        reply(message('{"agent": "code_change", "verdict": "found", "headline": "Deploy v42", "confidence": 1.7}')),
        reply(message('```json\n{"agent": "code_change", "verdict": "Found (deploy v42)", "headline": "Deploy v42 at '
                      '11:58", "confidence": 0.8, "evidence_ids": "E2", "details": "{\\"sha\\": \\"abc123\\"}", '
                      '"suggested_hypothesis": null}\n```')),
    )
    respx.post(URL).mock(side_effect=api)
    client = MetaResponsesClient(cfg())

    out = await client.structured(role="specialist", system="S", user="Judge the change evidence.",
                                  schema=SpecialistVerdict)

    assert out is not None and out.verdict == "found" and out.confidence == 0.8
    assert out.evidence_ids == ["E2"] and out.details == {"sha": "abc123"} and out.suggested_hypothesis is None
    fmt = api.bodies[0]["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["name"] == "SpecialistVerdict"
    assert fmt["schema"]["additionalProperties"] is False
    assert set(fmt["schema"]["required"]) == set(SpecialistVerdict.model_fields)
    assert api.models == ["muse-spark-1.3", "muse-spark-1.3"]
    retry_text = user_text(api.bodies[1]["input"][0])
    assert retry_text.startswith("Judge the change evidence.")             # stable prefix, error at the END
    assert "confidence: Input should be less than or equal to 1 (got 1.7)" in retry_text
    assert retry_text.rstrip().endswith("fixing these problems.")


@respx.mock
async def test_structured_returns_none_after_attempts_and_retries_truncation_with_bigger_budget(
        caplog: pytest.LogCaptureFixture) -> None:
    client = MetaResponsesClient(cfg())
    api = FakeAPI(*[reply(message("I think the deploy did it.")) for _ in range(3)])
    respx.post(URL).mock(side_effect=api)
    assert await client.structured(role="verdict", system="S", user="u", schema=SpecialistVerdict) is None
    assert len(api.bodies) == 3

    api = FakeAPI(
        reply(message('{"agent": "x", "verdict": "found", "headline": "Deploy'), status="incomplete",
              reason="max_output_tokens"),
        reply(message('{"agent": "x", "verdict": "found", "headline": "Deploy v42", "confidence": 0.7}')),
    )
    respx.post(URL).mock(side_effect=api)
    with caplog.at_level(logging.WARNING, logger="omniops.llm"):
        out = await client.structured(role="verdict", system="S", user="u", schema=SpecialistVerdict, attempts=1)
    assert out is not None and out.headline == "Deploy v42"
    assert [b["max_output_tokens"] for b in api.bodies] == [8_000, 16_000]  # the bump is not an attempt
    assert "stopped at max_output_tokens" in caplog.text


# ---------------------------------------------------------------------------------------------
# Transport retries, escalation, timeouts
# ---------------------------------------------------------------------------------------------


@respx.mock
async def test_429_and_5xx_are_retried_honouring_retry_after() -> None:
    api = FakeAPI(
        httpx.Response(429, headers={"retry-after": "0"},
                       json={"error": {"message": "Rate limit exceeded", "type": "rate_limit_error"}}),
        httpx.Response(529, json={"error": {"message": "overloaded", "type": "server_error"}}),
        reply(message("ok")),
    )
    respx.post(URL).mock(side_effect=api)
    client = MetaResponsesClient(cfg())
    resp = await client.complete(role="decision", system="S", input="ping")
    assert resp.text == "ok" and len(api.bodies) == 3 and client.llm_calls == 1


@respx.mock
async def test_invalid_request_is_not_retried_and_missing_key_fails_before_sending() -> None:
    api = FakeAPI(httpx.Response(400, json={"error": {"message": "reasoning.effort none is not supported",
                                                      "type": "invalid_request_error", "param": "reasoning"}}))
    respx.post(URL).mock(side_effect=api)
    with pytest.raises(LLMError) as err:
        await MetaResponsesClient(cfg()).complete(role="decision", system="S", input="ping")
    assert err.value.status == 400 and err.value.param == "reasoning" and len(api.bodies) == 1
    assert "retrying will not help" in err.value.lesson

    with pytest.raises(LLMError, match="MODEL_API_KEY is not set"):
        await MetaResponsesClient(ModelConfig(api_key="")).complete(role="decision", system="S", input="ping")
    assert len(api.bodies) == 1


@respx.mock
async def test_escalation_from_spark_to_wonder_is_sticky_for_the_run() -> None:
    good = '{"agent": "dependency", "verdict": "not_found", "headline": "No upstream errors", "confidence": 0.6}'
    api = FakeAPI(*[reply(message("Upstream looks fine to me.")) for _ in range(3)],
                  reply(message(good)), reply(message(good)), reply(message(good)))
    respx.post(URL).mock(side_effect=api)
    client = MetaResponsesClient(cfg())
    run = client.for_run("run-1")

    first = await run.structured(role="specialist", system="S", user="u", schema=SpecialistVerdict)
    assert first is not None and first.verdict == "not_found"
    assert api.models == ["muse-spark-1.3"] * 3 + ["muse-wonder-1.0"]   # 1 try + 2 retries, then escalate
    assert run.escalations == [{"role": "specialist", "from": "muse-spark-1.3", "to": "muse-wonder-1.0",
                                "reason": run.escalations[0]["reason"]}]
    assert run.escalations[0]["reason"].startswith("validation: invalid JSON")

    await run.structured(role="specialist", system="S", user="u", schema=SpecialistVerdict)
    assert api.models[-1] == "muse-wonder-1.0"                          # sticky within the run
    await client.for_run("run-2").structured(role="specialist", system="S", user="u", schema=SpecialistVerdict)
    assert api.models[-1] == "muse-spark-1.3"                           # a new run starts on the mid tier

    assert run.stats()["llm_calls"] == 5 and run.stats()["escalations"] == 1
    assert client.llm_calls == 6


@respx.mock
async def test_unavailable_model_escalates_immediately() -> None:
    api = FakeAPI(httpx.Response(404, json={"error": {"message": "model not found", "type": "invalid_request_error"}}),
                  reply(message("hello from the heavy tier")))
    respx.post(URL).mock(side_effect=api)
    resp = await MetaResponsesClient(cfg()).for_run("r").complete(role="specialist", system="S", input="hi")
    assert resp.text == "hello from the heavy tier" and resp.model == "muse-wonder-1.0"
    assert api.models == ["muse-spark-1.3", "muse-wonder-1.0"]


@respx.mock
async def test_outer_wait_expires_first_and_the_request_is_not_resent() -> None:
    sent = 0

    async def hang(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        await asyncio.sleep(5)
        return httpx.Response(200, json=reply(message("too late")))

    respx.post(URL).mock(side_effect=hang)
    t0 = time.monotonic()
    with pytest.raises(LLMTimeout):
        await MetaResponsesClient(cfg()).complete(role="report", system="S", input="write", timeout_s=0.3)
    assert time.monotonic() - t0 < 2.0 and sent == 1
    assert http_timeout_for(900) > 900


@respx.mock
async def test_reported_output_ceiling_is_remembered_and_retried_once() -> None:
    api = FakeAPI(
        httpx.Response(400, json={"error": {"message": "max_output_tokens must be less than or equal to 32,768",
                                            "type": "invalid_request_error", "param": "max_output_tokens"}}),
        reply(message("# Report\nTL;DR: deploy v42.")),
        reply(message("# Report again")),
    )
    respx.post(URL).mock(side_effect=api)
    client = MetaResponsesClient(cfg())
    resp = await client.complete(role="report", system="S", input="write the report")
    assert resp.text == "# Report\nTL;DR: deploy v42."
    assert [b["max_output_tokens"] for b in api.bodies] == [64_000, 32_768]
    await client.complete(role="report", system="S", input="again")
    assert api.bodies[-1]["max_output_tokens"] == 32_768                 # the probed ceiling sticks


@respx.mock
async def test_commentary_before_a_call_keeps_phase_and_unreplayable_reasoning_is_dropped() -> None:
    api = FakeAPI(
        reply({"type": "reasoning", "id": "rs_bare", "summary": []}, message("Let me check the pods first."),
              call("c1", "slow_sql", '{"sql": "select * from k8s_pods"}')),
        reply(message("Pods are crash-looping (E1).")),
    )
    respx.post(URL).mock(side_effect=api)

    async def slow_sql(args: dict[str, Any]) -> ToolOutput:
        return ToolOutput("3 pods in CrashLoopBackOff")

    result = await MetaResponsesClient(cfg()).run_tool_loop(
        role="decision", system="S", user="What broke?", tools=[ToolSpec("slow_sql", "Run SQL", SQL_PARAMS, slow_sql)],
        deadline=time.monotonic() + 60)
    assert result.final_text == "Pods are crash-looping (E1)."
    replay = api.bodies[1]["input"]
    assert [i["type"] for i in replay] == ["message", "message", "function_call", "function_call_output"]
    assert replay[1]["phase"] == "commentary" and replay[1]["content"][0]["text"] == "Let me check the pods first."
    assert [e.kind for e in result.events][:2] == ["assistant_text", "tool_call"]


@respx.mock
async def test_escalation_inside_a_tool_loop_drops_the_other_models_reasoning() -> None:
    api = FakeAPI(
        reply(reasoning(1), call("c1", "slow_sql", '{"sql": "select 1"}')),
        httpx.Response(404, json={"error": {"message": "model muse-spark-1.3 is not available",
                                            "type": "invalid_request_error", "code": "model_not_found"}}),
        reply(reasoning(2), message("Upstream payments is timing out (E1).")),
    )
    respx.post(URL).mock(side_effect=api)

    async def slow_sql(args: dict[str, Any]) -> ToolOutput:
        return ToolOutput("rows")

    run = MetaResponsesClient(cfg()).for_run("run-7")
    result = await run.run_tool_loop(role="specialist", system="S", user="Check dependencies.",
                                     tools=[ToolSpec("slow_sql", "Run SQL", SQL_PARAMS, slow_sql)],
                                     deadline=time.monotonic() + 60)
    assert result.final_text == "Upstream payments is timing out (E1)."
    assert api.models == ["muse-spark-1.3", "muse-spark-1.3", "muse-wonder-1.0"]
    assert [i["type"] for i in api.bodies[2]["input"]] == ["message", "function_call", "function_call_output"]
    assert run.stats()["escalations"] == 1 and run.model_for("specialist") == "muse-wonder-1.0"


# ---------------------------------------------------------------------------------------------
# Regressions: cut-off replies, commentary, failed turns, budgets, concurrency, streaming
# ---------------------------------------------------------------------------------------------


@respx.mock
async def test_an_answer_cut_off_at_the_output_limit_is_asked_again_with_the_raised_budget() -> None:
    api = FakeAPI(
        reply(reasoning(1), message("The root cause is the payments pod OOM; mitigat"), status="incomplete",
              reason="max_output_tokens"),
        reply(message("Root cause: payments pod OOM (E1). Mitigation: raise its memory limit.")),
    )
    respx.post(URL).mock(side_effect=api)
    result = await MetaResponsesClient(cfg()).run_tool_loop(role="chat", system="S", user="Why is payments down?",
                                                            tools=[], deadline=time.monotonic() + 60)
    assert result.final_text == "Root cause: payments pod OOM (E1). Mitigation: raise its memory limit."
    assert result.stopped_by == "final_text" and len(api.bodies) == 2
    assert [b["max_output_tokens"] for b in api.bodies] == [32_000, 64_000]
    assert "cut off at the output limit" in user_text(api.bodies[1]["input"][-1])
    assert [e.payload["reason"] for e in result.events if e.kind == "pushback"] == ["cut_off"]


@respx.mock
async def test_a_tool_call_cut_off_at_the_output_limit_is_not_run_with_repaired_arguments() -> None:
    cut = {**call("c1", "record_hypotheses", '{"hypotheses": [{"s": "a"}, {"s": "b"}, {"s": "c'),
           "status": "incomplete"}
    api = FakeAPI(
        reply(reasoning(1), cut, status="incomplete", reason="max_output_tokens"),
        reply(call("c2", "record_hypotheses", '{"hypotheses": [{"s": "a"}, {"s": "b"}, {"s": "c"}]}')),
        reply(message("Three hypotheses recorded.")),
    )
    respx.post(URL).mock(side_effect=api)
    received: list[Any] = []

    async def record(args: dict[str, Any]) -> ToolOutput:
        received.append(args["hypotheses"])
        return ToolOutput("ok")

    params = {"type": "object", "properties": {"hypotheses": {"type": "array"}}}
    result = await MetaResponsesClient(cfg()).run_tool_loop(
        role="decision", system="S", user="Form hypotheses.", tools=[ToolSpec("record_hypotheses", "", params, record)],
        deadline=time.monotonic() + 60)
    assert received == [[{"s": "a"}, {"s": "b"}, {"s": "c"}]]        # never the repaired two-item prefix
    first = next(e.payload for e in result.events if e.kind == "tool_result")
    assert first["is_error"] and "cut off at the output limit" in first["content"]
    assert api.bodies[1]["input"][-1]["output"].startswith("ERROR")
    assert api.bodies[1]["max_output_tokens"] == 32_000              # the call is asked again with more room


@respx.mock
async def test_commentary_before_a_tool_call_is_never_returned_as_the_answer() -> None:
    async def ok(args: dict[str, Any]) -> ToolOutput:
        return ToolOutput("rows")

    tools = [ToolSpec("slow_sql", "Run SQL", SQL_PARAMS, ok)]
    preamble = reply(message("Let me check the pod restarts first."), call("c1", "slow_sql", '{"sql": "select 1"}'))
    client = MetaResponsesClient(cfg())

    api = FakeAPI(preamble, httpx.Response(400, json={"error": {"message": "invalid input",
                                                                "type": "invalid_request_error"}}))
    respx.post(URL).mock(side_effect=api)
    failed = await client.run_tool_loop(role="decision", system="S", user="Go.", tools=tools,
                                        deadline=time.monotonic() + 60)
    assert failed.stopped_by == "error" and failed.final_text == ""
    assert [e.payload["text"] for e in failed.events if e.kind == "assistant_text"] == [
        "Let me check the pod restarts first."]                    # still visible for diagnostics

    api = FakeAPI(preamble, reply(message("")))
    respx.post(URL).mock(side_effect=api)
    capped = await client.run_tool_loop(role="decision", system="S", user="Go.", tools=tools,
                                        deadline=time.monotonic() + 60, max_steps=1)
    assert capped.stopped_by == "max_steps" and capped.final_text == ""


@respx.mock
async def test_a_timed_out_turn_is_retried_within_the_loop_deadline_then_answered_now() -> None:
    config = cfg()
    config.roles["decision"] = dataclasses.replace(config.role("decision"), timeout_s=0.3)
    client = MetaResponsesClient(config)

    api = AsyncAPI(hang, reply(message("Root cause: deploy v42 (E1).")))
    respx.post(URL).mock(side_effect=api)
    t0 = time.monotonic()
    result = await client.run_tool_loop(role="decision", system="S", user="Investigate.", tools=[],
                                        deadline=time.monotonic() + 60)
    assert result.stopped_by == "final_text" and result.final_text == "Root cause: deploy v42 (E1)."
    assert len(api.bodies) == 2 and time.monotonic() - t0 < 2.0

    async def ok(args: dict[str, Any]) -> ToolOutput:
        return ToolOutput("rows")

    api = AsyncAPI(hang, hang, hang, reply(message("Best supported answer; gaps: no traces.")))
    respx.post(URL).mock(side_effect=api)
    result = await client.run_tool_loop(role="decision", system="S", user="Investigate.",
                                        tools=[ToolSpec("slow_sql", "Run SQL", SQL_PARAMS, ok)],
                                        deadline=time.monotonic() + 60)
    assert result.final_text == "Best supported answer; gaps: no traces." and result.stopped_by == "error"
    assert len(api.bodies) == 4 and "tools" not in api.bodies[3]   # two retries, then one answer-now turn
    assert user_text(api.bodies[3]["input"][-1]).startswith("Time is up")


async def test_answer_now_turn_ends_inside_the_callers_outer_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    from omniops.agent import chat, orchestrator
    from omniops.llm import meta

    seen: list[tuple[bool, float]] = []

    async def turn(wrap_up: bool, timeout_s: float, budget: int) -> LLMResponse:
        seen.append((wrap_up, timeout_s))
        if wrap_up:
            return LLMResponse(text="Answer from the evidence so far.", tool_calls=[], usage=Usage(), model="m")
        return LLMResponse(text="", tool_calls=[ToolCall("c1", "slow", {})], usage=Usage(), model="m")

    async def slow(args: dict[str, Any]) -> ToolOutput:
        await asyncio.sleep(0.3)                                    # overruns the loop deadline
        return ToolOutput("rows")

    async def run(deadline_in: float) -> Any:
        return await drive_tool_loop(
            turn=turn, transcript=ChatTranscript(), user="q",
            tools=[ToolSpec("slow", "", {"type": "object", "properties": {}}, slow)],
            deadline=time.monotonic() + deadline_in, max_steps=5, turn_timeout_s=150.0, max_output_tokens=1000,
            ceiling=128_000)

    result = await run(0.1)
    assert result.stopped_by == "deadline" and result.final_text == "Answer from the evidence so far."
    (wrap_s,) = [t for w, t in seen if w]
    assert wrap_s <= meta.WRAP_UP_OVERRUN_S - 0.15                  # it ends by deadline + overrun ...
    assert chat.LOOP_GRACE_S > meta.WRAP_UP_OVERRUN_S              # ... which the callers' outer waits outlast
    assert orchestrator.LOOP_GRACE_S > meta.WRAP_UP_OVERRUN_S

    seen.clear()
    monkeypatch.setattr(meta, "WRAP_UP_OVERRUN_S", 0.05)
    late = await run(0.1)
    assert late.stopped_by == "deadline" and late.final_text == "" and [w for w, _ in seen] == [False]


@respx.mock
async def test_structured_timeout_is_one_budget_for_every_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    from omniops.llm import meta

    async def slow_invalid(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.3)
        return httpx.Response(200, json=reply(message("I think the deploy did it.")))

    api = AsyncAPI(*[slow_invalid] * 4)
    respx.post(URL).mock(side_effect=api)
    client = MetaResponsesClient(cfg())
    t0 = time.monotonic()
    assert await client.structured(role="verdict", system="S", user="u", schema=SpecialistVerdict,
                                   timeout_s=0.5) is None
    assert time.monotonic() - t0 < 0.7 and len(api.bodies) == 1   # no re-ask with almost nothing left

    monkeypatch.setattr(meta, "STRUCTURED_RETRY_MIN_S", 0.0)
    api.bodies.clear()
    t0 = time.monotonic()
    assert await client.structured(role="verdict", system="S", user="u", schema=SpecialistVerdict,
                                   timeout_s=0.5) is None
    assert time.monotonic() - t0 < 0.7 and len(api.bodies) == 2   # the re-ask only gets what is left


@respx.mock
async def test_a_concurrent_success_does_not_reset_another_calls_failure_streak() -> None:
    seen: list[tuple[str, str]] = []

    async def api(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        who = body["input"][0]["content"][0]["text"]
        seen.append((who, body["model"]))
        if who == "A":
            await asyncio.sleep(0.03)
            return httpx.Response(500, json={"error": {"message": "boom", "type": "server_error"}})
        await asyncio.sleep(0.005)
        return httpx.Response(200, json=reply(message("ok")))

    respx.post(URL).mock(side_effect=api)
    run = MetaResponsesClient(cfg(max_http_attempts=1)).for_run("run-9")
    done = False

    async def sibling() -> None:                                    # another specialist, succeeding meanwhile
        while not done:
            await run.complete(role="specialist", system="S", input="B")

    task = asyncio.create_task(sibling())
    try:
        with pytest.raises(LLMError):
            await run.complete(role="specialist", system="S", input="A")
    finally:
        done = True
        await task
    assert [m for w, m in seen if w == "A"] == ["muse-spark-1.3"] * 3 + ["muse-wonder-1.0"] * 3
    assert [(e["from"], e["to"]) for e in run.escalations] == [("muse-spark-1.3", "muse-wonder-1.0")]


@respx.mock
async def test_concurrent_calls_over_the_output_ceiling_all_retry_at_it() -> None:
    both_sent = asyncio.Event()
    over = 0
    budgets: list[int] = []

    async def api(request: httpx.Request) -> httpx.Response:
        nonlocal over
        body = json.loads(request.content)
        budgets.append(body["max_output_tokens"])
        if body["max_output_tokens"] > 32_768:
            over += 1
            if over == 2:
                both_sent.set()
            await asyncio.wait_for(both_sent.wait(), 2)            # both are in flight before either is rejected
            return httpx.Response(400, json={"error": {
                "message": "max_output_tokens must be less than or equal to 32,768",
                "type": "invalid_request_error", "param": "max_output_tokens"}})
        return httpx.Response(200, json=reply(message("ok")))

    respx.post(URL).mock(side_effect=api)
    client = MetaResponsesClient(cfg())
    results = await asyncio.gather(*(client.complete(role="decision", system="S", input=f"q{i}",
                                                     max_output_tokens=64_000) for i in range(2)))
    assert [r.text for r in results] == ["ok", "ok"]
    assert sorted(budgets) == [32_768, 32_768, 64_000, 64_000] and client.ceilings == {"muse-wonder-1.0": 32_768}


VLLM_CONTEXT = ("'max_tokens' or 'max_completion_tokens' is too large: 32000. This model's maximum context length is "
                "32768 tokens and your request has 8000 input tokens (32000 > 32768 - 8000).")


def test_reported_ceiling_only_trusts_a_number_tied_to_the_output_limit() -> None:
    def err(detail: str) -> LLMError:
        return LLMError("rejected", status=400, detail=detail, model_failure=False)

    assert reported_ceiling(err("max_output_tokens must be less than or equal to 32,768"), 64_000) == 32_768
    assert reported_ceiling(err("max_tokens is too large: 200000. This model supports at most 128000 completion "
                                "tokens, whereas you provided 200000."), 200_000) == 128_000
    assert reported_ceiling(err("max_tokens: 200000 > 64000, which is the maximum allowed number of output tokens"),
                            200_000) == 64_000
    assert reported_ceiling(err(VLLM_CONTEXT), 32_000) is None      # 8000 is the input size, not a ceiling
    assert reported_ceiling(err("max_output_tokens is invalid (trace 4711)"), 64_000) is None


def sse(*events: dict[str, Any]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


@respx.mock
async def test_report_streams_and_a_stalled_stream_is_retried_once() -> None:
    final = reply(reasoning(1), message("# Report\nTL;DR: deploy v42 (E1)."))
    events = [{"type": "response.created", "response": {"id": "resp_1", "status": "in_progress"}},
              {"type": "response.output_item.added", "item": {"type": "reasoning", "id": "rs_1"}},
              {"type": "response.output_text.delta", "delta": "# Report"},
              {"type": "response.completed", "response": final}]
    headers = {"content-type": "text/event-stream"}

    async def stalls(request: httpx.Request) -> httpx.Response:
        async def body() -> Any:
            yield sse(*events[:3])
            await asyncio.sleep(5)                                  # output started, then nothing
            yield sse(events[3])
        return httpx.Response(200, headers=headers, content=body())

    api = AsyncAPI(stalls, httpx.Response(200, headers=headers, content=sse(*events)))
    respx.post(URL).mock(side_effect=api)
    client = MetaResponsesClient(cfg(stream_stall_s=0.2))
    t0 = time.monotonic()
    resp = await client.complete(role="report", system="S", input="write the report")
    assert resp.text == "# Report\nTL;DR: deploy v42 (E1)." and time.monotonic() - t0 < 2.0
    assert len(api.bodies) == 2 and all(b["stream"] is True for b in api.bodies)
    assert resp.usage.input_tokens == 100 and client.llm_calls == 1

    api = FakeAPI(reply(message("ok")))
    respx.post(URL).mock(side_effect=api)
    await client.complete(role="decision", system="S", input="ping")
    assert "stream" not in api.bodies[0]                            # only long roles stream
