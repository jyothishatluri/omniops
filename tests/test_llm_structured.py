"""Schema-bound output: the JSON repair ladder, lenient coercion, label normalisation, Meta's schema subset."""

from __future__ import annotations

import json
from typing import Any, Literal

import pytest
from pydantic import BaseModel, Field

from omniops.agent.types import SpecialistVerdict
from omniops.llm import coerce_and_validate, normalize_label, parse_json_lenient, to_json_schema
from omniops.llm.structured import (
    ARG_PARSE_ERROR_KEY,
    StructuredTurn,
    fit_arguments,
    parse_tool_arguments,
    run_structured,
)

VERDICTS = ["confirmed", "refuted", "inconclusive"]


class Check(BaseModel):
    name: str
    status: Literal["confirmed", "refuted", "inconclusive"] = "inconclusive"


class Hypothesis(BaseModel):
    statement: str
    confidence: float = Field(ge=0.0, le=1.0)
    checks: list[Check] = Field(default_factory=list)
    note: str | None = None


class HypothesisSet(BaseModel):
    hypotheses: list[Hypothesis]


class Node(BaseModel):
    children: list[Node] = []


@pytest.mark.parametrize(("raw", "expected"), [
    ('```json\n{"a": 1}\n```', {"a": 1}),                                         # fences
    ('{"a": 1}', {"a": 1}),                                                       # strict parse
    ('{"a": 1, "b": [1, 2,],}', {"a": 1, "b": [1, 2]}),                           # trailing commas
    ('Here you go: {"a": {"b": [1, 2]}} - hope it helps', {"a": {"b": [1, 2]}}),  # balanced-brace scan
    ('<result>{"a": 1}</result>', {"a": 1}),                                      # stray XML tags
    ('<json>\n```json\n{"a": 1}\n```\n</json>', {"a": 1}),
    ('{"items": [{"n": 1}, {"n": 2}, {"n": 3', {"items": [{"n": 1}, {"n": 2}]}),  # truncation: drop the cut item
    ('{"a": {"b": {"c": 1, "d": "e', {"a": {"b": {"c": 1}}}),
    ('["a", "b', ["a"]),
])
def test_repair_ladder(raw: str, expected: Any) -> None:
    assert parse_json_lenient(raw) == expected


def test_repair_ladder_reports_why_nothing_parsed() -> None:
    with pytest.raises(ValueError, match="no parseable JSON"):
        parse_json_lenient("I think the deploy did it.")
    with pytest.raises(ValueError, match="empty reply"):
        parse_json_lenient("   ")


def test_tool_arguments_are_parsed_leniently() -> None:
    assert parse_tool_arguments({"sql": "select 1"}) == {"sql": "select 1"}
    assert parse_tool_arguments('"{\\"sql\\": \\"select 1\\"}"') == {"sql": "select 1"}   # double-encoded
    assert parse_tool_arguments("") == {}
    assert ARG_PARSE_ERROR_KEY in parse_tool_arguments("[1, 2]")
    assert ARG_PARSE_ERROR_KEY in parse_tool_arguments("select * from pods")
    params = {"type": "object", "properties": {"sql": {"type": "string"}}, "additionalProperties": False}
    assert fit_arguments({"arguments": {"sql": "select 1"}}, params) == {"sql": "select 1"}   # injected envelope
    assert fit_arguments({"sql": "select 1", "reason": "x"}, params) == {"sql": "select 1"}


@pytest.mark.parametrize("raw", [
    '[{"statement": "deploy v42", "confidence": 0.6}]',                                       # bare array
    '{"hypotheses": {"hypotheses": [{"statement": "deploy v42", "confidence": 0.6}]}}',      # double-wrapped
    '{"output": {"hypotheses": [{"statement": "deploy v42", "confidence": 0.6}]}}',          # envelope
    '{"hypotheses": "[{\\"statement\\": \\"deploy v42\\", \\"confidence\\": 0.6}]"}',         # nested JSON string
    '{"hypotheses": [{"statement": "deploy v42", "confidence": 0.6, "checks": null}]}',      # null -> default
])
def test_shape_variants_are_coerced_before_validation(raw: str) -> None:
    obj, err = coerce_and_validate(raw, HypothesisSet)
    assert err is None and obj is not None
    assert obj.hypotheses[0].statement == "deploy v42" and obj.hypotheses[0].checks == []


