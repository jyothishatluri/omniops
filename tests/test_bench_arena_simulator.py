"""Arena launches through the real Slack layer: ``SimulatorSlack`` drives ``omniops.slack.simulator``
(the real Bolt app); only the agent flows are a local stub. What the records module reads back is
exactly what the simulated Slack thread showed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from omniops.agent.context import Deps
from omniops.agent.types import Alert, ConclusionCard, RunResult, ToolEvent
from omniops.bench.arena import launch as L
from omniops.bench.arena import records as R
from omniops.bench.arena.runner import SimulatorSlack
from omniops.engine.catalog import Catalog
from omniops.llm.scripted import ScriptedLLM
from omniops.settings import Settings

sim_mod = pytest.importorskip("omniops.slack.simulator")
CHANNEL = "C0ARENAOPS"
FAULT = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)


class StubFlows:
    """Stands in for the orchestrator / chat flow: reports progress, posts an early card, uses a tool."""

    def __init__(self) -> None:
        self.investigations: list[Alert] = []
        self.questions: list[str] = []

    async def investigate(self, alert: Alert, deps: Deps, *, status: Any = None, thread: str | None = None
                          ) -> RunResult:
        self.investigations.append(alert)
        if status is not None:
            status.step("Seeds: pods, events, deploys")
            await status.early_card(ConclusionCard(title="worker OOMKilled", verdict="Leading hypothesis: memory limit",
                                                   status="preliminary"))
            await status.done("Specialists finished")
        events = [ToolEvent(agent="primary", name="run_sql", arguments={"sql": "SELECT pod, reason FROM k8s_pods"},
                            output="worker-7d9 | OOMKilled", evidence_id="E2")]
        return RunResult(run_id="inv1", kind="investigation", alert=alert, tool_events=events,
                         answer_markdown="*TL;DR* worker exceeds its memory limit [E2].\n\n*Mitigation* bound the read.",
                         card=ConclusionCard(title="worker OOMKilled", verdict="worker exceeds its 64Mi limit",
                                             confidence=0.8, causal_level="mechanism"))

    async def answer(self, question: str, deps: Deps, *, thread: str | None = None, status: Any = None
                     ) -> RunResult:
        self.questions.append(question)
        return RunResult(run_id="chat1", kind="chat", answer_markdown="The storefront returns HTTP 500 from frontend.")


class HumanReports:
    """Alert normaliser double: bots are not expected; humans are reports only when ``accept``."""

    def __init__(self, accept: bool) -> None:
        self.accept = accept

    @staticmethod
    def is_automated(event: dict[str, Any]) -> bool:
        return bool(event.get("bot_id") or event.get("subtype") == "bot_message")

    @staticmethod
    def is_recovery(alert: Alert) -> bool:
        return False

    async def parse_slack_alert(self, event: dict[str, Any], llm: Any, **_: Any) -> Alert | None:
        if self.is_automated(event) or not self.accept or "support desk" not in str(event.get("text")):
            return None
        return Alert(platform="human", title="Customer report", text=str(event.get("text")), customer_report=True)


def surface(flows: StubFlows, **kw: Any) -> SimulatorSlack:
    deps = Deps(catalog=Catalog(), llm=ScriptedLLM(), settings=Settings(alert_channels=[CHANNEL]), memory=None)
    sim = sim_mod.SlackSimulator(deps, flows, alert_channel=CHANNEL, status_interval_s=0.01, **kw)
    return SimulatorSlack(sim, CHANNEL, mention_grace_s=10, poll_s=0.01)


def grafana_launch() -> L.LaunchMessage:
    started = FAULT + timedelta(minutes=2)
    payload = [{"labels": {"alertname": "Container OOMKilled", "grafana_folder": "Platform alerts", "omniops": "arena",
                           "severity": "critical", "namespace": "batch", "pod": "worker-7d9", "container": "worker",
                           "__alert_rule_uid__": "omniops-k8s-oomkilled"},
                "annotations": {"summary": "batch/worker-7d9 container worker was OOMKilled",
                                "__dashboardUid__": "omniops-arena", "__panelId__": "6", "__values__": '{"A": 1}'},
                "startsAt": started.isoformat().replace("+00:00", "Z"), "fingerprint": "f1",
                "generatorURL": "http://localhost:3000/alerting/grafana/omniops-k8s-oomkilled/view?orgId=1",
                "status": {"state": "active", "silencedBy": [], "inhibitedBy": []}}]
    alerts, _ = L.select_launch_alerts(L.alerts_from_alertmanager(payload), FAULT)
    return L.build_launch(alerts, [], since=FAULT, grafana_url="http://localhost:3000", subject="the online shop")


def customer_report() -> L.LaunchMessage:
    symptoms = [L.Symptom("the storefront home page", "failing", "it returns an error (HTTP 500)")]
    return L.build_launch([], symptoms, since=FAULT, grafana_url="http://localhost:3000", subject="the online shop")


async def test_grafana_alert_is_investigated_and_the_thread_becomes_the_case_record() -> None:
    flows = StubFlows()
    slack = surface(flows)                                   # the real alert normaliser
    try:
        posted = await slack.post(grafana_launch())
        assert posted.requested_by == "alert"
        transcript, results = await slack.collect(posted.ts, timeout_s=20, quiet_s=0)
    finally:
        await slack.close()
    assert len(flows.investigations) == 1
    alert = flows.investigations[0]
    assert alert.platform == "grafana" and alert.rule_name == "Container OOMKilled" and alert.source_ts == posted.ts
    assert alert.triggered_at is not None and abs((alert.triggered_at - (FAULT + timedelta(minutes=2))).total_seconds()) < 1

    final, source = R.render_final(transcript, results)
    assert source == "slack"
    assert "worker exceeds its 64Mi limit" in final and "*TL;DR* worker exceeds its memory limit" in final
    assert "Leading hypothesis" not in final and "FIRING" not in final
    inter = R.render_intermediate(transcript)
    assert "Leading hypothesis: memory limit" in inter and "(status)" in inter
    assert [r.run_id for r in results] == ["inv1"]
    assert "SELECT pod, reason FROM k8s_pods" in R.render_actions(results)


async def test_customer_report_taken_up_by_triage_gets_no_extra_mention() -> None:
    flows = StubFlows()
    slack = surface(flows, alerts=HumanReports(accept=True))
    try:
        posted = await slack.post(customer_report())
        transcript, _ = await slack.collect(posted.ts, timeout_s=20, quiet_s=0)
    finally:
        await slack.close()
    assert posted.requested_by == "triage" and len(flows.investigations) == 1 and flows.questions == []
    assert flows.investigations[0].customer_report
    assert not [m for m in transcript.messages if m.user == L.ONCALL_USER]


async def test_customer_report_declined_by_triage_is_asked_in_the_thread() -> None:
    flows = StubFlows()
    slack = surface(flows, alerts=HumanReports(accept=False))
    try:
        posted = await slack.post(customer_report())
        transcript, _ = await slack.collect(posted.ts, timeout_s=20, quiet_s=0)
    finally:
        await slack.close()
    assert posted.requested_by == "mention" and flows.questions == ["investigate"] and flows.investigations == []
    oncall = [m for m in transcript.messages if m.user == L.ONCALL_USER]
    assert len(oncall) == 1 and oncall[0].final.text == f"<@{slack.bot_user_id}> investigate"
    final, source = R.render_final(transcript)
    assert source == "slack" and "HTTP 500 from frontend" in final and "support desk" not in final
