"""Offline fixtures shared by the seeds / specialists / memory tests (no tests here).

A Prometheus + Loki catalog (real manifests) plus two TEST-ONLY manifests under
tests/fixtures/manifests: ``testkube`` (k8s_state / k8s_events / k8s_logs) and ``testdeploy``
(change_events). Every HTTP call is answered by respx.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from omniops.agent.context import Deps, RunContext
from omniops.agent.memory import MemoryStore
from omniops.agent.types import Alert
from omniops.engine import loader, timeutil
from omniops.engine.catalog import Catalog
from omniops.settings import Settings

TRIGGER = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
MANIFESTS = Path(__file__).parent / "fixtures" / "manifests"
ALL_SOURCES = ["prometheus", "loki", "testkube", "testdeploy"]
CHANGE_URL = "https://git.example.com/shop/commit/abc123"
ALERT_QUERY = 'sum(rate(http_requests_total{service="checkout",code=~"5.."}[5m])) > 5'


_MANIFEST_CACHE: dict[str, Any] = {}


def _cached_manifests(strict: bool = True) -> Any:
    """Parsing every manifest takes seconds; these tests only need it once per directory set."""
    key = f"{os.environ.get('OMNIOPS_MANIFEST_DIRS', '')}|{strict}"
    if key not in _MANIFEST_CACHE:
        _MANIFEST_CACHE[key] = loader.load_all_manifests(strict=strict)
    return _MANIFEST_CACHE[key]


@pytest.fixture
def source_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMNIOPS_CONFIG", raising=False)
    monkeypatch.setenv("OMNIOPS_MANIFEST_DIRS", str(MANIFESTS))
    monkeypatch.setattr("omniops.engine.catalog.load_all_manifests", _cached_manifests)
    for k, v in {"PROMETHEUS_URL": "http://prom.test", "LOKI_URL": "http://loki.test",
                 "TESTKUBE_URL": "http://kube.test", "TESTDEPLOY_URL": "http://deploy.test"}.items():
        monkeypatch.setenv(k, v)


def _ns(dt: datetime) -> str:
    return str(int(dt.timestamp() * 1e9))


def _loki(request: httpx.Request) -> httpx.Response:
    start = int(request.url.params["start"]) / 1e9
    if start > (TRIGGER - timedelta(hours=12)).timestamp():          # the analysis window
        vals = [[_ns(TRIGGER - timedelta(minutes=5 - i)), f"ERROR payment client timeout after 3000ms order={i}"]
                for i in range(4)]
        vals.append([_ns(TRIGGER - timedelta(minutes=8)), "ERROR cache miss for key sku-1"])
    else:                                                            # the same window a day earlier
        vals = [[_ns(TRIGGER - timedelta(days=1, minutes=8)), "ERROR cache miss for key sku-9"]]
    return httpx.Response(200, json={"status": "success", "data": {"resultType": "streams", "result": [
        {"stream": {"service_name": "checkout", "level": "error"}, "values": vals}]}})


def _prom_range(request: httpx.Request) -> httpx.Response:
    p = request.url.params
    s, e, step = int(float(p["start"])), int(float(p["end"])), int(float(p["step"]))
    vals = [[t, "9" if t > TRIGGER.timestamp() - 600 else "1"] for t in range(s, e + 1, step)]
    return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": {"service": "checkout"}, "values": vals}]}})


PODS = {"items": [
    {"metadata": {"namespace": "shop", "name": "checkout-7f9c-abc"}, "spec": {"nodeName": "kind-worker"},
     "status": {"phase": "Running", "containerStatuses": [
         {"name": "checkout", "ready": False, "restartCount": 4,
          "state": {"waiting": {"reason": "CrashLoopBackOff", "message": "back-off 40s"}},
          "lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137}}}]}},
    {"metadata": {"namespace": "shop", "name": "cart-1"}, "spec": {"nodeName": "kind-worker"},
     "status": {"phase": "Running", "containerStatuses": [
         {"name": "cart", "ready": True, "restartCount": 0, "state": {"running": {}}}]}},
]}
EVENTS = {"items": [
    {"metadata": {"namespace": "shop"}, "type": "Warning", "reason": "BackOff", "count": 7,
     "involvedObject": {"kind": "Pod", "name": "checkout-7f9c-abc"}, "message": "Back-off restarting failed container",
     "lastTimestamp": "2026-10-01T11:58:30Z"},
    {"metadata": {"namespace": "shop"}, "type": "Normal", "reason": "Pulled", "count": 1,
     "involvedObject": {"kind": "Pod", "name": "cart-1"}, "message": "pulled", "lastTimestamp": "2026-10-01T11:50:00Z"},
]}
BATCH_POD = {"metadata": {"namespace": "batch", "name": "cache-warmer-fill-x1"}, "spec": {"nodeName": "kind-worker"},
             "status": {"phase": "Running", "containerStatuses": [
                 {"name": "fill", "ready": False, "restartCount": 9,
                  "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                  "lastState": {"terminated": {"reason": "Error", "exitCode": 1}}}]}}
BATCH_EVENT = {"metadata": {"namespace": "batch"}, "type": "Warning", "reason": "BackOff", "count": 21,
               "involvedObject": {"kind": "Pod", "name": "cache-warmer-fill-x1"},
               "message": "Back-off restarting failed container", "lastTimestamp": "2026-10-01T11:57:00Z"}
CHANGES = {"changes": [
    {"time": "2026-10-01T11:52:00Z", "app": "checkout", "message": "sync checkout to rev abc123 (raise batch size)",
     "revision": "abc123", "author": "dev@example.com", "url": CHANGE_URL},
    {"time": "2026-09-30T09:00:00Z", "app": "cart", "message": "deploy cart v2", "revision": "v2", "author": "ops",
     "url": "https://git.example.com/shop/commit/v2"},
]}
RULES = {"status": "success", "data": {"groups": [{"name": "g", "file": "f", "rules": [
    {"name": "CheckoutErrors", "query": ALERT_QUERY, "duration": 300, "type": "alerting", "state": "firing",
     "health": "ok", "labels": {"severity": "critical"}}]}]}}
ALERTS = {"status": "success", "data": {"alerts": [
    {"labels": {"alertname": "CheckoutErrors", "severity": "critical"}, "state": "firing",
     "activeAt": "2026-10-01T11:58:00Z", "value": "9"},
    {"labels": {"alertname": "RedisMemoryHigh", "severity": "warning", "service": "redis"}, "state": "firing",
     "activeAt": "2026-10-01T11:40:00Z", "value": "0.97"},
]}}


def mock_sources(m: respx.MockRouter, *, loki_status: int = 200) -> dict[str, respx.Route]:
    """Register every endpoint the seed pack and the specialists touch; returns the routes by name."""
    routes = {
        "rules": m.get("http://prom.test/api/v1/rules").respond(json=RULES),
        "alerts": m.get("http://prom.test/api/v1/alerts").respond(json=ALERTS),
        "range": m.get("http://prom.test/api/v1/query_range").mock(side_effect=_prom_range),
        "pods": m.get("http://kube.test/api/v1/namespaces/shop/pods").respond(json=PODS),
        "events": m.get("http://kube.test/api/v1/namespaces/shop/events").respond(json=EVENTS),
        "all_pods": m.get("http://kube.test/api/v1/pods").respond(json={"items": [*PODS["items"], BATCH_POD]}),
        "all_events": m.get("http://kube.test/api/v1/events").respond(
            json={"items": [*EVENTS["items"], BATCH_EVENT]}),
        "logs": m.get(url__regex=r"http://kube\.test/api/v1/namespaces/shop/pods/[^/]+/log").respond(
            text="starting batch of 5000 items\njava.lang.OutOfMemoryError: Java heap space\n"),
        "changes": m.get("http://deploy.test/changes").respond(json=CHANGES),
    }
    if loki_status == 200:
        routes["loki"] = m.get("http://loki.test/loki/api/v1/query_range").mock(side_effect=_loki)
    else:
        routes["loki"] = m.get("http://loki.test/loki/api/v1/query_range").respond(loki_status, text="boom")
    return routes


def make_alert(**over: Any) -> Alert:
    base: dict[str, Any] = {
        "platform": "prometheus", "rule_name": "CheckoutErrors", "title": "[FIRING:1] CheckoutErrors",
        "service": "checkout", "namespace": "shop", "query": ALERT_QUERY, "triggered_at": TRIGGER, "value": "9",
        "severity": "critical", "text": "checkout 5xx rate above 5 req/s. critical threshold 5, warning threshold 2",
        "url": "http://grafana.test/alerting/CheckoutErrors"}
    base.update(over)
    return Alert(**base)


def make_ctx(*, llm: Any, sources: list[str] | None = None, settings: Settings | None = None,
             memory: MemoryStore | None = None, alert: Alert | None = None, budget_s: float = 600.0) -> RunContext:
    cat = Catalog.from_env(only=sources or ALL_SOURCES)
    anchor = timeutil.TimeAnchor.for_trigger(TRIGGER, wall_now=TRIGGER + timedelta(hours=1))
    deps = Deps(catalog=cat, llm=llm, settings=settings or Settings(), memory=memory)
    return RunContext.create(deps, anchor, budget_s, alert=alert or make_alert(), run_id="run1")


def ids_in(text: str, section: str) -> list[str]:
    """Evidence ids listed as record lines ("E7 (agent; title): ...") after ``section`` in a prompt."""
    tail = text.split(section, 1)[-1]
    return re.findall(r"^([ES]\d+) \(", tail, flags=re.MULTILINE)


async def close_ctx(ctx: RunContext) -> None:
    ctx.close()
    await ctx.deps.catalog.aclose()
