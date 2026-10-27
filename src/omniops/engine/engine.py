"""SQLEngine: SQL over observability APIs.

    model SQL -> parse (sqlglot) -> pin now() to the run anchor -> per-occurrence pushdown plan
    -> validate required filters -> resolve + clamp time windows -> fan out IN-lists
    -> fetch every source in parallel (direct REST) -> rows -> DuckDB temp tables
    -> rewrite SQL onto the temp tables -> execute -> digests, caps, links, positive controls

One place for data discipline: time anchoring, retention clamps, future clamps, host-name
variants, positive controls for empty results, explicit truncation and loud failures.
"""

from __future__ import annotations

import asyncio
import importlib
import itertools
import json
import math
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

import duckdb
import pyarrow as pa
from sqlglot import exp

from omniops.engine import http, pushdown, templating, timeutil, transform
from omniops.engine.catalog import Catalog, CatalogEntry
from omniops.engine.results import QueryResult, message_digest, timeseries_digest

MAX_REQUESTS_PER_QUERY = 60
FETCH_ROW_CAP = 20_000
RESULT_FETCH_CAP = 20_000


class EngineError(RuntimeError):
    lesson = "describe the table (describe_table) and fix the query; do not repeat the same query"


class UnknownTableError(EngineError):
    lesson = "search the catalog (search_catalog) for the right table; never invent table names"


class MissingFilterError(EngineError):
    lesson = "add the required filter(s) shown; describe_table lists them with examples"


class SQLExecutionError(EngineError):
    pass


class QueryTimeoutError(EngineError):
    lesson = "the SQL took too long: aggregate earlier, narrow the window, or add filters (not a schema problem)"


_ARROW = {
    "VARCHAR": pa.string(), "JSON": pa.string(), "BIGINT": pa.int64(), "DOUBLE": pa.float64(),
    "BOOLEAN": pa.bool_(), "TIMESTAMP": pa.timestamp("us", tz="UTC"), "DATE": pa.date32(),
    "INTERVAL": pa.duration("us"),
}


@dataclass
class FetchPlan:
    entry: CatalogEntry
    occurrence: pushdown.Occurrence | None
    filters: dict[str, Any]
    start: datetime | None
    end: datetime | None
    limit: int | None
    notes: list[str] = field(default_factory=list)

    @property
    def table(self) -> str:
        return self.entry.table.name

    def key(self) -> str:
        return json.dumps([self.table, self.filters, self.start.isoformat() if self.start else None,
                           self.end.isoformat() if self.end else None, self.limit], sort_keys=True, default=str)


@dataclass
class FetchOutcome:
    rows: list[dict[str, Any]]
    notes: list[str]
    logs: list[http.RequestLog]
    errors: list[str]
    truncated: bool = False


class FetchCache:
    """Shared across all agents of one run: byte-identical fetches hit the API once.

    A fetch runs in its own task and every caller (the first one included) awaits it through
    ``asyncio.shield``: cancelling one caller (a seed's timeout, a specialist's deadline) only ends
    that caller's wait, never the fetch other agents are waiting on. The fetch itself is cancelled
    only when its last waiter goes away.
    """

    def __init__(self) -> None:
        self._data: dict[str, FetchOutcome] = {}
        self._inflight: dict[str, asyncio.Task[FetchOutcome]] = {}
        self._waiters: dict[asyncio.Task[FetchOutcome], int] = {}

    async def get_or_fetch(self, key: str, fn: Any) -> tuple[FetchOutcome, bool]:
        if key in self._data:
            return self._data[key], True
        task = self._inflight.get(key)
        hit = task is not None
        if task is None:
            task = asyncio.ensure_future(fn())
            self._inflight[key] = task
            task.add_done_callback(lambda t: self._settle(key, t))
        self._waiters[task] = self._waiters.get(task, 0) + 1
        try:
            return await asyncio.shield(task), hit
        except asyncio.CancelledError:
            if not task.done() and self._waiters.get(task, 0) <= 1:
                task.cancel()  # nobody else is waiting for this fetch
            raise
        finally:
            left = self._waiters.get(task, 1) - 1
            if left > 0:
                self._waiters[task] = left
            else:
                self._waiters.pop(task, None)

    def _settle(self, key: str, task: asyncio.Task[FetchOutcome]) -> None:
        if self._inflight.get(key) is task:
            del self._inflight[key]
        if task.cancelled() or task.exception() is not None:  # exception() also marks it retrieved
            return
        out = task.result()
        if not out.errors:
            self._data[key] = out


@dataclass
class StagedWrite:
    id: str
    table: str
    filters: dict[str, Any]
    summary: str
    created: float
    expires: float
    claimed: bool = False


