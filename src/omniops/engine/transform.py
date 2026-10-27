"""Response -> rows, driven by a table's ``response`` and ``columns`` specs.

Pipeline: ``rows_path`` selects row elements -> each ``expand`` step multiplies rows (explode,
zip, columnar, entries) -> each column is read by path / expression, transformed and coerced to
its SQL type -> ``dynamic`` adds flattened unmapped keys.

Code measures, the LLM judges: every parse happens here, deterministically, never in a prompt.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
from datetime import datetime, timedelta
from typing import Any

from omniops.engine import jsonpath, templating, timeutil
from omniops.engine.manifest import ColumnSpec, ErrorRule, ExpandStep, TableSpec


class ApiBodyError(RuntimeError):
    """A 2xx response whose body says it failed. Never treat these as empty data."""

    lesson = ("the API rejected the request in its response body (often a query-language syntax or "
              "unknown-field error): fix what the message names; this is a failure, not empty data")

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        if hint:
            self.lesson = f"{hint}; {type(self).lesson}"


class TransformError(RuntimeError):
    lesson = "the response did not have the shape the manifest expects: use another table or report the gap"


_SAFE_COL = re.compile(r"[^a-zA-Z0-9_]+")


def check_errors(body: Any, rules: list[ErrorRule]) -> None:
    for rule in rules:
        try:
            v = jsonpath.resolve(rule.path, body)
        except jsonpath.PathError:
            continue
        fired = False
        if rule.equals is not None and v is not None:
            if v == rule.equals or (isinstance(v, list) and jsonpath.has_wildcard(rule.path) and rule.equals in v):
                fired = True
        if rule.non_empty and v not in (None, "", [], {}):
            fired = True
        if fired:
            msg: Any = jsonpath.resolve(rule.message, body) if rule.message else v
            if not isinstance(msg, str):
                msg = json.dumps(msg, default=str)[:800]
            hint = f" ({rule.hint})" if rule.hint else ""
            raise ApiBodyError(f"API reported an error in a 2xx body: {msg}{hint}", hint=rule.hint)


# ---------------------------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------------------------


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    low = s.lower()
    if low in {"nan", "+nan", "-nan"}:
        return math.nan
    if low in {"+inf", "inf", "infinity"}:
        return math.inf
    if low in {"-inf", "-infinity"}:
        return -math.inf
    try:
        return float(s)
    except ValueError:
        return None


def _to_int(v: Any) -> int | None:
    f = _to_float(v)
    if f is None or math.isnan(f) or math.isinf(f):
        return None
    return int(f)


def _to_bool(v: Any) -> bool | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v).strip().lower() in {"true", "1", "yes", "y", "t", "on"}


def _to_ts(v: Any) -> datetime | None:
    if v is None or v == "":
        return None
    try:
        return timeutil.to_datetime(v)
    except (timeutil.TimeParseError, ValueError, OverflowError, OSError):
        return None


def coerce(value: Any, sql_type: str) -> Any:
    if value is None:
        return None
    t = sql_type.upper()
    if t == "VARCHAR":
        if isinstance(value, (dict, list)):
            return json.dumps(value, default=str, separators=(",", ":"))
        if isinstance(value, datetime):
            return timeutil.fmt_rfc3339_ms(value)
        return str(value)
    if t == "JSON":
        return json.dumps(value, default=str, separators=(",", ":"))
    if t == "BIGINT":
        return _to_int(value)
    if t == "DOUBLE":
        return _to_float(value)
    if t == "BOOLEAN":
        return _to_bool(value)
    if t == "TIMESTAMP":
        return _to_ts(value)
    if t == "DATE":
        ts = _to_ts(value)
        return ts.date() if ts else None
    if t == "INTERVAL":
        try:
            return timeutil.parse_duration(value)
        except timeutil.TimeParseError:
            return None
    return value


def apply_transform(value: Any, name: str | None, arg: str | None = None) -> Any:
    if name is None or value is None:
        return value
    if name in {"epoch_s", "epoch_ms", "epoch_us", "epoch_ns"}:
        f = _to_float(value)
        if f is None:
            return None
        div = {"epoch_s": 1, "epoch_ms": 1e3, "epoch_us": 1e6, "epoch_ns": 1e9}[name]
        return timeutil.datetime.fromtimestamp(f / div, tz=timeutil.UTC)
    if name in {"auto_time", "iso"}:
        return _to_ts(value)
    if name == "json":
        return json.dumps(value, default=str, separators=(",", ":"))
    if name == "str":
        return str(value)
    if name == "int":
        return _to_int(value)
    if name == "float":
        return _to_float(value)
    if name == "bool":
        return _to_bool(value)
    if name == "lower":
        return str(value).lower()
    if name == "upper":
        return str(value).upper()
    if name in {"ns_to_ms", "us_to_ms", "s_to_ms"}:
        f = _to_float(value)
        if f is None:
            return None
        return f * {"ns_to_ms": 1e-6, "us_to_ms": 1e-3, "s_to_ms": 1e3}[name]
    if name == "duration_to_s":
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return timeutil.parse_duration(value).total_seconds()
        except timeutil.TimeParseError:
            return None
    if name == "join":
        sep = arg if arg is not None else ","
        if isinstance(value, (list, tuple)):
            return sep.join(str(x) for x in value)
        if isinstance(value, dict):
            return sep.join(f"{k}:{v}" for k, v in value.items())
        return str(value)
    if name == "first":
        if isinstance(value, (list, tuple)):
            return value[0] if value else None
        return value
    if name == "length":
        return len(value) if hasattr(value, "__len__") else None
    if name == "from_json":
        return _parse_json_str(value)
    if name == "b64decode":
        try:
            return base64.b64decode(str(value)).decode("utf-8", errors="replace")
        except (binascii.Error, ValueError):
            return None
    if name == "k8s_quantity":
        return k8s_quantity(value)
    if name == "redact":
        return redact(value)
    raise TransformError(f"unknown transform {name}")


_QTY = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*([a-zA-Z]*)\s*$")
_QTY_MULT = {"": 1, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18,
             "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "Pi": 2**50, "Ei": 2**60}


def k8s_quantity(value: Any) -> float | None:
    """Kubernetes resource quantity ('250m', '128Mi', '1.5Gi', '3n') -> float (cores or bytes)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    m = _QTY.match(str(value)) if value is not None else None
    if not m or m.group(2) not in _QTY_MULT:
        return None
    return float(m.group(1)) * _QTY_MULT[m.group(2)]


