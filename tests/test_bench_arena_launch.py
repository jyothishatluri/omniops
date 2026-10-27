"""Arena launch messages through their real builders: what reaches Slack must never carry ground truth.

The answer keys are read here only to prove their absence (the launch module itself never reads
them). The Grafana-shaped message is fed to the alert normaliser the bot uses in production.
"""

from __future__ import annotations

import builtins
import itertools
import json
import os
import pathlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from omniops.bench.arena import launch as L

ROOT = Path(__file__).resolve().parents[1]
ARENA = Path(os.environ.get("ARENA_DIR", ROOT.parent / "vendor" / "project-arena"))
RULES = ROOT / "deploy" / "arena" / "observability" / "grafana"
FAULT = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
GRAFANA = "http://localhost:3000"

needs_arena = pytest.mark.skipif(not (ARENA / "scenarios" / "scenarios.json").is_file(),
                                 reason="Arena checkout not present (set ARENA_DIR)")


def answer_keys() -> dict[str, dict[str, Any]]:
    names = json.loads((ARENA / "scenarios" / "scenarios.json").read_text())
    return {n: json.loads((ARENA / "scenarios" / "faults" / n / "answer-key.json").read_text()) for n in names}


def probe(path: str, status: int = 200, ms: float = 80.0) -> L.EndpointProbe:
    return L.EndpointProbe(path, status, ms)


def every_customer_report() -> list[L.LaunchMessage]:
    """Customer reports for every combination of symptom kinds across the storefront journeys."""
    out = []
    for suite, journeys in (("full", L.STOREFRONT_JOURNEYS), ("smoke", L.SMOKE_JOURNEYS)):
        baseline = [probe(j.path) for j in journeys]
        variants = [(200, 80.0), (500, 120.0), (503, 40.0), (0, 15000.0), (200, 9000.0)]
        for combo in itertools.product(variants, repeat=len(journeys)):
            current = [probe(j.path, s, ms) for j, (s, ms) in zip(journeys, combo, strict=True)]
            symptoms = L.user_visible_symptoms(journeys, baseline, current)
            out.append(L.build_launch([], symptoms, since=FAULT, grafana_url=GRAFANA, subject=L.SUBJECTS[suite]))
    return out


@needs_arena
def test_customer_reports_never_contain_any_answer_key() -> None:
    keys = answer_keys()
    assert len(keys) == 21
    phrases, tokens, names = L.forbidden_from_truths(keys.values())
    causes = [k["ground_truth"]["cause"] for k in keys.values()]
    reports = every_customer_report()
    assert len({r.text for r in reports}) > 50            # the combinations really produce different reports
    for msg in reports:
        assert msg.path == "customer_report" and not msg.attachments
        text = msg.visible_text().lower()
        for cause in causes:
            assert cause.lower() not in text
            assert cause.lower()[:60] not in text
        assert L.leak_check(msg.visible_text(), phrases=phrases, tokens=tokens, scenario_ids=names) == []