class SQLEngine:
    def __init__(
        self,
        catalog: Catalog,
        anchor: timeutil.TimeAnchor | None = None,
        *,
        cache: FetchCache | None = None,
        positive_controls: bool = True,
        query_timeout_s: float = 90.0,
        write_mode: str = "stage",  # stage | auto (benchmarks with explicit opt-in) | deny
    ) -> None:
        self.catalog = catalog
        self.anchor = anchor or timeutil.TimeAnchor.wall_clock()
        self.cache = cache or FetchCache()
        self.positive_controls = positive_controls
        self.query_timeout_s = query_timeout_s
        self.write_mode = write_mode
        self.staged_writes: dict[str, StagedWrite] = {}
        self.con = duckdb.connect(":memory:")
        # Model-written SQL runs here: no file system, network or extension access, and the settings
        # are locked so a query cannot turn them back on. GLOBAL TimeZone so cursors inherit UTC.
        for setting in ("SET GLOBAL TimeZone='UTC'", "SET threads=4", "SET enable_external_access=false",
                        "SET autoinstall_known_extensions=false", "SET autoload_known_extensions=false",
                        "SET lock_configuration=true"):
            self.con.execute(setting)
        self._lock = threading.Lock()
        self._n = itertools.count(1)
        self.request_log: list[dict[str, Any]] = []

    def close(self) -> None:
        try:
            self.con.close()
        except duckdb.Error:
            pass

    # ------------------------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------------------------

    def _check_unknown_tables(self, tree: exp.Expression) -> None:
        ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
        visible = self.catalog.visible()
        for t in tree.find_all(exp.Table):
            name = t.name
            if not name or name in ctes or name in visible or t.args.get("db"):
                continue
            if isinstance(t.this, (exp.Anonymous, exp.Func)):
                continue  # table functions: range(), generate_series(), unnest()
            if name in self.catalog.entries:
                raise UnknownTableError(f"table {name!r} is out of scope for this agent. "
                                        f"Tables in scope: {', '.join(sorted(visible))[:600]}")
            near = [r["table"] for r in self.catalog.search(name.replace("_", " "), limit=5)]
            raise UnknownTableError(f"table {name!r} does not exist. Closest tables: {', '.join(near) or 'none'}. "
                                    "Some tables you might expect do not exist; search the catalog.")

    def _sql_limit(self, occ: pushdown.Occurrence) -> int | None:
        """LIMIT n (+ OFFSET) is pushed to the API only when the API's rows are exactly the SQL's
        input: nothing reorders, groups, joins or windows them, and every WHERE conjunct was pushed.
        Otherwise the first n rows the API returns need not hold the n rows the SQL keeps."""
        sel = occ.select
        if sel is None or sel.args.get("order") or sel.args.get("group") or sel.args.get("joins"):
            return None
        if sel.args.get("distinct") or sel.args.get("having") or sel.args.get("qualify") or \
                any(e.find(exp.AggFunc) or e.find(exp.Window) for e in sel.expressions):
            return None
        if occ.unpushed:
            return None
        pushed = {id(p.node) for p in occ.predicates if p.pushed}
        if any(id(c) not in pushed for c in pushdown.conjuncts(sel.args.get("where"))):
            return None
        n, offset = _limit_offset(sel)
        return None if n is None or offset is None else n + offset

    def plan(self, occ: pushdown.Occurrence) -> FetchPlan:
        entry = self.catalog.get(occ.table)
        assert entry is not None
        t = entry.table
        filters: dict[str, Any] = {}
        start: datetime | None = None
        end: datetime | None = None
        notes: list[str] = []
        filter_only = {c.name for c in t.columns if c.filter_only}
        pushed_on: dict[str, list[pushdown.Predicate]] = {}
        for p in occ.predicates:
            if t.time is not None and p.column == t.time.column:
                lo, hi = _time_bounds(p)
                if lo is not None:
                    start = max(start, lo) if start else lo
                if hi is not None:
                    end = min(end, hi) if end else hi
                p.pushed = lo is not None or hi is not None
                continue
            f = t.filter_for(p.column)
            if f is None:
                continue
            allowed = set(f.ops)
            fans_out = p.op == "in" and "=" in allowed and f.fan_out
            if p.op not in allowed and not fans_out:
                raise EngineError(f"{t.name}.{p.column} supports operators {f.ops}, not {p.op!r}")
            prev = pushed_on.setdefault(f.key, [])
            if prev:
                # A second predicate on the same request parameter: one parameter carries one value.
                # A lower + upper bound become one BETWEEN when the filter takes it; otherwise the
                # first stays pushed and DuckDB applies the rest to the fetched rows.
                rng = _range_pair(prev[0], p) if len(prev) == 1 else None
                if rng is not None and "between" in allowed:
                    filters[f.key] = rng
                    filters[f"{f.key}__op"] = "between"
                    p.pushed = True
                    prev.append(p)
                    continue
                if p.column in filter_only:
                    raise EngineError(
                        f"{t.name}.{p.column} is filter-only (the API does not return it), so SQL cannot apply "
                        f"a second predicate on it ({prev[0].op} and {p.op}); use one predicate"
                        + (" or BETWEEN" if "between" in allowed else ""))
                if rng is not None and prev[0].op in ("<", "<="):
                    # Push the lower bound: the rare tail (5xx codes, slow spans) is what an API row
                    # cap must not cut off; DuckDB applies the upper bound.
                    prev[0].pushed = False
                    prev[0] = p
                else:
                    continue
            else:
                prev.append(p)
            p.pushed = True
            filters[f.key] = p.value
            filters.pop(f"{f.key}__op", None)
            if p.op in {">", ">=", "<", "<=", "!=", "like", "between"}:
                filters[f"{f.key}__op"] = p.op
        for f in t.filters:
            if f.key not in filters and f.default is not None:
                filters[f.key] = f.default
        missing = [f.column for f in t.filters if f.required and f.key not in filters]
        for group in t.require_one_of:
            keys = [t.filter_for(c).key if t.filter_for(c) else c for c in group]
            if not any(k in filters for k in keys):
                missing.append(" | ".join(group))
        if missing:
            ex = next((x.sql for x in t.examples), "")
            raise MissingFilterError(
                f"{t.name} requires WHERE filter(s) on: {', '.join(missing)}. "
                f"Example: {' '.join(ex.split())}" if ex else f"{t.name} requires WHERE filter(s) on: {', '.join(missing)}")
        if t.time is not None:
            start, end, tnotes = self._resolve_window(entry, start, end)
            notes += tnotes
        return FetchPlan(entry, occ, filters, start, end, self._sql_limit(occ), notes)

    def _resolve_window(self, entry: CatalogEntry, start: datetime | None, end: datetime | None
                        ) -> tuple[datetime, datetime, list[str]]:
        t = entry.table
        assert t.time is not None
        notes: list[str] = []
        anchor = self.anchor
        if start is None and end is None:
            start, end = anchor.default_window(t.time.default_lookback)
        elif start is None:
            assert end is not None
            start = end - timeutil.parse_duration(t.time.default_lookback)
        elif end is None:
            end = anchor.now
        assert start is not None and end is not None
        wall = datetime.now(timeutil.UTC)
        ceiling = max(anchor.now, min(wall, anchor.now + timedelta(minutes=10)))
        if start > ceiling:
            raise EngineError(f"time range starts in the future ({timeutil.fmt_rfc3339(start)} > anchor "
                              f"{timeutil.fmt_rfc3339(anchor.now)}); relative times are pinned to the anchor")
        if end > ceiling:
            end = ceiling
        if end <= start:
            raise EngineError(f"empty time range {timeutil.fmt_rfc3339(start)} -> {timeutil.fmt_rfc3339(end)}")
        retention = t.time.retention or (entry.manifest.retention.get(t.kind) if entry.manifest else None)
        keep = timeutil.parse_retention(retention)
        if keep is not None:
            floor = anchor.now - keep
            if start < floor:
                if end <= floor:
                    raise EngineError(f"{t.name}: the whole window is beyond retention ({retention}); "
                                      "that data no longer exists - do not report it as absent")
                notes.append(f"start clamped to retention ({retention}): {timeutil.fmt_rfc3339(floor)}")
                start = floor
        if t.time.max_window:
            mw = timeutil.parse_duration(t.time.max_window)
            if end - start > mw:
                notes.append(f"window clamped to max {t.time.max_window} (kept the most recent part)")
                start = end - mw
        return start, end, notes

    # ------------------------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------------------------

    def _fan_out(self, plan: FetchPlan) -> list[dict[str, Any]]:
        t = plan.entry.table
        axes: list[tuple[str, list[Any]]] = []
        base: dict[str, Any] = {}
        for k, v in plan.filters.items():
            f = next((f for f in t.filters if f.key == k), None)
            if isinstance(v, list) and f is not None and f.fan_out:
                vals = list(dict.fromkeys(v))
                if len(vals) > f.max_fan_out:
                    plan.notes.append(f"IN-list on {f.column} capped at {f.max_fan_out} of {len(vals)} values")
                    vals = vals[: f.max_fan_out]
                axes.append((k, vals))
            else:
                base[k] = v
        combos = [dict(base)]
        for k, vals in axes:
            combos = [{**c, k: v} for c in combos for v in vals]
        if len(combos) > MAX_REQUESTS_PER_QUERY:
            plan.notes.append(f"fan-out capped at {MAX_REQUESTS_PER_QUERY} of {len(combos)} requests")
            combos = combos[:MAX_REQUESTS_PER_QUERY]
        return combos

    def _ctx(self, plan: FetchPlan, filters: dict[str, Any], start: datetime | None, end: datetime | None
             ) -> dict[str, Any]:
        t = plan.entry.table
        ctx: dict[str, Any] = {"filters": filters, "limit": plan.limit, "captures": {},
                               "page": {"offset": 0, "number": t.pagination.start, "size": t.pagination.page_size},
                               "anchor": {"now": self.anchor.now}}
        if start is not None and end is not None:
            tp = t.time.target_points if t.time else 300
            ms = t.time.min_step if t.time else "10s"
            ctx["time"] = http.time_ctx(start, end, tp, ms)
        return ctx

    async def _fetch_once(self, plan: FetchPlan, filters: dict[str, Any], start: datetime | None,
                          end: datetime | None) -> FetchOutcome:
        entry = plan.entry
        t = entry.table
        logs: list[http.RequestLog] = []
        ctx = self._ctx(plan, filters, start, end)
        if entry.computed:
            # Computed tables read their filters and window from ``ctx`` (one fan-out combo of the
            # plan) and return their notes in the outcome, so the notes are cached with the rows.
            mod, _, fn = (t.python or "").partition(":")
            func = getattr(importlib.import_module(mod), fn)
            out = _as_outcome(t, await func(self, plan, ctx), logs)
            # Filter-only columns echo this request's (fan-out combo's) filter values, like build_rows.
            echo = {c.name: (c.type, filters.get(f.key)) for c in t.columns if c.filter_only
                    for f in [t.filter_for(c.name)] if f is not None}
            for r in out.rows:
                for name, (typ, v) in echo.items():
                    if r.get(name) is None and v is not None and not isinstance(v, (list, tuple, dict)):
                        r[name] = transform.coerce(v, typ)
            if out.errors and not any("PARTIAL" in n for n in out.notes):
                out.notes.append(f"PARTIAL: {len(out.errors)} source(s) failed: {out.errors[0][:300]}")
            return out
        rt = entry.runtime
        assert rt is not None
        if t.python:
            mod, _, fn = t.python.partition(":")
            func = getattr(importlib.import_module(mod), fn)
            return _as_outcome(t, await func(rt, t, {**rt.template_ctx(), **ctx}, logs), logs)
        manifest = entry.manifest
        assert manifest is not None

        def count_rows(resp: http.Response) -> tuple[int, int]:
            """(API items on the page, rows after expand): pagination advances by items."""
            try:
                body = resp.body if t.response.text is None else resp.text
                text = resp.text if t.response.text else None
                items = len(transform.row_elements(t, body, text=text)[1])
                return items, (len(transform.row_scopes(t, body, text=text)) if t.response.expand else items)
            except Exception:  # noqa: BLE001
                return 0, 0

        res = await http.run_table_requests(rt, t, ctx, logs=logs, row_counter=count_rows, max_rows=t.max_rows)
        rows: list[dict[str, Any]] = []
        scalar_filters = {k: v for k, v in filters.items() if not isinstance(v, list)}
        for resp in res.responses:
            last = t.all_steps[-1]
            if resp.status >= 400 and resp.status in last.accept_status and resp.status not in last.accept_status_rows:
                continue
            text = resp.text if t.response.text is not None else None
            rows.extend(transform.build_rows(t, resp.body, filters=scalar_filters, text=text,
                                             extra_error_rules=manifest.errors))
        return FetchOutcome(rows, list(res.notes), logs, [], res.truncated)

    async def fetch(self, plan: FetchPlan, start: datetime | None = None, end: datetime | None = None
                    ) -> FetchOutcome:
        start = start if start is not None else plan.start
        end = end if end is not None else plan.end
        combos = self._fan_out(plan)

        async def one(filters: dict[str, Any]) -> FetchOutcome:
            key = json.dumps([plan.table, filters, start.isoformat() if start else None,
                              end.isoformat() if end else None, plan.limit], sort_keys=True, default=str)
            out, _ = await self.cache.get_or_fetch(key, lambda: self._fetch_once(plan, filters, start, end))
            return out

        results = await asyncio.gather(*(one(c) for c in combos), return_exceptions=True)
        rows: list[dict[str, Any]] = []
        notes: list[str] = []
        logs: list[http.RequestLog] = []
        errors: list[str] = []
        inner_errors: list[str] = []  # partial failures inside a request (a computed table's sources)
        first_exc: BaseException | None = None
        truncated = False
        for r in results:
            if isinstance(r, BaseException):
                if isinstance(r, asyncio.CancelledError):
                    raise r
                errors.append(f"{type(r).__name__}: {r}")
                first_exc = first_exc or r
                continue
            rows.extend(r.rows)
            notes.extend(r.notes)
            logs.extend(r.logs)
            inner_errors.extend(r.errors)
            truncated = truncated or r.truncated
        if errors and not rows and len(errors) == len(combos) and first_exc is not None:
            raise first_exc
        if errors:
            notes.append(f"PARTIAL: {len(errors)} of {len(combos)} request(s) failed: {errors[0][:300]}")
        if len(rows) > FETCH_ROW_CAP:
            notes.append(f"fetched rows capped at {FETCH_ROW_CAP} of {len(rows)}")
            rows = rows[:FETCH_ROW_CAP]
            truncated = True
        if plan.entry.table.zero_fill and rows and start and end and plan.entry.table.time:
            tcol = plan.entry.table.time.column
            vcol = next((c.name for c in plan.entry.table.columns if c.name in ("count", "value")), None)
            if vcol:
                groups = [c.name for c in plan.entry.table.columns if c.type in ("VARCHAR",) and not c.filter_only]
                # Fill with the bucket size the API actually used (servers and filters widen it),
                # never with a guessed step: a wrong step inserts fake zero buckets.
                step = _bucket_step(rows, tcol, groups, start, end)
                if step:
                    rows = transform.zero_fill(rows, tcol, vcol, step, start, end, groups)
        return FetchOutcome(rows, notes, logs, errors + inner_errors, truncated)

    async def _with_host_variants(self, plan: FetchPlan, out: FetchOutcome) -> FetchOutcome:
        if out.rows:
            return out
        for f in plan.entry.table.filters:
            if f.variants != "hostname":
                continue
            v = plan.filters.get(f.key)
            if not isinstance(v, str):
                continue
            alts = []
            if "." in v:
                alts.append(v.split(".")[0])
            alts += [v.lower()] if v != v.lower() else []
            for alt in alts:
                alt_plan = FetchPlan(plan.entry, plan.occurrence, {**plan.filters, f.key: alt}, plan.start, plan.end,
                                     plan.limit, list(plan.notes))
                alt_out = await self.fetch(alt_plan)
                if alt_out.rows:
                    # New row dicts: the fetched ones are shared with the FetchCache entry for the
                    # alternate form, and relabelling them in place would corrupt that entry.
                    rows = [{**r, f.column: v} if r.get(f.column) == alt else r for r in alt_out.rows]
                    note = (f"host {v!r} returned nothing; the short/alternate form {alt!r} matched "
                            "(different data types carry different host-name forms)")
                    return FetchOutcome(rows, [*alt_out.notes, note], alt_out.logs, alt_out.errors, alt_out.truncated)
            out.notes.append(f"no data for host {v!r} or its alternate forms {alts}")
        return out

    # ------------------------------------------------------------------------------------
    # DuckDB
    # ------------------------------------------------------------------------------------

    def _load(self, plan: FetchPlan, rows: list[dict[str, Any]]) -> str:
        t = plan.entry.table
        name = f"__vt_{next(self._n)}"
        fields = []
        declared = [c.name for c in t.columns]
        dyn: list[str] = []
        for r in rows[:2000]:
            for k in r:
                if k not in declared and k not in dyn:
                    dyn.append(k)
        for c in t.columns:
            fields.append(pa.field(c.name, _ARROW.get(c.type, pa.string())))
        for k in dyn:
            fields.append(pa.field(k, _infer_arrow([r.get(k) for r in rows[:2000]])))
        schema = pa.schema(fields)
        cols: dict[str, list[Any]] = {f.name: [] for f in fields}
        for r in rows:
            for f in fields:
                v = r.get(f.name)
                if f.type == pa.string() and v is not None and not isinstance(v, str):
                    v = json.dumps(v, default=str) if isinstance(v, (dict, list)) else str(v)
                elif pa.types.is_floating(f.type) and v is not None and not isinstance(v, float):
                    v = transform.coerce(v, "DOUBLE")
                elif pa.types.is_integer(f.type) and v is not None and not isinstance(v, int):
                    v = transform.coerce(v, "BIGINT")
                cols[f.name].append(v)
        table = pa.Table.from_pydict(cols, schema=schema)
        json_cols = {c.name for c in t.columns if c.type == "JSON"}
        select = ", ".join(f'CAST("{f.name}" AS JSON) AS "{f.name}"' if f.name in json_cols else f'"{f.name}"'
                           for f in fields)
        with self._lock:
            cur = self.con.cursor()
            try:
                cur.register(f"{name}_arrow", table)
                cur.execute(f'CREATE TABLE "{name}" AS SELECT {select} FROM "{name}_arrow"')
                cur.unregister(f"{name}_arrow")
            finally:
                cur.close()
        return name

    def _execute(self, sql: str) -> tuple[list[str], list[str], list[tuple[Any, ...]]]:
        # Every cursor is its own DuckDB connection and interrupt() only stops a query running on the
        # connection it is called on: arm the timer on the cursor that runs the SQL. The lock covers
        # cursor creation only, so a slow query never holds up other queries' loads.
        with self._lock:
            cur = self.con.cursor()
        timer = threading.Timer(self.query_timeout_s, cur.interrupt)
        timer.start()
        try:
            cur.execute(sql)
            desc = cur.description or []
            rows = cur.fetchmany(RESULT_FETCH_CAP)
        except duckdb.InterruptException as exc:
            raise QueryTimeoutError(f"SQL exceeded {self.query_timeout_s}s") from exc
        except duckdb.Error as exc:
            raise SQLExecutionError(f"SQL error: {exc}") from exc
        finally:
            timer.cancel()
            cur.close()
        return [d[0] for d in desc], [str(d[1]) for d in desc], [tuple(r) for r in rows]

    # ------------------------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------------------------

    async def query(self, sql: str, *, purpose: str = "") -> QueryResult:
        t0 = time.monotonic()
        tree = pushdown.parse(sql)
        tree = pushdown.pin_relative_times(tree, self.anchor)
        self._check_unknown_tables(tree)
        evaluate = pushdown.ConstantEvaluator(self.con)
        occs = pushdown.extract_occurrences(tree, self.catalog.columns_map(), evaluate)
        plans = [self.plan(o) for o in occs]

        writes = [p for p in plans if p.entry.table.kind == "write"]
        if writes:
            return self._stage_write(sql, writes[0], t0)

        outcomes = await asyncio.gather(*(self.fetch(p) for p in plans), return_exceptions=True)
        notes: list[str] = []
        windows: dict[str, tuple[str, str]] = {}
        links: list[dict[str, str]] = []
        req_logs: list[dict[str, Any]] = []
        fetched: dict[str, int] = {}
        final_outcomes: list[FetchOutcome] = []
        for plan, out in zip(plans, outcomes):
            if isinstance(out, BaseException):
                raise out
            out = await self._with_host_variants(plan, out)
            final_outcomes.append(out)
        for plan, out in zip(plans, final_outcomes):
            occ = plan.occurrence
            assert occ is not None
            # Off the event loop: _load takes the engine lock, which must never block the loop.
            vt = await asyncio.to_thread(self._load, plan, out.rows)
            label = plan.table if occ.alias == plan.table else f"{plan.table} AS {occ.alias}"
            fetched[label] = len(out.rows)
            new = exp.Table(this=exp.to_identifier(vt), alias=exp.TableAlias(this=exp.to_identifier(occ.alias)))
            occ.node.replace(new)
            filter_only = {c.name for c in plan.entry.table.columns if c.filter_only}
            tspec = plan.entry.table.time
            if tspec is not None and not tspec.reapply:
                filter_only.add(tspec.column)
            for p in occ.predicates:
                # Only predicates the request carried: the API was authoritative for those.
                if p.column in filter_only and (p.pushed or (tspec is not None and p.column == tspec.column)):
                    pushdown.strip_conjunct(p.node)
            notes += [f"{label}: {n}" for n in plan.notes + out.notes]
            if out.truncated:
                notes.append(f"{label}: source data truncated - results may be incomplete")
            if plan.start and plan.end:
                windows[label] = (timeutil.fmt_rfc3339(plan.start), timeutil.fmt_rfc3339(plan.end))
            link = self._link(plan)
            if link:
                links.append({"label": label, "url": link})
            req_logs += [lg.redacted() for lg in out.logs]
            if occ.unpushed:
                pass  # evaluated by DuckDB; nothing to report
        final_sql = tree.sql(dialect=pushdown.READ_DIALECT)
        cols, types, rows = await asyncio.to_thread(self._execute, final_sql)
        total = len(rows)
        if total >= RESULT_FETCH_CAP:
            notes.append(f"result has at least {RESULT_FETCH_CAP} rows; aggregate in SQL")
        for plan, out in zip(plans, final_outcomes):
            occ = plan.occurrence
            want = _limit_offset(occ.select)[0] if occ is not None and occ.select is tree else None
            if plan.limit is not None and want is not None and len(out.rows) >= plan.limit and total < want:
                # The API stopped at the pushed LIMIT and SQL re-filtering (e.g. exact-case matching of
                # a predicate the API applies loosely) dropped some of those rows.
                notes.append(f"{plan.table}: LIMIT {plan.limit} was sent to the API, which returned {len(out.rows)} "
                             f"row(s); the SQL kept {total}, so more matching rows may exist - drop the LIMIT or "
                             "aggregate in SQL")
        res = QueryResult(sql=sql, columns=cols, types=types, rows=rows, total_rows=total,
                          tables=sorted({p.table for p in plans}), notes=notes, links=links, requests=req_logs,
                          windows=windows, fetched_rows=fetched)
        res.digests = timeseries_digest(cols, types, rows) + message_digest(cols, rows)
        # Also when an aggregate turned an empty fetch into one row (count(*) = 0, max() = NULL).
        if plans and (total == 0 or all(not o.rows for o in final_outcomes)):
            await self._positive_control(res, plans, final_outcomes)
        res.elapsed_ms = int((time.monotonic() - t0) * 1000)
        self.request_log += req_logs
        return res

    async def _positive_control(self, res: QueryResult, plans: list[FetchPlan], outs: list[FetchOutcome]) -> None:
        empties = [(p, o) for p, o in zip(plans, outs) if not o.rows]
        if not empties:
            res.notes.append("the API returned rows but your SQL predicates/joins filtered all of them out")
            res.empty_verdict = None
            return
        if not self.positive_controls:
            res.empty_verdict = "inconclusive"
            return
        verdicts = []
        for plan, out in empties[:2]:
            t = plan.entry.table
            if not t.positive_control or plan.start is None or plan.end is None:
                verdicts.append("inconclusive")
                continue
            capped = [n for n in plan.notes if "capped at" in n]
            if out.errors or out.truncated or capped:
                # Partial coverage cannot refute: part of the requested data was never fetched.
                why = (out.errors or capped or ["source data truncated"])[0]
                res.notes.append(f"positive control skipped for {plan.table}: the fetch was partial ({why[:200]}), "
                                 "so 0 rows is not proof of absence")
                verdicts.append("inconclusive")
                continue
            span = plan.end - plan.start
            ctrl_end = plan.start
            lookback = timeutil.parse_duration(t.time.control_lookback) if t.time else timedelta(hours=24)
            ctrl_start = ctrl_end - max(lookback, min(span * 4, lookback * 7))
            # One control per fan-out combo: every IN-list value must prove itself (a typo'd value
            # must not ride on a valid one's rows).
            probe = replace(plan, notes=[])
            combos = self._fan_out(probe)
            axes = [k for k, v in plan.filters.items() if isinstance(v, list) and any(c.get(k) != v for c in combos)]
            try:
                ctrl_start, ctrl_end, _ = self._resolve_window(plan.entry, ctrl_start, ctrl_end)
                ctrls = await asyncio.gather(*(self.fetch(replace(probe, filters=c, notes=[]), ctrl_start, ctrl_end)
                                               for c in combos))
            except Exception as exc:  # noqa: BLE001
                res.notes.append(f"positive control for {plan.table} failed: {exc}")
                verdicts.append("inconclusive")
                continue
            window = f"{timeutil.fmt_rfc3339(ctrl_start)} -> {timeutil.fmt_rfc3339(ctrl_end)}"
            dead = [c for c, o in zip(combos, ctrls) if not o.rows or o.errors]
            if not dead:
                res.notes.append(f"positive control: the same {plan.table} filters returned "
                                 f"{sum(len(o.rows) for o in ctrls)} rows in {window}, so the "
                                 "filter is valid and the requested window is genuinely empty")
                verdicts.append("absent")
            elif axes and len(dead) < len(combos):
                which = "; ".join(", ".join(f"{k}={c.get(k)!r}" for k in axes) for c in dead[:5])
                res.notes.append(f"positive control: {plan.table} returned rows in the preceding {window} for some "
                                 f"values but ALSO 0 rows for {which}; that value is probably wrong (typo, case, "
                                 "host form), so this empty result is not proof of absence for it")
                verdicts.append("inconclusive")
            else:
                res.notes.append(f"positive control: the same {plan.table} filters ALSO returned 0 rows in the "
                                 f"preceding {window}; "
                                 "the filter (tag/label/facet name or value, host form, env) is probably wrong")
                verdicts.append("inconclusive")
        res.empty_verdict = "absent" if verdicts and all(v == "absent" for v in verdicts) else "inconclusive"

    def _link(self, plan: FetchPlan) -> str | None:
        t = plan.entry.table
        if not t.ui_link or plan.entry.runtime is None:
            return None
        scalar = {k: (v[0] if isinstance(v, list) and v else v) for k, v in plan.filters.items()}
        ctx = {**plan.entry.runtime.template_ctx(), **self._ctx(plan, scalar, plan.start, plan.end)}
        if plan.entry.manifest and plan.entry.manifest.ui_base_url:
            ctx["ui_base_url"] = templating.render_value(plan.entry.manifest.ui_base_url, ctx)
        try:
            link = templating.render_value(t.ui_link, ctx)
        except Exception:  # noqa: BLE001
            return None
        return str(link) if link else None

    def _stage_write(self, sql: str, plan: FetchPlan, t0: float) -> QueryResult:
        wid = "W" + uuid.uuid4().hex[:6]
        summary = f"{plan.table} with " + ", ".join(f"{k}={str(v)[:80]}" for k, v in plan.filters.items()
                                                    if not k.endswith("__op"))
        sw = StagedWrite(wid, plan.table, plan.filters, summary, time.time(), time.time() + 1800)
        self.staged_writes[wid] = sw
        res = QueryResult(sql=sql, columns=["write_id", "status", "summary"], types=["VARCHAR"] * 3,
                          rows=[(wid, "staged", summary)], total_rows=1, tables=[plan.table],
                          staged_write={"id": wid, "summary": summary})
        res.notes.append("writes are never executed inline: a human approves them in Slack (expires in 30 min)")
        res.elapsed_ms = int((time.monotonic() - t0) * 1000)
        return res

    async def execute_write(self, write_id: str) -> QueryResult:
        """Run a staged write after approval. Claimed atomically so a retried click runs once."""
        sw = self.staged_writes.get(write_id)
        if sw is None:
            raise EngineError(f"unknown staged write {write_id}")
        if sw.claimed:
            raise EngineError(f"staged write {write_id} was already executed")
        if time.time() > sw.expires:
            raise EngineError(f"staged write {write_id} expired")
        sw.claimed = True
        entry = self.catalog.get(sw.table)
        assert entry is not None
        plan = FetchPlan(entry, None, sw.filters, None, None, None)
        out = await self._fetch_once(plan, sw.filters, None, None)
        rows = [tuple(r.values()) for r in out.rows]
        cols = list(out.rows[0].keys()) if out.rows else ["status"]
        return QueryResult(sql=f"-- executed staged write {write_id}", columns=cols, types=["VARCHAR"] * len(cols),
                           rows=rows or [("ok",)], total_rows=len(rows) or 1, tables=[sw.table])


