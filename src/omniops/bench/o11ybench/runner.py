"""o11y-bench in-container runner: the task arrives as a Slack DM, the answer leaves as ATIF.

    python -m omniops.bench.o11ybench.runner [--instruction-file F] [--out F] [--timeout-s S]

Flow: pick one HTTP source per signal (Grafana's own API for dashboards, datasources, alerting and
staged writes; Prometheus, Loki and Tempo directly, else through Grafana's datasource proxy) -> pin
the process clock to ``O11Y_SCENARIO_TIME_ISO`` -> probe each source once -> start the Slack
simulator (the real Bolt handlers; staged writes are approved by the simulated user) -> DM the task
with the scenario time, as a user would -> wait -> the bot's final Slack reply becomes the
trajectory's final agent step.

Retrospective lessons encoded (OpsIntelligence):
- D1 / 4.1: Slack is the product surface and "canaries must test the path production uses": the
  benchmark enters through the same DM handler users hit, never a side door into the agent.
- D5: SQL over REST APIs, no MCP. Harbor's MCP server config is accepted and ignored.
- 3.3: one time source per run - the scenario clock, pinned for the whole process before anything
  runs (no trigger window: benchmark questions name their own ranges).
- 3.12.4: the catalog holds only sources that can run, one path per signal, so the model never
  sees the same data under two table names.
- 4.1 "verify the effect, not the config": every source answers one smoke query before the DM, and
  the written trajectory is read back with the grader's final-answer rule.
- 4.1 loud zeros: no sources, a model that never answered, zero tool calls and an empty answer are
  recorded as warnings in the trajectory (and the first two fail the trial as infrastructure).
- 3.18 / 4.8: the trajectory is flushed after every tool call (a killed job still leaves a case
  file), and budgets nest: chat budget + watchdog < runner timeout < Harbor's agent timeout.
- D11: writes are staged and approved in Slack (by the simulated user here), never executed inline.
- 3.16: the task text and every tool result are data; nothing here acts on their content.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import inspect
import itertools
import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any

from omniops.agent.types import RunResult
from omniops.bench.o11ybench.trajectory import (
    ToolStep,
    TrajectoryBuilder,
    clip,
    final_answer,
    slack_to_markdown,
    validate_trajectory,
)
from omniops.engine.loader import missing_env, resolve_env
from omniops.engine.timeutil import TimeAnchor, fmt_rfc3339, to_datetime

if TYPE_CHECKING:
    from omniops.agent.context import Deps
    from omniops.engine.catalog import Catalog
    from omniops.llm.base import LoopEvent, ToolSpec

log = logging.getLogger("omniops.bench.o11ybench")

DEFAULT_INSTRUCTION = Path("/app/instruction.txt")
DEFAULT_OUT = Path("/logs/agent/trajectory.json")
DEFAULT_TIMEOUT_S = 540.0     # Harbor's agent timeout is 600 s; leave time to write the trajectory
SCENARIO_ENV = "O11Y_SCENARIO_TIME_ISO"
SOURCES_OVERRIDE_ENV = "OMNIOPS_O11YBENCH_SOURCES"
PROBE_TIMEOUT_S = 20.0

EXIT_OK = 0                   # answered, or a graded failure (timeout, wrong answer, no answer)
EXIT_CONFIG = 2               # infrastructure: no instruction, no data sources, no LLM client
EXIT_MODEL_UNREACHED = 3      # infrastructure: zero model tokens, zero tool calls and no answer
EXIT_HARNESS = 4              # infrastructure: the Slack simulator itself crashed

# One HTTP path per signal, direct API first: no Grafana proxy timeout (30 s) and no proxy auth.
SIGNAL_SOURCES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("grafana", ("grafana",)),
    ("metrics", ("prometheus", "grafana_prometheus")),
    ("logs", ("loki", "grafana_loki")),
    ("traces", ("tempo", "grafana_tempo")),
)
# Smoke tables when a manifest declares no live ``tests``: cheap, unfiltered entity lists.
PROBE_SUFFIXES = ("_datasources", "_targets", "_labels", "_tag_names", "_buildinfo")
SYSTEM_TEXT = (
    "OmniOps: Slack-native incident investigator (o11y-bench adapter).\n"
    "Entry point: the task arrives as a Slack DM from a simulated on-call user; the final answer is the bot's "
    "final Slack reply. Writes (dashboards) are staged and approved in Slack by the simulated user.\n"
    "Data access: SQL over the observability HTTP APIs below (no MCP)."
)
TRANSCRIPT_MAX_ENTRIES = 200
TRANSCRIPT_TEXT_MAX_CHARS = 2000
# The bot's approval card after the simulated user decided (slack/app.py ``_decide_write``); the match
# starts after the card's leading emoji. The card echoes the write's inputs (a whole dashboard JSON)
# after the result columns, so the answer keeps the outcome's head only, with a sized marker.
_WRITE_OUTCOME = re.compile(r"(?:Approved by \S+ (?:and executed|but the write failed)|Cancelled by \S+;)[^\n]*")
_SLACK_USER = re.compile(r"<@[A-Z0-9_]+>")
WRITE_OUTCOME_MAX_CHARS = 500


# ---------------------------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------------------------


def select_sources(manifests: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """(source ids to enable, signals with no usable source). ``OMNIOPS_O11YBENCH_SOURCES``
    (comma list) overrides the choice, e.g. to force the Grafana proxies."""
    override = [s.strip() for s in os.environ.get(SOURCES_OVERRIDE_ENV, "").split(",") if s.strip()]
    if override:
        return override, []
    chosen: list[str] = []
    missing: list[str] = []
    for signal, candidates in SIGNAL_SOURCES:
        usable = [n for n in candidates if n in manifests and not missing_env(manifests[n], resolve_env(manifests[n]))]
        if usable:
            chosen.append(usable[0])
        else:
            missing.append(signal)
    return chosen, missing


def scenario_clock() -> tuple[str, TimeAnchor]:
    """The benchmark's clock: the env value verbatim (graders and regrades key on it) and the anchor
    every relative time in the run is pinned to."""
    raw = os.environ.get(SCENARIO_ENV, "").strip()
    if raw:
        return raw, TimeAnchor(now=to_datetime(raw), source="o11y_scenario_clock")
    anchor = TimeAnchor.wall_clock()
    return fmt_rfc3339(anchor.now), anchor


@contextlib.contextmanager
def pinned_wall_clock(anchor: TimeAnchor) -> Iterator[None]:
    """Inside the benchmark process 'now' is the scenario clock (3.3): every anchor built from the
    wall clock (chat runs, engines created for approvals) gets a copy of ``anchor``. Restored on exit."""
    original = TimeAnchor.__dict__["wall_clock"]
    TimeAnchor.wall_clock = classmethod(  # type: ignore[method-assign,assignment]
        lambda cls: dataclasses.replace(anchor, notes=list(anchor.notes)))
    try:
        yield
    finally:
        TimeAnchor.wall_clock = original  # type: ignore[method-assign]


def compose_dm(instruction: str, scenario_iso: str) -> str:
    """The DM a user would send: the current time (the scenario's 'now'), then the question.
    ``Current time:`` is the marker o11y-bench's regrade docs tell people to look for."""
    return f"Current time: {scenario_iso}\n\n{instruction.strip()}"


def build_deps(sources: list[str]) -> Deps:
    """Production dependencies for the container: catalog limited to ``sources``, Meta model client."""
    from omniops.agent.context import Deps
    from omniops.engine.catalog import Catalog
    from omniops.llm.models import ModelConfig
    from omniops.settings import Settings

    if not sources:                       # Catalog.from_env(only=[]) would enable every configured source
        raise RuntimeError("no data source is configured: set GRAFANA_URL / PROMETHEUS_URL / LOKI_URL / TEMPO_URL")
    catalog = Catalog.from_env(only=sources)
    if not catalog.runtimes:
        raise RuntimeError(f"none of the selected sources are configured: {sources}; unavailable: "
                           f"{catalog.unavailable}; load errors: {catalog.load_errors[:3]}")
    config = ModelConfig.from_env()
    if not config.api_key:
        raise RuntimeError(f"{config.api_key_env} is not set: the model cannot be reached")
    if config.protocol == "chat":
        from omniops.llm.meta import ChatCompletionsClient as Client
    else:
        from omniops.llm.meta import MetaResponsesClient as Client
    return Deps(catalog=catalog, llm=Client(config), settings=Settings.from_env(), memory=None)


def _probe_sql(catalog: Catalog, source: str) -> str | None:
    """The manifest's first live smoke test for ``source``, else a count over a cheap entity table."""
    runtime = catalog.runtimes.get(source)
    if runtime is None:
        return None
    tables = [t for t in runtime.manifest.tables if t.name in catalog.entries]
    for t in tables:
        for test in t.tests:
            if all(os.environ.get(v) for v in test.when_env):
                return test.sql
    for suffix in PROBE_SUFFIXES:
        for t in tables:
            if t.name.endswith(suffix) and not any(f.required for f in t.filters) and not t.require_one_of:
                return f"SELECT count(*) AS n FROM {t.name}"
    return None


async def probe_sources(catalog: Catalog, anchor: TimeAnchor) -> dict[str, str]:
    """One smoke query per source before the DM: 'ok: N rows', 'FAILED: <lesson>' or 'no probe'."""
    from omniops.engine.engine import SQLEngine

    engine = SQLEngine(catalog, anchor)

    async def one(source: str) -> tuple[str, str]:
        sql = _probe_sql(catalog, source)
        if sql is None:
            return source, "no probe table"
        try:
            res = await asyncio.wait_for(engine.query(sql, purpose="o11y-bench preflight"), PROBE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - a failed probe is reported, the run still goes ahead
            lesson = getattr(exc, "lesson", "") or ""
            return source, f"FAILED: {type(exc).__name__}: {clip(f'{exc} {lesson}'.strip(), 300)}"
        return source, f"ok: {res.total_rows} rows ({sql})"

    try:
        return dict(await asyncio.gather(*(one(s) for s in sorted(catalog.runtimes))))
    finally:
        engine.close()


def _model_name() -> str:
    from omniops.llm.models import DEFAULT_MODELS

    return os.environ.get("OMNIOPS_MODEL_HEAVY", "").strip() or DEFAULT_MODELS["heavy"]


def _version() -> str:
    try:
        return metadata.version("omniops")
    except metadata.PackageNotFoundError:
        return "0+unknown"


# ---------------------------------------------------------------------------------------------
# Live recording: every tool call reaches the trajectory file as it happens
# ---------------------------------------------------------------------------------------------


def _field(obj: Any, *names: str) -> Any:
    for n in names:
        v = obj.get(n) if isinstance(obj, Mapping) else getattr(obj, n, None)
        if v is not None:
            return v
    return None


class LiveRecorder:
    """Turns the tool loop's ``tool_call`` / ``tool_result`` events into trajectory steps."""

    def __init__(self, builder: TrajectoryBuilder) -> None:
        self.builder = builder
        self._pending: dict[str, tuple[str, dict[str, Any], float]] = {}   # insertion order = call order
        self._ids = itertools.count(1)

    def on_event(self, ev: LoopEvent, role: str) -> None:
        payload = ev.payload or {}
        call = _field(payload, "call", "tool_call") or payload
        cid = _field(call, "id", "call_id", "tool_call_id", "source_call_id")
        if ev.kind == "tool_call":
            args = _field(call, "arguments", "args")
            args = args if isinstance(args, dict) else ({} if args is None else {"_raw": args})
            self._pending[str(cid or f"live-{next(self._ids)}")] = (
                str(_field(call, "name", "tool") or "tool"), args, ev.ts)
        elif ev.kind == "tool_result":
            key = str(cid) if cid is not None and str(cid) in self._pending else next(iter(self._pending), None)
            name, args, ts = (self._pending.pop(key) if key is not None
                              else (str(_field(payload, "name", "tool") or "tool"), {}, ev.ts))
            out = _field(payload, "output", "content", "result")
            if out is not None and not isinstance(out, str):      # a ToolOutput object
                text, is_error = str(_field(out, "content") or ""), bool(_field(out, "is_error"))
            else:
                text, is_error = out or "", bool(_field(payload, "is_error"))
            self.builder.add_tool_step(ToolStep(key or f"live-{next(self._ids)}", name, args, text, is_error, ts,
                                                {"agent": role, "recorded": "live"}))
            print(f"[tool] {role}:{name} -> {len(text)} chars{' ERROR' if is_error else ''}", flush=True)


class RecordingLLM:
    """Transparent proxy over the model client that taps every tool loop's events and tool list.

    Recording at the LLM boundary works whatever the Slack layer exposes, so the trajectory grows
    step by step even if the run is killed before the bot answers. Per-run views (``for_run``) are
    wrapped too, so agent code that scopes the client per run is still recorded.
    """

    def __init__(self, inner: Any, recorder: LiveRecorder) -> None:
        self._inner = inner
        self._recorder = recorder

    @property
    def usage(self) -> Any:
        return self._inner.usage

    async def complete(self, **kwargs: Any) -> Any:
        return await self._inner.complete(**kwargs)

    async def structured(self, **kwargs: Any) -> Any:
        return await self._inner.structured(**kwargs)

    async def run_tool_loop(self, *, tools: list[ToolSpec], on_event: Callable[[LoopEvent], Any] | None = None,
                            **kwargs: Any) -> Any:
        role = str(kwargs.get("role", "agent"))
        self._recorder.builder.add_tool_definitions(
            [{"name": t.name, "description": t.description, "parameters": t.parameters} for t in tools])

        async def tap(ev: LoopEvent) -> None:
            try:
                self._recorder.on_event(ev, role)
            except Exception:
                log.exception("live trajectory recording failed")
            if on_event is not None:
                r = on_event(ev)
                if inspect.isawaitable(r):
                    await r

        return await self._inner.run_tool_loop(tools=tools, on_event=tap, **kwargs)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)     # AttributeError when the client has no such method
        if name == "for_run":
            return lambda run_id: RecordingLLM(attr(run_id), self._recorder)
        return attr


# ---------------------------------------------------------------------------------------------
# Talking to the Slack simulator (built against its documented contract, tolerant of details)
# ---------------------------------------------------------------------------------------------


async def _maybe(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _supported_kwargs(fn: Callable[..., Any], wanted: dict[str, Any]) -> dict[str, Any]:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return wanted
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return wanted
    return {k: v for k, v in wanted.items() if k in params}


def _required_positionals(fn: Callable[..., Any]) -> int:
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return 0
    return sum(1 for p in params if p.default is p.empty
               and p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD))


def _as_results(value: Any) -> list[RunResult]:
    if isinstance(value, RunResult):
        return [value]
    if isinstance(value, list | tuple):
        return [r for r in value if isinstance(r, RunResult)]
    return []


def _run_results(sim: Any, *returned: Any) -> list[RunResult]:
    """Run results the simulator exposes (attribute or accessor), else ones dm()/wait_idle() returned."""
    for attr in ("run_results", "results", "last_result"):
        value = getattr(sim, attr, None)
        if callable(value):
            try:
                value = value()
            except TypeError:         # a method that needs arguments is not a results accessor
                continue
        found = _as_results(value)
        if found:
            return found
    return [r for v in returned for r in _as_results(v)]


def _entry_text(item: Any) -> str:
    """The Slack text of a transcript entry: a message, or a simulator log entry (API call / delivery)."""
    if not isinstance(item, Mapping):
        return str(item)
    for holder in (item, item.get("args"), (item.get("body") or {}).get("event")):
        if isinstance(holder, Mapping) and isinstance(holder.get("text"), str):
            return holder["text"]
    return ""


def _compact(item: Any) -> Any:
    """Scalars and the message text of one transcript entry (Block Kit bodies are dropped)."""
    if not isinstance(item, Mapping):
        return clip(str(item), TRANSCRIPT_TEXT_MAX_CHARS)
    out = {k: v for k, v in item.items() if isinstance(v, str | int | float | bool) or v is None}
    for k in ("text", "args", "body"):
        out.pop(k, None)
    args = item.get("args") if isinstance(item.get("args"), Mapping) else {}
    out.update({k: args[k] for k in ("channel", "ts", "thread_ts") if isinstance(args.get(k), str)})
    text = _entry_text(item)
    if text:
        out["text"] = clip(text, TRANSCRIPT_TEXT_MAX_CHARS)
    return out


def _transcript(sim: Any) -> list[Any]:
    fn = getattr(sim, "transcript", None)
    if fn is None:
        return []
    raw = fn() if callable(fn) else fn
    items = raw if isinstance(raw, list) else [raw]
    return json.loads(json.dumps([_compact(i) for i in items], default=str))


async def _final_answer_text(sim: Any, handle: Any) -> str:
    fn = getattr(sim, "final_answer_text", None)
    if fn is None:
        return ""
    value = fn(handle) if _required_positionals(fn) and handle is not None else fn()
    return str(await _maybe(value) or "")


def write_outcomes(transcript: list[Any]) -> list[str]:
    """What the bot's approval cards say once the simulated user decided: one line per distinct
    outcome, in order, Slack markup removed, clipped to its head."""
    seen: dict[str, None] = {}
    for item in transcript:
        for m in _WRITE_OUTCOME.finditer(_entry_text(item)):
            line = slack_to_markdown(_SLACK_USER.sub("the on-call user", m.group(0)))
            if len(line) > WRITE_OUTCOME_MAX_CHARS:
                line = (f"{line[:WRITE_OUTCOME_MAX_CHARS]}..."
                        f"[{len(line) - WRITE_OUTCOME_MAX_CHARS} characters omitted]")
            seen.setdefault(line, None)
    return list(seen)


def _default_simulator() -> Callable[..., Any]:
    from omniops.slack.simulator import SlackSimulator

    return SlackSimulator


# ---------------------------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------------------------


@dataclass
class RunOutcome:
    exit_code: int
    answer: str
    tool_calls: int
    trajectory: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


def _tool_steps(results: list[RunResult]) -> list[ToolStep]:
    steps: list[ToolStep] = []
    for r in results:
        for n, ev in enumerate(r.tool_events, start=1):
            steps.append(ToolStep(f"{r.run_id}-{n}", ev.name, ev.arguments, ev.output, ev.is_error, ev.ts,
                                  {"agent": ev.agent, "evidence_id": ev.evidence_id, "elapsed_ms": ev.elapsed_ms}))
    return steps


def _approvals(sim: Any, results: list[RunResult], sim_kwargs: Mapping[str, Any]) -> list[tuple[float | None, str]]:
    """(time, user-step text) for each staged write the simulated user approved."""
    summaries = {str(w.get("id") or w.get("write_id")): str(w.get("summary") or w.get("table") or "")
                 for r in results for w in r.staged_writes}
    recorded = getattr(sim, "approvals", None)
    if isinstance(recorded, list):           # what the simulated human actually clicked, with when
        clicks = [(a.get("at"), str(a.get("write_id") or "?")) for a in recorded if isinstance(a, Mapping)]
    elif "auto_approve_writes" in sim_kwargs:
        clicks = [(None, wid) for wid in summaries]
    else:
        clicks = []
    return [(at if isinstance(at, int | float) else None,
             f"[Slack] The on-call user clicked Approve on staged write {wid}: {clip(summaries.get(wid, ''), 600)}"
             .rstrip(": ")) for at, wid in clicks]


def _usage_totals(llm: Any, results: list[RunResult]) -> tuple[int, int, int, float, int]:
    """(prompt incl. cached, completion incl. reasoning, cached, cost, reasoning) for the whole process."""
    u = getattr(llm, "usage", None)
    if u is not None and (u.input_tokens or u.output_tokens):
        return u.input_tokens, u.output_tokens, u.cached_tokens, u.cost_usd, u.reasoning_tokens
    s = [r.stats for r in results]
    return (sum(x.input_tokens for x in s), sum(x.output_tokens for x in s), sum(x.cached_tokens for x in s),
            sum(x.cost_usd for x in s), sum(x.reasoning_tokens for x in s))


async def run_task(
    instruction: str,
    deps: Deps,
    *,
    out_path: Path | None,
    scenario_iso: str,
    anchor: TimeAnchor,
    simulator_factory: Callable[..., Any] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    sources: list[str] | None = None,
    preflight: dict[str, str] | None = None,
    preflight_warnings: list[str] | None = None,
) -> RunOutcome:
    """DM ``instruction`` to the bot through the Slack simulator and write the ATIF trajectory."""
    builder = TrajectoryBuilder(out_path, agent_name="omniops", agent_version=_version(), model_name=_model_name())
    for w in preflight_warnings or []:
        builder.warn(w)
    settings = deps.settings
    if settings.chat_budget_s + settings.watchdog_grace_s >= timeout_s:
        builder.warn(f"budgets do not nest: chat {settings.chat_budget_s:.0f}s + watchdog "
                     f"{settings.watchdog_grace_s:.0f}s >= runner timeout {timeout_s:.0f}s")
    dm_text = compose_dm(instruction, scenario_iso)
    builder.system_message = f"{SYSTEM_TEXT}\n{anchor.describe()}\n{deps.catalog.sources_overview()}"
    builder.user_message = dm_text
    builder.extra.update({"entry_point": "slack_dm", "data_access": "sql_over_http", "mcp": "not used",
                          "sources": sources or sorted(deps.catalog.runtimes), "scenario_time": scenario_iso})
    if preflight:
        builder.extra["preflight"] = preflight
        for source, status in preflight.items():
            if status.startswith("FAILED"):
                builder.warn(f"preflight {source} {status}")
    builder.metrics_extra["reasoning_effort"] = "per-role (OmniOps model lineup)"
    builder.flush()

    recorder = LiveRecorder(builder)
    run_deps = dataclasses.replace(deps, llm=RecordingLLM(deps.llm, recorder))
    sim_kwargs: dict[str, Any] = {}
    exit_code = EXIT_OK
    answer = ""
    returned: dict[str, Any] = {}
    t0 = time.monotonic()
    sim: Any = None

    async def converse() -> None:
        returned["handle"] = await _maybe(sim.dm(dm_text))
        wait = sim.wait_idle
        returned["idle"] = await _maybe(wait(**_supported_kwargs(wait, {"timeout": timeout_s})))

    with pinned_wall_clock(anchor):
        try:
            factory = simulator_factory or _default_simulator()
            sim_kwargs = _supported_kwargs(factory, {"auto_approve_writes": True, "anchor": anchor})
            if "auto_approve_writes" not in sim_kwargs:
                builder.warn("the Slack simulator does not take auto_approve_writes: staged writes stay unapproved")
            sim = factory(run_deps, **sim_kwargs)
            builder.extra["clock"] = {"now": scenario_iso, "process_clock_pinned": True,
                                      "simulator_anchor": "anchor" in sim_kwargs}
            if hasattr(sim, "start"):
                await _maybe(sim.start())
            print(f"[omniops] DM sent at scenario time {scenario_iso}", flush=True)
            try:
                await asyncio.wait_for(converse(), timeout=timeout_s)
            except TimeoutError:
                returned["idle"] = False
            if returned.get("idle") is False:
                builder.warn(f"runner timeout after {timeout_s:.0f}s: the bot had not finished; "
                             "the answer is what Slack shows now")
            answer = slack_to_markdown(await _final_answer_text(sim, returned.get("handle")))
        except Exception as exc:
            log.exception("Slack simulator failed")
            builder.warn(f"Slack simulator failed: {type(exc).__name__}: {clip(str(exc), 500)}")
            exit_code = EXIT_HARNESS
        results = _run_results(sim, returned.get("handle"), returned.get("idle")) if sim is not None else []
        transcript: list[Any] = []
        approvals = _approvals(sim, results, sim_kwargs) if sim is not None else []
        if sim is not None:
            try:
                transcript = _transcript(sim)
            except Exception as exc:  # noqa: BLE001 - the transcript is diagnostics; its loss is recorded
                builder.warn(f"could not read the Slack transcript: {exc}")
            builder.extra["slack_transcript"] = transcript
            for closer in ("aclose", "close", "stop"):
                if hasattr(sim, closer):
                    try:
                        await _maybe(getattr(sim, closer)())
                    except Exception:
                        log.warning("simulator %s() failed", closer, exc_info=True)
                    break

    if results:
        authoritative = _tool_steps(results)
        if authoritative or not builder.tool_steps:
            builder.replace_tool_steps(authoritative)
        builder.extra["runs"] = [{"run_id": r.run_id, "kind": r.kind, "status": r.status,
                                  "evidence": len(r.evidence), "warnings": r.warnings[:20],
                                  "stats": r.stats.as_dict()} for r in results]
    elif sim is not None:
        builder.warn("the Slack simulator exposed no run results: tool steps come from live loop events")
    for at, text in approvals:
        builder.add_user_event(text, at)
    builder.extra["final_answer_source"] = "slack_reply" if answer else "none"
    if not answer and results and results[-1].answer_markdown.strip():
        answer = slack_to_markdown(results[-1].answer_markdown)
        builder.warn("no final Slack reply was found: using the run's answer text")
        builder.extra["final_answer_source"] = "run_result"
    outcomes = [o for o in write_outcomes(transcript) if o not in answer]
    if answer and outcomes:      # the approval card is the bot's last Slack message: the user sees both
        answer = answer + "\n\nStaged writes:\n" + "\n".join(f"- {o}" for o in outcomes)
    if answer:
        builder.set_final(answer)
    else:
        builder.warn("no final answer: the grader will score this trial as no_answer")
    if not builder.tool_steps:
        builder.warn("zero tool calls: nothing was queried")

    prompt, completion, cached, cost, reasoning = _usage_totals(deps.llm, results)
    builder.totals.prompt_tokens, builder.totals.completion_tokens = prompt, completion
    builder.totals.cached_tokens, builder.totals.cost_usd = cached, cost
    builder.metrics_extra["reasoning_tokens"] = reasoning
    answered = (any(r.status in ("ok", "insufficient_data") and r.answer_markdown.strip() for r in results)
                if results else bool(answer))
    if exit_code == EXIT_OK and not (prompt or completion) and not builder.tool_steps and not answered:
        builder.warn("the model was never reached (zero tokens, zero tool calls, no answer)")
        exit_code = EXIT_MODEL_UNREACHED
    builder.extra["exit_code"] = exit_code
    data = builder.to_dict()
    problems = validate_trajectory(data)
    if final_answer(data) != answer.strip():        # what the grader will read back (4.1)
        problems.append("the grader's final-answer rule does not select the bot's final reply")
    for p in problems:
        builder.warn(f"trajectory check: {p}")
    builder.flush()
    elapsed = time.monotonic() - t0
    print(f"[omniops] done in {elapsed:.1f}s | {len(builder.tool_steps)} tool calls | {prompt} in "
          f"({cached} cached) {completion} out ${cost:.4f} | answer {len(answer)} chars", flush=True)
    for w in builder.warnings:
        print(f"[omniops] WARNING {w}", flush=True)
    return RunOutcome(exit_code, answer, len(builder.tool_steps), builder.to_dict(), list(builder.warnings))


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def _write_config_failure(out: Path, instruction: str, scenario_iso: str, message: str) -> None:
    """Even a run that cannot start leaves a trajectory saying why (4.1: never silent)."""
    b = TrajectoryBuilder(out, agent_name="omniops", agent_version=_version(), model_name=_model_name())
    b.system_message = "OmniOps o11y-bench runner could not start."
    b.user_message = compose_dm(instruction, scenario_iso) if instruction else ""
    b.warn(message)
    b.extra["exit_code"] = EXIT_CONFIG
    b.flush()


async def amain(args: argparse.Namespace) -> int:
    os.environ.setdefault("OMNIOPS_BENCHMARK_MODE", "1")
    os.environ.setdefault("OMNIOPS_WRITE_MODE", "stage")
    scenario_iso, anchor = scenario_clock()
    out = Path(args.out)
    try:
        instruction = Path(args.instruction_file).read_text().strip()
    except OSError as exc:
        print(f"[omniops] cannot read the instruction: {exc}", file=sys.stderr, flush=True)
        _write_config_failure(out, "", scenario_iso, f"cannot read the instruction: {exc}")
        return EXIT_CONFIG
    from omniops.engine.loader import load_all_manifests

    manifests, errors = load_all_manifests(strict=False)
    for e in errors:
        log.warning("manifest load error: %s", e)
    sources, missing = select_sources(manifests)
    notes = [f"no data source for signal(s): {', '.join(missing)}"] if missing else []
    print(f"[omniops] sources: {', '.join(sources) or 'none'}", flush=True)
    try:
        deps = build_deps(sources)
    except Exception as exc:  # noqa: BLE001 - any configuration failure is infrastructure: exit 2
        print(f"[omniops] cannot start: {exc}", file=sys.stderr, flush=True)
        _write_config_failure(out, instruction, scenario_iso, f"cannot start: {exc}")
        return EXIT_CONFIG
    try:
        preflight = await probe_sources(deps.catalog, anchor)
        for source, status in preflight.items():
            print(f"[omniops] preflight {source}: {status}", flush=True)
        outcome = await run_task(instruction, deps, out_path=out, scenario_iso=scenario_iso, anchor=anchor,
                                 timeout_s=args.timeout_s, sources=sources, preflight=preflight,
                                 preflight_warnings=notes)
    finally:
        await deps.catalog.aclose()
        closer = getattr(deps.llm, "aclose", None)
        if closer is not None:
            await _maybe(closer())
    return outcome.exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="omniops.bench.o11ybench.runner",
                                     description="o11y-bench in-container runner: Slack DM in, ATIF trajectory out")
    parser.add_argument("--instruction-file", default=str(DEFAULT_INSTRUCTION))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--timeout-s", type=float,
                        default=float(os.environ.get("OMNIOPS_BENCH_TIMEOUT_S", DEFAULT_TIMEOUT_S)))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stderr)
    started = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[omniops] o11y-bench runner {_version()} started {started}", flush=True)
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
