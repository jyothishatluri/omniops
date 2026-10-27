"""Computed tables: things no single API offers, built from roles so they work on any platform.

- ``omni_log_patterns``: Drain3 template mining over whichever source binds the ``logs`` role.
- ``omni_change_events``: deploys, config changes, flags and code changes from every source that
  binds ``change_events`` / ``code_changes``, normalised and de-duplicated.
- ``omni_active_alerts``: firing alerts across every alerting source.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from omniops.engine import templating, timeutil
from omniops.engine.manifest import TableSpec

if TYPE_CHECKING:
    from omniops.engine.catalog import Catalog
    from omniops.engine.engine import FetchPlan, SQLEngine


_SPECS: dict[str, dict[str, Any]] = {
    "omni_log_patterns": {
        "role": ["logs"],
        "spec": {
            "name": "omni_log_patterns",
            "title": "Log patterns (computed)",
            "description": (
                "Mines log templates (Drain3) from whichever source provides logs. One row per template with "
                "count, share, first/last seen, dominant level and a raw sample line. Filter on service and/or "
                "query (the source's native log query); optional source picks one logs source; error_only=true "
                "keeps warn/error. Counts are from fetched lines: when fetched_lines hits the cap they are "
                "estimates - say so."
            ),
            "kind": "logs",
            "python": "omniops.engine.computed.log_patterns:fetch",
            "filters": [
                {"column": "service", "description": "Service name"},
                {"column": "query", "description": "Native log query for the source (overrides the role default)"},
                {"column": "source", "description": "Restrict to one configured source"},
                {"column": "error_only", "type": "BOOLEAN", "description": "Only warning/error lines"},
            ],
            "require_one_of": [["service", "query"]],
            "time": {"column": "ts", "default_lookback": "1h", "max_window": "7d"},
            "columns": [
                {"name": "template", "description": "Mined template; <*> marks variable parts"},
                {"name": "count", "type": "BIGINT"},
                {"name": "pct", "type": "DOUBLE", "description": "Share of fetched lines (%)"},
                {"name": "first_seen", "type": "TIMESTAMP"},
                {"name": "last_seen", "type": "TIMESTAMP"},
                {"name": "level", "description": "Most common level for this template"},
                {"name": "sample", "description": "A raw line matching the template"},
                {"name": "fetched_lines", "type": "BIGINT"},
                {"name": "source", "description": "Configured source the lines came from (e.g. loki, datadog)"},
                {"name": "table_name", "description": "Table the lines came from"},
                {"name": "service", "filter_only": True},
                {"name": "query", "filter_only": True},
                {"name": "error_only", "type": "BOOLEAN", "filter_only": True},
                {"name": "ts", "type": "TIMESTAMP", "filter_only": True},
            ],
            "examples": [{
                "sql": "SELECT template, count, pct, first_seen, level, sample FROM omni_log_patterns "
                       "WHERE service = 'checkout' AND error_only = true ORDER BY count DESC LIMIT 15",
                "description": "Top error templates for a service in the analysis window.",
                "verified": True,
            }],
            "positive_control": False,
        },
    },
    "omni_change_events": {
        "role": ["change_events", "code_changes"],
        "spec": {
            "name": "omni_change_events",
            "title": "Change events (computed, all sources)",
            "description": (
                "Deploys, rollouts, config/flag changes and code changes from every configured source, "
                "normalised and de-duplicated. Default window: 48h before the anchor. Filter on service to scope; "
                "kind tells you deploy vs config vs code."
            ),
            "kind": "changes",
            "python": "omniops.engine.computed.change_events:fetch",
            "filters": [
                {"column": "service", "description": "Service/application/repo name"},
                {"column": "source", "description": "Restrict to one configured source"},
            ],
            "time": {"column": "ts", "default_lookback": "48h", "max_window": "30d"},
            "columns": [
                {"name": "ts", "type": "TIMESTAMP"},
                {"name": "source", "description": "Configured source (e.g. github, argocd, datadog)"},
                {"name": "table_name", "description": "Table the change came from"},
                {"name": "kind", "description": "deploy | config | code | flag | other"},
                {"name": "service"},
                {"name": "title"},
                {"name": "version"},
                {"name": "author"},
                {"name": "url"},
                {"name": "details"},
            ],
            "examples": [{
                "sql": "SELECT ts, source, kind, service, title, version, author FROM omni_change_events "
                       "WHERE ts BETWEEN window_start() - INTERVAL 48 HOUR AND window_end() ORDER BY ts DESC",
                "description": "Everything that changed in the 48h before the alert, newest first.",
                "verified": True,
            }],
            "positive_control": False,
        },
    },
    "omni_active_alerts": {
        "role": ["active_alerts"],
        "spec": {
            "name": "omni_active_alerts",
            "title": "Active alerts (computed, all sources)",
            "description": "Currently firing/pending alerts from every configured alerting source, normalised.",
            "kind": "alerts",
            "python": "omniops.engine.computed.active_alerts:fetch",
            "filters": [{"column": "source", "description": "Restrict to one configured source"}],
            "columns": [
                {"name": "ts", "type": "TIMESTAMP", "description": "When it became active"},
                {"name": "source"},
                {"name": "name"},
                {"name": "state"},
                {"name": "severity"},
                {"name": "service"},
                {"name": "labels", "type": "JSON"},
                {"name": "url"},
            ],
            "examples": [{
                "sql": "SELECT source, name, state, severity, service, ts FROM omni_active_alerts ORDER BY ts DESC",
                "description": "What else is firing right now (correlated alerts).",
                "verified": True,
            }],
            "positive_control": False,
        },
    },
}


def register_computed(cat: Catalog) -> None:
    roles = cat.roles_summary()
    for spec in _SPECS.values():
        if any(r in roles for r in spec["role"]):
            cat.add_computed(TableSpec.model_validate(spec["spec"]))


def render_binding_filters(binding_filters: dict[str, str], ctx: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, tmpl in binding_filters.items():
        v = templating.render_value(tmpl, ctx)
        if v not in (None, ""):
            out[k] = v.strip() if isinstance(v, str) else v
    return out


async def role_fetch(engine: SQLEngine, plan: FetchPlan, ctx: dict[str, Any], role: str, ctx_vars: dict[str, Any],
                     overrides: dict[str, Any] | None = None
                     ) -> list[tuple[str, Any, list[dict[str, Any]], list[str], str | None]]:
    """Fetch every binding of ``role`` over the request's window.

    ``ctx`` is the per-request context the engine passes a computed table: ``ctx['filters']`` holds
    one fan-out combo (never the plan's whole IN-list) and ``ctx['time']`` the request window.
    Returns (source name, binding, rows, notes, error) per binding that could run; ``error`` is set
    when that source failed or answered only partially, so the union is not taken as complete.
    """
    from omniops.engine.engine import EngineError, FetchPlan

    out: list[tuple[str, Any, list[dict[str, Any]], list[str], str | None]] = []
    want_source = (ctx.get("filters") or {}).get("source")
    window = ctx.get("time") or {}
    for rt, b in engine.catalog.role_bindings(role):
        if want_source and want_source not in (rt.name, rt.manifest.source):
            continue
        entry = engine.catalog.get(b.table)
        if entry is None:
            continue
        filters = render_binding_filters(b.filters, {**ctx_vars, "env": rt.env})
        if overrides:
            filters.update({k: v for k, v in overrides.items() if v is not None})
        t = entry.table
        missing = [f.column for f in t.filters if f.required and f.key not in filters and f.default is None]
        if missing:
            out.append((rt.name, b, [], [f"{b.table}: skipped (role binding lacks required {missing})"], None))
            continue
        start, end = window.get("start", plan.start), window.get("end", plan.end)
        notes: list[str] = []
        if t.time is not None and start and end:
            try:
                start, end, notes = engine._resolve_window(entry, start, end)
            except EngineError as exc:
                out.append((rt.name, b, [], [f"{b.table}: {exc}"], None))
                continue
        sub = FetchPlan(entry, None, filters, start if t.time else None, end if t.time else None, None, notes)
        try:
            res = await engine.fetch(sub)
        except Exception as exc:  # noqa: BLE001 - one broken source must not sink the union
            err = f"{b.table}: {type(exc).__name__}: {str(exc)[:300]}"
            out.append((rt.name, b, [], [err], err))
            continue
        err = f"{b.table}: {res.errors[0][:300]}" if res.errors else None
        out.append((rt.name, b, res.rows, notes + res.notes, err))
    return out


def col(row: dict[str, Any], binding: Any, canonical: str, *fallbacks: str) -> Any:
    name = binding.columns.get(canonical, canonical)
    if name in row and row[name] is not None:
        return row[name]
    for f in fallbacks:
        if row.get(f) is not None:
            return row[f]
    return None


def as_ts(v: Any) -> Any:
    if v is None:
        return None
    try:
        return timeutil.to_datetime(v)
    except (timeutil.TimeParseError, ValueError, OverflowError):
        return None
