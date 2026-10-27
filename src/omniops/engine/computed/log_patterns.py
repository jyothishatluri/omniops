"""omni_log_patterns: Drain3 template mining over the ``logs`` role. CPU work runs in a thread."""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import TYPE_CHECKING, Any

from omniops.engine.computed import as_ts, col, role_fetch

if TYPE_CHECKING:
    from omniops.engine.engine import FetchOutcome, FetchPlan, SQLEngine

_ERROR_LEVELS = {"error", "err", "fatal", "critical", "crit", "emerg", "alert", "warn", "warning", "severe"}


def mine(lines: list[tuple[str, Any, str, str]]) -> list[dict[str, Any]]:
    """lines: (message, ts, level, source_table). Returns one dict per template."""
    from drain3 import TemplateMiner
    from drain3.template_miner_config import TemplateMinerConfig

    cfg = TemplateMinerConfig()
    cfg.profiling_enabled = False
    cfg.drain_sim_th = 0.45
    cfg.drain_depth = 4
    cfg.parametrize_numeric_tokens = True
    miner = TemplateMiner(config=cfg)
    stats: dict[int, dict[str, Any]] = {}
    for msg, ts, level, src in lines:
        r = miner.add_log_message(msg[:2000])
        cid = r["cluster_id"]
        s = stats.setdefault(cid, {"count": 0, "first": ts, "last": ts, "levels": Counter(), "sample": msg[:500],
                                   "source": src})
        s["count"] += 1
        if ts is not None:
            s["first"] = ts if s["first"] is None or ts < s["first"] else s["first"]
            s["last"] = ts if s["last"] is None or ts > s["last"] else s["last"]
        if level:
            s["levels"][level.lower()] += 1
    total = max(1, len(lines))
    clusters = {c.cluster_id: c for c in miner.drain.clusters}
    out = []
    for cid, s in stats.items():
        tmpl = clusters[cid].get_template() if cid in clusters else s["sample"]
        out.append({
            "template": tmpl[:1000], "count": s["count"], "pct": round(100.0 * s["count"] / total, 2),
            "first_seen": s["first"], "last_seen": s["last"],
            "level": s["levels"].most_common(1)[0][0] if s["levels"] else None,
            "sample": s["sample"], "fetched_lines": len(lines),
            "source": s["source"].split("\t")[0], "table_name": s["source"].split("\t")[-1],
        })
    out.sort(key=lambda r: -r["count"])
    return out


async def fetch(engine: SQLEngine, plan: FetchPlan, ctx: dict[str, Any]) -> FetchOutcome:
    from omniops.engine.engine import FetchOutcome

    filters = ctx.get("filters") or {}  # this request's fan-out combo, not the plan's IN-list
    service = filters.get("service")
    error_only = bool(filters.get("error_only"))
    query = filters.get("query")
    vars_ = {"service": service, "error_only": error_only, "env": None, "host": None, "query": query}
    overrides = {"query": query} if query else None
    results = await role_fetch(engine, plan, ctx, "logs", vars_, overrides)
    lines: list[tuple[str, Any, str, str]] = []
    notes: list[str] = []
    errors: list[str] = []
    for src, b, rows, bnotes, err in results:
        notes.extend(bnotes)
        errors += [err] if err else []
        for r in rows:
            msg = col(r, b, "message", "line", "body", "log")
            if not isinstance(msg, str) or not msg.strip():
                continue
            level = col(r, b, "level", "status", "severity") or ""
            if error_only and str(level).lower() not in _ERROR_LEVELS and level:
                continue
            lines.append((msg, as_ts(col(r, b, "ts", "timestamp")), str(level), f"{src}\t{b.table}"))
    if not results:
        want = filters.get("source")
        notes.append(f"no configured logs source is named {want!r}" if want
                     else "no configured source binds the logs role")
    if not lines:
        return FetchOutcome([], notes, [], errors)
    return FetchOutcome(await asyncio.to_thread(mine, lines), notes, [], errors)
