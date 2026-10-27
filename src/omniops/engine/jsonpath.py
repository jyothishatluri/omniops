"""A small, fast JSONPath subset used by manifests.

Supported syntax:
    $                  response root
    $.a.b              object keys
    $.a[0] / $.a[-1]   array index
    $.a[*] / $.a.*     iterate array elements / object values
    $['k.with.dots']   quoted keys (single or double quotes)
    a.b                relative to the current row element (no leading ``$``)
    @alias.x           an expanded element bound by an ``expand`` step
    [#i]               index of the current ``rows_path`` element (substituted by the caller)

``resolve`` returns a single value for paths without wildcards and a list for paths with them.
Missing keys resolve to ``None`` (or are skipped inside wildcard iteration): absent data is never
an exception, because real API payloads omit fields freely.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

_MISSING = object()
_IDENT = re.compile(r"[A-Za-z0-9_\-:$/]+")


class PathError(ValueError):
    pass


@lru_cache(maxsize=4096)
def parse(path: str) -> tuple[str, tuple[tuple[str, Any], ...]]:
    """Parse a path into (anchor, segments). anchor is '$', '' (relative) or '@alias'."""
    p = path.strip()
    if not p:
        raise PathError("empty path")
    anchor = ""
    i = 0
    if p[0] == "$":
        anchor = "$"
        i = 1
    elif p[0] == "@":
        m = _IDENT.match(p, 1)
        if not m:
            raise PathError(f"bad alias in {path!r}")
        anchor = "@" + m.group(0)
        i = m.end()
    segs: list[tuple[str, Any]] = []
    n = len(p)
    expect_key = anchor == ""  # relative paths start directly with a key
    while i < n:
        c = p[i]
        if c == ".":
            i += 1
            expect_key = True
            continue
        if c == "[":
            j = p.find("]", i)
            if j < 0:
                raise PathError(f"unclosed [ in {path!r}")
            inner = p[i + 1 : j].strip()
            i = j + 1
            if inner == "*":
                segs.append(("wild", None))
            elif inner.startswith("#"):
                segs.append(("var", inner[1:]))
            elif (inner.startswith("'") and inner.endswith("'")) or (
                inner.startswith('"') and inner.endswith('"')
            ):
                segs.append(("key", inner[1:-1]))
            else:
                try:
                    segs.append(("index", int(inner)))
                except ValueError as exc:
                    raise PathError(f"bad index [{inner}] in {path!r}") from exc
            expect_key = False
            continue
        if c == "*":
            segs.append(("wild", None))
            i += 1
            expect_key = False
            continue
        if not expect_key:
            raise PathError(f"unexpected {c!r} at {i} in {path!r}")
        j = i
        while j < n and p[j] not in ".[":
            j += 1
        segs.append(("key", p[i:j]))
        i = j
        expect_key = False
    return anchor, tuple(segs)


def has_wildcard(path: str) -> bool:
    return any(kind == "wild" for kind, _ in parse(path)[1])


def _step(value: Any, kind: str, arg: Any, variables: dict[str, int]) -> list[Any]:
    if kind == "key":
        if isinstance(value, dict):
            v = value.get(arg, _MISSING)
            return [] if v is _MISSING else [v]
        return []
    if kind == "index" or kind == "var":
        idx = variables.get(arg, None) if kind == "var" else arg
        if idx is None:
            raise PathError(f"unbound index variable #{arg}")
        if isinstance(value, list) and -len(value) <= idx < len(value):
            return [value[idx]]
        return []
    if kind == "wild":
        if isinstance(value, list):
            return list(value)
        if isinstance(value, dict):
            return list(value.values())
        return []
    raise PathError(f"unknown segment kind {kind}")


def resolve(
    path: str,
    root: Any,
    current: Any = None,
    aliases: dict[str, Any] | None = None,
    variables: dict[str, int] | None = None,
) -> Any:
    """Resolve ``path``. Single value (or None) without wildcards, list with wildcards."""
    anchor, segs = parse(path)
    if anchor == "$":
        start = root
    elif anchor == "":
        start = current if current is not None else root
    else:
        name = anchor[1:]
        if aliases is None or name not in aliases:
            raise PathError(f"unknown alias @{name} in {path!r}")
        start = aliases[name]
    variables = variables or {}
    values = [start]
    wild = False
    for kind, arg in segs:
        if kind == "wild":
            wild = True
        nxt: list[Any] = []
        for v in values:
            nxt.extend(_step(v, kind, arg, variables))
        values = nxt
        if not values:
            break
    if wild:
        return values
    return values[0] if values else None


def resolve_list(path: str, root: Any, **kw: Any) -> list[Any]:
    """Resolve to a list: wildcard results as-is; a single list value as-is; scalars wrapped."""
    v = resolve(path, root, **kw)
    if v is None:
        return []
    if has_wildcard(path):
        return v
    return v if isinstance(v, list) else [v]


def set_path(target: dict[str, Any], dotted: str, value: Any) -> None:
    """Set ``a.b.c`` inside nested dicts (creating them). Used for body.<path> pagination params."""
    parts = dotted.split(".")
    cur = target
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value
