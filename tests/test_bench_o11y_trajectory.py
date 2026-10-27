"""ATIF trajectories as o11y-bench's grader reads them.

The grader's final-answer rule and ATIF parser are loaded from the vendored o11y-bench sources
(``grading/models.py`` needs Python 3.14 to import, so the relevant functions are extracted with
``ast`` and executed against the vendor's own dataclasses). When the vendor tree is absent the same
cases run against our replication only.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from typing import Any

import pytest

from omniops.bench.o11ybench.trajectory import (
    CLOSING_NOTE_MAX_CHARS,
    SCHEMA_VERSION,
    ToolStep,
    TrajectoryBuilder,
    clip,
    final_answer,
    slack_to_markdown,
    validate_trajectory,
)

BENCH_DIR = Path(os.environ.get("O11Y_BENCH_DIR", Path(__file__).resolve().parents[2] / "vendor" / "o11y-bench"))
LONG = "Root cause: payment-service 5xx share peaked at 12.4% at 10:42 UTC. " * 5


def _segment(src: str, node: ast.AST, indent: str = "") -> str:
    text = ast.get_source_segment(src, node) or ""
    return indent + text


def load_vendor_grader() -> Any:
    """``parse_atif_trajectory(dir) -> Transcript`` built from the vendor's own source, or None."""
    models_py, parser_py = BENCH_DIR / "grading" / "models.py", BENCH_DIR / "grading" / "transcript_parser.py"
    if not (models_py.is_file() and parser_py.is_file()):
        return None
    msrc, psrc = models_py.read_text(), parser_py.read_text()
    mtree, ptree = ast.parse(msrc), ast.parse(psrc)
    parts = ["from __future__ import annotations", "import json", "from dataclasses import dataclass, field",
             "from pathlib import Path", "from typing import Any"]
    for node in mtree.body:
        if isinstance(node, ast.ClassDef) and node.name in ("ToolCall", "ToolResult", "Message"):
            parts.append("@dataclass\n" + _segment(msrc, node))
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "CLOSING_NOTE_MAX_CHARS" for t in node.targets):
            parts.append(_segment(msrc, node))
    transcript = next(n for n in mtree.body if isinstance(n, ast.ClassDef) and n.name == "Transcript")
    methods = [_segment(msrc, f, "    ") for f in transcript.body
               if isinstance(f, ast.FunctionDef) and f.name in ("final_answer_indexes", "final_answer")]
    parts.append("@dataclass\nclass Transcript:\n    messages: list = field(default_factory=list)\n\n"
                 + "\n\n".join(methods))
    for node in ptree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ("_parse_atif_steps", "_result_is_error",
                                                                "parse_atif_trajectory"):
            parts.append(_segment(psrc, node))
    ns: dict[str, Any] = {}
    exec(compile("\n\n".join(parts), str(parser_py), "exec"), ns)  # noqa: S102 - vendored grader code
    return ns["parse_atif_trajectory"]


VENDOR_PARSE = load_vendor_grader()


def graded_answers(data: dict[str, Any], tmp_path: Path) -> list[str]:
    """Our rule's answer, plus the vendor grader's answer when available."""
    answers = [final_answer(data)]
    if VENDOR_PARSE is not None:
        d = tmp_path / f"t{len(list(tmp_path.iterdir()))}"
        d.mkdir()
        (d / "trajectory.json").write_text(json.dumps(data))
        answers.append(VENDOR_PARSE(d).final_answer())
    return answers


def step(n: int, source: str, message: str, *, calls: int = 0) -> dict[str, Any]:
    s: dict[str, Any] = {"step_id": n, "source": source, "message": message}
    if calls:
        ids = [f"c{n}-{i}" for i in range(calls)]
        s["tool_calls"] = [{"tool_call_id": i, "function_name": "run_sql", "arguments": {}} for i in ids]
        s["observation"] = {"results": [{"source_call_id": i, "content": "rows"} for i in ids]}
    return s


def traj(*steps: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "agent": {"name": "x", "version": "1"}, "steps": list(steps)}


CASES: list[tuple[str, dict[str, Any], str]] = [
    ("long final", traj(step(1, "system", "sys"), step(2, "user", "q"), step(3, "agent", "", calls=2),
                        step(4, "agent", LONG)), LONG.strip()),
    ("short closing note joins earlier long text",
     traj(step(1, "user", "q"), step(2, "agent", LONG), step(3, "agent", "", calls=1), step(4, "agent", "Done.")),
     LONG.strip() + "\n\nDone."),
    ("final text that also calls tools is planning", traj(step(1, "user", "q"), step(2, "agent", LONG),
                                                          step(3, "agent", "checking", calls=1)), ""),
    ("text before the last user message does not count",
     traj(step(1, "user", "q"), step(2, "agent", LONG), step(3, "user", "approve")), ""),
    ("tool-use placeholder is not text", traj(step(1, "user", "q"), step(2, "agent", "(tool use)")), ""),
    ("approval user step then the answer", traj(step(1, "user", "q"), step(2, "agent", "", calls=1),
                                                step(3, "user", "[Slack] approve"), step(4, "agent", "Saved.")),
     "Saved."),
]


