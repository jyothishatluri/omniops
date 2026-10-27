"""An alert posted in Slack drives the real Bolt app, the alert parser and investigate(): the early card is
posted when the agents finish and edited in place into the final card; the report lands in the thread."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import respx
from test_orchestrator_support import (  # noqa: F401 - source_env is a fixture (pytestmark)
    LOGS_SQL,
    finish,
    hypotheses_step,
    make_deps,
    mock_sources,
    source_env,
    sql,
)

from omniops.agent import DefaultRunner
from omniops.engine.timeutil import TimeAnchor
from omniops.llm.scripted import ScriptedLLM
from omniops.slack.simulator import SlackSimulator

pytestmark = pytest.mark.usefixtures("source_env")

FIXTURE = Path(__file__).parent / "fixtures" / "slack_alerts" / "grafana_firing.json"


def _meta(m: dict[str, Any]) -> tuple[str, str]:
    md = m.get("metadata") or {}
    return str(md.get("event_type") or ""), str((md.get("event_payload") or {}).get("run_id") or "")


async def test_alert_in_slack_gets_early_card_then_final_card(tmp_path: Any) -> None:
    event = json.loads(FIXTURE.read_text())["event"]
    posted = datetime.fromtimestamp(float(event["ts"]), UTC)
    llm = ScriptedLLM(loops={"decision": [
        [sql(LOGS_SQL, "checkout error logs")],
        hypotheses_step(),
        finish("H1: payment client timeouts cause the checkout 5xx."),
    ]}, complete={"report": "*TL;DR*\nThe most likely contributing factor is payment client timeouts.\n\n"
                            "*Root cause*\nPayment calls time out after 3000 ms."})
    with respx.mock(assert_all_called=False) as m:
        mock_sources(m)
        deps = make_deps(llm, tmp_path=tmp_path)
        sim = SlackSimulator(deps, runner=DefaultRunner(anchor=TimeAnchor(now=posted + timedelta(minutes=5))),
                             alert_channel=event["channel"], status_interval_s=0.0)
        try:
            ts = await sim.post_alert(event)
            assert await sim.wait_idle(60)
            shown = sim.final_answer_text(event["channel"], ts)
        finally:
            await sim.close()

    [result] = sim.run_results
    assert result.status == "ok" and result.alert is not None and result.alert.service == "checkout"
    thread = sim.thread(event["channel"], ts)
    cards = [msg for msg in thread if _meta(msg)[0] == "omniops_card"]
    assert len(cards) == 1                                              # one card message, edited in place
    card = cards[0]
    assert card["edits"] and "Preliminary" in json.dumps(card["edits"][0])
    assert _meta(card)[1] == result.run_id                              # trail, card and run share one id
    status = next(msg for msg in thread if _meta(msg)[0] == "omniops_status")
    assert _meta(status)[1] == result.run_id
    assert "payment client timeouts" in shown
    assert (tmp_path / "cases" / f"{result.run_id}.json").exists()
