"""The o11y-bench runner through the *real* Slack simulator and Bolt app (D27: the production path).

Only the agent flow is a local stand-in (the chat flow is built in parallel): it runs a scripted
tool loop through ``deps.llm`` with real SQL over the Grafana manifest (HTTP mocked with respx) and
stages a dashboard save. Everything between the DM and the trajectory is production code: the DM
handler, the report pages, the approval card, the simulated user's click, the approved write.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from omniops.agent.context import Deps
from omniops.agent.types import RunResult, ToolEvent
from omniops.bench.o11ybench import runner
from omniops.bench.o11ybench.trajectory import final_answer, validate_trajectory
from omniops.engine.catalog import Catalog
from omniops.engine.engine import SQLEngine
from omniops.engine.timeutil import TimeAnchor, to_datetime
from omniops.llm.base import ToolOutput, ToolSpec
from omniops.llm.scripted import ScriptedLLM
from omniops.settings import Settings
from omniops.slack.simulator import SlackSimulator

GRAFANA = "http://grafana.test"
SCENARIO = "2026-10-26T12:00:00Z"
QUESTION = "Add a deployment annotation query to the svc-annotate dashboard, then tell me which datasources exist."
LIST_SQL = "SELECT name, type, uid FROM grafana_datasources"
DASHBOARD = '{"uid": "svc-annotate", "title": "Service Overview Annotate", "version": 1, "panels": []}'
WRITE_SQL = ("SELECT uid, version, url, status FROM grafana_dashboard_write WHERE uid = 'svc-annotate' "
             f"AND folder_uid = '' AND message = 'Add deployment annotations' AND dashboard_json = '{DASHBOARD}'")
ANSWER = ("Two datasources are configured: Prometheus (prometheus) and Loki (loki). "
          "The dashboard save for svc-annotate is staged for your approval.")


@pytest.fixture(scope="module")
def grafana_catalog() -> Catalog:
    """Grafana only; the env is read when the catalog is built."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GRAFANA_URL", GRAFANA)
        for name in ("GRAFANA_TOKEN", "GRAFANA_USERNAME", "GRAFANA_ORG_ID"):
            mp.delenv(name, raising=False)
        return Catalog.from_env(only=["grafana"])


class ChatStandIn:
    """The chat flow's contract (``answer(question, deps, thread=, status=) -> RunResult``)."""

    def __init__(self) -> None:
        self.questions: list[str] = []
        self.clock: list[Any] = []

    async def investigate(self, *args: Any, **kwargs: Any) -> RunResult:
        raise AssertionError("a DM must take the chat flow")

    async def answer(self, question: str, deps: Deps, *, thread: str | None = None, status: Any = None) -> RunResult:
        self.questions.append(question)
        engine = SQLEngine(deps.catalog, write_mode=deps.settings.write_mode)   # anchor: the pinned wall clock
        self.clock.append(engine.anchor.now)
        events: list[ToolEvent] = []
        staged: list[dict[str, Any]] = []

        async def run_sql(args: dict[str, Any]) -> ToolOutput:
            res = await engine.query(args["sql"])
            if res.staged_write:
                staged.append({**res.staged_write, "engine": engine})
            text = res.render()
            events.append(ToolEvent("chat", "run_sql", args, text))
            return ToolOutput(text)

        tool = ToolSpec("run_sql", "Run SQL", {"type": "object", "properties": {"sql": {"type": "string"}},
                                               "required": ["sql"]}, run_sql)
        loop = await deps.llm.for_run("chat1").run_tool_loop(role="chat", system="s", user=question, tools=[tool],
                                                              deadline=time.monotonic() + 30)
        return RunResult("chat1", "chat", loop.final_text, tool_events=events, staged_writes=staged)


async def test_dm_to_approved_dashboard_write_through_the_real_simulator(tmp_path: Path,
                                                                         grafana_catalog: Catalog) -> None:
    llm = ScriptedLLM(loops={"chat": [[{"name": "run_sql", "arguments": {"sql": LIST_SQL}}],
                                      [{"name": "run_sql", "arguments": {"sql": WRITE_SQL}}], ANSWER]})
    deps = Deps(catalog=grafana_catalog, llm=llm, settings=Settings(), memory=None)
    agent = ChatStandIn()
    sims: list[SlackSimulator] = []

    def factory(deps: Deps, **kwargs: Any) -> SlackSimulator:
        sims.append(SlackSimulator(deps, agent, status_interval_s=0.01, **kwargs))
        return sims[-1]

    out = tmp_path / "agent" / "trajectory.json"
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{GRAFANA}/api/datasources").mock(return_value=httpx.Response(200, json=[
            {"id": 1, "uid": "prometheus", "name": "Prometheus", "type": "prometheus"},
            {"id": 2, "uid": "loki", "name": "Loki", "type": "loki"}]))
        save = mock.post(f"{GRAFANA}/api/dashboards/db").mock(return_value=httpx.Response(200, json={
            "id": 7, "uid": "svc-annotate", "url": "/d/svc-annotate/service-overview-annotate", "status": "success",
            "version": 2, "slug": "service-overview-annotate"}))
        outcome = await runner.run_task(QUESTION, deps, out_path=out, scenario_iso=SCENARIO,
                                        anchor=TimeAnchor(now=to_datetime(SCENARIO), source="o11y_scenario_clock"),
                                        simulator_factory=factory, timeout_s=runner.DEFAULT_TIMEOUT_S)
        assert save.call_count == 1                       # executed once, after the simulated approval
        body = json.loads(save.calls[0].request.content)
        assert body["dashboard"]["uid"] == "svc-annotate" and body["message"] == "Add deployment annotations"

    sim = sims[0]
    dm = runner.compose_dm(QUESTION, SCENARIO)
    assert agent.questions == [dm]                         # the task arrived as the DM text, line breaks kept
    assert agent.clock == [to_datetime(SCENARIO)]          # one clock: the scenario's
    assert [a["write_id"] for a in sim.approvals] == [sim.run_results[0].staged_writes[0]["id"]]
    assert outcome.exit_code == runner.EXIT_OK

    data = json.loads(out.read_text())
    assert validate_trajectory(data) == []
    steps = data["steps"]
    assert [s["source"] for s in steps] == ["system", "user", "agent", "agent", "user", "agent"]
    assert steps[1]["message"] == dm
    assert [s["tool_calls"][0]["arguments"]["sql"] for s in steps[2:4]] == [LIST_SQL, WRITE_SQL]
    assert "Prometheus" in steps[2]["observation"]["results"][0]["content"]
    assert "staged" in steps[3]["observation"]["results"][0]["content"]
    wid = sim.approvals[0]["write_id"]
    assert steps[4]["message"].startswith(f"[Slack] The on-call user clicked Approve on staged write {wid}: "
                                          "grafana_dashboard_write with uid=svc-annotate")

    final = steps[-1]
    assert "tool_calls" not in final
    assert final["message"] == outcome.answer == final_answer(data)
    assert outcome.answer.startswith("Two datasources are configured: Prometheus (prometheus) and Loki (loki).")
    assert ("Update after approval:\n- Approved by the on-call user and executed (1 row(s)). uid=svc-annotate"
            in outcome.answer)
    assert "<@" not in outcome.answer and ":white_check_mark:" not in outcome.answer
    assert data["extra"]["final_answer_source"] == "slack_reply"
    assert data["extra"]["runs"][0]["run_id"] == "chat1"
    kinds = {e.get("kind") for e in data["extra"]["slack_transcript"]}
    assert {"delivery", "api", "approval"} <= kinds
    assert data["extra"]["warnings"] == []                 # a healthy run raises no loud-zero warning
