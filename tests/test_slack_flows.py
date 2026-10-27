"""Slack entry point, driven through the simulator into the real Bolt app (D27).

The agent flows (orchestrator/chat) and the alert normaliser are stubbed with small local doubles so
these tests exercise only the Slack layer: listeners, de-dup, flap handling, status trail, card and
paged report delivery, approvals, previews and errors. One test runs the real alert normaliser.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from omniops.agent.context import Deps
from omniops.agent.types import Alert, ConclusionCard, RunResult
from omniops.engine.catalog import Catalog
from omniops.llm.scripted import ScriptedLLM
from omniops.settings import Settings
from omniops.slack.app import (
    ACTION_APPROVE,
    ACTION_CANCEL,
    ACTION_KEEP,
    ACTION_SHARE,
    ACTION_VOTE_UP,
    THREAD_CONTEXT_CHARS,
)
from omniops.slack.simulator import APPROVER, BOT_USER_ID, SlackSimulator
from omniops.slack.status import STOP_ACTION

ALERTS = "C_ALERTS"
GENERAL = "C_GENERAL"
FIXTURES = Path(__file__).parent / "fixtures" / "slack_alerts"


# ---------------------------------------------------------------------------------------------
# Local doubles
# ---------------------------------------------------------------------------------------------


def grafana_alert(state: str = "FIRING", rule: str = "HighErrorRate", service: str = "checkout") -> dict[str, Any]:
    """What Grafana's Slack contact point posts (default title/message templates)."""
    title = f"[{state}:1] Platform alerts ({rule} {service})"
    body = (f"**{'Firing' if state == 'FIRING' else 'Resolved'}**\n\nValue: A=12.5\nLabels:\n"
            f" - alertname = {rule}\n - grafana_folder = Platform alerts\n - service = {service}\n"
            "Annotations:\n - summary = 5xx ratio above 5% for 5m\n"
            "Source: http://grafana.test/alerting/grafana/aez1kq7/view\n"
            f"Silence: http://grafana.test/alerting/silence/new?matcher=alertname%3D{rule}\n")
    return {"username": "Grafana", "text": "", "attachments": [{
        "color": "#D63232" if state == "FIRING" else "#36a64f", "title": title,
        "title_link": "http://grafana.test/alerting/list", "text": body, "fallback": title,
        "footer": "Grafana v13.2.3", "ts": int(time.time())}]}


class StubAlerts:
    """Minimal alert normaliser: bots are alerts (title from the attachment), humans are reports
    only when they say something is broken."""

    @staticmethod
    def is_automated(event: dict[str, Any]) -> bool:
        return bool(event.get("bot_id") or event.get("subtype") == "bot_message")

    @staticmethod
    def is_recovery(alert: Alert) -> bool:
        return alert.status == "resolved"

    async def parse_slack_alert(self, event: dict[str, Any], llm: Any, **_: Any) -> Alert | None:
        text = str(event.get("text") or "")
        if not self.is_automated(event):
            if "broken" not in text:
                return None
            return Alert(platform="human", title=f"Reported: {text[:80]}", text=text, customer_report=True)
        att = (event.get("attachments") or [{}])[0]
        title = str(att.get("title") or text)
        if not title:
            return None
        status = "resolved" if "RESOLVED" in title else "firing"
        name = re.sub(r"^\[[^\]]*\]\s*", "", title)
        return Alert(platform="grafana", rule_name=name, title=name, status=status, text=str(att.get("text") or ""))


LONG_REPORT = "\n\n".join(
    [":rotating_light: *TL;DR* checkout 5xx came from the payment client timeout change [E1][E2]."]
    + [f"*Evidence {i}.* " + ("Error ratio rose from 0.2% to 12.5% after deploy v2.4.1 on checkout. " * 18)
       for i in range(1, 31)]
    + ["*Gaps* none left that a tool could fill. END-OF-REPORT"])


def card(status: str = "final", **kw: Any) -> ConclusionCard:
    base = {"title": "Checkout 5xx spike", "verdict": "Deploy v2.4.1 lowered the payment client timeout to 200 ms",
            "root_cause": "payment client timeout 200 ms (was 2 s)", "causal_level": "trigger", "confidence": 0.82,
            "mitigation": ["Roll back checkout to v2.4.0"], "status": status}
    return ConclusionCard(**{**base, **kw})


