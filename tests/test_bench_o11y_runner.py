"""The o11y-bench in-container runner through its real entry points (``run_task`` / ``main``).

The Slack simulator and the chat flow are built in parallel, so a small stub simulator stands in:
it receives the DM, runs one tool loop through ``deps.llm`` (as the chat flow does), returns a
``RunResult`` and shows a Slack reply. The runner must turn that into a valid ATIF trajectory whose
final step the grader selects, flushing tool steps live.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest
import respx

from omniops.agent.context import Deps
from omniops.agent.types import RunResult, RunStats, ToolEvent
from omniops.bench.o11ybench import runner
from omniops.bench.o11ybench.trajectory import final_answer, validate_trajectory
from omniops.engine.loader import load_all_manifests
from omniops.engine.timeutil import TimeAnchor
from omniops.llm.base import LoopEvent, LoopResult, ToolOutput, ToolSpec, Usage
from omniops.settings import Settings

SCENARIO = "2026-10-26T12:00:00Z"
SCENARIO_DT = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
QUESTION = "What datasources are configured in Grafana? List their names and types."
SLACK_REPLY = ("*Datasources* (2):\n- Prometheus (prometheus)\n- Loki (loki)\n"
               "Open <https://grafana.test/connections/datasources?a=1&amp;b=2|Grafana data sources>")
STACK_ENV = ("GRAFANA_URL", "PROMETHEUS_URL", "LOKI_URL", "TEMPO_URL")
_MANIFESTS: dict[str, Any] = {}


@pytest.fixture
def manifests(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The bundled manifests, parsed once per session (the runner and Catalog.from_env both load them)."""
    if not _MANIFESTS:
        _MANIFESTS.update(load_all_manifests(strict=False)[0])

    def cached(strict: bool = True) -> tuple[dict[str, Any], list[str]]:
        return dict(_MANIFESTS), []

    monkeypatch.setattr("omniops.engine.loader.load_all_manifests", cached)
    monkeypatch.setattr("omniops.engine.catalog.load_all_manifests", cached)
    return dict(_MANIFESTS)


@dataclass
class StubCatalog:
    runtimes: dict[str, Any] = field(default_factory=lambda: {"grafana": object()})

    def sources_overview(self) -> str:
        return "grafana: Grafana HTTP API"

    async def aclose(self) -> None:
        return None


class StubLLM:
    """Emits the tool loop events exactly as ``llm.meta.drive_tool_loop`` does, and counts usage."""

    def __init__(self, calls: list[tuple[str, dict[str, Any]]], usage: Usage | None = None) -> None:
        self.calls = calls
        self.usage = usage or Usage(input_tokens=12_000, cached_tokens=8_000, output_tokens=900,
                                    reasoning_tokens=400, cost_usd=0.04)
        self.scoped: list[str | None] = []

    def for_run(self, run_id: str | None) -> StubLLM:
        self.scoped.append(run_id)
        return self

    async def run_tool_loop(self, *, role: str, system: str, user: str, tools: list[ToolSpec], deadline: float,
                            on_event: Any = None, **_: Any) -> LoopResult:
        by_name = {t.name: t for t in tools}
        for n, (name, args) in enumerate(self.calls, start=1):
            cid = f"call_{n}"
            await on_event(LoopEvent(n, "tool_call", {"id": cid, "name": name, "arguments": args}))
            out = await by_name[name].handler(args)
            await on_event(LoopEvent(n, "tool_result", {"id": cid, "name": name, "content": out.content,
                                                         "is_error": out.is_error, "evidence_id": out.evidence_id}))
        await on_event(LoopEvent(len(self.calls) + 1, "final", {"text": "done", "stopped_by": "final_text"}))
        return LoopResult("done", len(self.calls) + 1, "final_text", Usage(), [])


