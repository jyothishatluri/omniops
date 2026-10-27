"""Runtime CLI commands: catalog, describe, sql, selftest, serve, ask, investigate.

Registered into ``omniops.cli`` (``cli_runtime.register(subparsers)``); a command may return a
coroutine, which ``cli.main`` runs.

Lessons encoded (OpsIntelligence retrospective):
- D6 / 4.1: ``selftest`` runs every configured source's manifest smoke queries live and fails loudly
  (a FAIL line per test, a banner, nonzero exit). Zero configured sources or zero tests run is a
  failure too (loud zeros), and sources missing credentials are listed by env var *name* only.
- 3.3: ``sql --at/--window`` pins ``now()`` to one anchor, exactly like a run's trigger window.
- D1 / 4.1: ``ask`` and ``investigate`` enter through the Slack simulator, i.e. the production Bolt
  handlers, never a side door into the agent.
- D23: ``serve`` (Socket Mode) stops taking events on SIGTERM/SIGINT, drains in-flight runs for the
  slowest run budget, then interrupts what is left (each run posts its notice).
- 3.10.4: SQL errors print the engine's lesson, the same text the model sees.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console

if TYPE_CHECKING:
    from omniops.agent.context import Deps
    from omniops.engine.catalog import Catalog, CatalogEntry
    from omniops.engine.manifest import TableTest
    from omniops.engine.timeutil import TimeAnchor

log = logging.getLogger("omniops.cli")
console = Console(soft_wrap=True)                 # results and lessons are never re-wrapped
SELFTEST_TIMEOUT_S = 90.0
SELFTEST_CONCURRENCY = 4


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the runtime commands to ``omniops``'s subparsers."""
    p = sub.add_parser("catalog", help="list configured tables (search by keyword)")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=_cmd_catalog)

    p = sub.add_parser("describe", help="describe a table the way the model sees it")
    p.add_argument("table")
    p.set_defaults(func=_cmd_describe)

    p = sub.add_parser("sql", help="run SQL against the configured sources")
    p.add_argument("sql")
    p.add_argument("--at", help="pin now() to this time (ISO 8601 or epoch), like an alert's trigger time")
    p.add_argument("--window", help="analysis window ending at --at (or now), e.g. 1h")
    p.set_defaults(func=_cmd_sql)

    p = sub.add_parser("selftest", help="live smoke queries from the manifests for configured sources")
    p.add_argument("--source", action="append", help="only this source (repeatable)")
    p.set_defaults(func=_cmd_selftest)

    p = sub.add_parser("serve", help="run the Slack app (Socket Mode: SLACK_BOT_TOKEN + SLACK_APP_TOKEN)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=_cmd_serve)

    p = sub.add_parser("ask", help="one-shot question through the Slack simulator (DM to the bot)")
    p.add_argument("question")
    p.add_argument("--approve-writes", action="store_true", help="approve staged writes as the simulated user")
    p.add_argument("--timeout", type=float, help="seconds to wait for the answer (default: chat budget + grace)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=_cmd_ask)

    p = sub.add_parser("investigate", help="post an alert into the simulated alert channel and investigate it")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--alert-file", type=Path, help="JSON: a Slack message event (or event_callback envelope)")
    src.add_argument("--text", help="alert text posted by an integration (or by a person with --human)")
    p.add_argument("--human", action="store_true", help="post --text as a person's incident report")
    p.add_argument("--approve-writes", action="store_true", help="approve staged writes as the simulated user")
    p.add_argument("--timeout", type=float, help="seconds to wait (default: investigation budget + grace)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=_cmd_investigate)


# ---------------------------------------------------------------------------------------------
# Shared setup
# ---------------------------------------------------------------------------------------------


def make_deps(*, sources: list[str] | None = None, memory: bool = True) -> Deps:
    """Process-wide dependencies from the environment: catalog, LLM client, settings, memory."""
    from omniops.agent.context import Deps
    from omniops.engine.catalog import Catalog
    from omniops.llm import make_llm
    from omniops.settings import Settings

    settings = Settings.from_env()
    catalog = Catalog.from_env(only=sources)
    _report_catalog_gaps(catalog)
    store = None
    if memory:
        try:
            from omniops.agent.memory import MemoryStore

            Path(settings.memory_path).parent.mkdir(parents=True, exist_ok=True)
            store = MemoryStore(settings.memory_path)
        except Exception as exc:  # noqa: BLE001 - memory is optional; its absence is said out loud
            log.warning("long-term memory disabled: %s", exc)
    return Deps(catalog=catalog, llm=make_llm(settings), settings=settings, memory=store)


async def _close_deps(deps: Deps) -> None:
    await deps.catalog.aclose()
    if deps.memory is not None:
        deps.memory.close()


def _report_catalog_gaps(catalog: Catalog) -> None:
    if not catalog.runtimes:
        log.warning("no data source is configured: set a source's env vars (see .env.example)")
    for err in catalog.load_errors:
        log.warning("manifest load error: %s", err)


def _setup_logging(verbose: bool, default: int = logging.WARNING) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else default, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _anchor(at: str | None, window: str | None) -> TimeAnchor:
    from omniops.engine.timeutil import TimeAnchor, parse_duration, to_datetime

    now = to_datetime(at) if at else datetime.now(UTC).replace(microsecond=0)
    if window:
        return TimeAnchor(now=now, window_start=now - parse_duration(window), window_end=now, source="cli")
    return TimeAnchor(now=now, source="cli" if at else "wall_clock")


def _error_lines(exc: BaseException) -> str:
    lesson = getattr(exc, "lesson", None)
    return f"{type(exc).__name__}: {exc}" + (f"\nlesson: {lesson}" if lesson else "")


# ---------------------------------------------------------------------------------------------
# catalog / describe / sql
# ---------------------------------------------------------------------------------------------


def _cmd_catalog(args: argparse.Namespace) -> int:
    from omniops.engine.catalog import Catalog

    _setup_logging(False)
    catalog = Catalog.from_env()
    console.print(catalog.sources_overview(), markup=False)
    rows = catalog.search(args.query, limit=args.limit)
    for r in rows:                           # one plain line per table: greppable, never wrapped
        req = f" [requires {', '.join(r['required_filters'])}]" if r["required_filters"] else ""
        console.print(f"{r['table']:<40} {r['source']:<16} {r['kind']:<11}{req} {r['summary'][:120]}",
                      markup=False, highlight=False)
    if not rows:
        console.print("[yellow]no tables match[/]" if args.query else "[red]no tables configured[/]")
    for name, missing in sorted(catalog.unavailable.items()):
        console.print(f"[dim]not configured: {name} (missing {', '.join(missing)})[/]")
    return 0 if rows else 1


def _cmd_describe(args: argparse.Namespace) -> int:
    from omniops.engine.catalog import Catalog

    _setup_logging(False)
    try:
        console.print(Catalog.from_env().describe(args.table), markup=False)
    except KeyError as exc:
        console.print(f"[red]{exc.args[0] if exc.args else exc}[/]")
        return 1
    return 0


async def _cmd_sql(args: argparse.Namespace) -> int:
    from omniops.engine.catalog import Catalog
    from omniops.engine.engine import SQLEngine

    _setup_logging(False)
    catalog = Catalog.from_env()
    anchor = _anchor(args.at, args.window)
    engine = SQLEngine(catalog, anchor)
    try:
        res = await engine.query(args.sql, purpose="cli")
    except Exception as exc:  # noqa: BLE001 - the engine's lesson is the point of the output
        console.print("[bold red]ERROR[/]")
        console.print(_error_lines(exc), markup=False)
        return 1
    finally:
        engine.close()
        await catalog.aclose()
    console.print(anchor.describe(), markup=False, style="dim")
    console.print(res.render(), markup=False)
    return 0


# ---------------------------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------------------------


async def _cmd_selftest(args: argparse.Namespace) -> int:
    from omniops.engine.catalog import Catalog

    _setup_logging(False)
    wanted = list(args.source or [])
    catalog = Catalog.from_env(only=wanted or None)
    try:
        return await run_selftest(catalog, wanted)
    finally:
        await catalog.aclose()


async def run_selftest(catalog: Catalog, wanted: list[str] | None = None) -> int:
    """Run the manifests' live smoke tests. 0 = all passed, 1 = a failure, 2 = nothing ran."""
    failures = 0
    for name in wanted or []:
        if name not in catalog.runtimes:
            failures += 1
            missing = catalog.unavailable.get(name)
            why = f"missing {', '.join(missing)}" if missing else "unknown source"
            console.print(f"[bold red]FAIL[/] {name}: not configured ({why})")
    for name, missing in sorted(catalog.unavailable.items()):
        if not wanted:
            console.print(f"[dim]SKIP {name}: not configured (missing {', '.join(missing)})[/]")
    jobs: list[tuple[str, CatalogEntry, TableTest]] = []
    for name, rt in catalog.runtimes.items():
        for entry in catalog.entries.values():
            if entry.runtime is not rt:
                continue
            for test in entry.table.tests:
                unset = [v for v in test.when_env if not rt.env.get(v)]
                if unset:
                    console.print(f"[dim]SKIP {name}.{entry.table.name}: needs {', '.join(unset)}[/]")
                    continue
                jobs.append((name, entry, test))
    gate = asyncio.Semaphore(SELFTEST_CONCURRENCY)
    outcomes = await asyncio.gather(*(_one_test(catalog, entry, test, gate) for _, entry, test in jobs))
    for (name, entry, test), (ok, detail) in zip(jobs, outcomes):
        if ok:
            console.print(f"[green]PASS[/] {name}.{entry.table.name}: {detail}", markup=True, highlight=False)
        else:
            failures += 1
            console.print(f"[bold red]FAIL[/] {name}.{entry.table.name}: {test.sql}", highlight=False)
            console.print(f"     {detail}", markup=False)
    if not jobs and not failures:
        console.print("[bold red]SELFTEST RAN NOTHING[/]: no configured source has smoke tests"
                      + (f" ({', '.join(wanted)})" if wanted else ""))
        return 2
    if failures:
        console.print(f"[bold red]SELFTEST FAILED[/]: {failures} failure(s), {len(jobs)} test(s) run")
        return 1
    console.print(f"[bold green]SELFTEST PASSED[/]: {len(jobs)} test(s) across {len(catalog.runtimes)} source(s)")
    return 0


async def _one_test(catalog: Catalog, entry: CatalogEntry, test: TableTest, gate: asyncio.Semaphore
                    ) -> tuple[bool, str]:
    from omniops.engine.engine import SQLEngine
    from omniops.engine.timeutil import TimeAnchor

    async with gate:
        engine = SQLEngine(catalog, TimeAnchor.wall_clock(), positive_controls=False)
        try:
            res = await asyncio.wait_for(engine.query(test.sql, purpose="selftest"), SELFTEST_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - a failing smoke test is reported, not raised
            return False, _error_lines(exc)
        finally:
            engine.close()
    if res.total_rows < test.min_rows:
        return False, f"{res.total_rows} row(s), expected at least {test.min_rows} (API shape drift?)"
    return True, f"{res.total_rows} row(s) in {res.elapsed_ms} ms"


# ---------------------------------------------------------------------------------------------
# serve / ask / investigate
# ---------------------------------------------------------------------------------------------


async def _cmd_serve(args: argparse.Namespace) -> int:
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

    from omniops.slack.app import build_bot

    _setup_logging(args.verbose, logging.INFO)
    bot_token, app_token = os.environ.get("SLACK_BOT_TOKEN"), os.environ.get("SLACK_APP_TOKEN")
    if not bot_token or not app_token:
        console.print("[bold red]serve needs SLACK_BOT_TOKEN (xoxb-...) and SLACK_APP_TOKEN (xapp-..., "
                      "connections:write)[/]; see slack_app_manifest.yaml")
        return 2
    deps = make_deps()
    if not deps.settings.alert_channels:
        log.warning("OMNIOPS_ALERT_CHANNELS is empty: alert posts will not be investigated automatically")
    bot = build_bot(deps, bot_token=bot_token)
    handler = AsyncSocketModeHandler(bot.app, app_token)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:          # platforms without signal handlers: Ctrl-C still works
            pass
    await handler.connect_async()
    log.info("OmniOps connected to Slack (Socket Mode); %d source(s), alert channels: %s",
             len(deps.catalog.runtimes), ", ".join(deps.settings.alert_channels) or "none")
    try:
        await stop.wait()
    finally:
        await handler.close_async()          # no new events; in-flight runs keep going
        drain = deps.settings.investigation_guard_s
        log.info("draining in-flight runs (up to %.0f s)", drain)
        await bot.shutdown(drain)
        await _close_deps(deps)
    return 0


async def _cmd_ask(args: argparse.Namespace) -> int:
    from omniops.slack.simulator import SlackSimulator

    _setup_logging(args.verbose)
    deps = make_deps()
    sim = SlackSimulator(deps, auto_approve_writes=args.approve_writes)
    timeout = args.timeout or deps.settings.chat_guard_s + 30
    try:
        await sim.dm(args.question)
        idle = await sim.wait_idle(timeout)
        answer = sim.final_answer_text()
    finally:
        await sim.close()
        await _close_deps(deps)
    return _print_answer(answer, idle, sim)


async def _cmd_investigate(args: argparse.Namespace) -> int:
    from omniops.slack.simulator import SlackSimulator

    _setup_logging(args.verbose)
    if args.alert_file is not None:
        try:
            event = json.loads(args.alert_file.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            console.print(f"[red]cannot read {args.alert_file}: {exc}[/]")
            return 2
        if not isinstance(event, dict):
            console.print(f"[red]{args.alert_file} must hold a JSON object (a Slack message event)[/]")
            return 2
    deps = make_deps()
    sim = SlackSimulator(deps, auto_approve_writes=args.approve_writes)
    timeout = args.timeout or deps.settings.investigation_guard_s + 60
    try:
        if args.alert_file is not None:
            # A captured event carries its own channel id: replay it into the simulated alert channel.
            ts = await sim.post_alert(event, channel=sim.alert_channel)
        elif args.human:
            ts = await sim.post_user_message(sim.alert_channel, args.text)
        else:
            ts = await sim.post_alert({"text": args.text, "username": "alerts"})
        idle = await sim.wait_idle(timeout)
        answer = sim.final_answer_text(sim.alert_channel, ts)
    finally:
        await sim.close()
        await _close_deps(deps)
    if not answer:
        console.print("[yellow]No investigation ran: the message was not recognised as an alert (or it was a "
                      "recovery notice).[/]")
        return 1
    return _print_answer(answer, idle, sim)


def _print_answer(answer: str, idle: bool, sim: Any) -> int:
    console.print(answer or "(no answer)", markup=False)
    for w in (w for r in sim.run_results for w in r.staged_writes):
        approved = any(a["write_id"] == str(w.get("id")) for a in sim.approvals)
        state = "approved by the simulated user" if approved else "staged, not executed (use --approve-writes)"
        console.print(f"[dim]write {w.get('id')}: {w.get('summary', '')} - {state}[/]")
    if not idle:
        console.print("[bold red]timed out: the bot had not finished; this is what Slack showed[/]")
    return 0 if answer and idle else 1