class StubRunner:
    """Records calls; reports progress on the status trail like the real flows."""

    def __init__(self, *, report: str = LONG_REPORT, staged: list[dict[str, Any]] | None = None,
                 fail: Exception | None = None, wait_for_stop: bool = False, answer_text: str | None = None,
                 stage: Any = None) -> None:
        self.investigations: list[tuple[Alert, str | None]] = []
        self.questions: list[tuple[str, str | None]] = []
        self.report, self.staged, self.fail, self.wait_for_stop = report, staged or [], fail, wait_for_stop
        self.answer_text = answer_text or "The p95 latency of checkout is 420 ms over the last hour [E1]."
        self.stage = stage

    async def investigate(self, alert: Alert, deps: Deps, *, status: Any = None, thread: str | None = None
                          ) -> RunResult:
        self.investigations.append((alert, thread))
        n = len(self.investigations)
        if status is not None:
            status.step("Seeds: alert rule, deploys, error logs")
            await status.done("Specialists finished")
        if self.fail is not None:
            raise self.fail
        if self.wait_for_stop:
            while status is not None and not status.stop_requested:
                await asyncio.sleep(0.01)
            return RunResult(run_id=f"inv{n}", kind="investigation", answer_markdown="Partial: stopped early.",
                             status="stopped")
        if status is not None:
            await status.early_card(card("preliminary", verdict="Leading hypothesis: payment client timeout"))
        return RunResult(run_id=f"inv{n}", kind="investigation", answer_markdown=self.report, card=card(),
                         alert=alert, staged_writes=list(self.staged))

    async def answer(self, question: str, deps: Deps, *, thread: str | None = None, status: Any = None
                     ) -> RunResult:
        self.questions.append((question, thread))
        if status is not None:
            status.step("Querying metrics")
        staged = list(self.staged) + ([await self.stage()] if self.stage is not None else [])
        return RunResult(run_id=f"chat{len(self.questions)}", kind="chat", answer_markdown=self.answer_text,
                         staged_writes=staged)


def make_deps(catalog: Catalog | None = None, **settings: Any) -> Deps:
    return Deps(catalog=catalog or Catalog(), llm=ScriptedLLM(),
                settings=Settings(alert_channels=[ALERTS], **settings), memory=None)


def sim_for(runner: StubRunner, deps: Deps | None = None, **kw: Any) -> SlackSimulator:
    kw.setdefault("alerts", StubAlerts())
    return SlackSimulator(deps or make_deps(), runner, status_interval_s=0.01, **kw)


def kind(m: dict[str, Any]) -> str:
    return str((m.get("metadata") or {}).get("event_type") or "")


def action_ids(m: dict[str, Any]) -> list[str]:
    return [e.get("action_id") for b in m.get("blocks") or [] if b.get("type") == "actions"
            for e in b.get("elements") or []]


def bot_replies(sim: SlackSimulator, channel: str, ts: str) -> list[dict[str, Any]]:
    return [m for m in sim.thread(channel, ts)[1:] if m.get("user") == BOT_USER_ID]


def text_of(sim: SlackSimulator, m: dict[str, Any]) -> str:
    return sim.bot.present.to_text(m.get("blocks") or []) or str(m.get("text") or "")


# ---------------------------------------------------------------------------------------------
# Alert channel
# ---------------------------------------------------------------------------------------------


async def test_alert_gets_status_trail_card_and_paged_report_in_its_thread() -> None:
    runner = StubRunner()
    sim = sim_for(runner)
    ts = await sim.post_alert(grafana_alert())
    assert await sim.wait_idle(10)

    assert len(runner.investigations) == 1
    alert, _ = runner.investigations[0]
    assert alert.title.startswith("Platform alerts") and alert.source_ts == ts and alert.source_channel == ALERTS
    replies = bot_replies(sim, ALERTS, ts)
    kinds = [kind(m) for m in replies]
    assert kinds[:2] == ["omniops_status", "omniops_card"]
    pages = [m for m in replies if kind(m) == "omniops_report"]
    assert len(pages) >= 2, "a long report is paged, never clipped"

    trail = replies[0]
    assert trail["edits"], "the trail is edited in place"
    assert "done in" in trail["text"] and "Specialists finished" in trail["text"]
    assert STOP_ACTION not in action_ids(trail), "a finished trail must not look live"
    assert STOP_ACTION in action_ids({"blocks": trail["edits"][0]["blocks"]}), "a live trail offers Stop"

    card_msg = replies[1]
    assert card_msg["edits"], "the final card edits the early card in place"
    assert "Leading hypothesis" in sim.bot.present.to_text(card_msg["edits"][0]["blocks"])
    assert "Deploy v2.4.1" in text_of(sim, card_msg)

    first_page = pages[0]
    assert any("Writing the full report" in sim.bot.present.to_text(e["blocks"]) for e in first_page["edits"]), \
        "page 1 replaces the placeholder"
    assert ACTION_VOTE_UP in action_ids(pages[-1])
    assert all(ACTION_VOTE_UP not in action_ids(p) for p in pages[:-1]), "buttons only on the last page"
    for m in replies:
        assert len(m.get("blocks") or []) <= 50

    answer = sim.final_answer_text(ALERTS, ts)
    assert "Deploy v2.4.1" in answer and "TL;DR" in answer and "END-OF-REPORT" in answer
    assert "[Helpful]" not in answer
    assert [r.run_id for r in sim.run_results] == ["inv1"]