class StubSimulator:
    """Stands in for slack.simulator.SlackSimulator: DM in, chat run, Slack reply out."""

    instances: ClassVar[list[StubSimulator]] = []

    def __init__(self, deps: Deps, auto_approve_writes: bool = False, anchor: TimeAnchor | None = None,
                 reply: str = SLACK_REPLY, staged: bool = False, fail: bool = False) -> None:
        self.deps, self.auto_approve_writes, self.anchor = deps, auto_approve_writes, anchor
        self.reply, self.staged, self.fail = reply, staged, fail
        self.dms: list[str] = []
        self.results: list[RunResult] = []
        self.clock_seen: datetime | None = None
        self.live_steps_seen: list[int] = []
        self.out_path: Path | None = None
        self.closed = False
        StubSimulator.instances.append(self)

    async def dm(self, text: str) -> str:
        if self.fail:
            raise RuntimeError("socket closed")
        self.dms.append(text)
        self.clock_seen = TimeAnchor.wall_clock().now
        events: list[ToolEvent] = []

        async def run_sql(args: dict[str, Any]) -> ToolOutput:
            if self.out_path is not None and self.out_path.exists():   # the previous call is already on disk
                self.live_steps_seen.append(len(json.loads(self.out_path.read_text())["steps"]))
            content = "name | type\nPrometheus | prometheus\nLoki | loki"
            events.append(ToolEvent("chat", "run_sql", args, content, evidence_id="E1"))
            return ToolOutput(content, evidence_id="E1")

        tool = ToolSpec("run_sql", "Run SQL", {"type": "object", "properties": {"sql": {"type": "string"}}}, run_sql)
        llm = self.deps.llm.for_run("r1")
        await llm.run_tool_loop(role="chat", system="s", user=text, tools=[tool], deadline=time.monotonic() + 30)
        writes = [{"id": "w1", "table": "grafana_dashboard_write", "summary": "save dashboard 'Service Overview'"}]
        self.results.append(RunResult("r1", "chat", "Two datasources: Prometheus and Loki.", tool_events=events,
                                      staged_writes=writes if self.staged else [], stats=RunStats()))
        return "1700000000.000100"

    async def wait_idle(self, timeout: float | None = None) -> None:
        return None

    def final_answer_text(self, thread_ts: str) -> str:
        return self.reply

    def transcript(self) -> list[dict[str, Any]]:
        msgs = [{"user": "U1", "text": d} for d in self.dms] + [{"bot": True, "text": self.reply}]
        if self.staged:
            msgs.append({"bot": True, "text": ":white_check_mark: Approved by <@U1> and executed (1 row(s)). "
                                              "uid=svc-overview; url=/d/svc-overview"})
        return msgs

    async def aclose(self) -> None:
        self.closed = True


def make_deps(llm: Any) -> Deps:
    return Deps(catalog=StubCatalog(), llm=llm, settings=Settings())   # type: ignore[arg-type]


def factory(**opts: Any) -> Any:
    def make(deps: Deps, auto_approve_writes: bool = False, anchor: TimeAnchor | None = None) -> StubSimulator:
        return StubSimulator(deps, auto_approve_writes, anchor, **opts)
    return make


async def run(tmp_path: Path, llm: Any, **opts: Any) -> tuple[runner.RunOutcome, dict[str, Any], StubSimulator]:
    out = tmp_path / "agent" / "trajectory.json"
    StubSimulator.instances.clear()
    original = TimeAnchor.wall_clock
    outcome = await runner.run_task(QUESTION, make_deps(llm), out_path=out, scenario_iso=SCENARIO,
                                    anchor=TimeAnchor(now=SCENARIO_DT, source="o11y_scenario_clock"),
                                    simulator_factory=factory(**opts), timeout_s=600,
                                    preflight={"grafana": "ok: 2 rows (SELECT count(*) AS n FROM grafana_datasources)"})
    assert TimeAnchor.wall_clock == original                     # the process clock pin is undone
    sim = StubSimulator.instances[-1]
    return outcome, json.loads(out.read_text()), sim


