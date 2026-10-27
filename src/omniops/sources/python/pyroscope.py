"""Pyroscope flame graphs -> per-function rows (python escape hatch).

Tables: ``pyroscope_top_functions`` (``SelectMergeStacktraces``) and ``pyroscope_diff_functions``
(``Diff``). Both are Connect RPCs: ``POST /querier.v1.QuerierService/<Method>`` with a JSON body
(https://grafana.com/docs/pyroscope/latest/reference-server-api/), answered in protojson
(camelCase fields, int64 as strings, zero values omitted).

Why python: the merged profile comes back as a flame graph whose levels are flat int64 arrays -
4-tuples ``[x_offset(delta), total, self, name_index]`` for ``SelectMergeStacktraces`` and
7-tuples ``[xl(delta), total_left, self_left, xr(delta), total_right, self_right, name_index]``
for ``Diff`` (grafana/pyroscope pkg/model/flamegraph.go and flamegraph_diff.go). Turning that
into one row per function needs strided chunking, delta decoding, an index lookup into
``names`` and a parent walk to avoid double counting recursive frames - none of which the
declarative response language can express.

Decoding mirrors ``deltaDecoding`` in pkg/model/flamegraph.go: for each level and each offset
column, ``x_abs = delta + prev`` and ``prev = x_abs + total``; the parent of a node at level ``i``
is the node at level ``i-1`` whose ``[x, x + total)`` interval contains the node's ``x``.
"""

from __future__ import annotations

import bisect
from datetime import timedelta
from typing import Any

from omniops.engine import http, templating, timeutil

QUERIER = "/querier.v1.QuerierService/"
DEFAULT_PROFILE_TYPE = "process_cpu:cpu:nanoseconds:cpu:nanoseconds"
DEFAULT_MAX_NODES = 8192  # Pyroscope limits.max_flamegraph_nodes_default


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _escape(v: Any) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"')


def label_selector(filters: dict[str, Any], key: str = "label_selector") -> str:
    """``label_selector`` wins; else ``{service_name="<service>"}``; else ``{}`` (everything)."""
    sel = (filters.get(key) or "").strip()
    if sel:
        return sel
    svc = filters.get("service")
    if svc and not isinstance(svc, list):
        return '{service_name="' + _escape(svc) + '"}'
    return "{}"


def _unit(profile_type: str) -> str | None:
    parts = profile_type.split(":")
    return parts[2] if len(parts) >= 5 else None


# ---------------------------------------------------------------------------------------------
# Flame graph decoding
# ---------------------------------------------------------------------------------------------


def _decode_levels(raw_levels: list[Any], width: int, offsets: tuple[int, ...]) -> list[list[list[int]]]:
    levels: list[list[list[int]]] = []
    for lvl in raw_levels:
        vals = [_int(v) for v in ((lvl or {}).get("values") or [])] if isinstance(lvl, dict) else [_int(v) for v in lvl]
        prev = {o: 0 for o in offsets}
        nodes = []
        for i in range(0, len(vals) - width + 1, width):
            node = vals[i:i + width]
            for o in offsets:
                node[o] += prev[o]
                prev[o] = node[o] + node[o + 1]
            nodes.append(node)
        levels.append(nodes)
    return levels


def _ancestors(levels: list[list[list[int]]], name_pos: int, coords: tuple[int, ...]) -> list[list[frozenset[int]]]:
    """Per node, the set of name indexes on its path from the root (excluding itself).

    ``coords`` are offset positions to locate the parent with, tried in order; a coordinate is
    used only when the node has a non-zero width there (diff nodes may exist on one side only).
    """
    anc: list[list[frozenset[int]]] = []
    for li, nodes in enumerate(levels):
        if li == 0:
            anc.append([frozenset() for _ in nodes])
            continue
        parents = levels[li - 1]
        index: dict[int, tuple[list[int], list[int]]] = {}
        for c in coords:
            pos = [j for j, p in enumerate(parents) if p[c + 1] > 0]
            index[c] = ([parents[j][c] for j in pos], pos)
        cur: list[frozenset[int]] = []
        for node in nodes:
            found: frozenset[int] = frozenset()
            for c in coords:
                if node[c + 1] <= 0:
                    continue
                starts, pos = index[c]
                k = bisect.bisect_right(starts, node[c]) - 1
                if 0 <= k < len(pos):
                    j = pos[k]
                    found = anc[li - 1][j] | {parents[j][name_pos]}
                break
            cur.append(found)
        anc.append(cur)
    return anc


