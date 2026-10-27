"""Sandboxed Jinja templating for manifests.

A template string that is exactly ``{{ expr }}`` renders to the expression's native Python value
(int, list, dict, None) so JSON bodies keep their types. Anything else renders to a string.
Values that render to ``None`` are dropped from dicts, which is how optional parameters vanish.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any

from jinja2 import StrictUndefined, Undefined
from jinja2.sandbox import SandboxedEnvironment

from omniops.engine import timeutil

_NATIVE_RE = re.compile(r"^\s*\{\{(?P<expr>(?:(?!\{\{|\}\}).)+)\}\}\s*$", re.S)


class _LenientUndefined(Undefined):
    """Missing attributes render as None/empty instead of raising: optional filters are common."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return self

    def __getitem__(self, key: Any) -> Any:
        return self


def _dt(v: Any) -> datetime:
    return timeutil.to_datetime(v)


def _quote(v: Any) -> str:
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _squote(v: Any) -> str:
    s = str(v).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{s}'"


_LUCENE_SPECIAL = re.compile(r'([+\-=&|><!(){}\[\]^"~*?:\\/ ])')


def _tilde_quote(v: Any) -> str:
    """Dynatrace selector quoting: "value" with ~ escaping ~ and \"."""
    return '"' + str(v).replace("~", "~~").replace('"', '~"') + '"'


def _lucene_escape(v: Any) -> str:
    return _LUCENE_SPECIAL.sub(r"\\\1", str(v))


def _label_escape(v: Any) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _regex_escape(v: Any) -> str:
    return re.escape(str(v))


def _as_list(v: Any) -> list[Any]:
    if v is None or isinstance(v, Undefined):
        return []
    if isinstance(v, (list, tuple, set)):
        return list(v)
    return [v]


def _format_each(v: Any, fmt: str) -> list[str]:
    return [fmt.replace("{}", str(x)) for x in _as_list(v)]


def _join_or(v: Any, fmt: str = "{}", sep: str = " OR ", wrap: bool = True) -> str:
    items = _format_each(v, fmt)
    if not items:
        return ""
    s = sep.join(items)
    return f"({s})" if wrap and len(items) > 1 else s


def _seconds(v: Any) -> int:
    if isinstance(v, timedelta):
        return int(v.total_seconds())
    return int(timeutil.parse_duration(v).total_seconds())


def _b64(v: Any) -> str:
    return base64.b64encode(str(v).encode()).decode()


def _kv_list(v: Any, sep: str = ",", kv: str = "=", key_name: str = "Name", value_name: str = "Value") -> list[dict[str, str]]:
    """'a=1,b=2' (or a dict, or a list of 'k=v') -> [{"Name": "a", "Value": "1"}, ...] (AWS dimensions)."""
    if v is None or isinstance(v, Undefined) or v == "":
        return []
    if isinstance(v, dict):
        pairs = list(v.items())
    else:
        items = v if isinstance(v, (list, tuple)) else str(v).split(sep)
        pairs = []
        for it in items:
            k, _, val = str(it).partition(kv)
            if k.strip():
                pairs.append((k.strip(), val.strip()))
    return [{key_name: str(k), value_name: str(val)} for k, val in pairs]


def _compact(v: Any) -> Any:
    """Drop None / empty-string entries from a list or dict."""
    if isinstance(v, dict):
        return {k: x for k, x in v.items() if x is not None and x != ""}
    if isinstance(v, (list, tuple)):
        return [x for x in v if x is not None and x != ""]
    return v


def _make_env(strict: bool) -> SandboxedEnvironment:
    env = SandboxedEnvironment(
        undefined=StrictUndefined if strict else _LenientUndefined,
        autoescape=False,
        keep_trailing_newline=False,
    )
    env.filters.update(
        {
            "epoch_s": lambda v: timeutil.fmt_epoch_s(_dt(v)),
            "epoch_ms": lambda v: timeutil.fmt_epoch_ms(_dt(v)),
            "epoch_us": lambda v: timeutil.fmt_epoch_us(_dt(v)),
            "epoch_ns": lambda v: timeutil.fmt_epoch_ns(_dt(v)),
            "rfc3339": lambda v: timeutil.fmt_rfc3339(_dt(v)),
            "rfc3339_ms": lambda v: timeutil.fmt_rfc3339_ms(_dt(v)),
            "iso": lambda v: _dt(v).isoformat(),
            "strftime": lambda v, fmt: _dt(v).strftime(fmt),
            "prom_duration": timeutil.fmt_prom_duration,
            "seconds": _seconds,
            "quote": _quote,
            "squote": _squote,
            "tilde_quote": _tilde_quote,
            "lucene_escape": _lucene_escape,
            "label_escape": _label_escape,
            "regex_escape": _regex_escape,
            "as_list": _as_list,
            "format_each": _format_each,
            "join_or": _join_or,
            "b64": _b64,
            "compact": _compact,
            "kv_list": _kv_list,
            "json": lambda v: json.dumps(v, separators=(",", ":"), default=str),
        }
    )
    env.globals.update({"now_utc": lambda: datetime.now(timeutil.UTC)})
    return env


_ENV = _make_env(strict=False)


@lru_cache(maxsize=8192)
def _compile_template(src: str):  # type: ignore[no-untyped-def]
    return _ENV.from_string(src)


@lru_cache(maxsize=8192)
def _compile_expr(src: str):  # type: ignore[no-untyped-def]
    return _ENV.compile_expression(src, undefined_to_none=True)


def render_value(template: Any, ctx: dict[str, Any]) -> Any:
    """Render a template value recursively (str / dict / list); other types pass through."""
    if isinstance(template, str):
        if "{{" not in template and "{%" not in template:
            return template
        m = _NATIVE_RE.match(template)
        if m and "{%" not in template:
            val = _compile_expr(m.group("expr").strip())(**ctx)
            if isinstance(val, Undefined):
                return None
            return val
        return _compile_template(template).render(**ctx)
    if isinstance(template, dict):
        out: dict[str, Any] = {}
        for k, v in template.items():
            rk = render_value(k, ctx) if isinstance(k, str) else k
            rv = render_value(v, ctx)
            if rv is None:
                continue
            out[rk] = rv
        return out
    if isinstance(template, list):
        rendered = [render_value(v, ctx) for v in template]
        return [v for v in rendered if v is not None]
    return template


def evaluate(expr: str, ctx: dict[str, Any]) -> Any:
    """Evaluate a bare Jinja expression (poll conditions, ``when`` guards)."""
    val = _compile_expr(expr.strip().removeprefix("{{").removesuffix("}}").strip())(**ctx)
    return None if isinstance(val, Undefined) else val