async def test_dm_in_trajectory_out(tmp_path: Path) -> None:
    llm = StubLLM([("run_sql", {"sql": "SELECT name, type FROM grafana_datasources"}),
                   ("run_sql", {"sql": "SELECT count(*) FROM grafana_datasources"})])
    StubSimulator.instances.clear()
    outcome, data, sim = await run(tmp_path, llm)

    assert outcome.exit_code == runner.EXIT_OK
    assert sim.dms == [f"Current time: {SCENARIO}\n\n{QUESTION}"]          # the task arrives as a Slack DM
    assert sim.auto_approve_writes is True and sim.anchor is not None and sim.closed
    assert sim.clock_seen == SCENARIO_DT                                     # one clock: the scenario's
    assert llm.scoped == ["r1"]
    assert validate_trajectory(data) == []
    sources = [s["source"] for s in data["steps"]]
    assert sources == ["system", "user", "agent", "agent", "agent"]
    tool_steps = data["steps"][2:4]
    assert [s["tool_calls"][0]["arguments"]["sql"] for s in tool_steps] == [
        "SELECT name, type FROM grafana_datasources", "SELECT count(*) FROM grafana_datasources"]
    assert all(s["observation"]["results"][0]["content"].startswith("name | type") for s in tool_steps)
    final = data["steps"][-1]
    assert "tool_calls" not in final
    expected = ("*Datasources* (2):\n- Prometheus (prometheus)\n- Loki (loki)\n"
                "Open [Grafana data sources](https://grafana.test/connections/datasources?a=1&b=2)")
    assert final["message"] == expected == outcome.answer
    assert final_answer(data) == expected
    fm = data["final_metrics"]
    assert (fm["total_prompt_tokens"], fm["total_cached_tokens"], fm["total_completion_tokens"]) == (12_000, 8_000, 900)
    assert fm["total_cost_usd"] == pytest.approx(0.04)
    assert fm["extra"]["total_tool_calls"] == 2
    assert data["extra"]["entry_point"] == "slack_dm" and data["extra"]["mcp"] == "not used"
    assert data["extra"]["preflight"]["grafana"].startswith("ok")
    assert data["agent"]["tool_definitions"][0]["name"] == "run_sql"


async def test_tool_steps_reach_the_file_while_the_run_is_live(tmp_path: Path) -> None:
    llm = StubLLM([("run_sql", {"sql": "SELECT 1"}), ("run_sql", {"sql": "SELECT 2"})])
    out = tmp_path / "agent" / "trajectory.json"

    def make(deps: Deps, auto_approve_writes: bool = False, anchor: TimeAnchor | None = None) -> StubSimulator:
        sim = StubSimulator(deps, auto_approve_writes, anchor)
        sim.out_path = out
        return sim

    StubSimulator.instances.clear()
    await runner.run_task(QUESTION, make_deps(llm), out_path=out, scenario_iso=SCENARIO,
                          anchor=TimeAnchor(now=SCENARIO_DT), simulator_factory=make, timeout_s=600)
    # system + user before the first call; one more step on disk before the second call ran
    assert StubSimulator.instances[-1].live_steps_seen == [2, 3]


async def test_staged_write_is_approved_by_the_simulated_user(tmp_path: Path) -> None:
    llm = StubLLM([("run_sql", {"sql": "SELECT 1"})])
    outcome, data, _ = await run(tmp_path, llm, staged=True, reply="Staged the dashboard for approval.")
    assert validate_trajectory(data) == []
    user_steps = [s["message"] for s in data["steps"] if s["source"] == "user"]
    assert user_steps[-1].startswith("[Slack] The on-call user clicked Approve on staged write w1")
    assert data["steps"][-1]["source"] == "agent"
    assert outcome.answer == ("Staged the dashboard for approval.\n\nStaged writes:\n- Approved by the on-call user "
                              "and executed (1 row(s)). uid=svc-overview; url=/d/svc-overview")
    assert final_answer(data) == outcome.answer


async def test_simulator_crash_is_an_infrastructure_failure_with_a_valid_trajectory(tmp_path: Path) -> None:
    outcome, data, _ = await run(tmp_path, StubLLM([]), fail=True)
    assert outcome.exit_code == runner.EXIT_HARNESS
    assert validate_trajectory(data) == []
    assert any("Slack simulator failed: RuntimeError: socket closed" in w for w in data["extra"]["warnings"])


async def test_model_never_reached_fails_the_trial_loudly(tmp_path: Path) -> None:
    outcome, data, _ = await run(tmp_path, StubLLM([], usage=Usage()), reply="")
    # the stub's run result still carries an answer: answered runs are graded, not failed
    assert outcome.exit_code == runner.EXIT_OK
    assert outcome.answer == "Two datasources: Prometheus and Loki."
    assert "no final Slack reply was found: using the run's answer text" in data["extra"]["warnings"]

    class Silent(StubSimulator):
        async def dm(self, text: str) -> str:
            self.dms.append(text)
            return "ts"

    out = tmp_path / "silent.json"
    result = await runner.run_task(QUESTION, make_deps(StubLLM([], usage=Usage())), out_path=out,
                                   scenario_iso=SCENARIO, anchor=TimeAnchor(now=SCENARIO_DT),
                                   simulator_factory=lambda deps, **kw: Silent(deps, reply=""), timeout_s=600)
    assert result.exit_code == runner.EXIT_MODEL_UNREACHED
    warnings = json.loads(out.read_text())["extra"]["warnings"]
    assert "zero tool calls: nothing was queried" in warnings
    assert "no final answer: the grader will score this trial as no_answer" in warnings