async def test_duplicate_deliveries_run_once() -> None:
    runner = StubRunner(report="short report")
    sim = sim_for(runner)
    ts = await sim.post_alert(grafana_alert())
    await sim.redeliver(-1)                              # Slack retry: same event id
    await sim.redeliver(-1, same_event_id=False)         # another delivery of the same post
    assert await sim.wait_idle(10)
    assert len(runner.investigations) == 1
    assert sum(kind(m) == "omniops_status" for m in bot_replies(sim, ALERTS, ts)) == 1


async def test_human_mention_delivered_as_message_and_app_mention_answers_once() -> None:
    runner = StubRunner()
    sim = sim_for(runner)
    await sim.post_user_message(GENERAL, f"<@{BOT_USER_ID}> what is the p95 latency of checkout?")
    assert await sim.wait_idle(10)
    deliveries = [e["body"]["event"]["type"] for e in sim.transcript() if e["kind"] == "delivery"
                  and e["body"].get("event", {}).get("user") == "U_USER"]
    assert deliveries == ["message", "app_mention"]
    assert runner.questions == [("what is the p95 latency of checkout?", None)]


async def test_recovery_from_a_bot_is_skipped() -> None:
    runner = StubRunner()
    sim = sim_for(runner)
    ts = await sim.post_alert(grafana_alert("RESOLVED"))
    assert await sim.wait_idle(10)
    assert runner.investigations == []
    assert bot_replies(sim, ALERTS, ts) == []


async def test_flapping_retrigger_links_to_the_earlier_run_and_humans_still_get_a_run() -> None:
    runner = StubRunner(report="short report")
    sim = sim_for(runner)
    first = await sim.post_alert(grafana_alert())
    assert await sim.wait_idle(10)
    second = await sim.post_alert(grafana_alert())
    assert await sim.wait_idle(10)
    assert len(runner.investigations) == 1

    notices = bot_replies(sim, ALERTS, second)
    assert len(notices) == 1 and kind(notices[0]) == "omniops_notice"
    run_id = sim.bot.flaps.recent(runner.investigations[0][0].key(), 3600).run_id
    assert f"/archives/{ALERTS}/p{first.replace('.', '')}" in notices[0]["text"]
    assert run_id in notices[0]["text"]

    await sim.post_user_message(ALERTS, f"<@{BOT_USER_ID}> investigate", thread_ts=second)
    assert await sim.wait_idle(10)
    assert len(runner.investigations) == 2, "humans always get a run"


async def test_flap_check_failure_runs_anyway() -> None:
    class BrokenKey(Alert):
        def key(self) -> str:
            raise RuntimeError("no key")

    class Alerts(StubAlerts):
        async def parse_slack_alert(self, event: dict[str, Any], llm: Any, **_: Any) -> Alert | None:
            a = await super().parse_slack_alert(event, llm)
            return BrokenKey(**a.model_dump()) if a else None

    runner = StubRunner(report="short report")
    sim = sim_for(runner, alerts=Alerts())
    await sim.post_alert(grafana_alert())
    await sim.post_alert(grafana_alert())
    assert await sim.wait_idle(10)
    assert len(runner.investigations) == 2


async def test_human_incident_report_in_alert_channel_is_investigated_chatter_is_not() -> None:
    runner = StubRunner(report="short report")
    sim = sim_for(runner)
    await sim.post_user_message(ALERTS, "lunch in 10?")
    ts = await sim.post_user_message(ALERTS, "Support here: checkout is broken for customers since 14:05")
    assert await sim.wait_idle(10)
    assert len(runner.investigations) == 1
    assert runner.investigations[0][0].customer_report
    assert any(kind(m) == "omniops_report" for m in bot_replies(sim, ALERTS, ts))


async def test_real_alert_normaliser_parses_a_grafana_post() -> None:
    runner = StubRunner(report="short report")
    sim = SlackSimulator(make_deps(), runner, status_interval_s=0.01)      # real omniops.agent.alert
    await sim.post_alert(grafana_alert())
    assert await sim.wait_idle(10)
    assert len(runner.investigations) == 1
    assert runner.investigations[0][0].platform == "grafana"


# ---------------------------------------------------------------------------------------------
# Mentions and DMs
# ---------------------------------------------------------------------------------------------


