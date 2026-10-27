"""omni_change_events: deploys / config / flag / code changes from every source, de-duplicated.

Change feeds return duplicate variant rows per change and some aggregation keys merge distinct
deploys (OpsIntelligence 4.2), so de-duplication keys on title, service, version and the minute.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from omniops.engine.computed import as_ts, col, role_fetch

if TYPE_CHECKING:
    from omniops.engine.engine import FetchOutcome, FetchPlan, SQLEngine


def _kind(role: str, title: str, table_kind: str) -> str:
    t = (title or "").lower()
    if role == "code_changes":
        return "code"
    if any(w in t for w in ("flag", "feature toggle", "featureflag")):
        return "flag"
    if any(w in t for w in ("config", "configmap", "setting", "parameter")):
        return "config"
    if any(w in t for w in ("deploy", "release", "rollout", "sync", "upgrade", "image")) or table_kind == "changes":
        return "deploy"
    return "other"


async def fetch(engine: SQLEngine, plan: FetchPlan, ctx: dict[str, Any]) -> FetchOutcome:
    from omniops.engine.engine import FetchOutcome

    service = (ctx.get("filters") or {}).get("service")
    vars_ = {"service": service, "env": None, "host": None, "error_only": False, "query": None}
    rows_out: list[dict[str, Any]] = []
    notes: list[str] = []
    errors: list[str] = []
    seen: set[tuple[Any, ...]] = set()
    for role in ("change_events", "code_changes"):
        for src, b, rows, bnotes, err in await role_fetch(engine, plan, ctx, role, vars_):
            notes.extend(bnotes)
            errors += [err] if err else []
            entry = engine.catalog.get(b.table)
            tkind = entry.table.kind if entry else ""
            for r in rows:
                ts = as_ts(col(r, b, "ts", "timestamp", "created_at", "date"))
                title = str(col(r, b, "title", "message", "name", "description") or "")[:300]
                svc = col(r, b, "service", "repo", "application")
                version = col(r, b, "version", "sha", "revision")
                key = (title[:120], str(svc), str(version), ts.replace(second=0, microsecond=0) if ts else None)
                if key in seen:
                    continue
                seen.add(key)
                extra = {k: v for k, v in r.items() if k not in b.columns.values() and v is not None}
                rows_out.append({
                    "ts": ts, "source": src, "table_name": b.table, "kind": _kind(role, title, tkind),
                    "service": svc, "title": title, "version": version,
                    "author": col(r, b, "author", "user", "creator"), "url": col(r, b, "url", "html_url"),
                    "details": json.dumps(extra, default=str)[:1500],
                })
    rows_out.sort(key=lambda r: (r["ts"] is None, r["ts"]), reverse=True)
    return FetchOutcome(rows_out, notes, [], errors)
