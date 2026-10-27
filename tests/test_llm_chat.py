"""ChatCompletionsClient: the same LLMClient interface on any OpenAI-compatible /chat/completions endpoint."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import respx

from omniops.agent.types import SpecialistVerdict
from omniops.llm import ChatCompletionsClient, ModelConfig, ToolOutput, ToolSpec

BASE = "https://llm.internal.example/v1"
URL = f"{BASE}/chat/completions"
SQL = {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}


def cfg() -> ModelConfig:
    return ModelConfig(base_url=BASE + "/", protocol="chat", api_key="chat-key", retry_base_s=0.01,
                       retry_jitter_s=0.0, models={"heavy": "big-model", "mid": "small-model", "fast": "small-model"})


def completion(content: str | None, *tool_calls: dict[str, Any], finish: str = "stop") -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": content, "refusal": None}
    if tool_calls:
        msg["tool_calls"] = list(tool_calls)
    return {"id": "chatcmpl-1", "object": "chat.completion", "model": "big-model",
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150,
                      "prompt_tokens_details": {"cached_tokens": 100},
                      "completion_tokens_details": {"reasoning_tokens": 10}}}


def tool_call(call_id: str, name: str, arguments: str) -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


class FakeAPI:
    def __init__(self, *replies: dict[str, Any]) -> None:
        self.replies = list(replies)
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer chat-key"
        self.bodies.append(json.loads(request.content))
        return httpx.Response(200, json=self.replies.pop(0))


@respx.mock
async def test_chat_tool_loop_replays_assistant_calls_and_tool_results_in_call_order() -> None:
    api = FakeAPI(
        completion(None, tool_call("t1", "run_sql", '{"sql": "select slow"}'),
                   tool_call("t2", "run_sql", '{"sql": "select fast"}'), finish="tool_calls"),
        completion("Checkout errors follow deploy v42 (E1)."),
    )
    respx.post(URL).mock(side_effect=api)

    async def run_sql(args: dict[str, Any]) -> ToolOutput:
        if "slow" in args["sql"]:
            await asyncio.sleep(0.05)
        return ToolOutput(f"rows: {args['sql']}")

    client = ChatCompletionsClient(cfg())
    result = await client.run_tool_loop(role="chat", system="STATIC", user="Why is checkout failing?",
                                        tools=[ToolSpec("run_sql", "Run SQL", SQL, run_sql)],
                                        deadline=time.monotonic() + 30)

    assert result.final_text == "Checkout errors follow deploy v42 (E1)." and result.stopped_by == "final_text"
    first, second = api.bodies
    assert first["model"] == "big-model" and first["messages"][0] == {"role": "system", "content": "STATIC"}
    assert first["tools"][0] == {"type": "function", "function": {"name": "run_sql", "description": "Run SQL",
                                                                   "parameters": SQL}}
    assert first["tool_choice"] == "auto" and first["reasoning_effort"] == "medium"
    assert first["max_completion_tokens"] == 32_000 and "include" not in first and "input" not in first
    msgs = second["messages"]
    assert msgs[: len(first["messages"])] == first["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool", "tool"]
    assert [c["id"] for c in msgs[2]["tool_calls"]] == ["t1", "t2"]
    assert [(m["tool_call_id"], m["content"]) for m in msgs[3:]] == [("t1", "rows: select slow"),
                                                                    ("t2", "rows: select fast")]
    assert result.usage.input_tokens == 240 and result.usage.cached_tokens == 200
    assert result.usage.reasoning_tokens == 20


@respx.mock
async def test_chat_structured_uses_response_format_and_maps_effort() -> None:
    api = FakeAPI(
        completion('{"agent": "dependency", "verdict": "not_found", "headline": "No upstream errors"', finish="length"),
        completion('{"agent": "dependency", "verdict": "not_found", "headline": "No upstream errors", '
                   '"confidence": 0.7}'),
    )
    respx.post(URL).mock(side_effect=api)
    out = await ChatCompletionsClient(cfg()).structured(role="verdict", system="S", user="u",
                                                        schema=SpecialistVerdict, effort="max")
    assert out is not None and out.verdict == "not_found" and out.confidence == 0.7
    fmt = api.bodies[0]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["name"] == "SpecialistVerdict"
    assert api.bodies[0]["reasoning_effort"] == "xhigh"                  # generic endpoints have no "max"
    assert [b["max_completion_tokens"] for b in api.bodies] == [8_000, 16_000]   # cut off -> bigger budget


@respx.mock
async def test_chat_tool_call_cut_off_at_the_length_limit_is_not_run() -> None:
    api = FakeAPI(
        completion(None, tool_call("t1", "run_sql", '{"sql": "select 1", "limit": 10, "note": "trunc'), finish="length"),
        completion("Nothing was run from the cut-off call."),
    )
    respx.post(URL).mock(side_effect=api)
    ran: list[dict[str, Any]] = []

    async def run_sql(args: dict[str, Any]) -> ToolOutput:
        ran.append(args)
        return ToolOutput("rows")

    result = await ChatCompletionsClient(cfg()).run_tool_loop(
        role="chat", system="S", user="q", tools=[ToolSpec("run_sql", "Run SQL", SQL, run_sql)],
        deadline=time.monotonic() + 30)
    assert ran == [] and result.final_text == "Nothing was run from the cut-off call."
    tool_msg = api.bodies[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["content"].startswith("ERROR")
    assert "cut off at the output limit" in tool_msg["content"]


@respx.mock
async def test_a_context_window_rejection_resizes_only_that_request() -> None:
    msg = ("'max_tokens' or 'max_completion_tokens' is too large: 32000. This model's maximum context length is "
           "32768 tokens and your request has 8000 input tokens (32000 > 32768 - 8000).")
    bodies: list[dict[str, Any]] = []

    def api(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(400, json={"error": {"message": msg, "type": "BadRequestError", "param": None}})
        return httpx.Response(200, json=completion("ok"))

    respx.post(URL).mock(side_effect=api)
    client = ChatCompletionsClient(cfg())
    assert (await client.complete(role="chat", system="S", input="q")).text == "ok"
    await client.complete(role="chat", system="S", input="q2")
    assert [b["max_completion_tokens"] for b in bodies] == [32_000, 32_768 - 8_000 - 64, 32_000]
    assert client.ceilings == {}                                    # the input size is not the model's ceiling