async def test_mention_in_alert_thread_investigates_with_thread_context_then_follow_up_is_chat() -> None:
    runner = StubRunner(report="short report")
    sim = sim_for(runner)
    ts = await sim.post_user_message(ALERTS, "x")          # chatter: ignored
    alert_ts = await sim.post_alert(grafana_alert())
    assert await sim.wait_idle(10)
    assert len(runner.investigations) == 1

    await sim.post_user_message(ALERTS, f"<@{BOT_USER_ID}> investigate again", thread_ts=alert_ts)
    assert await sim.wait_idle(10)
    assert len(runner.investigations) == 2
    _, context = runner.investigations[1]
    assert context is not None and "investigate again" in context and "HighErrorRate" in context

    await sim.post_user_message(ALERTS, f"<@{BOT_USER_ID}> what changed before it fired?", thread_ts=alert_ts)
    assert await sim.wait_idle(10)
    assert len(runner.investigations) == 2
    question, thread = runner.questions[-1]
    assert question == "what changed before it fired?" and thread and "short report" in thread
    assert ts                                              # the chatter post exists but got no reply
    assert bot_replies(sim, ALERTS, ts) == []


async def test_injected_app_mention_is_delivered_like_slack_and_handled_once() -> None:
    runner = StubRunner(report="short report")
    sim = sim_for(runner)
    alert_ts = await sim.post_alert(grafana_alert())
    assert await sim.wait_idle(10)
    await sim.inject_message({"type": "app_mention", "channel": ALERTS, "thread_ts": alert_ts, "user": "U_ONCALL",
                              "text": f"<@{BOT_USER_ID}> please investigate"})
    assert await sim.wait_idle(10)
    deliveries = [e["body"]["event"]["type"] for e in sim.transcript() if e["kind"] == "delivery"
                  and e["body"].get("event", {}).get("user") == "U_ONCALL"]
    assert deliveries == ["message", "app_mention"]
    assert len(runner.investigations) == 2 and runner.questions == []


async def test_dm_question_is_answered_in_the_dm() -> None:
    runner = StubRunner()
    sim = sim_for(runner)
    await sim.dm("What is the p95 latency of checkout?")
    assert await sim.wait_idle(10)
    assert runner.questions == [("What is the p95 latency of checkout?", None)]
    dm = sim.workspace.dm_channel("U_USER")
    bot = [m for m in sim.messages(dm) if m.get("user") == BOT_USER_ID]
    assert kind(bot[0]) == "omniops_status" and "done in" in bot[0]["text"]
    assert "420 ms" in sim.final_answer_text()
    assert sim.final_answer_text() == sim.final_answer_text(dm)


async def test_dm_keeps_the_line_breaks_of_a_multi_line_question() -> None:
    runner = StubRunner()
    sim = sim_for(runner)
    await sim.dm(f"<@{BOT_USER_ID}> Build a dashboard:\n  1. p95   latency\n\n  2. error rate\n{{\"panels\": 2}}")
    assert await sim.wait_idle(10)
    assert runner.questions == [("Build a dashboard:\n1. p95 latency\n\n2. error rate\n{\"panels\": 2}", None)]


async def test_bot_never_acts_on_its_own_posts() -> None:
    runner = StubRunner(report="short report")
    sim = sim_for(runner)
    await sim.post_alert(grafana_alert())
    assert await sim.wait_idle(10)
    echoed = [e for e in sim.transcript() if e["kind"] == "delivery"
              and e["body"].get("event", {}).get("user") == BOT_USER_ID]
    assert echoed, "the simulator echoes bot posts like Slack does"
    assert len(runner.investigations) == 1 and runner.questions == []


# ---------------------------------------------------------------------------------------------
# Errors and stop
# ---------------------------------------------------------------------------------------------


async def test_run_failure_posts_error_with_run_id_and_freezes_the_trail() -> None:
    runner = StubRunner(fail=RuntimeError("upstream exploded token=abc123secret"))
    sim = sim_for(runner)
    ts = await sim.post_alert(grafana_alert())
    assert await sim.wait_idle(10)
    replies = bot_replies(sim, ALERTS, ts)
    trail, error = replies[0], [m for m in replies if kind(m) == "omniops_error"]
    assert "failed after" in trail["text"] and STOP_ACTION not in action_ids(trail)
    assert len(error) == 1
    run_id = (error[0]["metadata"]["event_payload"])["run_id"]
    assert run_id and f"`{run_id}`" in error[0]["text"]
    assert "abc123secret" not in error[0]["text"] and "upstream exploded" in error[0]["text"]


async def test_a_timeout_inside_the_agent_is_not_reported_as_the_watchdog() -> None:
    runner = StubRunner(fail=TimeoutError("loki read timed out after 30 s"))
    sim = sim_for(runner)
    ts = await sim.post_alert(grafana_alert())
    assert await sim.wait_idle(10)
    trail = bot_replies(sim, ALERTS, ts)[0]
    assert "TimeoutError: loki read timed out" in trail["text"] and "watchdog" not in trail["text"]