async def test_scripted_llm_tool_loop_is_recorded(tmp_path: Path) -> None:
    scripted = pytest.importorskip("omniops.llm.scripted")
    llm = scripted.ScriptedLLM(loops={"chat": [[{"name": "run_sql", "arguments": {"sql": "SELECT 1"}}],
                                               "Two datasources."]})
    llm.usage = Usage(input_tokens=10, output_tokens=5)

    class LoopOnly(StubSimulator):
        async def dm(self, text: str) -> str:
            async def run_sql(args: dict[str, Any]) -> ToolOutput:
                return ToolOutput("1 row")
            tool = ToolSpec("run_sql", "Run SQL", {"type": "object", "properties": {"sql": {"type": "string"}}},
                            run_sql)
            await self.deps.llm.for_run("r9").run_tool_loop(role="chat", system="s", user=text, tools=[tool],
                                                             deadline=time.monotonic() + 30)
            return "ts"

        def transcript(self) -> list[dict[str, Any]]:
            return []

    out = tmp_path / "trajectory.json"
    outcome = await runner.run_task(QUESTION, make_deps(llm), out_path=out, scenario_iso=SCENARIO,
                                    anchor=TimeAnchor(now=SCENARIO_DT),
                                    simulator_factory=lambda deps, **kw: LoopOnly(deps, reply="Two datasources."),
                                    timeout_s=600)
    data = json.loads(out.read_text())
    assert outcome.exit_code == runner.EXIT_OK
    assert validate_trajectory(data) == []
    calls = [s for s in data["steps"] if s.get("tool_calls")]
    assert [c["tool_calls"][0]["arguments"] for c in calls] == [{"sql": "SELECT 1"}]
    assert calls[0]["observation"]["results"][0]["content"] == "1 row"
    assert "exposed no run results" in " ".join(data["extra"]["warnings"])