def flamegraph_functions(flamegraph: dict[str, Any] | None) -> tuple[list[dict[str, Any]], int]:
    """Aggregate a querier.v1.FlameGraph into per-function self/total values.

    Returns (rows sorted by self desc, grand total). ``total`` of a function counts each stack
    once even when the function recurses (only the outermost occurrence on a path is summed).
    """
    if not flamegraph:
        return [], 0
    names: list[str] = list(flamegraph.get("names") or [])
    levels = _decode_levels(flamegraph.get("levels") or [], 4, (0,))
    grand_total = _int(flamegraph.get("total")) or (levels[0][0][1] if levels and levels[0] else 0)
    anc = _ancestors(levels, 3, (0,))
    agg: dict[int, dict[str, int]] = {}
    for li, nodes in enumerate(levels):
        if li == 0:
            continue  # the synthetic "total" root
        for ni, (_x, total, self_, name_idx) in enumerate(nodes):
            a = agg.setdefault(name_idx, {"self": 0, "total": 0, "occurrences": 0, "min_depth": li})
            a["self"] += self_
            a["occurrences"] += 1
            a["min_depth"] = min(a["min_depth"], li)
            if name_idx not in anc[li][ni]:
                a["total"] += total
    rows = []
    for name_idx, a in agg.items():
        name = names[name_idx] if 0 <= name_idx < len(names) else f"<{name_idx}>"
        rows.append({
            "function": name,
            "self": a["self"],
            "total": a["total"],
            "self_pct": round(100.0 * a["self"] / grand_total, 3) if grand_total else None,
            "total_pct": round(100.0 * a["total"] / grand_total, 3) if grand_total else None,
            "occurrences": a["occurrences"],
            "depth": a["min_depth"],
        })
    rows.sort(key=lambda r: (-r["self"], -r["total"], r["function"]))
    return rows, grand_total


def flamegraph_diff_functions(fg: dict[str, Any] | None) -> tuple[list[dict[str, Any]], int, int]:
    """Aggregate a querier.v1.FlameGraphDiff into per-function baseline/current values.

    Left = baseline, right = current. Percentages are of each side's own ticks (leftTicks /
    rightTicks), so windows of different length or load compare fairly. Rows are sorted by the
    absolute change of the self share.
    """
    if not fg:
        return [], 0, 0
    names: list[str] = list(fg.get("names") or [])
    levels = _decode_levels(fg.get("levels") or [], 7, (0, 3))
    left_ticks = _int(fg.get("leftTicks")) or (levels[0][0][1] if levels and levels[0] else 0)
    right_ticks = _int(fg.get("rightTicks")) or (levels[0][0][4] if levels and levels[0] else 0)
    anc = _ancestors(levels, 6, (0, 3))
    agg: dict[int, dict[str, int]] = {}
    for li, nodes in enumerate(levels):
        if li == 0:
            continue
        for ni, (_xl, tl, sl, _xr, tr, sr, name_idx) in enumerate(nodes):
            a = agg.setdefault(name_idx, {"bs": 0, "bt": 0, "cs": 0, "ct": 0, "depth": li})
            a["bs"] += sl
            a["cs"] += sr
            a["depth"] = min(a["depth"], li)
            if name_idx not in anc[li][ni]:
                a["bt"] += tl
                a["ct"] += tr

    def pct(v: int, ticks: int) -> float | None:
        return round(100.0 * v / ticks, 3) if ticks else None

    rows = []
    for name_idx, a in agg.items():
        bsp, csp = pct(a["bs"], left_ticks), pct(a["cs"], right_ticks)
        btp, ctp = pct(a["bt"], left_ticks), pct(a["ct"], right_ticks)
        rows.append({
            "function": names[name_idx] if 0 <= name_idx < len(names) else f"<{name_idx}>",
            "baseline_self": a["bs"],
            "current_self": a["cs"],
            "baseline_total": a["bt"],
            "current_total": a["ct"],
            "baseline_self_pct": bsp,
            "current_self_pct": csp,
            "self_pct_change": round(csp - bsp, 3) if csp is not None and bsp is not None else None,
            "baseline_total_pct": btp,
            "current_total_pct": ctp,
            "total_pct_change": round(ctp - btp, 3) if ctp is not None and btp is not None else None,
            "depth": a["depth"],
        })
    rows.sort(key=lambda r: (-abs(r["self_pct_change"] or 0), -r["current_self"], r["function"]))
    return rows, left_ticks, right_ticks


# ---------------------------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------------------------


async def _post(rt: http.SourceRuntime, table: Any, method: str, body: dict[str, Any],
                logs: list[http.RequestLog]) -> dict[str, Any]:
    headers = templating.render_value(dict(rt.manifest.headers), rt.template_ctx()) or {}
    headers = {k: str(v) for k, v in headers.items() if v not in (None, "")}
    rr = http.RenderedRequest(
        method="POST",
        url=rt.base_url.rstrip("/") + QUERIER + method,
        params={},
        headers=headers,
        body=body,
        body_format="json",
        response_format="json",
        timeout_s=60.0,
    )
    resp = await http.send(rt, rr, table=table.name, logs=logs)
    payload = resp.body if isinstance(resp.body, dict) else {}
    if payload.get("code") and payload.get("message"):
        raise http.FetchError(f"{rt.name}/{table.name}: Pyroscope error {payload['code']}: {payload['message']}")
    return payload