def test_nested_json_string_inside_xml_is_reparsed_and_labels_normalised() -> None:
    inner = '<checks>[{"name": "c1", "status": "Refuted by the error-rate data"}, {"name": "c2", ' \
            '"status": "ok (expected recurrence)"}, {"name": "c3", "status": "not confirmed"}]</checks>'
    payload = {"hypotheses": [{"statement": "pool exhaustion", "confidence": 0.4, "checks": inner}]}
    obj, err = coerce_and_validate(json.dumps(payload), HypothesisSet)
    assert err is None and obj is not None
    assert [c.status for c in obj.hypotheses[0].checks] == ["refuted", "inconclusive", "inconclusive"]


def test_strict_out_keeps_important_constraints() -> None:
    obj, err = coerce_and_validate('{"hypotheses": [{"statement": "x", "confidence": 1.4}]}', HypothesisSet)
    assert obj is None
    assert err == ("schema validation failed for HypothesisSet:\n"
                   "- hypotheses.0.confidence: Input should be less than or equal to 1 (got 1.4)")
    obj, err = coerce_and_validate("The deploy caused it.", HypothesisSet)
    assert obj is None and err is not None and err.startswith("invalid JSON")


def test_label_normalisation_ladder() -> None:
    assert normalize_label("Refuted", VERDICTS) == "refuted"                                 # exact
    assert normalize_label("disproven", VERDICTS) == "refuted"                               # alias
    assert normalize_label("severity", ["sev"], aliases={"severity": "sev"}) == "sev"        # caller alias
    assert normalize_label("likely confirmed, see E3", VERDICTS) == "confirmed"              # first allowed word
    assert normalize_label("not confirmed", VERDICTS, default="inconclusive") == "inconclusive"  # negation
    assert normalize_label("self/upstream", ["self", "upstream", "none"]) == "self"
    assert normalize_label("weird", VERDICTS) is None
    verdict, err = coerce_and_validate(
        '{"agent": "dependency", "verdict": "self/upstream", "headline": "h", "confidence": 0.5}', SpecialistVerdict)
    assert err is None and verdict is not None and verdict.verdict == "inconclusive"         # safe default


def test_json_schema_is_in_the_subset_meta_accepts() -> None:
    schema = to_json_schema(HypothesisSet)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            assert "$ref" not in node and "$defs" not in node and "title" not in node and "default" not in node
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    hyp = schema["properties"]["hypotheses"]["items"]
    assert hyp["properties"]["statement"] == {"type": "string"}                              # required: not nullable
    assert {"type": "null"} in hyp["properties"]["note"]["anyOf"]                            # optional: nullable
    assert {"type": "null"} in hyp["properties"]["checks"]["anyOf"]
    assert "maximum=1" in hyp["properties"]["confidence"]["description"]

    verdict = to_json_schema(SpecialistVerdict)
    details = verdict["properties"]["details"]
    assert details["anyOf"][0] == {"type": "string"} and "JSON" in details["description"]  # free-form -> JSON text


def test_recursive_schemas_are_rejected_at_build_time() -> None:
    with pytest.raises(ValueError, match="recursive schema"):
        to_json_schema(Node)


async def test_a_reply_cut_off_after_the_budget_bump_is_rejected_never_repaired() -> None:
    class Plan(BaseModel):
        title: str
        items: list[str]

    cut = '{"title": "x", "items": ["a", "b", "c'
    replies: list[str] = []
    budgets: list[int] = []
    users: list[str] = []
    rejects: list[str] = []

    async def turn(text: str, budget: int) -> StructuredTurn:
        users.append(text)
        budgets.append(budget)
        r = replies.pop(0)
        return StructuredTurn(r, "max_output_tokens" if r == cut else "completed", "m")

    def on_reject(model: str, error: str) -> bool:
        rejects.append(error)
        return False

    replies.extend([cut, cut, '{"title": "x", "items": ["a", "b", "c"]}'])
    out = await run_structured(turn, user="u", schema=Plan, attempts=2, max_output_tokens=4000, ceiling=128_000,
                               on_reject=on_reject)
    assert out is not None and out.items == ["a", "b", "c"]          # not Plan(items=["a", "b"]) after the bump
    assert budgets == [4000, 8000, 8000]
    assert "cut off at the output limit" in users[2] and rejects == []

    replies.extend([cut])                                             # at the ceiling there is no bump
    assert await run_structured(turn, user="u", schema=Plan, attempts=1, max_output_tokens=128_000,
                                ceiling=128_000) is None              # the caller's fallback, not a repaired object
