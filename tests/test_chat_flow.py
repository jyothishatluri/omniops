"""answer() and the Slack DM path through their real entry points: scripted model turns, real tools and
validation, respx-mocked Prometheus + Loki (+ Grafana for staged writes)."""

from __future__ import annotations

import json
from typing import Any

import pytest
import respx
from test_orchestrator_support import (  # noqa: F401 - source_env is a fixture (pytestmark)
    ERROR_SQL,
    LOGS_SQL,
    evidence_ids,
    finish,
    make_deps,
    mock_sources,
    pinned_clock,
    source_env,
    sql,
)

from omniops.agent import DefaultRunner, answer
from omniops.llm.scripted import ScriptedLLM
from omniops.slack.simulator import SlackSimulator

pytestmark = pytest.mark.usefixtures("source_env")

ANNOTATE_SQL = ("SELECT id, message FROM grafana_annotation_write WHERE text = 'checkout rollback' "
                "AND dashboard_uid = 'checkout'")


def _answer_citing(text: str) -> Any:
    def step(items: list[dict[str, Any]]) -> dict[str, Any]:
        eid = evidence_ids(items)[-1]
        return finish(text.replace("{E}", eid))
    return step


async def test_chat_answer_cites_evidence_and_links(tmp_path: Any) -> None:
    llm = ScriptedLLM(loops={"chat": [
        [sql(ERROR_SQL, "peak checkout 5xx rate in the last hour")],
        _answer_citing("*Answer*\nThe peak checkout 5xx rate was 9.5 req/s [{E}] (see "
                       "<https://evil.example/x|dashboard>) [E99]."),
    ]})
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        result = await answer("What was the peak checkout 5xx rate in the last hour?",
                              make_deps(llm, tmp_path=tmp_path), anchor=pinned_clock(), run_id="chat1")

    eid = next(e.evidence_id for e in result.tool_events if e.name == "run_sql")
    assert result.kind == "chat" and result.status == "ok" and result.card is None
    assert f"9.5 req/s [{eid}]" in result.answer_markdown
    assert "evil.example" not in result.answer_markdown and "E99" not in result.answer_markdown
    assert "prom.test/graph" in result.answer_markdown                # the link comes from the data
    assert "Time range queried (UTC): 2026-10-01T12:00:00Z to 2026-10-01T13:00:00Z" in result.answer_markdown
    assert not llm.calls_for("complete")                              # answered from the loop, no rewrite
    assert not llm.calls_for("structured", "triage")                  # an obvious ops question: code triage
    assert all(e.agent == "chat" for e in result.tool_events)
    case = json.loads((tmp_path / "cases" / "chat1.json").read_text())
    assert case["kind"] == "chat" and case["telemetry"]["triage"]["intent"] == "data"
    assert case["question"].startswith("What was the peak")


async def test_live_state_question_is_measured_before_answering(tmp_path: Any) -> None:
    llm = ScriptedLLM(loops={"chat": [
        "It is probably the database being slow.",                    # an unmeasured answer
        [sql(LOGS_SQL, "checkout error logs now")],
        _answer_citing("*Answer*\nCheckout requests fail on payment client timeouts after 3000 ms [{E}]."),
    ]})
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        result = await answer("Why is checkout failing right now?", make_deps(llm, tmp_path=tmp_path),
                              anchor=pinned_clock())

    turns = json.dumps([c.items for c in llm.calls_for("loop_turn", "chat")])
    assert "LIVE STATE: you have not measured anything yet" in turns
    assert result.stats.pushbacks["chat.live_state"] == 1
    assert "payment client timeouts" in result.answer_markdown and "database" not in result.answer_markdown
    assert any(e.name == "run_sql" and not e.is_error for e in result.tool_events)


async def test_off_topic_question_is_declined(tmp_path: Any) -> None:
    llm = ScriptedLLM(structured={"ChatTriage": {"intent": "off_topic", "live_state": False,
                                                 "reason": "a cooking question"}})
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        result = await answer("Can you recommend a good lasagna recipe?", make_deps(llm, tmp_path=tmp_path),
                              anchor=pinned_clock())
    assert "only help with questions about your systems" in result.answer_markdown
    assert not result.tool_events and not llm.calls_for("loop_turn")
    assert llm.calls_for("structured", "triage")


async def test_chat_below_the_evidence_floor_does_not_guess(tmp_path: Any) -> None:
    llm = ScriptedLLM(loops={"chat": [
        [sql(LOGS_SQL, "checkout error logs")],
        finish("*Answer*\nThere are no checkout errors."),
    ]})
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m, broken=True)
        result = await answer("How many checkout errors were logged in the last hour?",
                              make_deps(llm, tmp_path=tmp_path), anchor=pinned_clock())
    assert result.status == "insufficient_data"
    assert result.answer_markdown.startswith(":grey_question: *Insufficient data to answer*")
    assert "no checkout errors" not in result.answer_markdown


async def test_staged_write_is_surfaced_and_approved_in_slack(tmp_path: Any) -> None:
    """The o11y-bench path: a DM through the real Slack app; the write is staged, the answer names it,
    Slack posts an approval card and the simulated human approves it, which executes it once."""
    llm = ScriptedLLM(loops={"chat": [
        [sql(ANNOTATE_SQL, "stage the rollback annotation on the checkout dashboard")],
        finish("*Answer*\nI staged an annotation 'checkout rollback' on the checkout dashboard."),
    ]})
    with respx.mock(assert_all_called=False) as m:
        routes = mock_sources(m)
        deps = make_deps(llm, tmp_path=tmp_path, sources=["prometheus", "loki", "grafana"])
        sim = SlackSimulator(deps, runner=DefaultRunner(anchor=pinned_clock()), auto_approve_writes=True,
                             status_interval_s=0.0)
        try:
            await sim.dm("Add an annotation 'checkout rollback' to the checkout dashboard")
            assert await sim.wait_idle(60)
            shown = sim.final_answer_text()
        finally:
            await sim.close()

    [result] = sim.run_results
    [write] = result.staged_writes
    assert write["table"] == "grafana_annotation_write" and write["filters"]["text"] == "checkout rollback"
    assert hasattr(write["engine"], "execute_write")
    assert result.status == "ok" and write["id"] in result.answer_markdown
    assert "waiting for a human to approve it in Slack" in result.answer_markdown
    assert write["id"] in shown
    assert [a["write_id"] for a in sim.approvals] == [write["id"]]
    posts = [c for c in routes["grafana"].calls if c.request.method == "POST"]
    assert len(posts) == 1 and posts[0].request.url.path == "/api/annotations"
    assert json.loads(posts[0].request.content)["text"] == "checkout rollback"
