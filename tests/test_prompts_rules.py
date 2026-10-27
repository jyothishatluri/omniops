"""Answer-shaping rules in the static prompts: comparison frames, differently scoped measurements and
totals over a window. Prompts are loaded through the same loaders the flows use."""

from __future__ import annotations

import re

import pytest

from omniops.agent import orchestrator, report

# Prompts are product text for any deployment: they never name a benchmark, its harness or its grader.
_BENCH_NAMES = re.compile(r"o11y[-_ ]?bench|\bbenchmark|\bharbor\b|\barena\b|\bgrader\b|terminal[-_ ]bench",
                          re.IGNORECASE)


def shared_section(title: str) -> str:
    """One '### <title>' block of the shared rules (up to the next heading)."""
    text = orchestrator.load_prompt("shared_rules")
    m = re.search(rf"^### {re.escape(title)}\n(.*?)(?=^#)", text, re.MULTILINE | re.DOTALL)
    assert m is not None, f"shared rules have no '### {title}' section"
    return " ".join(m.group(1).split())


def flat(text: str) -> str:
    return " ".join(text.split())


def bullets(text: str) -> list[str]:
    """The prompt's bullets and numbered items, whitespace-collapsed."""
    return [flat(item) for item in re.split(r"\n(?=- |\d+\. )", text)]


def bullet(text: str, opening: str) -> str:
    """The prompt bullet that starts with ``opening``."""
    for item in bullets(text):
        if item.lstrip("- ").startswith(opening):
            return item
    raise AssertionError(f"no bullet starts with {opening!r}")


CHAT = orchestrator.load_prompt("chat", "chat")
CHAT_ANSWER = report.load_prompt("chat_answer")


def test_shared_comparisons_answer_the_question_s_own_frame_first() -> None:
    comp = shared_section("Comparisons")
    assert "When the question defines the windows or the baseline" in comp
    assert ("the first verdict sentence gives, for each signal asked about, its value in the asked window, its "
            "value in the asked baseline and the ratio between the two") in comp
    assert "Findings from any other window" in comp and "separate sentence labelled with its own window" in comp
    assert "Never divide a value from one window by a value from another window that is not its baseline" in comp
    # the self-chosen baseline still applies when the question sets none
    assert "When the question sets no baseline, compare against an explicit one" in comp
    assert not re.search(r"\d", comp), "comparison rules carry no concrete numbers"


def test_shared_totals_come_from_one_aggregation_over_exactly_the_window() -> None:
    totals = shared_section("Totals over a window")
    assert "A single total over a window" in totals and "one aggregation over exactly that window" in totals
    assert "an instant query evaluated at the window's end with a range equal to the window" in totals
    assert "Never sum the step buckets of a range query into a total" in totals
    assert "unless the buckets are known to tile the window exactly" in totals
    assert not re.search(r"\d", totals)
    # every tool-using chat run sees it: the shared rules ride on the chat system prompt
    assert "### Totals over a window" in orchestrator.system_prompt("chat", "chat")


@pytest.mark.parametrize("prompt", [CHAT, CHAT_ANSWER], ids=["chat", "chat_answer"])
def test_chat_prompts_answer_a_framed_comparison_in_its_own_windows(prompt: str) -> None:
    rule = bullet(prompt, "When the question defines the windows or the baseline of a comparison")
    assert "the first sentence answers exactly that comparison" in rule
    assert "its value in the asked window, its value in the asked baseline and the ratio between them" in rule
    assert "Findings from other windows go in a separate sentence labelled with their own window" in rule
    assert "Never divide a value from one window by a value from another window that is not its baseline" in rule
    assert "a comparison the question framed is answered in its own windows first" in flat(prompt)
    assert "Every ratio divides a value by its own baseline" in flat(prompt)
    assert not re.search(r"\d", rule)


@pytest.mark.parametrize("prompt", [CHAT, CHAT_ANSWER], ids=["chat", "chat_answer"])
def test_chat_prompts_report_differently_scoped_measurements_side_by_side(prompt: str) -> None:
    text = flat(prompt)
    [rule] = [b for b in bullets(prompt) if "different scopes or resolutions" in b]
    assert "report both, each labelled with its scope, source and resolution" in rule
    assert "names its bucket bound, or reads \"at least <bound>\" when it is clamped" in rule
    assert "Lead with the value whose scope matches the question" in rule
    assert "Never silently drop a measured value" in rule
    assert not re.search(r"\d", rule)
    # one measurement still gets exactly one value: no range, bound or hedge
    assert re.search(r"(Never|Do not) give a range, a bound or a hedge for one measurement", text)
    assert "two different values for the same quantity" not in text      # the old blanket ban is gone
    assert "Exactly one value per measurement; values of the same quantity from differently scoped" in text


def test_chat_prompts_keep_totals_to_one_aggregation() -> None:
    assert "not from adding up step buckets" in bullet(CHAT, "A total over a window")
    rule = bullet(CHAT_ANSWER, "A total over a window")
    assert "one aggregation over exactly that window" in rule and "tile the window exactly" in rule


def test_prompts_name_no_benchmark() -> None:
    texts = {p.name: p.read_text(encoding="utf-8") for p in orchestrator.PROMPT_DIR.glob("*.md")}
    assert {"shared_rules.md", "chat.md", "chat_answer.md"} <= set(texts)
    hits = {name: m.group(0) for name, t in texts.items() if (m := _BENCH_NAMES.search(t))}
    assert hits == {}
