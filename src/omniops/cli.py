"""omniops command line.

    omniops manifest validate [FILE ...]   schema-validate manifests (all bundled ones by default)
    omniops manifest test [SOURCE ...]     offline checks: render, fixtures, example SQL
    omniops manifest render TABLE [-f k=v] show the HTTP request(s) a table would send
    omniops catalog [QUERY]                list configured tables (search by keyword)
    omniops describe TABLE                 describe a table the way the model sees it
    omniops sql "SELECT ..."               run SQL against the configured sources
    omniops selftest                       live boot-time smoke queries for configured sources
    omniops serve                          run the Slack app (Socket Mode)
    omniops ask "question"                 one-shot question through the Slack simulator
    omniops investigate --alert FILE       investigate an alert payload through the Slack simulator
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from rich.console import Console

console = Console()


def _cmd_manifest_validate(args: argparse.Namespace) -> int:
    from omniops.engine.loader import ManifestError, load_all_manifests, load_manifest_file

    if args.files:
        bad = 0
        for f in args.files:
            try:
                m = load_manifest_file(Path(f))
                console.print(f"[green]ok[/] {f}: {m.source} ({len(m.tables)} tables)")
            except Exception as exc:  # noqa: BLE001
                bad += 1
                console.print(f"[red]FAIL[/] {f}: {exc}")
        return 1 if bad else 0
    try:
        manifests, _ = load_all_manifests(strict=True)
    except ManifestError as exc:
        console.print(f"[red]FAIL[/]\n{exc}")
        return 1
    total = sum(len(m.tables) for m in manifests.values())
    console.print(f"[green]ok[/] {len(manifests)} sources, {total} tables")
    return 0


def _cmd_manifest_test(args: argparse.Namespace) -> int:
    from omniops.engine.loader import load_all_manifests
    from omniops.engine.manifest_check import check_manifest

    manifests, errors = load_all_manifests(strict=False)
    wanted = set(args.sources or [])
    failures = 0
    for e in errors:
        console.print(f"[red]LOAD FAIL[/] {e}")
        # Only a selected source's load error fails the run; others are reported.
        if not wanted or any(f"/{s}.yaml" in e or f" {s} " in e or f"{s!r}" in e for s in wanted):
            failures += 1
    missing = [s for s in wanted if s not in manifests]
    for s in missing:
        console.print(f"[red]FAIL[/] source {s!r} did not load")
    failures += len(missing)
    selected = [manifests[s] for s in args.sources if s in manifests] if args.sources else list(manifests.values())
    for m in selected:
        results = check_manifest(m, manifests)
        for r in results:
            if not r.ok:
                failures += 1
                console.print(f"[red]FAIL[/] {r.source}.{r.table} [{r.check}] {r.detail}")
            elif args.verbose:
                console.print(f"[green]ok[/]   {r.source}.{r.table} [{r.check}] {r.detail[:160]}")
                if args.show_rows and r.rows:
                    text = json.dumps(r.rows, default=str, indent=1)
                    console.print(text if len(text) <= 2000 else text[:2000] + " ...[truncated]", markup=False)
        n_ok = sum(r.ok for r in results)
        console.print(f"{m.source}: {n_ok}/{len(results)} checks passed")
    return 1 if failures else 0


def _parse_kv(items: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for it in items or []:
        k, _, v = it.partition("=")
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def _cmd_manifest_render(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime, timedelta

    from omniops.engine import http
    from omniops.engine.loader import load_all_manifests, table_index
    from omniops.engine.manifest_check import _dummy_runtime, sample_filters

    manifests, _ = load_all_manifests(strict=False)
    idx = table_index(manifests)
    if args.table not in idx:
        console.print(f"[red]unknown table {args.table}[/]")
        return 1
    m, t = idx[args.table]
    rt = _dummy_runtime(m)
    end = datetime.now(UTC).replace(microsecond=0)
    ctx = {"filters": {**sample_filters(t), **_parse_kv(args.filter)},
           "time": http.time_ctx(end - timedelta(hours=1), end), "limit": 100,
           "page": {"offset": 0, "number": 1, "size": t.pagination.page_size}, "captures": {}}
    for step in t.all_steps:
        rr = http.render_request(rt, step, ctx)
        console.print_json(json.dumps(rr.__dict__, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv(override=False)
    p = argparse.ArgumentParser(prog="omniops", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    mp = sub.add_parser("manifest", help="manifest tools")
    msub = mp.add_subparsers(dest="mcmd", required=True)
    v = msub.add_parser("validate")
    v.add_argument("files", nargs="*")
    v.set_defaults(func=_cmd_manifest_validate)
    t = msub.add_parser("test")
    t.add_argument("sources", nargs="*")
    t.add_argument("-v", "--verbose", action="store_true")
    t.add_argument("--show-rows", action="store_true")
    t.set_defaults(func=_cmd_manifest_test)
    r = msub.add_parser("render")
    r.add_argument("table")
    r.add_argument("-f", "--filter", action="append")
    r.set_defaults(func=_cmd_manifest_render)

    try:
        from omniops import cli_runtime

        cli_runtime.register(sub)
    except ImportError:
        pass

    args = p.parse_args(argv)
    rc = args.func(args)
    if asyncio.iscoroutine(rc):
        rc = asyncio.run(rc)
    return int(rc or 0)


if __name__ == "__main__":
    sys.exit(main())
