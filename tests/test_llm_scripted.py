"""ScriptedLLM: the offline LLMClient other modules test with runs the production loop and validator."""

from __future__ import annotations

import time
from typing import Any

from omniops.agent.types import SpecialistVerdict
from omniops.llm import LLMResponse, ScriptedLLM, ToolOutput, ToolSpec, Usage

PARAMS = {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}


async def test_structured_rules_by_schema_name_then_role_with_validation_feedback() -> None:
    seen: list[str] = []

    def judge(user: str) -> dict[str, Any]:
        seen.append(user)
        return {"agent": "code_change", "verdict": "found", "headline": "Deploy v42", "confidence": 0.8}

    llm = ScriptedLLM(structured={
        "SpecialistVerdict": [{"agent": "x", "verdict": "found", "headline": "h", "confidence": 3}, judge],
        "verdict": '```json\n{"agent": "v", "verdict": "Not applicable here", "headline": "n/a"}\n```',
    })

    out = await llm.structured(role="specialist", system="S", user="Judge it.", schema=SpecialistVerdict)
    assert out is not None and out.headline == "Deploy v42"
    first, second = llm.calls_for("structured")
    assert first.user == "Judge it." and second.user.startswith("Judge it.")
    assert second.user == seen[0]                                   # the callable saw the retry text
    assert "confidence: Input should be less than or equal to 1" in second.user

    class Other(SpecialistVerdict):
        pass

    by_role = await llm.structured(role="verdict", system="S", user="u", schema=Other)
    assert by_role is not None and by_role.verdict == "not_applicable"
    assert await llm.structured(role="memory", system="S", user="u", schema=Other) is None   # no rule -> fallback
    assert llm.calls_for("structured", "memory")[0].schema_name == "Other"


async def test_loop_steps_drive_real_handlers_gates_and_record_every_turn() -> None:
    ran: list[str] = []

    async def run_sql(args: dict[str, Any]) -> ToolOutput:
        ran.append(args["sql"])
        if "bad" in args["sql"]:
            err = RuntimeError("unknown column bad")
            err.lesson = "describe the table first"   # type: ignore[attr-defined]
            raise err
        return ToolOutput(f"rows for {args['sql']}", evidence_id=f"E{len(ran)}")

    pushes = iter(["Readiness gate: corroborate with a second source.", None])

    async def before_final(text: str, step: int) -> str | None:
        return next(pushes)

    llm = ScriptedLLM(loops={"decision": [
        [{"name": "run_sql", "arguments": {"sql": "select 1"}}, {"name": "run_sql", "arguments": '{"sql": "bad"}'}],
        "Draft answer (E1).",
        lambda items: {"text": "checking", "tool_calls": [{"name": "run_sql", "arguments": {"sql": "select 2"}}]},
        "Final: deploy v42 (E1, E3).",
    ]})
    tools = [ToolSpec("run_sql", "Run SQL", PARAMS, run_sql)]
    result = await llm.run_tool_loop(role="decision", system="S", user="Investigate.", tools=tools,
                                     deadline=time.monotonic() + 30, before_final=before_final)

    assert result.final_text == "Final: deploy v42 (E1, E3)." and result.stopped_by == "final_text"
    assert ran == ["select 1", "bad", "select 2"]
    turns = llm.calls_for("loop_turn", "decision")
    assert [t.tool_calls for t in turns] == [["run_sql", "run_sql"], [], ["run_sql"], []]
    second_turn_items = turns[1].items
    tool_msgs = [i for i in second_turn_items if i.get("role") == "tool"]
    assert tool_msgs[0]["content"] == "rows for select 1"
    assert "unknown column bad" in tool_msgs[1]["content"] and "Lesson: describe the table first" in tool_msgs[1]["content"]
    assert turns[2].items[-1] == {"role": "user", "content": "Readiness gate: corroborate with a second source."}
    assert [e.payload["evidence_id"] for e in result.events if e.kind == "tool_result"] == ["E1", None, "E3"]


async def test_loop_without_steps_answers_default_text_and_wrap_up_never_calls_tools() -> None:
    async def slow(args: dict[str, Any]) -> ToolOutput:
        return ToolOutput("rows")

    llm = ScriptedLLM(default_text="Nothing scripted.",
                      loops={"chat": [{"name": "run_sql", "arguments": {"sql": "x"}},
                                      {"name": "run_sql", "arguments": {"sql": "y"}}]})
    capped = await llm.run_tool_loop(role="chat", system="S", user="q", tools=[ToolSpec("run_sql", "", PARAMS, slow)],
                                     deadline=time.monotonic() + 30, max_steps=1)
    assert capped.stopped_by == "max_steps" and capped.final_text == "Nothing scripted."
    assert llm.calls_for("loop_turn")[-1].tools == []                   # the wrap-up turn offers no tools

    empty = await ScriptedLLM().run_tool_loop(role="report", system="S", user="q", tools=[],
                                              deadline=time.monotonic() + 30)
    assert empty.final_text == "(scripted: no more steps)" and empty.steps == 1


async def test_complete_rules_and_usage_parity() -> None:
    llm = ScriptedLLM(complete={"alert_parse": lambda user: {"service": "checkout", "echo": user},
                                "triage": "simple"})
    resp = await llm.complete(role="alert_parse", system="S", input="Alert: checkout 5xx")
    assert resp.text == '{"service": "checkout", "echo": "Alert: checkout 5xx"}'
    assert (await llm.complete(role="triage", system="S", input=[{"role": "user", "content": "hi"}])).text == "simple"
    assert llm.calls_for("complete", "triage")[0].user == "hi"

    canned = LLMResponse(text="raw", tool_calls=[], usage=Usage(input_tokens=5), model="m")
    fn = ScriptedLLM(complete=lambda role, user: canned if role == "memory" else f"{role}:{user}")
    assert await fn.complete(role="memory", system="S", input="x") is canned
    assert (await fn.complete(role="chat", system="S", input="x")).text == "chat:x"

    assert llm.for_run("r1") is llm and llm.stats()["llm_calls"] == 2 and llm.escalations == []