def test_customer_report_builder_reads_no_files(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the launch builder must not read files (answer keys, fault manifests)")

    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(pathlib.Path, "read_text", refuse)
    monkeypatch.setattr(pathlib.Path, "read_bytes", refuse)
    monkeypatch.setattr(os, "listdir", refuse)
    msgs = every_customer_report()
    alert = L.alerts_from_alertmanager([am_alert("Pod Pending for more than 2 minutes", FAULT + timedelta(minutes=3))])
    msgs.append(L.build_launch(alert, [], since=FAULT, grafana_url=GRAFANA, subject="the online shop"))
    assert msgs[-1].path == "alert"


def test_customer_report_says_only_what_got_worse() -> None:
    journeys = L.STOREFRONT_JOURNEYS
    baseline = [probe("/"), probe("/api/products", ms=120), probe(journeys[2].path, 500), probe(journeys[3].path)]
    current = [probe("/", 502), probe("/api/products", ms=6500), probe(journeys[2].path, 500), probe(journeys[3].path, 0)]
    symptoms = L.user_visible_symptoms(journeys, baseline, current)
    assert [(s.phrase, s.kind) for s in symptoms] == [
        ("the storefront home page", "failing"), ("browsing products", "slow"), ("viewing the shopping cart", "unreachable")]
    msg = L.build_launch([], symptoms, since=FAULT, grafana_url=GRAFANA, subject="the online shop")
    assert "HTTP 502" in msg.text and "about 6.5 s instead of the usual 0.1 s" in msg.text
    assert "recommendations" not in msg.text            # already failing while healthy: not a new symptom
    assert "10:00 UTC" in msg.text
    event = msg.slack_event(channel="C0ARENAOPS", ts="1793095300.000100")
    assert event["user"] == L.SUPPORT_USER and "bot_id" not in event and "subtype" not in event
    mention = msg.mention_event(channel="C0ARENAOPS", thread_ts=event["ts"], ts="1793095400.000100",
                                bot_user_id="U0OMNIOPS")
    assert mention["type"] == "message" and mention["thread_ts"] == event["ts"]
    assert mention["text"] == "<@U0OMNIOPS> investigate"     # the request carries no content of its own

    vague = L.build_launch([], [], since=FAULT, grafana_url=GRAFANA, subject="the online shop")
    assert "could not point to a specific error" in vague.text


def am_alert(name: str, starts: datetime, **labels: str) -> dict[str, Any]:
    uid = "omniops-" + name.lower().replace(" ", "-")[:20]
    return {
        "labels": {"alertname": name, "grafana_folder": "Platform alerts", "omniops": "arena", "severity": "critical",
                   "__alert_rule_uid__": uid, **labels},
        "annotations": {"summary": f"{name} summary", "description": "generic symptom", "__dashboardUid__": "omniops-arena",
                        "__panelId__": "6", "__values__": '{"A": 1, "C": 1}', "__orgId__": "1"},
        "startsAt": starts.strftime("%Y-%m-%dT%H:%M:%S.123456789Z"), "endsAt": "2026-10-27T11:00:00Z",
        "fingerprint": uid + "".join(sorted(labels.values())),
        "generatorURL": f"{GRAFANA}/alerting/grafana/{uid}/view?orgId=1",
        "status": {"state": "active", "silencedBy": [], "inhibitedBy": []},
    }


async def test_grafana_alert_launch_is_grafana_shaped_and_parses() -> None:
    started = FAULT + timedelta(minutes=2, seconds=30)
    payload = [
        am_alert("Container OOMKilled", started, namespace="batch", pod="worker-7d9", container="worker"),
        am_alert("Error log burst", FAULT - timedelta(hours=1), namespace="shop", app="frontend"),   # leftover
        am_alert("DatasourceError", started),
        {**am_alert("Silenced", started), "status": {"state": "suppressed", "silencedBy": ["x"], "inhibitedBy": []}},
    ]
    payload[2]["labels"]["alertname"] = "DatasourceError"
    alerts = L.alerts_from_alertmanager(payload)
    assert [a.name for a in alerts] == ["Container OOMKilled", "Error log burst", "DatasourceError"]
    assert all(not k.startswith("__") for a in alerts for k in {**a.labels, **a.annotations})
    selected, excluded = L.select_launch_alerts(alerts, FAULT)
    assert [a.name for a in selected] == ["Container OOMKilled"]
    assert {e["alertname"] for e in excluded} == {"Error log burst", "DatasourceError"}

    sent = FAULT + timedelta(minutes=4)
    msg = L.build_launch(selected, [], since=FAULT, grafana_url=GRAFANA, subject="the online shop",
                         excluded=excluded, now=sent)
    assert msg.path == "alert" and msg.excluded_alerts == excluded
    att = msg.attachments[0]
    assert att["title"] == "[FIRING:1] Platform alerts (Container OOMKilled worker batch arena worker-7d9 critical)"
    assert att["title_link"] == selected[0].generator_url
    assert att["text"].startswith("**Firing**\n\nValue: A=1, C=1\nLabels:\n - alertname = Container OOMKilled\n")
    from_ms = int((started.replace(microsecond=123456) - timedelta(hours=1)).timestamp() * 1000)
    assert f"Dashboard: {GRAFANA}/d/omniops-arena?from={from_ms}&orgId=1&to={int(sent.timestamp() * 1000)}\n" in att["text"]
    assert "&viewPanel=6\n" in att["text"]
    assert "matcher=__alert_rule_uid__%3Domniops-container-oomkilled" in att["text"]
    assert att["footer"].startswith("Grafana v") and msg.username == "Grafana"

    event = msg.slack_event(channel="C0ARENAOPS", ts=f"{sent.timestamp():.6f}")
    assert event["subtype"] == "bot_message" and "&amp;" in event["attachments"][0]["text"]
    alert_mod = pytest.importorskip("omniops.agent.alert")
    alert = await alert_mod.parse_slack_alert(event, None)
    assert alert is not None and alert.platform == "grafana" and alert.parsed_by == "deterministic"
    assert alert.rule_name == "Container OOMKilled" and alert.status == "firing"
    assert alert.rule_id == "omniops-container-oomkilled" and alert.namespace == "batch"
    assert alert.triggered_at is not None and abs((alert.triggered_at - started).total_seconds()) < 1


def test_webhook_copy_wins_over_the_polled_alert() -> None:
    started = FAULT + timedelta(minutes=1)
    polled = L.alerts_from_alertmanager([am_alert("Deployment has unavailable replicas", started, namespace="shop")])
    hook = {"status": "firing", "alerts": [
        {"status": "firing", "labels": dict(polled[0].labels), "annotations": dict(polled[0].annotations),
         "startsAt": started.isoformat(), "fingerprint": polled[0].fingerprint, "values": {"A": 2, "C": 1},
         "generatorURL": polled[0].generator_url, "silenceURL": f"{GRAFANA}/alerting/silence/new?x=1",
         "dashboardURL": f"{GRAFANA}/d/omniops-arena?from=1&orgId=1&to=2", "panelURL": ""},
        {"status": "resolved", "labels": {"alertname": "old"}, "annotations": {}, "startsAt": started.isoformat()}]}
    merged = L.merge_alerts(polled, L.alerts_from_webhook(hook))
    assert len(merged) == 1 and merged[0].source == "webhook" and merged[0].values == {"A": 2.0, "C": 1.0}
    text = L.grafana_slack_message(merged, grafana_url=GRAFANA, now=FAULT)["attachments"][0]["text"]
    assert f"Silence: {GRAFANA}/alerting/silence/new?x=1\n" in text and "Dashboard: " in text


@needs_arena
def test_leak_guard_catches_planted_ground_truth() -> None:
    keys = answer_keys()
    phrases, tokens, names = L.forbidden_from_truths(keys.values())
    cause = keys["oom"]["ground_truth"]["cause"]
    planted = f"Hi team, {cause}"
    assert any(h.startswith("phrase:") for h in L.leak_check(planted, phrases=phrases))
    assert [h.lower() for h in L.leak_check("the asset-syncer pod looks odd", tokens=tokens)] == [
        "identifier: asset-syncer"]
    assert L.leak_check("scenario cart-failure is running", scenario_ids=names) == ["scenario id: cart-failure"]


@needs_arena
def test_alert_rules_and_dashboard_encode_no_answer_key() -> None:
    keys = answer_keys()
    phrases, tokens, names = L.forbidden_from_truths(keys.values())
    images = {v["image"] for v in json.loads((ARENA / "scenarios" / "scenarios.json").read_text()).values()
              if v.get("image")}
    for path in (RULES / "alert-rules.yaml", RULES / "arena-overview.json"):
        text = path.read_text()
        assert L.leak_check(text, phrases=phrases, tokens=tokens, scenario_ids=[n for n in names if "-" in n]) == []
        assert not [i for i in images if i in text], path
    doc = yaml.safe_load((RULES / "alert-rules.yaml").read_text())
    rules = [r for g in doc["groups"] for r in g["rules"]]
    assert len(rules) == 14 and {g["folder"] for g in doc["groups"]} == {L.ALERT_FOLDER}
    dash = json.loads((RULES / "arena-overview.json").read_text())
    panels = {str(p["id"]): p for p in dash["panels"]}
    for r in rules:
        assert r["labels"]["omniops"] == "arena"
        assert {d["datasourceUid"] for d in r["data"]} <= {"prometheus", "loki", "__expr__"}
        expr = next(d for d in r["data"] if d["refId"] == "A")["model"]["expr"]
        assert r["annotations"]["__dashboardUid__"] == dash["uid"]
        assert panels[r["annotations"]["__panelId__"]]["targets"][0]["expr"] == expr
    ds = yaml.safe_load((RULES / "datasources.yaml").read_text())["datasources"]
    assert {d["uid"]: d["type"] for d in ds} == {"prometheus": "prometheus", "loki": "loki", "tempo": "tempo"}