def _selectors(filters: dict[str, Any], key: str) -> list[str] | None:
    v = filters.get(key)
    if not v:
        return None
    return [str(s).strip().lower() for s in (v if isinstance(v, list) else [v])]


async def top_functions(rt: http.SourceRuntime, table: Any, ctx: dict[str, Any],
                        logs: list[http.RequestLog]) -> list[dict[str, Any]]:
    """Fetch the merged flame graph for the window and return one row per function."""
    filters = ctx.get("filters") or {}
    tctx = ctx.get("time") or {}
    profile_type = filters.get("profile_type") or DEFAULT_PROFILE_TYPE
    selector = label_selector(filters)
    body: dict[str, Any] = {
        "profileTypeID": profile_type,
        "labelSelector": selector,
        "start": timeutil.fmt_epoch_ms(tctx["start"]),
        "end": timeutil.fmt_epoch_ms(tctx["end"]),
        "maxNodes": int(filters.get("max_nodes") or DEFAULT_MAX_NODES),
        "format": "PROFILE_FORMAT_FLAMEGRAPH",
    }
    span_ids = _selectors(filters, "span_id")
    if span_ids:
        body["spanSelector"] = span_ids
    trace_ids = _selectors(filters, "trace_id")
    if trace_ids:
        body["traceIdSelector"] = trace_ids
    payload = await _post(rt, table, "SelectMergeStacktraces", body, logs)
    rows, grand_total = flamegraph_functions(payload.get("flamegraph"))
    for r in rows:
        r.update({
            "grand_total": grand_total,
            "unit": _unit(profile_type),
            "profile_type": profile_type,
            "label_selector": selector,
            "service": filters.get("service") if not isinstance(filters.get("service"), list) else None,
            "span_id": filters.get("span_id") if isinstance(filters.get("span_id"), str) else None,
            "trace_id": filters.get("trace_id") if isinstance(filters.get("trace_id"), str) else None,
            "max_nodes": body["maxNodes"],
            "ts": None,
        })
    if logs:
        logs[-1].rows = len(rows)
    return rows


async def diff_functions(rt: http.SourceRuntime, table: Any, ctx: dict[str, Any],
                         logs: list[http.RequestLog]) -> list[dict[str, Any]]:
    """Compare the window's merged profile (right/current) with a baseline (left) via ``Diff``.

    Baseline window: the same length, ending ``baseline_offset`` before the window end
    (default = the window length, i.e. the period immediately before). Baseline selector:
    ``baseline_selector`` or the same selector (e.g. compare two versions with
    ``label_selector = '{service_name="x", version="2"}'`` and
    ``baseline_selector = '{service_name="x", version="1"}'`` and baseline_offset = '0s').
    """
    filters = ctx.get("filters") or {}
    tctx = ctx.get("time") or {}
    start, end = tctx["start"], tctx["end"]
    length = end - start
    offset_raw = filters.get("baseline_offset")
    offset = timeutil.parse_duration(offset_raw) if offset_raw not in (None, "") else length
    if not isinstance(offset, timedelta):
        offset = timedelta(seconds=float(offset))
    b_start, b_end = start - offset, end - offset
    profile_type = filters.get("profile_type") or DEFAULT_PROFILE_TYPE
    selector = label_selector(filters)
    b_selector = label_selector(filters, "baseline_selector") if filters.get("baseline_selector") else selector
    max_nodes = int(filters.get("max_nodes") or DEFAULT_MAX_NODES)

    def side(sel: str, s: Any, e: Any) -> dict[str, Any]:
        return {"profileTypeID": profile_type, "labelSelector": sel, "start": timeutil.fmt_epoch_ms(s),
                "end": timeutil.fmt_epoch_ms(e), "maxNodes": max_nodes, "format": "PROFILE_FORMAT_FLAMEGRAPH"}

    body = {"left": side(b_selector, b_start, b_end), "right": side(selector, start, end)}
    payload = await _post(rt, table, "Diff", body, logs)
    rows, left_ticks, right_ticks = flamegraph_diff_functions(payload.get("flamegraph"))
    for r in rows:
        r.update({
            "baseline_ticks": left_ticks,
            "current_ticks": right_ticks,
            "unit": _unit(profile_type),
            "profile_type": profile_type,
            "label_selector": selector,
            "baseline_selector": b_selector,
            "baseline_offset": offset_raw if isinstance(offset_raw, str) else None,
            "baseline_start": b_start,
            "baseline_end": b_end,
            "service": filters.get("service") if not isinstance(filters.get("service"), list) else None,
            "max_nodes": max_nodes,
            "ts": None,
        })
    if logs:
        logs[-1].rows = len(rows)
    return rows
