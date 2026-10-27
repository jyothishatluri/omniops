"""Load, merge and validate source manifests; decide which sources are configured.

- Manifests live in ``omniops/sources/manifests/*.yaml`` (plus any directory in
  ``OMNIOPS_MANIFEST_DIRS``). Reusable API shapes live in ``sources/templates``.
- ``extends: <template>`` copies a template (e.g. the Prometheus HTTP API) and renames its tables
  to the new source's prefix: Mimir, Thanos, VictoriaMetrics, Chronosphere and Grafana Cloud all
  reuse one Prometheus definition.
- A source is *available* only when all its ``required_env`` are set; unavailable sources are
  removed from the catalog entirely so the model never plans calls that will fail.
"""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from omniops.engine.manifest import SourceManifest, TableSpec

PACKAGE_SOURCES = "omniops.sources"
# source id -> template source ids it extends (nearest first); used for fixture fallback.
TEMPLATE_CHAIN: dict[str, list[str]] = {}
_APPENDABLE = {"pitfalls", "env", "errors", "required_env", "categories"}


class ManifestError(ValueError):
    pass


def _package_dir(sub: str) -> Path:
    return Path(str(resources.files(PACKAGE_SOURCES).joinpath(sub)))


def manifest_dirs() -> list[Path]:
    dirs = [_package_dir("manifests")]
    extra = os.environ.get("OMNIOPS_MANIFEST_DIRS", "")
    dirs += [Path(p).expanduser() for p in extra.split(os.pathsep) if p.strip()]
    return [d for d in dirs if d.exists()]


def fixtures_dir() -> Path:
    return _package_dir("fixtures")


def _read_yaml(path: Path) -> dict[str, Any]:
    with open(path) as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ManifestError(f"{path}: manifest must be a mapping")
    return data


def _load_template(name: str) -> dict[str, Any]:
    path = _package_dir("templates") / f"{name}.yaml"
    if not path.exists():
        raise ManifestError(f"unknown template {name!r} (looked in {path.parent})")
    return _read_yaml(path)


def _rename_tables(raw: dict[str, Any], old_prefix: str, new_prefix: str) -> None:
    def ren(name: str) -> str:
        return new_prefix + name[len(old_prefix):] if name.startswith(old_prefix) else name

    names = sorted((t["name"] for t in raw.get("tables", [])), key=len, reverse=True)

    def ren_text(text: str) -> str:
        # Rename references to sibling tables in prose too, longest names first.
        for n in names:
            text = re.sub(rf"\b{re.escape(n)}\b", ren(n), text)
        return text

    for t in raw.get("tables", []):
        for key in ("description", "title", "sort_hint"):
            if isinstance(t.get(key), str):
                t[key] = ren_text(t[key])
        t["pitfalls"] = [ren_text(p) for p in t.get("pitfalls", [])]
        for ex in t.get("examples", []):
            ex["sql"] = ren_text(ex["sql"])
            ex["description"] = ren_text(ex.get("description", ""))
        for te in t.get("tests", []):
            te["sql"] = ren_text(te["sql"])
    for t in raw.get("tables", []):
        t["name"] = ren(t["name"])
    raw["pitfalls"] = [ren_text(p) for p in raw.get("pitfalls", [])]
    for role, binding in list(raw.get("roles", {}).items()):
        for b in binding if isinstance(binding, list) else [binding]:
            b["table"] = ren(b["table"])


