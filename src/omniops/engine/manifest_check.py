"""Offline manifest checks: schema, request rendering, fixtures, example SQL.

Run with ``omniops manifest test [source ...]``. These checks need no credentials:
- every table's request(s) render with sample filters and a sample time window;
- every fixture (a documented or recorded response) transforms into the expected rows;
- every example SQL parses and references only declared tables and columns.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from omniops.engine import http, timeutil, transform
from omniops.engine.loader import fixtures_dir, table_index
from omniops.engine.manifest import SourceManifest, TableSpec


@dataclass
class CheckResult:
    source: str
    table: str
    check: str
    ok: bool
    detail: str = ""
    rows: list[dict[str, Any]] = field(default_factory=list)


def sample_filters(table: TableSpec) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in table.filters:
        if f.examples:
            out[f.key] = f.examples[0]
        elif f.default is not None:
            out[f.key] = f.default
        elif f.required:
            out[f.key] = {"BIGINT": 1, "DOUBLE": 1.0, "BOOLEAN": True}.get(f.type.upper(), "example")
    return out


def _dummy_runtime(m: SourceManifest) -> http.SourceRuntime:
    env = {v.name: (v.default or f"<{v.name}>") for v in m.env}
    for name in m.required_env:
        env.setdefault(name, f"<{name}>")
    rt = http.SourceRuntime(manifest=m, env=env)
    from omniops.engine import templating

    rt.base_url = str(templating.render_value(m.base_url, rt.template_ctx()) or "http://example.invalid")
    return rt


def check_render(m: SourceManifest, table: TableSpec) -> CheckResult:
    if table.python:
        return CheckResult(m.source, table.name, "render", True, f"python fetcher {table.python}")
    rt = _dummy_runtime(m)
    end = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    start = end - timedelta(hours=1)
    ctx: dict[str, Any] = {
        "filters": sample_filters(table),
        "time": http.time_ctx(start, end),
        "limit": 100,
        "page": {"offset": 0, "number": 1, "size": table.pagination.page_size},
        "captures": {"job_id": "JOB123", "query_id": "Q123", "sid": "SID1", "token": "T"},
    }
    try:
        rendered = [http.render_request(rt, s, ctx) for s in table.all_steps]
    except Exception as exc:  # noqa: BLE001 - surface any template error
        return CheckResult(m.source, table.name, "render", False, f"{type(exc).__name__}: {exc}")
    detail = "; ".join(f"{r.method} {r.url} params={json.dumps(r.params, default=str)[:200]}" for r in rendered)
    return CheckResult(m.source, table.name, "render", True, detail)


def _fixture_path(source: str, file: str) -> Path:
    from omniops.engine.loader import TEMPLATE_CHAIN

    path = Path(file)
    if path.is_absolute():
        return path
    for src in [source] + TEMPLATE_CHAIN.get(source, []):
        cand = fixtures_dir() / src / file
        if cand.exists():
            return cand
    return fixtures_dir() / source / file


def _load_fixture(source: str, file: str) -> tuple[Any, str | None]:
    path = _fixture_path(source, file)
    raw = path.read_text()
    if path.suffix in {".txt", ".log"}:
        return raw, raw
    if path.suffix == ".ndjson":
        return [json.loads(line) for line in raw.splitlines() if line.strip()], None
    return json.loads(raw), None


def check_fixtures(m: SourceManifest, table: TableSpec) -> list[CheckResult]:
    results = []
    for fx in table.fixtures:
        name = f"fixture:{fx.file}"
        try:
            body, text = _load_fixture(m.source, fx.file)
            rows = transform.build_rows(table, body, filters={**sample_filters(table), **fx.filters}, text=text,
                                        extra_error_rules=m.errors)
        except Exception as exc:  # noqa: BLE001
            if fx.expect_error and fx.expect_error in str(exc):
                results.append(CheckResult(m.source, table.name, name, True, f"raised as expected: {str(exc)[:120]}"))
            else:
                results.append(CheckResult(m.source, table.name, name, False, f"{type(exc).__name__}: {exc}"))
            continue
        if fx.expect_error:
            results.append(CheckResult(m.source, table.name, name, False,
                                       f"expected an error containing {fx.expect_error!r}, got {len(rows)} rows"))
            continue
        problems = []
        if fx.expect_rows is not None and len(rows) != fx.expect_rows:
            problems.append(f"expected {fx.expect_rows} rows, got {len(rows)}")
        if fx.expect_min_rows is not None and len(rows) < fx.expect_min_rows:
            problems.append(f"expected >= {fx.expect_min_rows} rows, got {len(rows)}")
        if fx.expect_columns:
            if not rows:
                problems.append("no rows to check expect_columns against")
            else:
                first = rows[0]
                for col, want in fx.expect_columns.items():
                    got = first.get(col)
                    if isinstance(want, dict) and want.get("not_null"):
                        if got is None:
                            problems.append(f"{col} is null")
                    elif isinstance(got, datetime):
                        try:
                            # Compare instants (any offset spelling) at the precision the expectation is written in.
                            w = timeutil.to_datetime(want)
                            same = w == got or (w.microsecond == 0 and w == got.replace(microsecond=0))
                        except (timeutil.TimeParseError, ValueError):
                            same = False
                        if not same:
                            problems.append(f"{col}: expected {want}, got {got.isoformat()}")
                    elif got != want and not (isinstance(got, float) and isinstance(want, (int, float))
                                              and abs(got - want) < 1e-9):
                        problems.append(f"{col}: expected {want!r}, got {got!r}")
        results.append(CheckResult(m.source, table.name, name, not problems, "; ".join(problems) or
                                   f"{len(rows)} rows", rows[:3]))
    return results


def check_examples(m: SourceManifest, table: TableSpec, all_tables: dict[str, tuple[SourceManifest, TableSpec]]
                   ) -> list[CheckResult]:
    results = []
    for ex in table.examples + [type("T", (), {"sql": t.sql})() for t in table.tests]:  # type: ignore[misc]
        try:
            tree = sqlglot.parse_one(ex.sql, read="duckdb")
        except sqlglot.errors.ParseError as exc:
            results.append(CheckResult(m.source, table.name, "example_sql", False, f"parse error: {exc}"))
            continue
        problems = []
        aliases: dict[str, str] = {}
        ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
        for t in tree.find_all(exp.Table):
            if t.name in ctes or t.name.startswith("omni_"):
                continue
            if t.name not in all_tables:
                problems.append(f"unknown table {t.name}")
            else:
                aliases[t.alias_or_name] = t.name
        if len(aliases) == 1:
            (tname,) = set(aliases.values())
            spec = all_tables[tname][1]
            declared = {c.name for c in spec.columns}
            select_aliases = {a.alias for a in tree.find_all(exp.Alias)}
            for col in tree.find_all(exp.Column):
                if col.table and col.table not in aliases:
                    continue
                if col.name not in declared and col.name not in select_aliases and col.name != "*":
                    problems.append(f"column {col.name!r} not declared on {tname}")
        results.append(CheckResult(m.source, table.name, "example_sql", not problems,
                                   "; ".join(sorted(set(problems))) or ex.sql[:120]))
    return results


def check_manifest(m: SourceManifest, all_manifests: dict[str, SourceManifest]) -> list[CheckResult]:
    idx = table_index(all_manifests)
    out: list[CheckResult] = []
    for table in m.tables:
        out.append(check_render(m, table))
        out.extend(check_fixtures(m, table))
        out.extend(check_examples(m, table, idx))
        if not table.examples:
            out.append(CheckResult(m.source, table.name, "has_examples", False, "table has no example SQL"))
        if not table.fixtures and not table.python and table.kind != "write":
            out.append(CheckResult(m.source, table.name, "has_fixture", False, "table has no offline fixture"))
    return out