async def test_watchdog_freezes_a_hung_run() -> None:
    class Hung(StubRunner):
        async def investigate(self, alert: Alert, deps: Deps, **kw: Any) -> RunResult:
            self.investigations.append((alert, None))
            await asyncio.sleep(30)
            raise AssertionError("the watchdog should have fired")

    runner = Hung()
    # The guard is budget + the report call's own allowance + grace (D25); no report allowance here.
    sim = sim_for(runner, make_deps(investigation_budget_s=0.05, watchdog_grace_s=0.0, report_call_s=0.0))
    ts = await sim.post_alert(grafana_alert())
    assert await sim.wait_idle(10)
    replies = bot_replies(sim, ALERTS, ts)
    assert "(watchdog)" in replies[0]["text"] and STOP_ACTION not in action_ids(replies[0])
    assert any(kind(m) == "omniops_error" and "(watchdog)" in m["text"] for m in replies)


async def test_delivery_failure_still_freezes_the_trail_and_reports_the_run_id() -> None:
    runner = StubRunner()
    sim = sim_for(runner)
    post = sim.client._m_chat_postMessage

    def rate_limited_reports(args: dict[str, Any]) -> dict[str, Any]:
        if (args.get("metadata") or {}).get("event_type") == "omniops_report":
            return {"ok": False, "error": "ratelimited"}
        return post(args)

    sim.client._m_chat_postMessage = rate_limited_reports  # type: ignore[method-assign]
    ts = await sim.post_alert(grafana_alert())
    assert await sim.wait_idle(10)
    replies = bot_replies(sim, ALERTS, ts)
    trail = replies[0]
    assert "failed after" in trail["text"] and "could not be posted" in trail["text"]
    assert STOP_ACTION not in action_ids(trail), "a frozen trail never looks live"
    error = [m for m in replies if kind(m) == "omniops_error"]
    assert len(error) == 1 and error[0]["metadata"]["event_payload"]["run_id"] in error[0]["text"]


async def test_stop_button_stops_the_run_and_the_trail_stops_looking_live() -> None:
    runner = StubRunner(wait_for_stop=True)
    sim = sim_for(runner)
    ts = await sim.post_alert(grafana_alert())
    for _ in range(200):
        if sim.find_buttons(STOP_ACTION):
            break
        await asyncio.sleep(0.01)
    _, trail_ts, run_id = sim.find_buttons(STOP_ACTION)[0]
    await sim.click(STOP_ACTION, run_id, trail_ts, ALERTS)
    assert await sim.wait_idle(10)
    trail = bot_replies(sim, ALERTS, ts)[0]
    assert "Stop requested by" in trail["text"] and STOP_ACTION not in action_ids(trail)
    assert "Partial: stopped early." in sim.final_answer_text(ALERTS, ts)