@pytest.mark.parametrize(("name", "data", "expected"), CASES, ids=[c[0] for c in CASES])
def test_final_answer_selection_matches_the_grader(name: str, data: dict[str, Any], expected: str,
                                                   tmp_path: Path) -> None:
    for answer in graded_answers(data, tmp_path):
        assert answer == expected, name


def test_vendor_grader_is_used_when_present() -> None:
    if not (BENCH_DIR / "grading").is_dir():
        pytest.skip("vendored o11y-bench not present")
    assert VENDOR_PARSE is not None
    assert CLOSING_NOTE_MAX_CHARS == 200


def _builder(path: Path | None) -> TrajectoryBuilder:
    b = TrajectoryBuilder(path, agent_name="omniops", agent_version="0.1.0", model_name="muse-wonder-1.0")
    b.system_message = "system"
    b.user_message = "Current time: 2026-10-26T12:00:00Z\n\nWhich datasources exist?"
    return b


def test_builder_output_is_valid_and_graded_as_the_final_answer(tmp_path: Path) -> None:
    out = tmp_path / "agent" / "trajectory.json"
    b = _builder(out)
    b.add_tool_definitions([{"name": "run_sql", "description": "SQL", "parameters": {"type": "object"}}])
    b.add_tool_step(ToolStep("c1", "run_sql", {"sql": "SELECT name, type FROM grafana_datasources"},
                             "name | type\nPrometheus | prometheus", ts=1_800_000_000.0))
    b.add_tool_step(ToolStep("c2", "run_sql", {"sql": "SELECT 1"}, "Binder Error", is_error=True))
    b.add_user_event("[Slack] The on-call user clicked Approve on staged write w1: save dashboard")
    b.set_final("Two datasources: Prometheus (prometheus) and Loki (loki).")
    b.totals.prompt_tokens, b.totals.cached_tokens, b.totals.completion_tokens = 1000, 400, 50
    b.flush()

    data = json.loads(out.read_text())
    assert validate_trajectory(data) == []
    assert [s["source"] for s in data["steps"]] == ["system", "user", "agent", "agent", "user", "agent"]
    assert data["steps"][-1].get("tool_calls") is None
    assert data["steps"][3]["observation"]["results"][0]["extra"] == {"is_error": True}
    assert data["final_metrics"]["total_steps"] == 6
    assert data["final_metrics"]["extra"]["total_tool_calls"] == 2
    assert sorted(p.name for p in out.parent.iterdir()) == ["trajectory.json"]   # the grader parses every *.json
    for answer in graded_answers(data, tmp_path):
        assert answer == "Two datasources: Prometheus (prometheus) and Loki (loki)."


def test_tool_steps_carry_no_text_so_short_answers_are_not_merged(tmp_path: Path) -> None:
    b = _builder(None)
    b.add_tool_step(ToolStep("c1", "run_sql", {}, "x" * 5000))
    b.set_final("42")
    for answer in graded_answers(b.to_dict(), tmp_path):
        assert answer == "42"


def test_partial_trajectory_after_a_kill_is_valid_and_has_no_answer(tmp_path: Path) -> None:
    out = tmp_path / "trajectory.json"
    b = _builder(out)
    b.add_tool_step(ToolStep("c1", "run_sql", {"sql": "SELECT 1"}, "1"))        # flushed live
    data = json.loads(out.read_text())
    assert validate_trajectory(data) == []
    assert final_answer(data) == ""


def test_validator_reports_harbor_rule_violations() -> None:
    bad = traj(step(1, "user", "q", calls=1), step(3, "agent", "a"))
    bad["steps"][1]["foo"] = 1
    bad["bogus"] = True
    bad["final_metrics"] = {"total_prompt_tokens": 1, "total_cached_tokens": 5, "total_steps": 9}
    problems = "\n".join(validate_trajectory(bad))
    for expected in ("tool_calls is only allowed on agent steps", "step_id 3 is not sequential",
                     "undeclared field 'foo'", "undeclared field 'bogus'", "total_steps 9", "cached tokens"):
        assert expected in problems


def test_clip_marks_and_sizes_every_elision() -> None:
    text = "a" * 100 + "z" * 100
    out = clip(text, 60)
    assert "[140 characters omitted]" in out
    assert out.startswith("a" * 40) and out.endswith("z" * 20)
    assert clip("short", 60) == "short"


def test_slack_markup_becomes_markdown_without_rewriting_links() -> None:
    text = ("See <https://grafana.test/d/abc?orgId=1&amp;from=1|the dashboard> and <https://x.test/a> "
            "for `rate(http_requests_total{status=~\"5..\"}[5m]) &gt; 0.1` &amp; more")
    out = slack_to_markdown(text)
    assert "[the dashboard](https://grafana.test/d/abc?orgId=1&from=1)" in out
    assert "https://x.test/a for" in out and "<https" not in out
    assert '> 0.1` & more' in out