def merge_extends(raw: dict[str, Any]) -> dict[str, Any]:
    """Resolve ``extends``: template first, then the manifest's own keys on top.

    Lists of tables merge by name (the manifest may override or add tables); ``drop_tables``
    removes template tables that the platform does not support.
    """
    parent_name = raw.get("extends")
    if not parent_name:
        return raw
    parent = copy.deepcopy(_load_template(parent_name))
    parent_source = parent["source"]
    parent = merge_extends(parent)
    TEMPLATE_CHAIN[raw["source"]] = [parent_source] + TEMPLATE_CHAIN.get(parent_source, [])
    old_prefix = parent_source + "_"
    new_prefix = raw["source"] + "_"
    _rename_tables(parent, old_prefix, new_prefix)
    merged = {**parent, **{k: v for k, v in raw.items() if k not in {"tables", "roles", "drop_tables", "append"}}}
    # ``append:`` extends the template's lists instead of replacing them.
    for key, extra in (raw.get("append") or {}).items():
        if key not in _APPENDABLE:
            raise ManifestError(f"append: only supports {sorted(_APPENDABLE)}, not {key!r}")
        merged[key] = list(merged.get(key) or []) + list(extra or [])
    tables = {t["name"]: t for t in parent.get("tables", [])}
    for name in raw.get("drop_tables", []):
        tables.pop(name, None)
    for t in raw.get("tables", []):
        tables[t["name"]] = {**tables.get(t["name"], {}), **t}
    merged["tables"] = list(tables.values())
    roles = {**parent.get("roles", {}), **raw.get("roles", {})}
    merged["roles"] = {r: b for r, b in roles.items()
                       if all(x["table"] in tables for x in (b if isinstance(b, list) else [b]))}
    merged.pop("extends", None)
    merged.pop("drop_tables", None)
    merged.pop("append", None)
    return merged


def parse_manifest(raw: dict[str, Any], origin: str = "<memory>") -> SourceManifest:
    try:
        return SourceManifest.model_validate(merge_extends(copy.deepcopy(raw)))
    except ValidationError as exc:
        raise ManifestError(f"{origin}: {exc}") from exc


_PARSED: dict[tuple[str, float, float], SourceManifest] = {}


def _templates_mtime() -> float:
    d = _package_dir("templates")
    return max((p.stat().st_mtime for p in d.glob("*.yaml")), default=0.0)


def load_manifest_file(path: Path) -> SourceManifest:
    """Parse + validate one manifest; cached by (path, mtime, templates mtime) since parsing is slow."""
    key = (str(path), path.stat().st_mtime, _templates_mtime())
    m = _PARSED.get(key)
    if m is None:
        m = parse_manifest(_read_yaml(path), str(path))
        _PARSED[key] = m
    return m


def load_all_manifests(strict: bool = True) -> tuple[dict[str, SourceManifest], list[str]]:
    """Load every manifest. Returns (manifests by source id, errors)."""
    out: dict[str, SourceManifest] = {}
    errors: list[str] = []
    for d in manifest_dirs():
        for path in sorted(d.glob("*.yaml")):
            try:
                m = load_manifest_file(path)
            except (ManifestError, yaml.YAMLError, OSError) as exc:
                errors.append(str(exc))
                continue
            if m.source in out:
                errors.append(f"{path}: duplicate source id {m.source!r}")
                continue
            out[m.source] = m
    seen: dict[str, str] = {}
    for m in out.values():
        for t in m.tables:
            if t.name in seen:
                errors.append(f"table {t.name!r} defined by both {seen[t.name]} and {m.source}")
            seen[t.name] = m.source
    if strict and errors:
        raise ManifestError("\n".join(errors))
    return out, errors


@dataclass
class SourceConfig:
    """One configured source instance (from omniops.yaml or auto-detected from env)."""

    use: str
    name: str = ""
    env: dict[str, str] = field(default_factory=dict)
    base_url: str | None = None
    enabled: bool = True


def resolve_env(manifest: SourceManifest, overrides: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()}
    for var in manifest.env:
        if var.default is not None and not env.get(var.name):
            env[var.name] = var.default
    env.update(overrides or {})
    return env


def missing_env(manifest: SourceManifest, env: dict[str, str]) -> list[str]:
    return [v for v in manifest.required_env if not env.get(v)]


def table_index(manifests: dict[str, SourceManifest]) -> dict[str, tuple[SourceManifest, TableSpec]]:
    return {t.name: (m, t) for m in manifests.values() for t in m.tables}
