"""Query results as the model sees them: digests first, whole rows, explicit truncation markers.

Lessons encoded (OpsIntelligence 3.9.3):
- Deterministic digests (onset, peak, recovery; top messages) go first so key facts survive any
  later clipping. Plain ASCII only.
- Row-preserving compaction: keep whole rows, drop the widest columns first, cap cells, and say
  exactly what was dropped. Time series are thinned evenly keeping first/last/peak/low points,
  never head-truncated (which loses the newest rows).
- Every truncation is explicit and sized.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from omniops.engine import timeutil

NUMERIC_TYPES = ("DOUBLE", "FLOAT", "BIGINT", "INTEGER", "HUGEINT", "SMALLINT", "TINYINT", "DECIMAL", "UBIGINT",
                 "UINTEGER", "REAL")
TIME_TYPES = ("TIMESTAMP", "TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ", "DATE")
CELL_CAP = 4000
RESULT_CHAR_BUDGET = 40_000
ROW_CAP = 500


def _is_num(t: str) -> bool:
    return t.upper().startswith(NUMERIC_TYPES)


def _is_time(t: str) -> bool:
    return t.upper().startswith(TIME_TYPES)


def fmt_cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return timeutil.fmt_rfc3339(v) if v.microsecond == 0 else timeutil.fmt_rfc3339_ms(v)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return str(v)
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        return f"{v:.6g}"
    s = str(v)
    return s.replace("|", "\\|").replace("\n", " ")


@dataclass
class QueryResult:
    sql: str
    columns: list[str]
    types: list[str]
    rows: list[tuple[Any, ...]]
    total_rows: int
    tables: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    digests: list[str] = field(default_factory=list)
    links: list[dict[str, str]] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    windows: dict[str, tuple[str, str]] = field(default_factory=dict)
    elapsed_ms: int = 0
    empty_verdict: str | None = None  # "absent" | "inconclusive" | None
    staged_write: dict[str, Any] | None = None
    fetched_rows: dict[str, int] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return self.total_rows == 0

    @property
    def has_data(self) -> bool:
        return self.total_rows > 0

    def as_dicts(self, limit: int | None = None) -> list[dict[str, Any]]:
        rows = self.rows if limit is None else self.rows[:limit]
        return [dict(zip(self.columns, r)) for r in rows]

    # ---------------------------------------------------------------------------------------

    def render(self, budget: int = RESULT_CHAR_BUDGET, max_rows: int = ROW_CAP) -> str:
        head = [f"[result] {self.total_rows} row(s) x {len(self.columns)} col(s) from {', '.join(self.tables) or 'sql'}"
                f" in {self.elapsed_ms} ms"]
        for t, (s, e) in self.windows.items():
            head.append(f"[window] {t}: {s} -> {e}")
        if self.staged_write:
            head.append(f"[staged write] {self.staged_write.get('summary', '')} - awaiting human approval in Slack")
        for n in self.notes:
            head.append(f"[note] {n}")
        if self.empty_verdict == "inconclusive":
            head.append("[empty] 0 rows is INCONCLUSIVE, not proof of absence: the filter may be wrong. Verify the "
                        "label/tag/facet exists and test the same query on a window known to have data.")
        elif self.empty_verdict == "absent":
            head.append("[empty] genuine absence: the same query returns data in another window (positive control).")
        if self.digests:
            head.append("[digest]")
            head += [f"  {d}" for d in self.digests]
        body, tail = self._table(budget - sum(len(h) + 1 for h in head), max_rows)
        out = "\n".join(head + body + tail)
        if self.links:
            out += "\n[links] " + " ; ".join(f"{lk['label']}: {lk['url']}" for lk in self.links[:5])
        return out

    def _table(self, budget: int, max_rows: int) -> tuple[list[str], list[str]]:
        if not self.columns:
            return [], []
        cols = list(range(len(self.columns)))
        rows = self._select_rows(max_rows)
        cells = [[fmt_cell(r[i])[:CELL_CAP] for i in cols] for r in rows]
        capped_cells = sum(1 for r in rows for i in cols if len(fmt_cell(r[i])) > CELL_CAP)
        dropped_cols: list[str] = []

        def size(cs: list[int], n: int) -> int:
            header = sum(len(self.columns[i]) + 3 for i in cs) + 2
            return header * 2 + sum(sum(len(c[i]) + 3 for i in cs) + 2 for c in cells[:n])

        n = len(cells)
        # Drop the widest columns first (but keep at least two), then shrink rows.
        while size(cols, n) > budget and len(cols) > 2:
            widest = max(cols, key=lambda i: sum(len(c[i]) for c in cells[:n]))
            if sum(len(c[widest]) for c in cells[:n]) < 200 * max(1, n) and size(cols, n) < budget * 3:
                break
            cols.remove(widest)
            dropped_cols.append(self.columns[widest])
        while size(cols, n) > budget and n > 5:
            n = max(5, int(n * 0.8))
        lines = ["| " + " | ".join(self.columns[i] for i in cols) + " |",
                 "|" + "|".join("---" for _ in cols) + "|"]
        if n < len(cells):
            keep = self._thin_indices(rows, n)
            cells = [cells[i] for i in keep]
        for c in cells:
            lines.append("| " + " | ".join(c[i] for i in cols) + " |")
        tail = []
        shown = len(cells)
        if shown < self.total_rows:
            how = "evenly thinned keeping first/last/peak/low" if self._time_col() is not None else "first rows"
            tail.append(f"[truncated] showing {shown} of {self.total_rows} rows ({how}); aggregate in SQL "
                        "(GROUP BY / time_bucket) or add filters to see the rest")
        if dropped_cols:
            tail.append(f"[truncated] dropped widest column(s) to fit: {', '.join(dropped_cols)} (select them explicitly)")
        if capped_cells:
            tail.append(f"[truncated] {capped_cells} cell(s) cut at {CELL_CAP} chars")
        return lines, tail

    def _time_col(self) -> int | None:
        for i, t in enumerate(self.types):
            if _is_time(t):
                return i
        return None

    def _select_rows(self, max_rows: int) -> list[tuple[Any, ...]]:
        if len(self.rows) <= max_rows:
            return list(self.rows)
        return [self.rows[i] for i in self._thin_indices(self.rows, max_rows)]

    def _thin_indices(self, rows: list[Any], n: int) -> list[int]:
        total = len(rows)
        if total <= n:
            return list(range(total))
        if self._time_col() is None:
            return list(range(n))
        keep = {0, total - 1}
        num_cols = [i for i, t in enumerate(self.types) if _is_num(t)]
        for i in num_cols[:2]:
            vals = [(r[i], k) for k, r in enumerate(rows) if isinstance(r[i], (int, float)) and not
                    (isinstance(r[i], float) and math.isnan(r[i]))]
            if vals:
                keep.add(max(vals)[1])
                keep.add(min(vals)[1])
        step = total / max(1, n - len(keep))
        k = 0.0
        while len(keep) < n and k < total:
            keep.add(int(k))
            k += step
        return sorted(keep)[:n]


# ---------------------------------------------------------------------------------------------
# Digests
# ---------------------------------------------------------------------------------------------

_DIGITS = re.compile(r"\b[0-9a-f]{8,}\b|\d+")


def timeseries_digest(columns: list[str], types: list[str], rows: list[tuple[Any, ...]], max_series: int = 8
                      ) -> list[str]:
    ts_i = next((i for i, t in enumerate(types) if _is_time(t)), None)
    if ts_i is None or len(rows) < 4:
        return []
    num_is = [i for i, t in enumerate(types) if _is_num(t) and i != ts_i]
    if not num_is:
        return []
    v_i = next((i for i in num_is if columns[i].lower() in {"value", "count", "avg", "p95", "rate"}), num_is[0])
    group_is = [i for i, t in enumerate(types) if i not in (ts_i, *num_is) and not _is_time(t)]
    series: dict[tuple[Any, ...], list[tuple[datetime, float]]] = {}
    for r in rows:
        ts, v = r[ts_i], r[v_i]
        if not isinstance(ts, datetime) or not isinstance(v, (int, float)) or (isinstance(v, float) and math.isnan(v)):
            continue
        key = tuple(str(r[g])[:60] for g in group_is[:3])
        series.setdefault(key, []).append((ts, float(v)))
    if not series:
        return []
    ranked = sorted(series.items(), key=lambda kv: -max(v for _, v in kv[1]))
    out = []
    if len(ranked) > max_series:
        out.append(f"{len(ranked)} series; digest covers the top {max_series} by peak {columns[v_i]}")
    for key, pts in ranked[:max_series]:
        pts.sort()
        out.append(_series_line(key, columns[v_i], pts))
    return out


def _series_line(key: tuple[Any, ...], vname: str, pts: list[tuple[datetime, float]]) -> str:
    label = ",".join(k for k in key if k) or "all"
    vals = [v for _, v in pts]
    n = len(vals)
    first_third = vals[: max(1, n // 3)]
    base = statistics.median(first_third)
    mad = statistics.median([abs(v - base) for v in first_third]) if len(first_third) > 1 else 0.0
    thresh = max(3 * mad * 1.4826, 0.5 * abs(base), 1e-9)
    peak_i = max(range(n), key=lambda i: vals[i])
    low_i = min(range(n), key=lambda i: vals[i])
    f = timeutil.fmt_rfc3339
    s = f"{label}: {n} pts {f(pts[0][0])}..{f(pts[-1][0])}; baseline~{fmt_cell(base)}"
    onset = next((i for i in range(n) if abs(vals[i] - base) > thresh), None)
    if onset is None:
        return s + f"; no significant change (min {fmt_cell(vals[low_i])}, max {fmt_cell(vals[peak_i])}); last {fmt_cell(vals[-1])}"
    direction = "up" if vals[onset] > base else "down"
    ext_i = peak_i if direction == "up" else low_i
    ratio = (vals[ext_i] / base) if base not in (0, 0.0) else float("inf")
    s += (f"; onset {f(pts[onset][0])} ({direction}); {'peak' if direction == 'up' else 'low'} "
          f"{fmt_cell(vals[ext_i])} at {f(pts[ext_i][0])}"
          + (f" (x{ratio:.1f} vs baseline)" if math.isfinite(ratio) else ""))
    rec = next((i for i in range(ext_i + 1, n) if abs(vals[i] - base) <= thresh), None)
    if rec is not None:
        s += f"; recovered {f(pts[rec][0])}"
    else:
        s += f"; NOT recovered by end (last {fmt_cell(vals[-1])})"
    return s


def message_digest(columns: list[str], rows: list[tuple[Any, ...]], top: int = 5) -> list[str]:
    msg_i = next((i for i, c in enumerate(columns) if c.lower() in {"message", "msg", "line", "body", "log"}), None)
    if msg_i is None or len(rows) < 10:
        return []
    counts: dict[str, int] = {}
    example: dict[str, str] = {}
    for r in rows:
        m = r[msg_i]
        if not isinstance(m, str):
            continue
        key = _DIGITS.sub("#", m.strip())[:160]
        counts[key] = counts.get(key, 0) + 1
        example.setdefault(key, m[:200])
    if not counts:
        return []
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:top]
    out = [f"top message shapes in {len(rows)} fetched rows (digits masked):"]
    for k, c in ranked:
        out.append(f"  {c}x {k.encode('ascii', 'replace').decode()}")
    return out