# ---------------------------------------------------------------------------------------------
# Staged writes (real engine, Grafana annotation write mocked with respx)
# ---------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def grafana_catalog() -> Catalog:
    """Grafana only (loading manifests is the slow part, so once per module); env is read at build time."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GRAFANA_URL", "http://grafana.test")
        mp.delenv("GRAFANA_TOKEN", raising=False)
        mp.delenv("GRAFANA_USERNAME", raising=False)
        return Catalog.from_env(only=["grafana"])


def stager(catalog: Catalog, *, with_engine: bool) -> Any:
    from omniops.engine.engine import SQLEngine

    async def stage() -> dict[str, Any]:
        engine = SQLEngine(catalog)
        res = await engine.query("SELECT id FROM grafana_annotation_write WHERE text = 'INC-7 rollback started'")
        assert res.staged_write is not None
        if with_engine:
            return {**res.staged_write, "engine": engine}
        sw = engine.staged_writes[res.staged_write["id"]]
        return {**res.staged_write, "table": sw.table, "filters": sw.filters, "expires": sw.expires}

    return stage


async def test_approval_runs_the_staged_write_exactly_once(grafana_catalog: Catalog) -> None:
    runner = StubRunner(stage=stager(grafana_catalog, with_engine=True), answer_text="Staged the annotation.")
    sim = sim_for(runner, make_deps(grafana_catalog))
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("http://grafana.test/api/annotations").mock(
            return_value=httpx.Response(200, json={"id": 41, "message": "Annotation added"}))
        await sim.dm("annotate the incident start")
        assert await sim.wait_idle(10)
        assert route.call_count == 0, "writes are never executed inline"
        (channel, card_ts, wid), = sim.find_buttons(ACTION_APPROVE)
        statuses = await asyncio.gather(sim.click(ACTION_APPROVE, wid, card_ts, channel),
                                        sim.click(ACTION_APPROVE, wid, card_ts, channel))   # double click
        assert statuses == [200, 200]
        assert await sim.wait_idle(10)
        assert route.call_count == 1
    approval = next(m for m in sim.messages(channel) if m["ts"] == card_ts)
    assert "Approved by <@U_USER> and executed" in approval["text"]
    assert "id=41" in approval["text"] and "INC-7" not in approval["text"]   # what came back, not what was sent
    assert not action_ids(approval), "a decided approval card has no buttons left"
    refused = [m["text"] for m in sim.ephemerals(user="U_USER")]
    assert any("already handled" in t and "nothing was executed" in t for t in refused)
    await grafana_catalog.aclose()


async def test_auto_approve_writes_records_the_simulated_human(grafana_catalog: Catalog) -> None:
    runner = StubRunner(stage=stager(grafana_catalog, with_engine=False))
    sim = sim_for(runner, make_deps(grafana_catalog), auto_approve_writes=True)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("http://grafana.test/api/annotations").mock(
            return_value=httpx.Response(200, json={"id": 42, "message": "Annotation added"}))
        await sim.dm("annotate the incident start")
        assert await sim.wait_idle(10)
        assert route.call_count == 1
    wid = sim.run_results[0].staged_writes[0]["id"]
    assert [(a["write_id"], a["by"]) for a in sim.approvals] == [(wid, APPROVER)]
    assert any(e["kind"] == "approval" and e["simulated_human"] for e in sim.transcript())
    await grafana_catalog.aclose()


async def test_cancel_never_executes(grafana_catalog: Catalog) -> None:
    runner = StubRunner(stage=stager(grafana_catalog, with_engine=True))
    sim = sim_for(runner, make_deps(grafana_catalog))
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("http://grafana.test/api/annotations").mock(return_value=httpx.Response(200, json={}))
        await sim.dm("annotate the incident start")
        assert await sim.wait_idle(10)
        (channel, card_ts, wid), = sim.find_buttons(ACTION_CANCEL)
        await sim.click(ACTION_CANCEL, wid, card_ts, channel)
        assert await sim.wait_idle(10)
        assert route.call_count == 0
    approval = next(m for m in sim.messages(channel) if m["ts"] == card_ts)
    assert "Cancelled by <@U_USER>" in approval["text"]
    await grafana_catalog.aclose()


async def test_write_mode_deny_offers_no_approval() -> None:
    runner = StubRunner(staged=[{"id": "W1", "summary": "dashboard save", "table": "t", "filters": {}}])
    sim = sim_for(runner, make_deps(write_mode="deny"))
    await sim.dm("save the dashboard")
    assert await sim.wait_idle(10)
    assert sim.find_buttons(ACTION_APPROVE) == []


# ---------------------------------------------------------------------------------------------
# /investigate: private preview first (D12)
# ---------------------------------------------------------------------------------------------


async def test_investigate_command_previews_privately_then_posts_for_everyone() -> None:
    runner = StubRunner()
    sim = sim_for(runner)
    ack = await sim.slash("/investigate", "checkout 5xx errors are spiking", channel=GENERAL)
    assert "privately" in ack
    assert await sim.wait_idle(10)
    assert len(runner.investigations) == 1
    assert [m for m in sim.messages(GENERAL) if m.get("user") == BOT_USER_ID] == [], "nothing public yet"
    preview = [m for m in sim.ephemerals(user="U_USER", channel=GENERAL) if ACTION_SHARE in action_ids(m)]
    assert len(preview) == 1 and ACTION_KEEP in action_ids(preview[0])

    await sim.click(ACTION_SHARE, channel=GENERAL)
    assert await sim.wait_idle(10)
    public = [m for m in sim.messages(GENERAL) if m.get("user") == BOT_USER_ID]
    assert len(public) == 1 and kind(public[0]) == "omniops_card"
    assert "Shared by <@U_USER>" in text_of(sim, public[0])
    report = [m for m in sim.thread(GENERAL, public[0]["ts"]) if kind(m) == "omniops_report"]
    assert len(report) >= 2
    assert "END-OF-REPORT" in sim.final_answer_text(GENERAL, public[0]["ts"])

    await sim.click(ACTION_SHARE, channel=GENERAL)          # the ephemeral still shows the button
    assert await sim.wait_idle(10)
    assert any("already handled" in m["text"] for m in sim.ephemerals(user="U_USER"))
    assert len([m for m in sim.messages(GENERAL) if m.get("user") == BOT_USER_ID]) == 1


async def test_investigate_command_keep_private_delivers_to_dm_only() -> None:
    runner = StubRunner(report="short report")
    sim = sim_for(runner)
    await sim.slash("/investigate", "checkout 5xx errors are spiking", channel=GENERAL)
    assert await sim.wait_idle(10)
    await sim.click(ACTION_KEEP, channel=GENERAL)
    assert await sim.wait_idle(10)
    assert [m for m in sim.messages(GENERAL) if m.get("user") == BOT_USER_ID] == []
    dm = sim.workspace.dm_channel("U_USER")
    assert "short report" in sim.final_answer_text(dm)


async def test_feedback_vote_reaches_memory() -> None:
    votes: list[tuple[str, int]] = []

    class Memory:
        def vote(self, run_id: str, delta: int) -> int:
            votes.append((run_id, delta))
            return delta

    deps = make_deps()
    deps.memory = Memory()  # type: ignore[assignment]
    runner = StubRunner(report="short report")
    sim = sim_for(runner, deps)
    await sim.dm("how is checkout?")
    assert await sim.wait_idle(10)
    (channel, ts, value), = sim.find_buttons(ACTION_VOTE_UP)
    assert value == "chat1", "the button carries the agent's run id, which memory is keyed by"
    await sim.click(ACTION_VOTE_UP, value, ts, channel)
    await sim.click(ACTION_VOTE_UP, value, ts, channel)             # a repeat vote counts once
    assert await sim.wait_idle(10)
    assert votes == [("chat1", 1)]
    notes = [m["text"] for m in sim.ephemerals(user="U_USER")]
    assert "Thanks, feedback recorded." in notes and "You already voted on this answer." in notes


# ---------------------------------------------------------------------------------------------
# Regressions
# ---------------------------------------------------------------------------------------------


async def test_a_failed_later_report_page_keeps_the_delivered_first_page() -> None:
    runner = StubRunner()
    sim = sim_for(runner)
    post = sim.client._m_chat_postMessage
    reports = 0

    def second_page_rate_limited(args: dict[str, Any]) -> dict[str, Any]:
        nonlocal reports
        if (args.get("metadata") or {}).get("event_type") == "omniops_report":
            reports += 1
            if reports == 2:                       # 1: the placeholder (page 1 edits it), 2: page 2
                return {"ok": False, "error": "ratelimited"}
        return post(args)

    sim.client._m_chat_postMessage = second_page_rate_limited  # type: ignore[method-assign]
    ts = await sim.post_alert(grafana_alert())
    assert await sim.wait_idle(10)
    replies = bot_replies(sim, ALERTS, ts)
    pages = [m for m in replies if kind(m) == "omniops_report"]
    assert pages and "TL;DR" in text_of(sim, pages[0]), "the delivered page 1 is not overwritten"
    assert all("Report not available" not in text_of(sim, m) for m in pages)
    assert any(kind(m) == "omniops_error" and "could not be posted" in m["text"] for m in replies)


async def test_a_failed_run_does_not_suppress_the_next_retrigger() -> None:
    runner = StubRunner(report="short report", fail=RuntimeError("LLM endpoint down"))
    sim = sim_for(runner)
    await sim.post_alert(grafana_alert())
    assert await sim.wait_idle(10)
    runner.fail = None                             # the outage is over when the monitor re-triggers
    second = await sim.post_alert(grafana_alert())
    assert await sim.wait_idle(10)
    assert len(runner.investigations) == 2
    assert not any(":repeat:" in m["text"] for m in bot_replies(sim, ALERTS, second))
    third = await sim.post_alert(grafana_alert())  # a delivered run does stand in for the next re-trigger
    assert await sim.wait_idle(10)
    assert len(runner.investigations) == 2
    assert any(":repeat:" in m["text"] for m in bot_replies(sim, ALERTS, third))


async def test_new_sentry_issues_from_one_rule_are_each_investigated() -> None:
    event = json.loads((FIXTURES / "sentry_issue.json").read_text())["event"]
    for k in ("ts", "event_ts"):
        event.pop(k)
    other = json.loads(json.dumps(event).replace("5123456789", "5123999999").replace(
        "ZeroDivisionError: division by zero", "OperationalError: could not connect to server"))
    runner = StubRunner(report="short report")
    sim = SlackSimulator(make_deps(), runner, status_interval_s=0.01)      # real omniops.agent.alert
    await sim.post_alert(event, channel=ALERTS)
    second = await sim.post_alert(other, channel=ALERTS)
    assert await sim.wait_idle(10)
    assert [a.incident_id for a, _ in runner.investigations] == ["5123456789", "5123999999"]
    assert not any(":repeat:" in m["text"] for m in bot_replies(sim, ALERTS, second))


async def test_thread_follow_up_keeps_the_newest_replies_within_what_the_agents_read() -> None:
    runner = StubRunner()                          # the long, paged report fills the thread
    sim = sim_for(runner)
    alert_ts = await sim.post_alert(grafana_alert())
    assert await sim.wait_idle(10)
    await sim.post_user_message(ALERTS, "MARKER-LATEST: we rolled back to v2.4.0 at 14:20 and errors persist",
                                thread_ts=alert_ts)
    await sim.post_user_message(ALERTS, f"<@{BOT_USER_ID}> given the rollback, what else could it be?",
                                thread_ts=alert_ts)
    assert await sim.wait_idle(10)
    _, thread = runner.questions[-1]
    assert thread is not None and len(thread) <= THREAD_CONTEXT_CHARS, "the agents keep only the head"
    assert "MARKER-LATEST" in thread and "HighErrorRate" in thread, "root and newest replies are kept"

    await sim.post_user_message(ALERTS, f"<@{BOT_USER_ID}> investigate again", thread_ts=alert_ts)
    assert await sim.wait_idle(10)
    _, context = runner.investigations[-1]
    assert context is not None and len(context) <= THREAD_CONTEXT_CHARS
    assert context.startswith("Requested by <@U_USER>: investigate again") and "MARKER-LATEST" in context


async def test_long_threads_are_read_to_the_newest_reply() -> None:
    runner = StubRunner(report="short report")
    sim = sim_for(runner)
    root = await sim.post_user_message(GENERAL, "checkout is broken for some customers")
    for i in range(205):
        await sim.post_user_message(GENERAL, f"note {i}: still looking", thread_ts=root)
    await sim.post_user_message(GENERAL, "NEWEST-FINDING: payment pods OOMKilled at 14:03", thread_ts=root)
    await sim.post_user_message(GENERAL, f"<@{BOT_USER_ID}> investigate", thread_ts=root)
    assert await sim.wait_idle(10)
    assert len(runner.investigations) == 1
    _, context = runner.investigations[0]
    assert context is not None and "NEWEST-FINDING" in context and "note 204" in context

    await sim.post_user_message(GENERAL, f"<@{BOT_USER_ID}> what changed before it broke?", thread_ts=root)
    assert await sim.wait_idle(10)
    assert len(runner.investigations) == 1, "the answer on a later page counts: the follow-up is chat"
    assert runner.questions[-1][0] == "what changed before it broke?"


async def test_quoted_broadcasts_and_mentions_never_reach_slack_live() -> None:
    payload = "Loki shows the payload: <!channel> deploy now <!subteam^S0ONCALL|@oncall> <@U999>"
    runner = StubRunner(report=f"*TL;DR* {payload}", answer_text=payload)
    sim = sim_for(runner)
    await sim.post_alert(grafana_alert(rule="<!here>"))
    await sim.dm("what does the log say?")
    assert await sim.wait_idle(10)
    sent = [e["args"] for e in sim.api_calls() if e["method"] in ("chat.postMessage", "chat.update")]
    dumped = json.dumps(sent)
    assert "Loki shows the payload" in dumped
    for token in ("<!channel>", "<!here>", "<!subteam^", "<@U999>"):
        assert token not in dumped, token


async def test_investigate_command_where_the_bot_is_not_a_member_still_reaches_the_user() -> None:
    runner = StubRunner()
    sim = sim_for(runner)
    sim.workspace.add_channel("G_SECRET", "war-room", member=False)       # a private channel without the bot
    await sim.slash("/investigate", "checkout 5xx errors are spiking", channel="G_SECRET")
    assert await sim.wait_idle(10)
    preview = [m for m in sim.ephemerals(user="U_USER", channel="G_SECRET") if ACTION_SHARE in action_ids(m)]
    assert len(preview) == 1 and preview[0]["source"] == "response_url"

    await sim.click(ACTION_SHARE, channel="G_SECRET")
    assert await sim.wait_idle(10)
    assert sim.messages("G_SECRET") == []
    dm = sim.workspace.dm_channel("U_USER")
    assert "END-OF-REPORT" in sim.final_answer_text(dm)
    notes = [m["text"] for m in sim.messages(dm) if kind(m) == "omniops_notice"]
    assert any("cannot post in this conversation" in n and "in your DMs" in n for n in notes)


async def test_private_run_reaches_the_dm_when_the_response_url_is_gone() -> None:
    sim = sim_for(StubRunner(report="short report"))
    sim.workspace.add_channel("D_TWO_PEOPLE", member=False)              # a DM between two people
    await sim.slash("/investigate", "checkout 5xx errors are spiking", channel="D_TWO_PEOPLE")
    for hook in sim._response_urls.values():
        hook["expires"] = 0.0                      # the run outlived the response_url (30 minutes)
    assert await sim.wait_idle(10)
    dm = sim.workspace.dm_channel("U_USER")
    assert [m for m in sim.messages(dm) if ACTION_SHARE in action_ids(m)], "the preview is in the user's DM"

    failing = sim_for(StubRunner(fail=RuntimeError("LLM endpoint down")))
    failing.workspace.add_channel("D_TWO_PEOPLE", member=False)
    await failing.slash("/investigate", "checkout 5xx errors are spiking", channel="D_TWO_PEOPLE")
    assert await failing.wait_idle(10)
    errors = [m["text"] for m in failing.messages(failing.workspace.dm_channel("U_USER"))
              if kind(m) == "omniops_error"]
    assert any("failed" in t and "LLM endpoint down" in t for t in errors)