def _time_bounds(p: pushdown.Predicate) -> tuple[datetime | None, datetime | None]:
    def dt(v: Any) -> datetime:
        return timeutil.to_datetime(v)

    if p.op == "between":
        lo, hi = p.value
        return dt(lo), dt(hi)
    if p.op in (">", ">="):
        return dt(p.value), None
    if p.op in ("<", "<="):
        return None, dt(p.value)
    if p.op == "=":
        v = dt(p.value)
        return v - timedelta(minutes=1), v + timedelta(minutes=1)
    return None, None


def _range_pair(a: pushdown.Predicate, b: pushdown.Predicate) -> tuple[Any, Any] | None:
    """(low, high) when one predicate is a lower bound and the other an upper bound."""
    lower, upper = {">", ">="}, {"<", "<="}
    if a.op in lower and b.op in upper:
        return a.value, b.value
    if a.op in upper and b.op in lower:
        return b.value, a.value
    return None


def _limit_offset(sel: exp.Select | None) -> tuple[int | None, int | None]:
    """(LIMIT n, OFFSET k) of a SELECT: n is None without a literal LIMIT, k is None when not a literal."""
    if sel is None or sel.args.get("limit") is None:
        return None, 0
    try:
        n = int(sel.args["limit"].expression.this)
    except (AttributeError, ValueError, TypeError):
        return None, 0
    off = sel.args.get("offset")
    if off is None:
        return n, 0
    try:
        return n, int(off.expression.this)
    except (AttributeError, ValueError, TypeError):
        return n, None