def test_source_selection_prefers_direct_apis_then_grafana_proxies(monkeypatch: pytest.MonkeyPatch,
                                                                    manifests: dict[str, Any]) -> None:
    for name in (*STACK_ENV, runner.SOURCES_OVERRIDE_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GRAFANA_URL", "http://grafana.test")
    assert runner.select_sources(manifests) == (["grafana", "grafana_prometheus", "grafana_loki", "grafana_tempo"], [])
    for name, url in zip(STACK_ENV[1:], ("http://prom.test", "http://loki.test", "http://tempo.test"), strict=True):
        monkeypatch.setenv(name, url)
    assert runner.select_sources(manifests) == (["grafana", "prometheus", "loki", "tempo"], [])
    monkeypatch.setenv(runner.SOURCES_OVERRIDE_ENV, "grafana, grafana_loki")
    assert runner.select_sources(manifests) == (["grafana", "grafana_loki"], [])


def test_scenario_clock_and_dm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(runner.SCENARIO_ENV, SCENARIO)
    iso, anchor = runner.scenario_clock()
    assert iso == SCENARIO and anchor.now == SCENARIO_DT and anchor.window_start is None
    assert runner.compose_dm("  Which jobs?\n", iso) == f"Current time: {SCENARIO}\n\nWhich jobs?"


def _args(tmp_path: Path, instruction: str | None) -> list[str]:
    inst = tmp_path / "instruction.txt"
    if instruction is not None:
        inst.write_text(instruction)
    return ["--instruction-file", str(inst), "--out", str(tmp_path / "agent" / "trajectory.json")]


def test_main_without_instruction_or_sources_exits_2_and_says_why(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                  manifests: dict[str, Any]) -> None:
    for name in (*STACK_ENV, runner.SOURCES_OVERRIDE_ENV, "OMNIOPS_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(runner.SCENARIO_ENV, SCENARIO)
    assert runner.main(_args(tmp_path, None)) == runner.EXIT_CONFIG
    data = json.loads((tmp_path / "agent" / "trajectory.json").read_text())
    assert validate_trajectory(data) == [] and "cannot read the instruction" in data["extra"]["warnings"][0]

    assert runner.main(_args(tmp_path, QUESTION)) == runner.EXIT_CONFIG
    data = json.loads((tmp_path / "agent" / "trajectory.json").read_text())
    assert "no data source is configured" in data["extra"]["warnings"][0]
    assert data["steps"][1]["message"].startswith(f"Current time: {SCENARIO}")


@respx.mock
def test_main_end_to_end_with_mocked_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                           manifests: dict[str, Any]) -> None:
    for name in (runner.SOURCES_OVERRIDE_ENV, "OMNIOPS_CONFIG", "OMNIOPS_LLM_API_KEY_ENV", "OMNIOPS_LLM_PROTOCOL"):
        monkeypatch.delenv(name, raising=False)
    env = {"GRAFANA_URL": "http://grafana.test", "PROMETHEUS_URL": "http://prom.test", "LOKI_URL": "http://loki.test",
           "TEMPO_URL": "http://tempo.test", "MODEL_API_KEY": "test-key", runner.SCENARIO_ENV: SCENARIO}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    respx.get("http://grafana.test/api/datasources").mock(return_value=httpx.Response(200, json=[
        {"id": 1, "uid": "prometheus", "name": "Prometheus", "type": "prometheus", "url": "http://localhost:9090"}]))
    respx.route(host="prom.test").mock(return_value=httpx.Response(200, json={
        "status": "success", "data": {"activeTargets": [], "droppedTargets": []}}))
    respx.route(host="loki.test").mock(return_value=httpx.Response(200, json={"status": "success", "data": ["job"]}))
    respx.route(host="tempo.test").mock(return_value=httpx.Response(400, text="bad request"))
    seen: list[Any] = []

    class StubClient(StubLLM):                    # stands in for llm.meta.MetaResponsesClient(config)
        def __init__(self, config: Any) -> None:
            assert config.api_key == "test-key" and config.models["heavy"] == "muse-wonder-1.0"
            super().__init__([("run_sql", {"sql": "SELECT 1"})])

        async def aclose(self) -> None:
            seen.append("llm closed")

    def make(deps: Deps, auto_approve_writes: bool = False, anchor: TimeAnchor | None = None) -> StubSimulator:
        seen.append(sorted(deps.catalog.runtimes))
        return StubSimulator(deps, auto_approve_writes, anchor)

    monkeypatch.setattr("omniops.llm.meta.MetaResponsesClient", StubClient)
    monkeypatch.setattr(runner, "_default_simulator", lambda: make)
    assert runner.main(_args(tmp_path, QUESTION)) == runner.EXIT_OK
    data = json.loads((tmp_path / "agent" / "trajectory.json").read_text())
    assert seen == [["grafana", "loki", "prometheus", "tempo"], "llm closed"]
    assert validate_trajectory(data) == []
    preflight = data["extra"]["preflight"]
    assert preflight["grafana"] == "ok: 1 rows (SELECT count(*) AS n FROM grafana_datasources)"
    assert preflight["loki"].startswith("ok") and preflight["prometheus"].startswith("ok")
    assert preflight["tempo"].startswith("FAILED") and any(w.startswith("preflight tempo FAILED")
                                                         for w in data["extra"]["warnings"])
    assert data["final_metrics"]["total_prompt_tokens"] == 12_000
    assert final_answer(data).startswith("*Datasources* (2)")
    assert "test-key" not in (tmp_path / "agent" / "trajectory.json").read_text()   # never persist secrets


def test_write_outcomes_keep_the_head_of_each_decision_and_never_mangle_times() -> None:
    dashboard = '{"panels": [' + ", ".join(['{"id": 1, "type": "timeseries"}'] * 100) + "]}"
    approved = (":white_check_mark: Approved by <@U0APPROVER> and executed (1 row(s)). "
                f"uid=svc; time=2026-10-26T10:42:10Z; dashboard_json={dashboard}")
    transcript = [{"kind": "api", "args": {"text": approved}},
                  {"kind": "api", "args": {"text": approved}},                      # card edits repeat it
                  {"kind": "api", "args": {"text": "Approval needed: save dashboard 'svc'"}},
                  {"kind": "delivery", "body": {"event": {"text": ":no_entry_sign: Cancelled by <@U_USER>; "
                                                                  "nothing was executed."}}}]
    first, cancelled = runner.write_outcomes(transcript)
    assert first.startswith("Approved by the on-call user and executed (1 row(s)). uid=svc; "
                            "time=2026-10-26T10:42:10Z; dashboard_json=")
    assert first.endswith("characters omitted]") and len(first) < runner.WRITE_OUTCOME_MAX_CHARS + 40
    assert cancelled == "Cancelled by the on-call user; nothing was executed."
