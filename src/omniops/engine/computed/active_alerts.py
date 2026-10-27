"""omni_active_alerts: firing alerts across every alerting source."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from omniops.engine.computed import as_ts, col, role_fetch

if TYPE_CHECKING:
    from omniops.engine.engine import FetchOutcome, FetchPlan, SQLEngine


async def fetch(engine: SQLEngine, plan: FetchPlan, ctx: dict[str, Any]) -> FetchOutcome:
    from omniops.engine.engine import FetchOutcome

    out = []
    notes: list[str] = []
    errors: list[str] = []
    vars_ = {"service": None, "env": None, "host": None, "error_only": False, "query": None}
    for src, b, rows, bnotes, err in await role_fetch(engine, plan, ctx, "active_alerts", vars_):
        notes.extend(bnotes)
        errors += [err] if err else []
        for r in rows:
            labels = col(r, b, "labels", "tags")
            if isinstance(labels, str):
                try:
                    labels = json.loads(labels)
                except json.JSONDecodeError:
                    labels = {"raw": labels}
            out.append({
                "ts": as_ts(col(r, b, "ts", "active_at", "triggered_at", "created_at")),
                "source": src,
                "name": col(r, b, "name", "alertname", "title"),
                "state": col(r, b, "state", "status"),
                "severity": col(r, b, "severity", "priority"),
                "service": col(r, b, "service"),
                "labels": labels,
                "url": col(r, b, "url"),
            })
    return FetchOutcome(out, notes, [], errors)