_REDACT_RULES = [
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^/\s@]+)@"), r"\1\2:***@"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|"
                r"client[_-]?secret|authorization)(\s*[=:]\s*)([^\s,;&\"']+)"), r"\1\2***"),
    (re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/-]{8,}=*"), "Bearer ***"),
    (re.compile(r"\b(?:xox[abposr]|xapp|ghp|gho|ghs|github_pat|glpat|sk|AKIA)[A-Za-z0-9_-]{8,}\b"), "***"),
]


def redact(value: Any) -> Any:
    """Mask credentials in free text (URL userinfo, key=value secrets, bearer and well-known token shapes)."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        value = json.dumps(value, default=str)
    s = str(value)
    for rx, repl in _REDACT_RULES:
        s = rx.sub(repl, s)
    return s


def _parse_json_str(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


# ---------------------------------------------------------------------------------------------
# Row expansion
# ---------------------------------------------------------------------------------------------


def _resolve(path: str, root: Any, scope: dict[str, Any]) -> Any:
    return jsonpath.resolve(path, root, current=scope["_"], aliases=scope, variables={"i": scope["_i"]})


def _expand(step: ExpandStep, root: Any, scope: dict[str, Any]) -> list[Any]:
    if step.explode is not None:
        v = _resolve(step.explode, root, scope)
        if step.parse:
            v = [_parse_json_str(x) for x in v] if isinstance(v, list) else _parse_json_str(v)
        if v is None:
            return []
        return v if isinstance(v, list) else [v]
    if step.entries is not None:
        v = _resolve(step.entries, root, scope)
        if step.parse:
            v = _parse_json_str(v)
        if not isinstance(v, dict):
            return []
        return [{"key": k, "value": val} for k, val in v.items()]
    if step.zip is not None:
        arrays = {k: _resolve(p, root, scope) for k, p in step.zip.items()}
        arrays = {k: (a if isinstance(a, list) else []) for k, a in arrays.items()}
        n = max((len(a) for a in arrays.values()), default=0)
        return [{k: (a[i] if i < len(a) else None) for k, a in arrays.items()} for i in range(n)]
    if step.columnar is not None:
        names = _resolve(step.columnar["columns"], root, scope)
        data = _resolve(step.columnar["data"], root, scope)
        if not isinstance(names, list) or not isinstance(data, list):
            return []
        names = [n if isinstance(n, str) else (n.get("name") if isinstance(n, dict) else str(n)) for n in names]
        if step.columnar.get("orientation", "row") == "row":
            return [dict(zip(names, r)) for r in data if isinstance(r, list)]
        cols = [c if isinstance(c, list) else [] for c in data]
        n = max((len(c) for c in cols), default=0)
        return [{name: (cols[j][i] if j < len(cols) and i < len(cols[j]) else None)
                 for j, name in enumerate(names)} for i in range(n)]
    return []


def _flatten(node: Any, prefix: str, out: dict[str, Any], max_cols: int, depth: int = 0) -> None:
    if len(out) >= max_cols:
        return
    if isinstance(node, dict) and depth < 4:
        for k, v in node.items():
            key = f"{prefix}_{k}" if prefix else str(k)
            _flatten(v, key, out, max_cols, depth + 1)
    else:
        name = _SAFE_COL.sub("_", prefix).strip("_").lower() or "value"
        if name[0].isdigit():
            name = "c_" + name
        if name not in out:
            out[name] = node


def row_elements(table: TableSpec, body: Any, text: str | None = None) -> tuple[Any, list[Any]]:
    """(root, elements): the API's own items (rows_path elements or text lines), before ``expand``.

    Pagination counts these: offsets and page sizes are in API items, not in expanded rows.
    """
    resp = table.response
    if resp.text is not None:
        src = text if text is not None else (body if isinstance(body, str) else "")
        rx = re.compile(resp.text.regex) if resp.text.regex else None
        elements = []
        for line in src.splitlines():
            if resp.text.skip_empty and not line.strip():
                continue
            el: dict[str, Any] = {"line": line}
            if rx:
                m = rx.search(line)
                if m:
                    el.update(m.groupdict())
            elements.append(el)
        root: Any = elements
    else:
        root = body
        if resp.rows_path in ("$", "$[*]"):
            elements = root if isinstance(root, list) else ([] if root is None else [root])
        else:
            elements = jsonpath.resolve_list(resp.rows_path, root)
    return root, elements


def row_scopes(table: TableSpec, body: Any, text: str | None = None) -> list[dict[str, Any]]:
    resp = table.response
    root, elements = row_elements(table, body, text)
    scopes: list[dict[str, Any]] = [{"_": el, "_i": i} for i, el in enumerate(elements)]
    for step in resp.expand:
        nxt: list[dict[str, Any]] = []
        for sc in scopes:
            items = _expand(step, root, sc)
            if not items and step.keep_empty:
                nxt.append({**sc, step.as_: None})
            for it in items:
                nxt.append({**sc, step.as_: it})
        scopes = nxt
    for sc in scopes:
        sc["$root"] = root
    return scopes


def _column_value(col: ColumnSpec, root: Any, scope: dict[str, Any], filters: dict[str, Any]) -> Any:
    if col.expr is not None:
        ctx = {k: v for k, v in scope.items() if not k.startswith("$")}
        ctx["filters"] = filters
        ctx["root"] = root
        value = templating.evaluate(col.expr, ctx)
    elif col.path is not None:
        value = _resolve(col.path, root, scope)
    else:
        value = None
    value = apply_transform(value, col.transform, col.transform_arg)
    return coerce(value, col.type)


def build_rows(
    table: TableSpec,
    body: Any,
    *,
    filters: dict[str, Any] | None = None,
    text: str | None = None,
    extra_error_rules: list[ErrorRule] | None = None,
) -> list[dict[str, Any]]:
    """Turn one response body into rows for ``table``.

    ``filters`` are the scalar filter values of the request that produced this body. Filter
    columns the API does not return (or returns null) are filled from them, so that the SQL
    re-applied in DuckDB still matches the pushed-down predicate.
    """
    filters = filters or {}
    if text is None and not isinstance(body, str):
        check_errors(body, list(extra_error_rules or []) + list(table.response.errors))
    root_body = body
    scopes = row_scopes(table, body, text=text)
    filter_cols = {f.column: f for f in table.filters}
    rows: list[dict[str, Any]] = []
    for sc in scopes:
        root = sc.pop("$root", root_body)
        row: dict[str, Any] = {}
        for col in table.columns:
            f = filter_cols.get(col.name)
            if col.filter_only:
                fv = filters.get(f.key) if f else None
                row[col.name] = coerce(fv if not isinstance(fv, list) else None, col.type)
                continue
            try:
                row[col.name] = _column_value(col, root, sc, filters)
            except (jsonpath.PathError, TransformError) as exc:
                raise TransformError(f"{table.name}.{col.name}: {exc}") from exc
            if row[col.name] is None and f is not None:
                fv = filters.get(f.key)
                if fv is not None and not isinstance(fv, (list, dict)):
                    row[col.name] = coerce(fv, col.type)
        if table.response.dynamic:
            node = _resolve(table.response.dynamic, root, sc)
            flat: dict[str, Any] = {}
            _flatten(node, table.response.dynamic_prefix.rstrip("_"), flat, table.response.dynamic_max_columns)
            for k, v in flat.items():
                if k not in row:
                    row[k] = json.dumps(v, default=str) if isinstance(v, (list, dict)) else v
        rows.append(row)
    return rows


def zero_fill(rows: list[dict[str, Any]], ts_col: str, value_col: str, step_s: int,
              start: datetime, end: datetime, group_cols: list[str]) -> list[dict[str, Any]]:
    """Insert zero rows for empty buckets: APIs omit them, which makes a burst look like it ended
    at its peak (OpsIntelligence 3.9.3)."""
    if not rows or step_s <= 0:
        return rows
    step = timedelta(seconds=step_s)
    groups: dict[tuple[Any, ...], dict[datetime, dict[str, Any]]] = {}
    for r in rows:
        ts = r.get(ts_col)
        if not isinstance(ts, datetime):
            return rows
        key = tuple(r.get(g) for g in group_cols)
        groups.setdefault(key, {})[ts] = r
    out: list[dict[str, Any]] = []
    for key, by_ts in groups.items():
        template = next(iter(by_ts.values()))
        first = min(by_ts)
        t = first
        while t - step >= start:
            t -= step
        while t <= end:
            if t in by_ts:
                out.append(by_ts[t])
            else:
                filler = {k: (None if k not in group_cols else template.get(k)) for k in template}
                filler[ts_col] = t
                filler[value_col] = 0
                out.append(filler)
            t += step
        for ts, r in by_ts.items():
            if ts > end or ts < start:
                out.append(r)
    return out
