"""Generate docs/SOURCES.md from the bundled manifests (never hand-edit that file).

    uv run --no-sync python scripts/gen_sources_doc.py
"""

from __future__ import annotations

from pathlib import Path

from omniops.engine.loader import load_all_manifests
from omniops.engine.manifest import SourceManifest, TableSpec

OUT = Path(__file__).resolve().parents[1] / "docs" / "SOURCES.md"


def _endpoint(t: TableSpec) -> str:
    if t.python:
        return f"python: `{t.python.split(':')[-1]}`"
    parts = []
    for s in t.all_steps:
        path = (s.url or s.path or "").replace("|", "\\|").replace("\n", " ")
        if len(path) > 90:
            path = path[:87] + "..."
        parts.append(f"{s.method} `{path}`")
    return " → ".join(parts)


def _cell(text: str, limit: int = 140) -> str:
    s = " ".join((text or "").split()).replace("|", "\\|")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _secret_env(m: SourceManifest) -> str:
    names = list(dict.fromkeys(m.required_env + [e.name for e in m.env if e.secret]))
    return ", ".join(f"`{n}`" for n in names) or "—"


def main() -> None:
    manifests, errors = load_all_manifests(strict=False)
    ms = sorted(manifests.values(), key=lambda m: (m.vendor or m.display_name).lower() + m.source)
    total = sum(len(m.tables) for m in ms)
    lines = [
        "# Supported sources",
        "",
        "Generated from `src/omniops/sources/manifests/*.yaml` by `scripts/gen_sources_doc.py`; do not edit by hand.",
        "",
        f"**{len(ms)} sources, {total} SQL tables.** A source is enabled when its required environment variables "
        "are set (see `.env.example`). Every table wraps a documented REST endpoint; the model discovers tables "
        "with `search_catalog`, reads `describe_table`, and queries them with SQL.",
        "",
        "## Overview",
        "",
        "| Platform | Source id | Tables | Roles | Required / secret env | Notes |",
        "|---|---|---:|---|---|---|",
    ]
    for m in ms:
        notes = f"[notes](sources/{m.source}.md)" if (OUT.parent / "sources" / f"{m.source}.md").exists() else ""
        api = f"[API docs]({m.docs_url})" if m.docs_url else ""
        lines.append(f"| {m.display_name} | `{m.source}` | {len(m.tables)} | {', '.join(sorted(m.roles)) or '—'} | "
                     f"{_secret_env(m)} | {' · '.join(x for x in (api, notes) if x)} |")
    for m in ms:
        lines += ["", f"## {m.display_name} (`{m.source}`)", "", _cell(m.description, 400), "",
                  "| SQL table | Kind | HTTP request | API reference |", "|---|---|---|---|"]
        for t in m.tables:
            ref = f"[docs]({t.docs_url})" if t.docs_url else ""
            lines.append(f"| `{t.name}` | {t.kind} | {_endpoint(t)} | {ref} |")
    if errors:
        lines += ["", "## Manifests that failed to load", ""] + [f"- {_cell(e, 300)}" for e in errors]
    OUT.write_text("\n".join(lines) + "\n")
    print(f"wrote {OUT} ({len(ms)} sources, {total} tables)")


if __name__ == "__main__":
    main()
