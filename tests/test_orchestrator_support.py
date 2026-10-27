"""Offline fixtures for the orchestrator and chat tests (no tests here).

A real Prometheus + Loki (+ Grafana for staged writes) catalog built from the shipped manifests; every
HTTP call is answered by respx. Model turns come from ``ScriptedLLM`` (the production tool loop and
structured driver run underneath it).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from omniops.agent.context import Deps
from omniops.agent.types import Alert
from omniops.engine import loader
from omniops.engine.catalog import Catalog
from omniops.engine.timeutil import TimeAnchor
from omniops.settings import Settings

TRIGGER = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
WALL = TRIGGER + timedelta(hours=1)
ALERT_QUERY = 'sum(rate(http_requests_total{service="checkout",code=~"5.."}[5m])) > 5'
ERROR_SQL = ("SELECT max(value) AS peak, arg_max(ts, value) AS peak_at FROM prometheus_query_range "
             "WHERE query = 'sum(rate(http_requests_total{service=\"checkout\",code=~\"5..\"}[5m]))'")
LOGS_SQL = "SELECT ts, message FROM loki_logs WHERE service = 'checkout' AND level = 'error' ORDER BY ts DESC LIMIT 20"
PROM_UI = "http://prom.test/graph"

_MANIFESTS: dict[str, Any] = {}


def _cached_manifests(strict: bool = True) -> Any:
    """Parsing every manifest takes seconds; load them once per test session."""
    if strict not in _MANIFESTS:
        _MANIFESTS[strict] = loader.load_all_manifests(strict=strict)
    return _MANIFESTS[strict]


@pytest.fixture
def source_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMNIOPS_CONFIG", raising=False)
    monkeypatch.delenv("OMNIOPS_MANIFEST_DIRS", raising=False)
    monkeypatch.setattr("omniops.engine.catalog.load_all_manifests", _cached_manifests)
    for k, v in {"PROMETHEUS_URL": "http://prom.test", "LOKI_URL": "http://loki.test",
                 "GRAFANA_URL": "http://grafana.test"}.items():
        monkeypatch.setenv(k, v)
    for k in ("PROMETHEUS_TOKEN", "GRAFANA_TOKEN", "GRAFANA_USERNAME", "GRAFANA_PASSWORD"):
        monkeypatch.delenv(k, raising=False)


def _ns(dt: datetime) -> str:
    return str(int(dt.timestamp() * 1e9))


def _matrix(metric: dict[str, str], request: httpx.Request, high: str, low: str) -> httpx.Response:
    p = request.url.params
    s, e = int(float(p["start"]) / (1e9 if len(p["start"]) > 12 else 1)), int(float(p["end"]) /
                                                                              (1e9 if len(p["end"]) > 12 else 1))
    step = max(15, int(float(p.get("step") or 60)))
    vals = [[t, high if t > TRIGGER.timestamp() - 600 else low] for t in range(s, e + 1, step)]
    return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": metric, "values": vals}]}})


def _prom_range(request: httpx.Request) -> httpx.Response:
    return _matrix({"service": "checkout"}, request, "9.5", "0.2")


def _loki(request: httpx.Request) -> httpx.Response:
    query = request.url.params.get("query", "")
    if "count_over_time" in query or query.startswith(("sum", "rate")):
        return _matrix({"service_name": "checkout"}, request, "42", "1")
    start = int(request.url.params["start"]) / 1e9
    if start > (TRIGGER - timedelta(hours=12)).timestamp():
        vals = [[_ns(TRIGGER - timedelta(minutes=5 - i)),
                 f"ERROR payment client timeout after 3000ms order={i} trace_id=4bf92f3577b34da6a3ce929d0e0e{i:04d}"]
                for i in range(4)]
    else:
        vals = [[_ns(TRIGGER - timedelta(days=1, minutes=8)), "ERROR cache miss for key sku-9"]]
    return httpx.Response(200, json={"status": "success", "data": {"resultType": "streams", "result": [
        {"stream": {"service_name": "checkout", "level": "error"}, "values": vals}]}})


RULES = {"status": "success", "data": {"groups": [{"name": "g", "file": "f", "rules": [
    {"name": "CheckoutErrors", "query": ALERT_QUERY, "duration": 300, "type": "alerting", "state": "firing",
     "health": "ok", "labels": {"severity": "critical"}}]}]}}
ALERTS = {"status": "success", "data": {"alerts": [
    {"labels": {"alertname": "CheckoutErrors", "severity": "critical"}, "state": "firing",
     "activeAt": "2026-10-01T11:58:00Z", "value": "9"}]}}
EMPTY = {"status": "success", "data": {"resultType": "matrix", "result": [], "alerts": [], "groups": [],
                                       "activeTargets": []}}


def loki_logs(request: httpx.Request) -> httpx.Response:
    """The default Loki handler (exported so tests can wrap it)."""
    return _loki(request)


def mock_sources(m: respx.MockRouter, *, broken: bool = False, loki: Callable[..., Any] | None = None
                 ) -> dict[str, respx.Route]:
    """Every endpoint the seeds, specialists and scripted queries touch. ``broken`` makes the data
    endpoints fail or come back empty (the evidence-floor scenario); ``loki`` replaces the Loki handler."""
    routes: dict[str, respx.Route] = {}
    if broken:
        routes["range"] = m.get("http://prom.test/api/v1/query_range").respond(json=EMPTY)
        routes["loki"] = m.get("http://loki.test/loki/api/v1/query_range").respond(500, text="ingester unavailable")
        routes["rules"] = m.get("http://prom.test/api/v1/rules").respond(json=EMPTY)
        routes["alerts"] = m.get("http://prom.test/api/v1/alerts").respond(json=EMPTY)
    else:
        routes["range"] = m.get("http://prom.test/api/v1/query_range").mock(side_effect=_prom_range)
        routes["loki"] = m.get("http://loki.test/loki/api/v1/query_range").mock(side_effect=loki or _loki)
        routes["rules"] = m.get("http://prom.test/api/v1/rules").respond(json=RULES)
        routes["alerts"] = m.get("http://prom.test/api/v1/alerts").respond(json=ALERTS)
    routes["prom_other"] = m.get(url__regex=r"http://prom\.test/.*").respond(json=EMPTY)
    routes["loki_other"] = m.get(url__regex=r"http://loki\.test/.*").respond(json=EMPTY)
    routes["grafana"] = m.route(url__regex=r"http://grafana\.test/.*").respond(json={"id": 7, "message": "ok"})
    return routes


def make_alert(**over: Any) -> Alert:
    base: dict[str, Any] = {
        "platform": "prometheus", "rule_name": "CheckoutErrors", "title": "[FIRING:1] CheckoutErrors",
        "service": "checkout", "namespace": "shop", "query": ALERT_QUERY, "triggered_at": TRIGGER, "value": "9",
        "severity": "critical", "text": "checkout 5xx rate above 5 req/s. critical threshold 5",
        "url": "http://grafana.test/alerting/CheckoutErrors", "source_ts": "1790856000.000100",
        "source_channel": "C_ALERTS"}
    base.update(over)
    return Alert(**base)


def make_deps(llm: Any, *, tmp_path: Any, sources: list[str] | None = None, **settings: Any) -> Deps:
    defaults: dict[str, Any] = {"specialists": [], "deepening_rounds": 0, "case_dir": str(tmp_path / "cases"),
                                "investigation_budget_s": 300.0, "chat_budget_s": 120.0, "watchdog_grace_s": 30.0}
    defaults.update(settings)
    cat = Catalog.from_env(only=sources or ["prometheus", "loki"])
    return Deps(catalog=cat, llm=llm, settings=Settings(**defaults))


def pinned_clock() -> TimeAnchor:
    """A benchmark-style pinned wall clock: the flows resolve their windows against it, never the real clock."""
    return TimeAnchor(now=WALL)


def evidence_ids(items: list[dict[str, Any]]) -> list[str]:
    """Evidence ids from tool results the scripted model has seen so far ("E7 | ..."), in order."""
    text = json.dumps(items)
    return list(dict.fromkeys(re.findall(r"\b(E\d+) \|", text)))


def sql(query: str, purpose: str) -> dict[str, Any]:
    return {"name": "run_sql", "arguments": {"sql": query, "purpose": purpose}}


def finish(summary: str, gaps: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {"name": "finish", "arguments": {"summary": summary, "self_executable_gaps": gaps or [],
                                            "dismissed_leads": []}}


def hypotheses_step(statement: str = "payments dependency timeouts (3000 ms client timeout) cause checkout 5xx",
                    level: str = "trigger") -> Callable[[list[dict[str, Any]]], dict[str, Any]]:
    """record_hypotheses citing the evidence the model has seen: a dependency leader and two alternatives."""
    def step(items: list[dict[str, Any]]) -> dict[str, Any]:
        ids = evidence_ids(items)
        return {"name": "record_hypotheses", "arguments": {"hypotheses": [
            {"id": "H1", "statement": statement, "causal_level": level, "category": "dependency",
             "confidence": 0.65, "supporting": ids[:2], "missing": [], "next_query": None},
            {"id": "H2", "statement": "a checkout deploy introduced a regression", "causal_level": "trigger",
             "category": "code_deploy", "confidence": 0.2, "contradicting": ids[:1]},
            {"id": "H3", "statement": "traffic surge saturates checkout pods", "causal_level": "mechanism",
             "category": "traffic", "confidence": 0.15}]}}
    return step


def ids_in(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\bE\d+\b", text)))
