"""End to end, offline: a Grafana alert posted in Slack, then a DM question, then a staged write approved
once, all through the real Slack simulator + Bolt app, the real alert parser, orchestrator, chat flow,
seeds, specialists, tools, SQL engine (bundled Prometheus, Loki and Grafana manifests), deepening,
report writer, card and renderer. Only the model turns are scripted (ScriptedLLM) and every HTTP call
is answered by respx.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from test_orchestrator_support import (  # noqa: F401 - source_env is a fixture (pytestmark)
    evidence_ids,
    finish,
    make_deps,
    source_env,
    sql,
)

from omniops.agent import DefaultRunner
from omniops.agent.memory import MemoryStore
from omniops.agent.specialists import SPECIALIST_NAMES
from omniops.engine.timeutil import TimeAnchor
from omniops.llm.scripted import ScriptedLLM
from omniops.slack.app import ACTION_APPROVE, META_APPROVAL, META_CARD, META_REPORT
from omniops.slack.render import validate_blocks
from omniops.slack.simulator import SlackSimulator

pytestmark = pytest.mark.usefixtures("source_env")

FIXTURE = Path(__file__).parent / "fixtures" / "slack_alerts" / "grafana_firing.json"
RULE_UID = "fe2kq1x9abcd0e"
ONSET = datetime(2026, 10, 20, 14, 2, tzinfo=UTC)               # the fixture's StartsAt
DEPLOY = ONSET - timedelta(minutes=6)
RATIO = ('sum(rate(http_requests_total{service="checkout",code=~"5.."}[5m])) '
         '/ sum(rate(http_requests_total{service="checkout"}[5m]))')
RATIO_SQL = f"SELECT max(value) AS peak_ratio, arg_max(ts, value) AS peak_at FROM prometheus_query_range " \
            f"WHERE query = '{RATIO}'"
LOGS_SQL = "SELECT ts, message FROM loki_logs WHERE service = 'checkout' AND level = 'error' ORDER BY ts DESC LIMIT 20"
CHECK_SQL = "SELECT count(*) AS timeouts FROM loki_logs WHERE service = 'checkout' AND message LIKE '%after 300ms%'"
IMPACT_SQL = "SELECT count(*) AS failed_checkouts FROM loki_logs WHERE service = 'checkout' AND level = 'error'"
ANNOTATE_SQL = ("SELECT id, message FROM grafana_annotation_write WHERE text = 'checkout rollback' "
                "AND dashboard_uid = 'checkout-red'")
EID = re.compile(r"\bE\d+\b")
URL = re.compile(r"https?://[^\s<>|`\"')\]]+")


# ---------------------------------------------------------------------------------------------
# The Grafana stack (Prometheus + Loki + Grafana HTTP APIs) as respx routes
# ---------------------------------------------------------------------------------------------


def _secs(v: str) -> float:
    x = float(v)
    return x / 1e9 if x > 1e12 else x


def _matrix(request: httpx.Request, metric: dict[str, str], high: str, low: str) -> httpx.Response:
    p = request.url.params
    start, end = int(_secs(p["start"])), int(_secs(p["end"]))
    step = max(15, int(float(p.get("step") or 60)))
    values = [[t, high if t >= DEPLOY.timestamp() else low] for t in range(start, end + 1, step)]
    return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": metric, "values": values}]}})


def _prom_range(request: httpx.Request) -> httpx.Response:
    return _matrix(request, {"service": "checkout"}, "0.0712", "0.0021")


def _loki(request: httpx.Request) -> httpx.Response:
    query = request.url.params.get("query", "")
    if "count_over_time" in query or query.startswith(("sum", "rate")):
        return _matrix(request, {"service_name": "checkout"}, "38", "1")
    start = _secs(request.url.params["start"])
    if start > (ONSET - timedelta(hours=12)).timestamp():            # the incident window
        values = [[str(int((ONSET - timedelta(minutes=4 - i)).timestamp() * 1e9)),
                   f"ERROR payment client timeout after 300ms order=A{i} trace_id=4bf92f3577b34da6a3ce929d0e0e{i:04d}"]
                  for i in range(4)]
    else:                                                            # the same window a day earlier
        values = [[str(int((ONSET - timedelta(days=1, minutes=8)).timestamp() * 1e9)), "ERROR cache miss for sku-9"]]
    return httpx.Response(200, json={"status": "success", "data": {"resultType": "streams", "result": [
        {"stream": {"service_name": "checkout", "level": "error"}, "values": values}]}})


RULE = {"id": 7, "uid": RULE_UID, "orgID": 1, "folderUID": "shop", "ruleGroup": "checkout",
        "title": "CheckoutHighErrorRate", "condition": "C", "for": "5m",
        "data": [{"refId": "A", "relativeTimeRange": {"from": 600, "to": 0}, "datasourceUid": "prometheus",
                  "model": {"expr": RATIO, "refId": "A"}},
                 {"refId": "C", "relativeTimeRange": {"from": 0, "to": 0}, "datasourceUid": "__expr__",
                  "model": {"type": "classic_conditions", "refId": "C", "conditions": [
                      {"evaluator": {"type": "gt", "params": [0.05]}, "operator": {"type": "and"},
                       "query": {"params": ["A"]}, "reducer": {"type": "last", "params": []}, "type": "query"}]}}],
        "labels": {"severity": "critical", "team": "payments"},
        "annotations": {"summary": "Checkout 5xx error rate above 5%"}}
AM_ALERTS = [{"labels": {"__alert_rule_uid__": RULE_UID, "alertname": "CheckoutHighErrorRate", "service": "checkout",
                         "namespace": "shop", "severity": "critical"},
              "annotations": {"summary": "Checkout 5xx error rate above 5%"}, "startsAt": "2026-10-20T14:02:00Z",
              "endsAt": "2026-10-20T14:12:00Z", "fingerprint": "6d1f4a2b9c3e8f07", "status": {"state": "active"},
              "generatorURL": f"http://grafana.test/alerting/grafana/{RULE_UID}/view?orgId=1"}]
DEPLOY_ANNOTATION = [{"id": 1201, "alertId": 0, "dashboardUID": "checkout-red", "panelId": 0, "userId": 1,
                      "time": int(DEPLOY.timestamp() * 1000), "timeEnd": int(DEPLOY.timestamp() * 1000),
                      "text": "Deployed checkout v2.14.0 (payment client timeout 3000ms -> 300ms)",
                      "tags": ["deploy", "checkout"], "data": {}}]
EMPTY = {"status": "success", "data": {"resultType": "matrix", "result": [], "alerts": [], "groups": [],
                                       "activeTargets": []}}


def _annotations(request: httpx.Request) -> httpx.Response:
    if request.method == "POST":
        return httpx.Response(200, json={"id": 77, "message": "Annotation added"})
    return httpx.Response(200, json=[] if request.url.params.get("type") == "alert" else DEPLOY_ANNOTATION)


def grafana_stack(m: respx.MockRouter) -> dict[str, respx.Route]:
    routes = {
        "range": m.get("http://prom.test/api/v1/query_range").mock(side_effect=_prom_range),
        "loki": m.get("http://loki.test/loki/api/v1/query_range").mock(side_effect=_loki),
        "rule": m.get(f"http://grafana.test/api/v1/provisioning/alert-rules/{RULE_UID}").respond(json=RULE),
        "rules": m.get("http://grafana.test/api/v1/provisioning/alert-rules").respond(json=[RULE]),
        "am_alerts": m.get("http://grafana.test/api/alertmanager/grafana/api/v2/alerts").respond(json=AM_ALERTS),
        "annotations": m.route(url="http://grafana.test/api/annotations").mock(side_effect=_annotations),
    }
    routes["prom_other"] = m.get(url__regex=r"http://prom\.test/.*").respond(json=EMPTY)
    routes["loki_other"] = m.get(url__regex=r"http://loki\.test/.*").respond(json=EMPTY)
    routes["grafana_other"] = m.route(url__regex=r"http://grafana\.test/.*").respond(json=[])
    return routes


# ---------------------------------------------------------------------------------------------
# Scripted model turns (only the model is scripted; everything around it is production code)
# ---------------------------------------------------------------------------------------------


def _cited(user: str, section: str) -> list[str]:
    return list(dict.fromkeys(EID.findall(user.split(section, 1)[-1])))


def _hypotheses(items: list[dict[str, Any]]) -> dict[str, Any]:
    ids = evidence_ids(items)
    return {"name": "record_hypotheses", "arguments": {"hypotheses": [
        {"id": "H1", "statement": "the checkout v2.14.0 deploy cut the payment client timeout to 300 ms, so payment "
         "calls time out and checkout returns 5xx", "causal_level": "trigger", "category": "code_deploy",
         "confidence": 0.65, "supporting": ids[:2], "next_query": None},
        {"id": "H2", "statement": "the payments provider is degraded", "causal_level": "mechanism",
         "category": "dependency", "confidence": 0.2},
        {"id": "H3", "statement": "a traffic surge saturates checkout pods", "causal_level": "mechanism",
         "category": "traffic", "confidence": 0.1}]}}


def _finish(items: list[dict[str, Any]]) -> dict[str, Any]:
    ids = evidence_ids(items)
    return finish(f"H1: the v2.14.0 deploy's 300 ms payment timeout causes the checkout 5xx [{', '.join(ids[:2])}].")


def _specialist_step(items: list[dict[str, Any]]) -> Any:
    found = re.search(r"SPECIALIST: (\w+)", str(items[0].get("content") if items else ""))
    name = found.group(1) if found else "unknown"
    queried = any(i.get("role") == "tool" for i in items)
    if name == "customer_impact" and not queried:
        return [sql(IMPACT_SQL, "failed checkout requests in the window")]
    return f"{name}: checked the seed evidence; nothing further in my scope."


def _specialist_verdict(user: str) -> dict[str, Any]:
    name = re.search(r'Set "agent" to "(\w+)"', user).group(1)  # type: ignore[union-attr]
    gathered = re.findall(r"^(E\d+) \(", user.split("EVIDENCE YOU GATHERED", 1)[-1], flags=re.MULTILINE)
    if name == "customer_impact":
        return {"verdict": "found", "headline": "4 checkout payment timeouts in 5 minutes; checkout requests fail",
                "confidence": 0.5, "evidence_ids": gathered[:2],
                "details": {"level": "medium", "affected": ["checkout"]}}
    return {"verdict": "not_found", "headline": f"{name}: nothing in scope explains the checkout errors",
            "confidence": 0.3, "evidence_ids": gathered[:1]}


def _merge(user: str) -> dict[str, Any]:
    ids = _cited(user, "EVIDENCE")
    return {"hypotheses": [{"id": "H1", "statement": "the checkout v2.14.0 deploy cut the payment client timeout to "
                            "300 ms, so payment calls time out and checkout returns 5xx", "causal_level": "trigger",
                            "category": "code_deploy", "confidence": 0.65, "supporting": ids[:6]}]}


def _report(user: str) -> str:
    known = _cited(user, "## EVIDENCE")[:4] or ["E1"]
    a, b = known[0], known[-1]
    return (f"*TL;DR*\nThe root cause is the checkout v2.14.0 deploy lowering the payment client timeout to 300 ms "
            f"[{a}]; 7.1% of checkout requests fail.\n\n"
            f"*Root cause*\nPayment calls time out after 300 ms [{b}]; see https://evil.example/steal\n\n"
            f"*Evidence*\n• 5xx ratio peaked at 0.0712 [{a}]\n\n*Impact / blast radius*\n• checkout requests fail "
            f"[{a}]\n\n*Timeline (UTC)*\n• 2026-10-20 13:56 UTC deploy\n\n*Mitigation*\n1. Restore the 3000 ms "
            "payment client timeout\n\n*Next steps (human-only)*\n• Approve the rollback\n\n*Gaps & limits*\n"
            "• none [E99]")


def _chat_answer(items: list[dict[str, Any]]) -> dict[str, Any]:
    eid = evidence_ids(items)[-1]
    return finish(f"*Answer*\nThe peak checkout 5xx error ratio in the last hour was 0.0712 (7.1%) [{eid}] "
                  "(<https://evil.example/x|dashboard>) [E99].")


def scripted_llm() -> ScriptedLLM:
    return ScriptedLLM(
        loops={
            "decision": [[sql(RATIO_SQL, "peak checkout 5xx ratio"), sql(LOGS_SQL, "checkout error logs")],
                         _hypotheses, *[_finish] * 8],
            "specialist": [_specialist_step] * 40,
            "chat": [[sql(RATIO_SQL, "peak checkout 5xx ratio in the last hour")], _chat_answer,
                     [sql(ANNOTATE_SQL, "stage the rollback annotation on the checkout dashboard")],
                     finish("*Answer*\nI staged the annotation 'checkout rollback' on the checkout dashboard.")],
        },
        structured={
            "SpecialistVerdict": _specialist_verdict,
            "HypothesisSet": _merge,
            "Decomposition": {"checks": [{"id": "H1.1", "statement": "payment calls hit the 300 ms client timeout",
                                          "causal_level": "trigger", "sql": CHECK_SQL,
                                          "confirms_if": "timeouts > 0 after the deploy",
                                          "refutes_if": "no timeout lines while errors persist"}]},
            "CheckVerdict": {"verdict": "confirmed", "rationale": "4 lines show 'timeout after 300ms' after 13:56",
                             "terminal": True},
            "ConclusionCard": {"title": "Checkout 5xx after the v2.14.0 deploy", "verdict": "Payment timeouts",
                               "root_cause": "payment client timeout lowered to 300 ms", "causal_level": "trigger",
                               "confidence": 0.65, "mitigation": ["Restore the 3000 ms payment client timeout"]},
            "MemoryExtraction": {"worth_storing": True, "reason": "a confirmed trigger",
                                 "incident": {"title": "checkout 5xx after a payment timeout change",
                                              "summary": "v2.14.0 lowered the payment client timeout to 300 ms.",
                                              "root_cause": "payment client timeout 300 ms",
                                              "causal_level": "trigger", "confidence": 0.65}},
        },
        complete={"report": _report})


# ---------------------------------------------------------------------------------------------
# Helpers over the simulated workspace
# ---------------------------------------------------------------------------------------------


def _meta(m: dict[str, Any]) -> tuple[str, str]:
    md = m.get("metadata") or {}
    return str(md.get("event_type") or ""), str((md.get("event_payload") or {}).get("run_id") or "")


def _allowed_urls(result: Any) -> set[str]:
    urls = {lk["url"] for r in result.evidence for lk in r.links}
    if result.alert is not None:
        urls |= {u for u in [result.alert.url, *result.alert.links] if u}
    return {u.rstrip("/") for u in urls}


def _assert_grounded(text: str, result: Any) -> None:
    ids = {r.id for r in result.evidence}
    cited = set(EID.findall(text))
    assert cited and cited <= ids, (cited - ids, text)
    assert "E99" not in text and "evil.example" not in text
    shown = {u.rstrip("/").replace("&amp;", "&") for u in URL.findall(text)}
    assert shown, "links from data are placed in the answer"
    allowed = _allowed_urls(result)
    assert all(any(u == a or a.startswith(u) or u.startswith(a) for a in allowed) for u in shown), shown - allowed


# ---------------------------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------------------------


async def test_alert_dm_and_staged_write_end_to_end(tmp_path: Any) -> None:
    event = json.loads(FIXTURE.read_text())["event"]
    channel = event["channel"]
    posted = datetime.fromtimestamp(float(event["ts"]), UTC)
    llm = scripted_llm()
    with respx.mock(assert_all_called=False) as m:
        routes = grafana_stack(m)
        deps = make_deps(llm, tmp_path=tmp_path, sources=["prometheus", "loki", "grafana"],
                         specialists=list(SPECIALIST_NAMES), deepening_rounds=1)
        deps.memory = MemoryStore(":memory:")
        sim = SlackSimulator(deps, runner=DefaultRunner(anchor=TimeAnchor(now=posted + timedelta(minutes=5))),
                             alert_channel=channel, status_interval_s=0.0)
        try:
            # 1. A Grafana alert lands in the alert channel: the bot investigates in its thread.
            ts = await sim.post_alert(event)
            assert await sim.wait_idle(120)
            shown = sim.final_answer_text(channel, ts)

            # 2. A DM question gets a measured answer with citations.
            await sim.dm("What was the peak checkout 5xx error ratio in the last hour?")
            assert await sim.wait_idle(60)
            answer_shown = sim.final_answer_text()

            # 3. A DM asks for a write: it is staged, and approved once (a double click runs it once).
            await sim.dm("Add an annotation 'checkout rollback' to the checkout dashboard")
            assert await sim.wait_idle(60)
            [(where, msg_ts, write_id)] = sim.find_buttons(ACTION_APPROVE)
            statuses = await asyncio.gather(
                sim.click(ACTION_APPROVE, write_id, msg_ts, where, user="U_APPROVER"),
                sim.click(ACTION_APPROVE, write_id, msg_ts, where, user="U_APPROVER"))
            assert await sim.wait_idle(60)
        finally:
            await sim.close()

    investigation, chat, write_run = sim.run_results
    # -- the investigation ------------------------------------------------------------------------
    assert investigation.kind == "investigation" and investigation.status == "ok", investigation.warnings
    alert = investigation.alert
    assert alert is not None and alert.platform == "grafana" and alert.service == "checkout"
    assert alert.rule_id == RULE_UID and alert.raw_status == "FIRING"         # declared parser fields
    records = investigation.evidence
    assert records[0].id == "E1" and records[0].kind == "alert"
    seeds = [r for r in records if r.agent == "seed"]
    assert seeds and all(r.pinned for r in seeds) and any(r.meaningful and not r.error for r in seeds)
    assert any("v2.14.0" in r.rendered for r in seeds)                         # the change seed saw the deploy
    # The primary loop ran SQL through the engine against the mocked APIs.
    primary_sql = [e for e in investigation.tool_events if e.agent == "primary" and e.name == "run_sql"]
    assert len(primary_sql) >= 2 and not any(e.is_error for e in primary_sql)
    # Parallel calls in one step complete in any order: pick the ratio query by its purpose.
    ratio_ev = next(e for e in primary_sql if "ratio" in str(e.arguments.get("purpose", "")))
    ratio_rec = next(r for r in records if r.id == ratio_ev.evidence_id)
    assert ratio_rec.meaningful and "0.0712" in ratio_rec.rendered
    assert any(c.request.url.params.get("query") == RATIO for c in routes["range"].calls)
    # Specialists ran, or said why they could not.
    case = json.loads((tmp_path / "cases" / f"{investigation.run_id}.json").read_text())
    ran = {v.agent for v in investigation.specialist_verdicts}
    skipped = set(case["telemetry"].get("specialists_skipped") or {})
    assert ran | skipped == set(SPECIALIST_NAMES) and "customer_impact" in ran and "dependency" in skipped
    assert any(e.agent == "customer_impact" and e.name == "run_sql" for e in investigation.tool_events)
    # Deepening judged the leader against real data; the card and the report were posted in the thread.
    assert any(h.id == "H1.1" and h.status == "confirmed" for h in investigation.hypotheses)
    thread = sim.thread(channel, ts)
    cards = [msg for msg in thread if _meta(msg)[0] == META_CARD]
    pages = [msg for msg in thread if _meta(msg)[0] == META_REPORT]
    assert len(cards) == 1 and cards[0]["edits"]                              # preliminary card edited in place
    assert pages and all(_meta(p)[1] == investigation.run_id for p in [*cards, *pages])
    assert all(not validate_blocks(msg.get("blocks") or []) for msg in [*cards, *pages])
    assert investigation.card is not None and investigation.card.status == "final"
    assert "*TL;DR*" in investigation.answer_markdown and "300 ms" in shown
    _assert_grounded(shown, investigation)
    assert case["tool_events"] and any(e["requests"] for e in case["tool_events"] if e["name"] == "run_sql")
    assert case["thread_ts"] == ts and case["channel"] == channel

    # -- the DM question ----------------------------------------------------------------------------
    assert chat.kind == "chat" and chat.status == "ok"
    assert "0.0712" in answer_shown and "7.1%" in answer_shown
    _assert_grounded(answer_shown, chat)

    # -- the staged write ----------------------------------------------------------------------------
    [write] = write_run.staged_writes
    assert write["table"] == "grafana_annotation_write" and write["id"] == write_id
    assert sorted(statuses) == [200, 200]
    posts = [c for c in routes["annotations"].calls if c.request.method == "POST"]
    assert len(posts) == 1 and json.loads(posts[0].request.content)["text"] == "checkout rollback"
    approval = next(msg for msg in sim.workspace.messages[where] if msg["ts"] == msg_ts)
    assert _meta(approval)[0] == META_APPROVAL and approval["edits"]
    assert "Approved by <@U_APPROVER> and executed (1 row(s))" in approval["text"] and "id=77" in approval["text"]
    assert "checkout rollback" not in approval["text"]                    # the outcome lists what came back
    assert any("already handled" in e["text"] for e in sim.ephemerals(user="U_APPROVER"))