def _bucket_step(rows: list[dict[str, Any]], tcol: str, groups: list[str], start: datetime, end: datetime
                 ) -> int | None:
    """The bucket size (s) the API actually used, inferred from the returned bucket times.

    The GCD of the gaps between distinct bucket times is a multiple of the true step, so every
    zero inserted with it is a real empty bucket. None (no zero-fill) when it cannot be told: one
    bucket only, sub-second or irregular stamps, or a fill that would explode the row count.
    """
    stamps = sorted({r.get(tcol) for r in rows if isinstance(r.get(tcol), datetime)})
    if len(stamps) < 2:
        return None
    gcd_ms = 0
    for a, b in itertools.pairwise(stamps):
        gcd_ms = math.gcd(gcd_ms, round((b - a).total_seconds() * 1000))
    if gcd_ms < 1000 or gcd_ms % 1000:
        return None
    step = gcd_ms // 1000
    n_groups = len({tuple(r.get(g) for g in groups) for r in rows})
    if (end - start).total_seconds() / step * n_groups > FETCH_ROW_CAP:
        return None
    return step


def _as_outcome(t: Any, res: Any, logs: list[http.RequestLog]) -> FetchOutcome:
    """Python and computed fetchers return rows, or a FetchOutcome when they have notes, partial
    failures or truncation to report (a silent cap reads as complete data)."""
    if isinstance(res, FetchOutcome):
        return FetchOutcome(_coerce_rows(t, res.rows), list(res.notes), logs, list(res.errors), res.truncated)
    return FetchOutcome(_coerce_rows(t, res), [], logs, [])


def _coerce_rows(t: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    types = {c.name: c.type for c in t.columns}
    out = []
    for r in rows:
        out.append({k: (transform.coerce(v, types[k]) if k in types else v) for k, v in r.items()})
    return out


def _infer_arrow(values: list[Any]) -> pa.DataType:
    seen = [v for v in values if v is not None]
    if not seen:
        return pa.string()
    if all(isinstance(v, bool) for v in seen):
        return pa.bool_()
    if all(isinstance(v, int) and not isinstance(v, bool) for v in seen):
        return pa.int64()
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in seen):
        return pa.float64()
    if all(isinstance(v, datetime) for v in seen):
        return pa.timestamp("us", tz="UTC")
    return pa.string()
