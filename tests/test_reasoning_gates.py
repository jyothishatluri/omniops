"""Readiness and depth gates driven through a real RunContext and catalog (Prometheus, Loki, Tempo)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from omniops.agent.context import Deps, RunContext
from omniops.agent.gates import (
    classify_pattern,
    depth_pushback,
    evidence_floor_ok,
    gate_pushback,
    readiness_blockers,
)
from omniops.agent.types import Alert, EvidenceRecord
from omniops.engine.catalog import Catalog
from omniops.engine.timeutil import TimeAnchor
from omniops.settings import Settings

TRIGGER = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def lgtm() -> Iterator[Catalog]:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("PROMETHEUS_URL", "http://prom.test")
        mp.setenv("LOKI_URL", "http://loki.test")
        mp.setenv("TEMPO_URL", "http://tempo.test")
        yield Catalog.from_env(only=["prometheus", "loki", "tempo"])


@pytest.fixture(scope="module")
def prom_only() -> Iterator[Catalog]:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("PROMETHEUS_URL", "http://prom.test")
        yield Catalog.from_env(only=["prometheus"])


def _ctx(catalog: Catalog, title: str, budget: float = 600) -> RunContext:
    deps = Deps(catalog=catalog, llm=None, settings=Settings())  # type: ignore[arg-type]
    anchor = TimeAnchor.for_trigger(TRIGGER, wall_now=TRIGGER + timedelta(hours=1))
    return RunContext.create(deps, anchor, budget, alert=Alert(platform="grafana", title=title, service="checkout"))


def _add(ctx: RunContext, table: str, sql: str = "SELECT 1", **kw: Any) -> str:
    rec = EvidenceRecord(id="", kind=kw.pop("kind", "query"), agent=kw.pop("agent", "primary"), title=table,
                         summary="5 rows", sql=sql, tables=[table], row_count=kw.pop("row_count", 5), **kw)
    return ctx.board.add(rec).id


def _hyps(ctx: RunContext, *specs: tuple[str, str, str, float]) -> None:
    ctx.tracker.update([{"id": hid, "statement": f"{hid} claim about {cat}", "causal_level": level,
                         "category": cat, "confidence": conf, "next_query": f"SELECT discriminating FROM {hid}"}
                        for hid, level, cat, conf in specs])


@pytest.mark.parametrize(("text", "pattern"), [
    ("High latency on checkout: p99 above 2s", "latency"),
    ("Error rate 5xx above 5% on api", "errors"),
    ("CPU saturation on node ip-10-0-1-2", "saturation"),
    ("checkout is down: health check failed", "availability"),
    ("Database connection errors from checkout", "dependency"),
    ("Something odd with the shop", "unknown"),
])
def test_classify_pattern(text: str, pattern: str) -> None:
    assert classify_pattern(text) == pattern


def test_classify_pattern_falls_back_to_evidence() -> None:
    rec = EvidenceRecord(id="E1", kind="seed", agent="seed", title="p99 latency of checkout", summary="slow")
    assert classify_pattern("Checkout ticket #42", [rec]) == "latency"


def test_latency_needs_apm_and_names_trace_tables(lgtm: Catalog) -> None:
    ctx = _ctx(lgtm, "High latency on checkout: p99 above 2s")
    _add(ctx, "prometheus_query_range", "SELECT * FROM prometheus_query_range WHERE query = 'up'")
    blockers = readiness_blockers(ctx)
    apm = [b for b in blockers if "APM/traces" in b]
    assert apm and "query these tables: tempo_search" in apm[0]
    assert not any("missing logs" in b for b in blockers)            # latency does not require logs
    assert any("only 1 meaningful evidence item" in b for b in blockers)
    assert any("no hypotheses recorded" in b for b in blockers)
    _add(ctx, "tempo_search", agent="seed")
    _hyps(ctx, ("H1", "trigger", "code_deploy", 0.5), ("H2", "mechanism", "dependency", 0.3))
    assert readiness_blockers(ctx) == []
    assert evidence_floor_ok(ctx)
    ctx.close()


def test_signal_required_only_when_a_source_exists(prom_only: Catalog) -> None:
    ctx = _ctx(prom_only, "Error rate 5xx above 5% on api")
    _add(ctx, "prometheus_query_range")
    _add(ctx, "prometheus_query_range", agent="seed")
    _hyps(ctx, ("H1", "trigger", "code_deploy", 0.5), ("H2", "mechanism", "traffic", 0.2))
    assert readiness_blockers(ctx) == []           # no logs or trace source configured: cannot be required
    ctx.close()


def test_saturation_needs_infrastructure_and_container_metrics_count(prom_only: Catalog) -> None:
    ctx = _ctx(prom_only, "CPU saturation on checkout")
    _add(ctx, "prometheus_query_range", "SELECT * FROM prometheus_query_range WHERE query = 'rate(http_total[5m])'")
    _add(ctx, "prometheus_query_range", "SELECT * FROM prometheus_query_range WHERE query = 'up'", agent="seed")
    _hyps(ctx, ("H1", "mechanism", "resource", 0.5), ("H2", "trigger", "code_deploy", 0.2))
    infra = [b for b in readiness_blockers(ctx) if "infrastructure" in b]
    assert infra and "prometheus_targets" in infra[0]
    _add(ctx, "prometheus_query_range",
         "SELECT * FROM prometheus_query_range WHERE query = 'max by (pod) (container_memory_working_set_bytes)'")
    assert readiness_blockers(ctx) == []
    ctx.close()


def test_errors_empties_and_absence_in_the_evidence_floor(prom_only: Catalog) -> None:
    ctx = _ctx(prom_only, "Error rate 5xx above 5% on api")
    _add(ctx, "prometheus_query_range", error="parse error", meaningful=False, confidence=0.0)
    _add(ctx, "prometheus_query_range", row_count=0, empty_verdict="inconclusive", meaningful=False, confidence=0.4)
    _add(ctx, "prometheus_label_values", meaningful=False)            # discovery-only lookup
    assert not evidence_floor_ok(ctx)
    _add(ctx, "prometheus_query_range", row_count=0, empty_verdict="absent")
    _add(ctx, "prometheus_query_range", confidence=0.2)               # weak signal (< 0.3) does not count
    assert not evidence_floor_ok(ctx)
    _add(ctx, "prometheus_query_range")
    assert evidence_floor_ok(ctx)
    ctx.close()


def test_tie_is_a_deferred_decision(prom_only: Catalog) -> None:
    ctx = _ctx(prom_only, "Error rate 5xx above 5% on api")
    _add(ctx, "prometheus_query_range")
    _add(ctx, "prometheus_query_range", agent="seed")
    _hyps(ctx, ("H1", "trigger", "code_deploy", 0.5), ("H2", "mechanism", "dependency", 0.45))
    tie = [b for b in readiness_blockers(ctx) if "deferred decision" in b]
    assert tie and "SELECT discriminating FROM H1" in tie[0] and "SELECT discriminating FROM H2" in tie[0]
    ctx.tracker.update([{"id": "H2", "statement": "H2 claim", "causal_level": "mechanism", "confidence": 0.1,
                         "status": "refuted"}])
    ctx.tracker.update([{"id": "H1", "statement": "H1 claim", "causal_level": "trigger", "confidence": 0.1,
                         "status": "refuted"}])
    assert any("are refuted" in b for b in readiness_blockers(ctx))
    ctx.close()


def test_depth_pushback_keys_on_causal_level_and_budget(lgtm: Catalog) -> None:
    ctx = _ctx(lgtm, "High latency on checkout: p99 above 2s")
    assert depth_pushback(ctx) is None                                # no leader yet
    _hyps(ctx, ("H1", "mechanism", "dependency", 0.5), ("H2", "symptom", "traffic", 0.2))
    text = depth_pushback(ctx)
    assert text is not None and "H1 is at mechanism level" in text and "why now?" in text
    assert "child spans" in text and "tempo_search" in text
    assert "baseline" in text or "preceding hour" in text
    ctx.tracker.update([{"id": "H1", "statement": "Release 2.4.0 made the profile index unique",
                         "causal_level": "trigger", "category": "code_deploy", "confidence": 0.5}])
    assert depth_pushback(ctx) is None                                # trigger reached
    ctx.close()
    low = _ctx(lgtm, "High latency on checkout", budget=100)
    _hyps(low, ("H1", "mechanism", "dependency", 0.5))
    assert depth_pushback(low) is None                                # report reserve would be eaten
    low.close()


def test_gate_pushbacks_are_capped(lgtm: Catalog) -> None:
    ctx = _ctx(lgtm, "Error rate 5xx above 5% on checkout")
    _hyps(ctx, ("H1", "symptom", "code_deploy", 0.5))
    readiness = [gate_pushback(ctx) for _ in range(3)]
    assert all(r is not None and r.startswith("READINESS GATE") for r in readiness)
    logs = next(line for line in readiness[0].splitlines() if "missing logs" in line)
    assert "query these tables: omni_log_patterns, loki_logs" in logs
    depth = [gate_pushback(ctx) for _ in range(2)]
    assert all(d is not None and d.startswith("DEPTH GATE") for d in depth)
    assert gate_pushback(ctx) is None
    assert ctx.stats.pushbacks == {"readiness": 3, "depth": 2}
    assert any("readiness gate: finishing with" in w for w in ctx.warnings)
    ctx.close()
