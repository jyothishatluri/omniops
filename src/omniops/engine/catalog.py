"""The catalog: which sources are configured, and the self-describing table surface the model sees.

The model learns one convention (search -> describe -> SQL with required filters) instead of dozens
of tool contracts. Sources whose credentials are missing are removed entirely, so the model never
plans queries that cannot run (OpsIntelligence 3.12.4).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from omniops.engine import templating
from omniops.engine.http import SourceRuntime
from omniops.engine.loader import SourceConfig, _rename_tables, load_all_manifests, missing_env, resolve_env
from omniops.engine.manifest import RoleBinding, SourceManifest, TableSpec

_WORD = re.compile(r"[a-z0-9]+")


@dataclass
class CatalogEntry:
    runtime: SourceRuntime | None
    manifest: SourceManifest | None
    table: TableSpec
    computed: bool = False

    @property
    def source(self) -> str:
        return self.runtime.name if self.runtime else "omniops"


@dataclass
class Catalog:
    runtimes: dict[str, SourceRuntime] = field(default_factory=dict)
    entries: dict[str, CatalogEntry] = field(default_factory=dict)
    unavailable: dict[str, list[str]] = field(default_factory=dict)
    load_errors: list[str] = field(default_factory=list)
    allowed_tables: set[str] | None = None

    # -- construction ---------------------------------------------------------------------

    @classmethod
    def from_env(cls, config_path: str | None = None, only: list[str] | None = None) -> Catalog:
        manifests, errors = load_all_manifests(strict=False)
        cat = cls(load_errors=errors)
        configs = cls._read_config(config_path)
        if configs is None:
            configs = [SourceConfig(use=s, name=s) for s in manifests]
        for cfg in configs:
            if not cfg.enabled:
                continue
            if only and cfg.name not in only and cfg.use not in only:
                continue
            m = manifests.get(cfg.use)
            if m is None:
                cat.load_errors.append(f"config references unknown manifest {cfg.use!r}")
                continue
            env = resolve_env(m, cfg.env)
            miss = missing_env(m, env)
            if miss:
                cat.unavailable[cfg.name or m.source] = miss
                continue
            cat.add_runtime(m, env, cfg.name or m.source, cfg.base_url)
        from omniops.engine.computed import register_computed

        register_computed(cat)
        return cat

    @staticmethod
    def _read_config(path: str | None) -> list[SourceConfig] | None:
        path = path or os.environ.get("OMNIOPS_CONFIG")
        if not path:
            default = Path("omniops.yaml")
            if not default.exists():
                return None
            path = str(default)
        with open(path) as fh:
            raw = yaml.safe_load(fh) or {}
        out = []
        for item in raw.get("sources", []):
            out.append(SourceConfig(use=item["use"], name=item.get("name", item["use"]),
                                    env={k: str(v) for k, v in (item.get("env") or {}).items()},
                                    base_url=item.get("base_url"), enabled=item.get("enabled", True)))
        return out

    def add_runtime(self, m: SourceManifest, env: dict[str, str], name: str, base_url: str | None = None
                    ) -> SourceRuntime | None:
        """Register one configured instance of a manifest.

        A second instance of the same manifest (two Prometheus servers) would bring the same table
        names and silently replace the first one's tables, so its tables get an instance prefix
        (``prom_staging__prometheus_query_range``) and its role bindings point at its own tables.
        """
        if name in self.runtimes or any(t.name in self.entries for t in m.tables):
            m = _instance_manifest(m, name)
            clash = [t.name for t in m.tables if t.name in self.entries]
            if name in self.runtimes or clash:
                self.load_errors.append(f"source instance {name!r} ({m.source}) skipped: its name or tables "
                                        f"{clash[:3]} clash with an already configured instance; rename it")
                return None
        rt = SourceRuntime(manifest=m, env=env, instance=name)
        rt.base_url = base_url or str(templating.render_value(m.base_url, rt.template_ctx()) or "")
        self.runtimes[name] = rt
        for t in m.tables:
            self.entries[t.name] = CatalogEntry(rt, m, t)
        return rt

    def add_computed(self, table: TableSpec) -> None:
        self.entries[table.name] = CatalogEntry(None, None, table, computed=True)

    async def aclose(self) -> None:
        for rt in self.runtimes.values():
            await rt.aclose()

    # -- scoping --------------------------------------------------------------------------

    def scoped(self, tables: set[str] | None) -> Catalog:
        """A view limited to some tables (specialist agents get a scope line, 3.10.4)."""
        view = Catalog(self.runtimes, self.entries, self.unavailable, self.load_errors, tables)
        return view

    def visible(self) -> dict[str, CatalogEntry]:
        if self.allowed_tables is None:
            return self.entries
        return {k: v for k, v in self.entries.items() if k in self.allowed_tables}

    def get(self, name: str) -> CatalogEntry | None:
        return self.visible().get(name)

    def columns_map(self) -> dict[str, set[str]]:
        return {n: {c.name for c in e.table.columns} for n, e in self.visible().items()}

    # -- roles ----------------------------------------------------------------------------

    def role_bindings(self, role: str) -> list[tuple[SourceRuntime, RoleBinding]]:
        out = []
        for rt in self.runtimes.values():
            for b in rt.manifest.role_bindings(role):
                if b.table in self.visible():
                    out.append((rt, b))
        return out

    def roles_summary(self) -> dict[str, list[str]]:
        roles: dict[str, list[str]] = {}
        for rt in self.runtimes.values():
            for role in rt.manifest.roles:
                if any(b.table in self.visible() for b in rt.manifest.role_bindings(role)):
                    roles.setdefault(role, []).append(rt.name)
        return roles

    # -- model-facing views ---------------------------------------------------------------

    def search(self, query: str, limit: int = 15) -> list[dict[str, Any]]:
        words = _WORD.findall(query.lower())
        scored = []
        for name, e in self.visible().items():
            t = e.table
            hay = " ".join([name, t.title, t.description, t.kind, e.source,
                            " ".join(e.manifest.categories if e.manifest else []),
                            e.manifest.display_name if e.manifest else ""]).lower()
            score = 0.0
            for w in words:
                if w in name:
                    score += 3
                if w in hay:
                    score += 1 + hay.count(w) * 0.1
            if not words:
                score = 1
            if score > 0:
                scored.append((score, name, e))
        scored.sort(key=lambda x: (-x[0], x[1]))
        out = []
        for _, name, e in scored[:limit]:
            req = [f.column for f in e.table.filters if f.required]
            out.append({
                "table": name,
                "source": e.source,
                "kind": e.table.kind,
                "summary": " ".join(e.table.description.split())[:220],
                "required_filters": req + ([" | ".join(g) for g in e.table.require_one_of]),
            })
        return out

    def describe(self, name: str) -> str:
        e = self.get(name)
        if e is None:
            near = [r["table"] for r in self.search(name.replace("_", " "), limit=5)]
            raise KeyError(f"table {name!r} does not exist. Did you mean: {', '.join(near) or 'nothing similar'}? "
                           "Use search_catalog; never guess table names.")
        t = e.table
        lines = [f"TABLE {name}  (source: {e.source}, kind: {t.kind})", " ".join(t.description.split())]
        if t.sort_hint:
            lines.append(f"Sort: {t.sort_hint}")
        lines.append("COLUMNS:")
        filt = {f.column: f for f in t.filters}
        for c in t.columns:
            mark = ""
            if c.name in filt:
                f = filt[c.name]
                mark = f"  [FILTER{' REQUIRED' if f.required else ''} ops: {', '.join(f.ops)}"
                if f.examples:
                    mark += f"; e.g. {f.examples[0]!r}"
                mark += "]"
            if t.time and c.name == t.time.column:
                mark += f"  [TIME RANGE: default {t.time.default_lookback} / trigger window"
                if t.time.retention:
                    mark += f", retention {t.time.retention}"
                if t.time.max_window:
                    mark += f", max window {t.time.max_window}"
                mark += "]"
            fo = " (filter-only; not returned by the API)" if c.filter_only else ""
            desc = f" - {c.description}" if c.description else (f" - {filt[c.name].description}" if c.name in filt
                                                               and filt[c.name].description else "")
            lines.append(f"  {c.name} {c.type}{fo}{desc}{mark}")
        if t.require_one_of:
            lines.append("REQUIRES at least one of: " + "; ".join(" | ".join(g) for g in t.require_one_of))
        if e.table.response.dynamic:
            lines.append("Plus dynamic columns flattened from the payload (names vary; SELECT * to see them).")
        pitfalls = list(t.pitfalls) + (list(e.manifest.pitfalls) if e.manifest else [])
        if pitfalls:
            lines.append("PITFALLS:")
            lines += [f"  - {p}" for p in pitfalls]
        if e.manifest and e.manifest.query_languages:
            lines.append("QUERY LANGUAGE NOTES:")
            lines += [f"  {k}: {' '.join(v.split())}" for k, v in e.manifest.query_languages.items()]
        if t.examples:
            lines.append("VERIFIED QUERY SHAPES:" if any(x.verified for x in t.examples) else "EXAMPLE QUERIES:")
            for ex in sorted(t.examples, key=lambda x: not x.verified):
                lines.append(f"  -- {ex.description}" if ex.description else "  --")
                lines.append("  " + " ".join(ex.sql.split()))
        return "\n".join(lines)

    def sources_overview(self) -> str:
        lines = ["CONFIGURED SOURCES:"]
        for name, rt in self.runtimes.items():
            n = sum(1 for e in self.visible().values() if e.runtime is rt)
            if n:
                lines.append(f"  {name}: {rt.manifest.display_name} ({n} tables; {', '.join(rt.manifest.categories)})")
        computed = [n for n, e in self.visible().items() if e.computed]
        if computed:
            lines.append(f"  omniops computed tables: {', '.join(sorted(computed))}")
        roles = self.roles_summary()
        if roles:
            lines.append("ROLES: " + "; ".join(f"{r}: {', '.join(s)}" for r, s in sorted(roles.items())))
        return "\n".join(lines)


def _instance_manifest(m: SourceManifest, instance: str) -> SourceManifest:
    """A copy of ``m`` whose tables (and the role bindings and prose that name them) carry an
    instance prefix, the same rename ``extends`` applies to template tables."""
    prefix = re.sub(r"[^a-z0-9_]+", "_", instance.lower()).strip("_") or "instance"
    if not prefix[0].isalpha():
        prefix = "i_" + prefix
    raw = m.model_dump(by_alias=True)
    _rename_tables(raw, "", f"{prefix}__")
    tables = [TableSpec.model_validate(t) for t in raw["tables"]]
    roles = {r: ([RoleBinding.model_validate(x) for x in b] if isinstance(b, list) else RoleBinding.model_validate(b))
             for r, b in raw["roles"].items()}
    return m.model_copy(update={"tables": tables, "roles": roles, "pitfalls": raw["pitfalls"]})
