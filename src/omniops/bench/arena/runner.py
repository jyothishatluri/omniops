"""Edge Delta AI SRE Arena runner: fault -> Slack launch -> OmniOps investigation -> archive -> judge.

    python -m omniops.bench.arena.runner --suite full --scenarios oom,payment-failure [--gitops]
        [--real-slack] [--dry-run] [--run-id ID] [--no-judge]

Per scenario, one case directory per attempt (``<scenario>-a<N>``) under ``runs/arena/<run_id>/``:
baseline reset -> user-visible baseline probe -> fault (kubectl or a GitOps commit) -> verify ->
wait (bounded) for Grafana alerts -> launch message into Slack (the simulator drives the real Bolt
handlers; ``--real-slack`` posts into a real channel and waits for the bot's thread reply; a customer
report that OmniOps' own triage does not take up gets an on-call "@OmniOps investigate" in the
thread, never both) -> wait for the investigation -> ``final.txt`` / ``intermediate.txt`` /
``actions.txt`` -> ``bench archive``
(detection not_measured) -> ``bench packet`` -> ``bench judge`` (Muse, labelled non-official) ->
reset. ``progress.json`` makes the run resumable.

Lessons encoded (OpsIntelligence retrospective):
- D1: Slack is the only entry point; the harness never calls the orchestrator directly.
- 3.18 smoke tests: pin the run to the production flow (real Bolt handlers), size waits from the
  budgets (investigation + report + watchdog), and a killed run leaves case files: every stage is
  recorded as it completes, so a crash resumes instead of silently losing the case.
- 4.1: loud zeros. A fault that was not observed, an empty answer, zero tool calls or a missing
  judge key are recorded as problems in ``progress.json`` and printed, never skipped quietly.
- 4.8 / D25: subprocess timeouts exceed the Arena's own waits (quota resets wait 20 minutes).
- D11 / D28 / Part 8 #34: humans approve publishes. GitOps fault commits are pushed only with
  ``--allow-push``; otherwise the runner prints the git commands and waits for the operator.
- 4.5: never block the event loop: subprocesses run in worker threads while Slack handlers run.
- 3.16: ground truth stays judge-only. The Arena's ConfigMap benchmark-control/suite-state names the
  active scenario; before any case the runner lists ConfigMaps as OmniOps' own Kubernetes identity and
  refuses to run if that namespace is visible (deploy/arena gives OmniOps a filtered, read-only view).
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import inspect
import json
import os
import shlex
import subprocess
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from omniops.bench.arena import launch as L
from omniops.bench.arena import records as R

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ARENA_DIR = PROJECT_ROOT.parent / "vendor" / "project-arena"
STATE_DIR = PROJECT_ROOT / "deploy" / "arena" / ".state"
DEFAULT_CONTEXT = "kind-incident-bench"
JUDGE_MODEL = "muse-wonder-1.0"
JUDGE_BASE_URL = "https://api.meta.ai/v1"
JUDGE_KEY_ENV = "MODEL_API_KEY"
NON_OFFICIAL = ("NON-OFFICIAL SCORES: judged by Meta {model} through {base_url}, not the published GPT-6-Astra "
                "judge, and the judge shares a model family with the investigating agent. Do not compare these "
                "numbers with the Arena leaderboard as if they were equivalent.")
STAGES = ("baseline", "probe", "fault", "verify", "detect", "launch", "investigate", "records", "archive",
          "packet", "judge", "reset")
RESUMABLE_FROM = STAGES.index("records")       # earlier stages hold in-memory Slack state: re-run them
TRIAGE_CAP_S = 300.0                            # simulator: longest wait for OmniOps' own triage of a report
CONTROL_NAMESPACES = ("benchmark-control",)     # Arena harness state: suite-state names the active scenario
# OmniOps' Kubernetes identity (deploy/arena/omniops-env.sh): always taken from the env file, so an admin
# KUBECONFIG exported in the runner's shell cannot bypass the read-only, namespace-filtering view.
KUBE_VIEW_ENV = ("KUBECONFIG", "KUBE_CONTEXT", "KUBERNETES_CONTEXT", "K8S_API_URL")


class RunnerError(RuntimeError):
    pass


# ---------------------------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------------------------


@dataclass
class RunnerConfig:
    suite: str = "full"
    scenarios: list[str] = field(default_factory=list)
    gitops: bool = False
    real_slack: bool = False
    dry_run: bool = False
    run_id: str = ""
    arena_dir: Path = DEFAULT_ARENA_DIR
    output_root: Path = PROJECT_ROOT / "runs" / "arena"
    context: str = DEFAULT_CONTEXT
    python: str = field(default_factory=lambda: os.environ.get("ARENA_PYTHON") or sys.executable)
    channel: str = "C0ARENAOPS"
    env_file: Path | None = None
    gitops_config: Path = STATE_DIR / "arena-gitops.json"
    alert_wait_s: float = 420.0
    alert_settle_s: float = 45.0
    clear_wait_s: float = 300.0
    investigation_timeout_s: float | None = None
    quiet_s: float = 90.0
    mention_grace_s: float = 90.0
    judge: bool = True
    judge_model: str = JUDGE_MODEL
    judge_base_url: str = JUDGE_BASE_URL
    judge_key_env: str = JUDGE_KEY_ENV
    allow_push: bool = False
    adopt_grafana_posts: bool = False
    bridge_port: int = 8787
    bridge_host: str = "127.0.0.1"
    grafana_url: str = ""

    @property
    def run_dir(self) -> Path:
        return self.output_root / self.run_id

    @property
    def run_file(self) -> Path:
        return self.run_dir / "arena.json"

    @property
    def product(self) -> str:
        return f"OmniOps [judge: {self.judge_model}, non-official]"

    @property
    def notice(self) -> str:
        return NON_OFFICIAL.format(model=self.judge_model, base_url=self.judge_base_url)


def arena_catalog(arena_dir: Path, suite: str) -> dict[str, dict[str, Any]]:
    """Scenario -> judge-only truth, read from the Arena checkout without executing it."""
    if suite == "full":
        names = json.loads((arena_dir / "scenarios" / "scenarios.json").read_text())
        out = {}
        for name in names:
            key = arena_dir / "scenarios" / "faults" / name / "answer-key.json"
            out[name] = json.loads(key.read_text()) if key.is_file() else {"scenario": name}
        return out
    if suite == "smoke":
        tree = ast.parse((arena_dir / "bench" / "smoke.py").read_text())
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "SCENARIOS" for t in node.targets):
                data = ast.literal_eval(node.value)
                return {k: {"scenario": k, "ground_truth": v} for k, v in data.items()}
        raise RunnerError("could not read the smoke scenario table from bench/smoke.py")
    raise RunnerError(f"unknown suite {suite!r}")


def arena_run_config(cfg: RunnerConfig, scenario: str) -> dict[str, Any]:
    """The Arena ``arena.json`` for this run (Muse judge through the openai-compatible provider)."""
    run: dict[str, Any] = {
        "run_id": cfg.run_id, "product": cfg.product, "suite": cfg.suite, "scenario": scenario,
        "output_dir": ".", "registry": "fixture.local", "tag": "v1", "context": cfg.context,
        "judge": {"provider": "openai-compatible", "base_url": cfg.judge_base_url, "api_key_env": cfg.judge_key_env,
                  "model": cfg.judge_model, "max_output_tokens": 16384, "timeout": 900},
    }
    if cfg.gitops:
        run["gitops"] = gitops_settings(cfg)
    return run


def gitops_settings(cfg: RunnerConfig) -> dict[str, Any]:
    if not cfg.gitops_config.is_file():
        raise RunnerError(f"GitOps settings not found at {cfg.gitops_config}; run deploy/arena/gitops.sh first")
    value = json.loads(cfg.gitops_config.read_text()).get("gitops")
    if not isinstance(value, dict):
        raise RunnerError(f"{cfg.gitops_config} has no gitops object")
    base = cfg.gitops_config.parent
    for key in ("app_repo", "fault_repo"):
        repo = dict(value[key])
        repo["checkout"] = str((base / repo["checkout"]).resolve())
        value[key] = repo
    return value


def investigation_timeout(cfg: RunnerConfig) -> float:
    """Wait = the investigation's outer guard (budget + report allowance + grace) + margin (4.8: CI timeout >=
    budgets)."""
    if cfg.investigation_timeout_s:
        return cfg.investigation_timeout_s
    from omniops.settings import Settings

    s = Settings.from_env()
    return s.investigation_guard_s + 120.0


# ---------------------------------------------------------------------------------------------
# Command execution (dry-run aware)
# ---------------------------------------------------------------------------------------------


@dataclass
class Shell:
    dry_run: bool
    log_path: Path | None = None
    printer: Callable[[str], None] = print

    async def run(self, argv: Sequence[str], *, cwd: Path | None = None, timeout: float = 600,
                  stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        if self.dry_run:
            raise RunnerError("dry run must not execute commands")
        started = time.monotonic()
        try:
            proc = await asyncio.to_thread(subprocess.run, list(argv), cwd=cwd, input=stdin, text=True,
                                           capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            self._log(argv, cwd, None, time.monotonic() - started, "", f"timed out after {timeout:.0f} s")
            raise RunnerError(f"command timed out after {timeout:.0f} s: {_show(argv)}") from exc
        self._log(argv, cwd, proc.returncode, time.monotonic() - started, proc.stdout, proc.stderr)
        if check and proc.returncode:
            tail = (proc.stderr or proc.stdout).strip()[-1500:]
            raise RunnerError(f"command failed ({proc.returncode}): {_show(argv)}\n{tail}")
        return proc

    def _log(self, argv: Sequence[str], cwd: Path | None, rc: int | None, secs: float, out: str, err: str) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a") as fh:
            fh.write(f"\n[{_now_iso()}] $ {_show(argv)}  (cwd={cwd}) rc={rc} {secs:.1f}s\n")
            for name, text in (("stdout", out), ("stderr", err)):
                if text.strip():
                    fh.write(f"--- {name} (tail) ---\n{text.strip()[-4000:]}\n")


def _show(argv: Sequence[str]) -> str:
    return " ".join(shlex.quote(a) if len(a) < 200 else "<script>" for a in argv)


def _push_commands(repo: str, path: str, rev: str, message: str) -> list[list[str]]:
    """The fault repository commit + push the operator (or ``--allow-push``) runs."""
    return [["git", "-C", repo, "add", path], ["git", "-C", repo, "commit", "-m", message],
            ["git", "-C", repo, "push", "origin", rev]]


# ---------------------------------------------------------------------------------------------
# Grafana alerts: Alertmanager API polling + webhook bridge
# ---------------------------------------------------------------------------------------------


class AlertFeed:
    """Firing ``omniops=arena`` alerts from Grafana's Alertmanager API, merged with webhook deliveries."""

    def __init__(self, grafana_url: str, token: str | None, *, bridge_host: str, bridge_port: int) -> None:
        self.grafana_url = grafana_url.rstrip("/")
        self.token = token
        self.bridge_host, self.bridge_port = bridge_host, bridge_port
        self.webhooks: list[tuple[float, dict[str, Any]]] = []
        self.errors: list[str] = []
        self._runner: Any = None

    @property
    def enabled(self) -> bool:
        return bool(self.grafana_url)

    async def start(self) -> None:
        if not self.bridge_port:
            return
        from aiohttp import web

        async def receive(request: web.Request) -> web.Response:
            try:
                self.webhooks.append((time.time(), await request.json()))
            except ValueError:
                return web.Response(status=400)
            return web.Response(text="ok")

        app = web.Application()
        app.router.add_post("/grafana", receive)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        try:
            await web.TCPSite(self._runner, self.bridge_host, self.bridge_port).start()
        except OSError as exc:                     # bridge is optional: polling still works
            self.errors.append(f"webhook bridge not started on {self.bridge_host}:{self.bridge_port}: {exc}")

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    async def firing(self) -> list[L.FiringAlert]:
        polled: list[L.FiringAlert] = []
        if self.enabled:
            import httpx

            headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
            params = {"active": "true", "silenced": "false", "inhibited": "false", "filter": 'omniops="arena"'}
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    r = await client.get(f"{self.grafana_url}/api/alertmanager/grafana/api/v2/alerts",
                                         params=params, headers=headers)
                r.raise_for_status()
                polled = L.alerts_from_alertmanager(r.json())
            except (httpx.HTTPError, ValueError) as exc:
                self.errors.append(f"{_now_iso()} alertmanager poll failed: {type(exc).__name__}: {exc}")
        hooked = [a for _, p in self.webhooks for a in L.alerts_from_webhook(p)
                  if a.labels.get("omniops") == "arena"]
        return L.merge_alerts(polled, hooked)

    async def wait_for(self, since: datetime, *, wait_s: float, settle_s: float, poll_s: float = 15.0
                       ) -> tuple[list[L.FiringAlert], list[dict[str, str]]]:
        """Bounded wait for alerts that started after ``since``; once one fires, let co-firing ones land."""
        deadline = time.monotonic() + wait_s
        first: float | None = None
        selected: list[L.FiringAlert] = []
        excluded: list[dict[str, str]] = []
        while True:
            selected, excluded = L.select_launch_alerts(await self.firing(), since)
            if selected and first is None:
                first = time.monotonic()
            if first is not None and time.monotonic() - first >= settle_s:
                break
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(poll_s)
        return selected, excluded

    async def wait_clear(self, wait_s: float, poll_s: float = 15.0) -> list[str]:
        """Wait until no arena symptom alert is firing (a previous scenario's alerts must not leak in)."""
        deadline = time.monotonic() + wait_s
        while True:
            names = sorted({a.name for a in await self.firing() if a.name not in L.INFRA_ALERTS})
            if not names or time.monotonic() >= deadline:
                return names
            await asyncio.sleep(poll_s)


# ---------------------------------------------------------------------------------------------
# User-visible probes
# ---------------------------------------------------------------------------------------------

_NODE_PROBE = """const base = "http://frontend.shop.svc.cluster.local:8080";
const paths = PATHS;
(async () => { const out = [];
  for (const p of paths) { const t0 = Date.now();
    try { const r = await fetch(base + p, {signal: AbortSignal.timeout(15000)}); await r.text();
      out.push({path: p, status: r.status, latency_ms: Date.now() - t0}); }
    catch (e) { out.push({path: p, status: 0, latency_ms: Date.now() - t0, error: String((e && e.name) || e)}); } }
  console.log(JSON.stringify(out)); })();"""

_PY_PROBE = """import json, time, urllib.request, urllib.error
out = []
for p in PATHS:
    t0 = time.time()
    try:
        with urllib.request.urlopen("http://api.incident-bench.svc.cluster.local:8080" + p, timeout=15) as r:
            r.read(); out.append({"path": p, "status": r.status, "latency_ms": (time.time() - t0) * 1000})
    except urllib.error.HTTPError as e:
        out.append({"path": p, "status": e.code, "latency_ms": (time.time() - t0) * 1000})
    except Exception as e:
        out.append({"path": p, "status": 0, "latency_ms": (time.time() - t0) * 1000, "error": type(e).__name__})
print(json.dumps(out))"""


# The smoke fixture's own pinned image (bench/smoke.py IMAGE), already on the node; read from the
# Arena checkout when it is there.
SMOKE_PROBE_IMAGE = "python:3.12.10-alpine3.21@sha256:9c51ecce261773a684c8345b2d4673700055c513b4d54bc0719337d3e4ee552e"
SMOKE_PROBE_NAMESPACE = "default"     # outside the fault namespace: no extra pods or events there


def probe_argv(context: str, suite: str, *, image: str = SMOKE_PROBE_IMAGE) -> list[str]:
    """Read-only GETs against the user-facing service, run from inside the cluster (like the Arena's verify).

    The smoke suite's only workload is the faulted API itself (one replica, Recreate): crashloop,
    image-pull or a Pending pod leave no container to exec into, so the client is a short-lived pod
    of its own and an API that does not answer at all is measured as unreachable."""
    if suite == "full":
        paths = json.dumps([j.path for j in L.STOREFRONT_JOURNEYS])
        return ["kubectl", "--context", context, "-n", "shop", "exec", "deployment/frontend", "--",
                "/nodejs/bin/node", "-e", _NODE_PROBE.replace("PATHS", paths)]
    paths = json.dumps([j.path for j in L.SMOKE_JOURNEYS])
    return ["kubectl", "--context", context, "-n", SMOKE_PROBE_NAMESPACE, "run", f"omniops-probe-{uuid.uuid4().hex[:8]}",
            f"--image={image}", "--image-pull-policy=IfNotPresent", "--restart=Never", "--rm", "--attach", "--quiet",
            "--labels=app.kubernetes.io/name=omniops-probe", "--command", "--",
            "python", "-c", _PY_PROBE.replace("PATHS", paths)]


def smoke_image(arena_dir: Path) -> str:
    """``IMAGE`` from the Arena's bench/smoke.py (read, never executed), else the pinned default."""
    try:
        tree = ast.parse((arena_dir / "bench" / "smoke.py").read_text())
    except (OSError, SyntaxError):
        return SMOKE_PROBE_IMAGE
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "IMAGE" for t in node.targets):
            value = getattr(node.value, "value", None)
            if isinstance(value, str) and value:
                return value
    return SMOKE_PROBE_IMAGE


def journeys(suite: str) -> tuple[L.Journey, ...]:
    return L.STOREFRONT_JOURNEYS if suite == "full" else L.SMOKE_JOURNEYS


# ---------------------------------------------------------------------------------------------
# Slack surfaces
# ---------------------------------------------------------------------------------------------


@dataclass
class Posted:
    """Where the launch landed, and how the investigation was requested."""

    ts: str
    requested_by: str        # alert (automated post) | triage (OmniOps took up the report) | mention | none


class SlackSurface(Protocol):
    capture: str

    async def post(self, message: L.LaunchMessage) -> Posted: ...

    async def collect(self, thread_ts: str, *, timeout_s: float, quiet_s: float
                      ) -> tuple[R.SlackTranscript, list[Any]]: ...

    async def close(self) -> None: ...


def build_deps(channel: str) -> Any:
    """Process-wide deps for the in-process simulator.

    Memory is off and the flap window is zero: every scenario is a separate incident, so neither an
    earlier scenario's lessons nor its "already investigated" record may leak into the next one.
    """
    from omniops.agent.context import Deps
    from omniops.engine.catalog import Catalog
    from omniops.llm import make_llm
    from omniops.settings import Settings

    settings = Settings.from_env()
    settings.alert_channels = [channel]
    settings.benchmark_mode = True
    settings.write_mode = "stage"
    settings.flap_window_s = 0.0
    return Deps(catalog=Catalog.from_env(), llm=make_llm(settings), settings=settings, memory=None)


_last_ts = 0.0


def _next_ts() -> str:
    """Strictly increasing Slack-style message timestamps for injected events."""
    global _last_ts
    _last_ts = max(time.time(), _last_ts + 0.000001)
    return f"{_last_ts:.6f}"


async def await_triage(replied: Callable[[], Awaitable[bool]], settled: Callable[[], bool], *, grace_s: float,
                       poll_s: float) -> bool:
    """True once OmniOps replied in the thread of a person's post (its own triage took the report up).

    False when triage finished without replying (``settled``) or the grace period ran out: then, as in
    production, a person asks the bot. Never both, so one report never starts two runs.
    """
    deadline = time.monotonic() + grace_s
    while True:
        if await replied():
            return True
        if settled() or time.monotonic() >= deadline:
            return False
        await asyncio.sleep(poll_s)


class SimulatorSlack:
    """The launch message enters the real Bolt handlers in-process through ``omniops.slack.simulator``.

    Simulator contract used here: ``SlackSimulator(deps, alert_channel=...)``; ``inject_message(event)``
    stores one Events API ``message`` event and dispatches it into the real Bolt app (plus the
    ``app_mention`` Slack sends when it mentions the bot) and returns its ts; ``thread(channel, ts)``
    lists the thread's messages, each with its earlier versions under ``edits``;
    ``wait_idle(timeout)``; ``run_results``; ``bot_user_id``; ``bot.tasks``; ``close()``. One simulator
    per case, so no Slack-side state (de-dup, flap records, threads) carries over between scenarios.
    """

    capture = "simulator"

    def __init__(self, sim: Any, channel: str, *, mention_grace_s: float = 90.0, poll_s: float = 1.0) -> None:
        self.sim, self.channel = sim, channel
        self.mention_grace_s, self.poll_s = mention_grace_s, poll_s

    @classmethod
    async def create(cls, channel: str, deps: Any | None = None, *, mention_grace_s: float = 90.0
                     ) -> SimulatorSlack:
        from omniops.slack.simulator import SlackSimulator

        deps = deps if deps is not None else build_deps(channel)
        try:
            sim = SlackSimulator(deps, alert_channel=channel)
        except TypeError:                          # older simulator: alert channel from settings only
            sim = SlackSimulator(deps)
        if hasattr(sim, "start"):
            await _maybe_await(sim.start())
        return cls(sim, channel, mention_grace_s=mention_grace_s)

    @property
    def bot_user_id(self) -> str:
        return str(getattr(self.sim, "bot_user_id", None) or "U0OMNIOPS")

    async def _inject(self, event: dict[str, Any]) -> Any:
        for name in ("inject_event", "inject_message"):
            fn = getattr(self.sim, name, None)
            if fn is not None:
                return await _maybe_await(fn(event))
        raise RunnerError("the Slack simulator exposes neither inject_event nor inject_message")

    async def post(self, message: L.LaunchMessage) -> Posted:
        ts = _next_ts()
        root = str(await self._inject(message.slack_event(channel=self.channel, ts=ts)) or ts)
        if not message.human_root:
            return Posted(root, "alert")

        async def replied() -> bool:
            return any(m.get("user") == self.bot_user_id and m.get("ts") != root for m in self._thread(root))

        # the simulator knows when triage finished, so the grace period only bounds a hung triage call
        grace = max(self.mention_grace_s, TRIAGE_CAP_S) if self._bot_tasks() is not None else self.mention_grace_s
        if await await_triage(replied, self._bot_settled, grace_s=grace, poll_s=self.poll_s):
            return Posted(root, "triage")
        await self._inject(message.mention_event(channel=self.channel, thread_ts=root, ts=_next_ts(),
                                                 bot_user_id=self.bot_user_id))
        return Posted(root, "mention")

    def _bot_tasks(self) -> Any:
        return getattr(getattr(self.sim, "bot", None), "tasks", None)

    def _bot_settled(self) -> bool:
        """No handler task is running (triage finished); unknown simulators rely on the grace period."""
        tasks = self._bot_tasks()
        return tasks is not None and not any(not t.done() for t in tasks)

    async def collect(self, thread_ts: str, *, timeout_s: float, quiet_s: float
                      ) -> tuple[R.SlackTranscript, list[Any]]:
        idle = await self._wait_idle(timeout_s)
        transcript = R.SlackTranscript.from_dicts(self.channel, thread_ts, self._thread(thread_ts),
                                                  bot_user_id=self.bot_user_id)
        results = [r for r in self._results() if _get(_get(r, "alert"), "source_ts") in (thread_ts, None)]
        if not idle:
            results.append({"run_id": "harness", "tool_events": [], "staged_writes": [],
                            "warning": f"investigation still running after {timeout_s:.0f} s"})
        return transcript, results

    async def _wait_idle(self, timeout_s: float) -> bool:
        fn = self.sim.wait_idle
        try:
            takes_timeout = bool(inspect.signature(fn).parameters)
        except (TypeError, ValueError):
            takes_timeout = False
        try:
            done = await asyncio.wait_for(_maybe_await(fn(timeout_s) if takes_timeout else fn()), timeout_s + 5)
        except TimeoutError:
            return False
        return done is not False

    def _thread(self, thread_ts: str) -> list[dict[str, Any]]:
        if hasattr(self.sim, "thread"):
            return list(self.sim.thread(self.channel, thread_ts))
        raw = self.sim.transcript() if callable(getattr(self.sim, "transcript", None)) else []
        return [m for m in raw if m.get("channel", self.channel) == self.channel
                and thread_ts in (m.get("ts"), m.get("thread_ts"))]

    def _results(self) -> list[Any]:
        value = getattr(self.sim, "run_results", [])
        return list(value() if callable(value) else value)

    async def close(self) -> None:
        if hasattr(self.sim, "close"):
            await _maybe_await(self.sim.close())


class RealSlack:
    """A real channel: harness identities post, OmniOps (``omniops serve``) answers in the thread.

    The bot ignores its own posts, so launches come from other identities: Grafana's own Slack contact
    point for alerts (adopted when ``OMNIOPS_ARENA_GRAFANA_POSTS=1``) or ``OMNIOPS_ARENA_ALERT_TOKEN`` (a
    second app's bot token), and ``OMNIOPS_ARENA_POSTER_TOKEN`` (a person's user token) for customer
    reports and the on-call "investigate" mention. Replies are polled; edits between polls can be
    missed, which is recorded as ``capture=polling``.
    """

    capture = "polling"

    def __init__(self, bot_token: str, poster_token: str, channel: str, *, adopt_grafana: bool,
                 alert_token: str | None = None, poll_s: float = 5.0, mention_grace_s: float = 90.0) -> None:
        from slack_sdk.web.async_client import AsyncWebClient

        self.reader = AsyncWebClient(token=bot_token)
        self.poster = AsyncWebClient(token=poster_token)
        self.alerter = AsyncWebClient(token=alert_token) if alert_token else None
        self.channel = channel
        self.adopt_grafana = adopt_grafana
        self.poll_s = poll_s
        self.mention_grace_s = mention_grace_s
        self.bot_user_id: str | None = None
        self.bot_id: str | None = None
        self.notes: list[str] = []

    async def start(self) -> None:
        auth = await self.reader.auth_test()
        self.bot_user_id, self.bot_id = auth.get("user_id"), auth.get("bot_id")
        poster = await self.poster.auth_test()
        if poster.get("bot_id"):
            raise RunnerError("OMNIOPS_ARENA_POSTER_TOKEN must be a person's user token (xoxp-): customer "
                              "reports and the 'investigate' mention come from people")
        if self.alerter is not None:
            alerter = await self.alerter.auth_test()
            if not alerter.get("bot_id") or alerter.get("user_id") == self.bot_user_id:
                raise RunnerError("OMNIOPS_ARENA_ALERT_TOKEN must be another app's bot token, not OmniOps' own")

    async def post(self, message: L.LaunchMessage) -> Posted:
        if message.path == "alert":
            if self.adopt_grafana:
                ts = await self._find_grafana_post(float(datetime.fromisoformat(message.since).timestamp()))
                if ts:
                    return Posted(ts, "alert")
                self.notes.append("Grafana's own Slack post was not found; the harness posted the alert")
            if self.alerter is not None:
                resp = await self.alerter.chat_postMessage(channel=self.channel, **message.post_kwargs())
                return Posted(str(resp["ts"]), "alert")
            self.notes.append("no alert identity: a person posted the alert text")
        kwargs = message.post_kwargs()
        kwargs.pop("username", None)                 # a person's post carries the person's identity
        kwargs.pop("icon_url", None)
        resp = await self.poster.chat_postMessage(channel=self.channel, **kwargs)
        ts = str(resp["ts"])

        async def replied() -> bool:
            resp = await self.reader.conversations_replies(channel=self.channel, ts=ts, limit=20)
            return any(m.get("ts") != ts and (m.get("user") == self.bot_user_id or
                                               (self.bot_id and m.get("bot_id") == self.bot_id))
                       for m in resp.get("messages") or [])

        if await await_triage(replied, lambda: False, grace_s=self.mention_grace_s, poll_s=self.poll_s):
            return Posted(ts, "triage")
        await self.poster.chat_postMessage(channel=self.channel, thread_ts=ts,
                                           text=message.mention_text(self.bot_user_id or ""))
        return Posted(ts, "mention")

    async def _find_grafana_post(self, since: float, wait_s: float = 120.0) -> str | None:
        """Grafana's own Slack contact point may already have posted this notification: use it."""
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            resp = await self.reader.conversations_history(channel=self.channel, oldest=f"{since:.6f}", limit=50)
            for m in reversed(resp.get("messages") or []):
                if any(str(a.get("title", "")).startswith("[FIRING") for a in m.get("attachments") or []):
                    return str(m["ts"])
            await asyncio.sleep(self.poll_s)
        return None

    async def collect(self, thread_ts: str, *, timeout_s: float, quiet_s: float
                      ) -> tuple[R.SlackTranscript, list[Any]]:
        seen: dict[str, dict[str, Any]] = {}
        deadline = time.monotonic() + timeout_s
        last_change = time.monotonic()
        while time.monotonic() < deadline:
            resp = await self.reader.conversations_replies(channel=self.channel, ts=thread_ts,
                                                           include_all_metadata=True, limit=200)
            for m in resp.get("messages") or []:
                version = {"text": m.get("text", ""), "blocks": m.get("blocks") or [],
                           "attachments": m.get("attachments") or [], "metadata": m.get("metadata"),
                           "seen_at": time.time()}
                cur = seen.get(m["ts"])
                if cur is None:
                    seen[m["ts"]] = {"ts": m["ts"], "user": m.get("user"), "bot_id": m.get("bot_id"),
                                     "edits": [], **version}
                    last_change = time.monotonic()
                elif _content(cur) != _content(version):
                    cur["edits"].append({k: cur[k] for k in ("text", "blocks", "attachments", "metadata", "seen_at")})
                    cur.update(version)
                    last_change = time.monotonic()
            if _status_frozen(seen.values()) and time.monotonic() - last_change >= quiet_s:
                break
            await asyncio.sleep(self.poll_s)
        transcript = R.SlackTranscript.from_dicts(self.channel, thread_ts, seen.values(), bot_user_id=self.bot_user_id,
                                                  bot_id=self.bot_id, capture=self.capture)
        return transcript, load_case_results(thread_ts)

    async def close(self) -> None:
        return None


def _content(v: dict[str, Any]) -> str:
    return json.dumps([v.get("text"), v.get("blocks"), v.get("attachments")], sort_keys=True, default=str)


def _status_frozen(messages: Any) -> bool:
    for m in messages:
        meta = m.get("metadata") or {}
        if meta.get("event_type") == R.STATUS_EVENT_TYPE:
            return (meta.get("event_payload") or {}).get("state") in ("finished", "failed")
    return False


def load_case_results(thread_ts: str) -> list[dict[str, Any]]:
    """RunResult case files the bot wrote for this thread (``OMNIOPS_CASE_DIR``), when the bot shares this disk."""
    from omniops.settings import Settings

    out = []
    for path in sorted(Path(Settings.from_env().case_dir).glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        alert = data.get("alert") or {}
        if thread_ts in (alert.get("source_ts"), data.get("thread_ts"), data.get("source_ts")):
            out.append(data)
    return out


async def _maybe_await(value: Any) -> Any:
    if isinstance(value, Awaitable):
        return await value
    return value


# ---------------------------------------------------------------------------------------------
# Progress (resumable state)
# ---------------------------------------------------------------------------------------------


class Progress:
    def __init__(self, path: Path, dry_run: bool) -> None:
        self.path, self.dry_run = path, dry_run
        self.data: dict[str, Any] = json.loads(path.read_text()) if path.is_file() else {"scenarios": {}}

    def entry(self, scenario: str) -> dict[str, Any]:
        return self.data["scenarios"].setdefault(scenario, {"attempt": 0, "case": "", "stages": {}, "status": "pending",
                                                            "problems": []})

    def resume_case(self, scenario: str) -> tuple[str, int]:
        """(case, first stage index): continue after records, otherwise start a new attempt from baseline."""
        e = self.entry(scenario)
        done = e["stages"]
        if e["case"] and "records" in done:
            nxt = next((i for i, s in enumerate(STAGES) if s not in done), len(STAGES))
            return e["case"], max(nxt, RESUMABLE_FROM)
        e["attempt"] += 1
        e["case"] = f"{scenario}-a{e['attempt']}"
        e["stages"], e["status"], e["problems"] = {}, "running", []
        self.save()
        return e["case"], 0

    def mark(self, scenario: str, stage: str, **info: Any) -> None:
        self.entry(scenario)["stages"][stage] = {"at": _now_iso(), **info}
        self.save()

    def problem(self, scenario: str, msg: str) -> None:
        self.entry(scenario)["problems"].append(f"{_now_iso()} {msg}")
        self.save()

    def save(self) -> None:
        if self.dry_run:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
        tmp.replace(self.path)


# ---------------------------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------------------------


@dataclass
class Stage:
    name: str
    describe: list[str]
    run: Callable[[], Awaitable[dict[str, Any] | None]]


@dataclass
class CaseState:
    scenario: str
    case: str
    fault_at: datetime | None = None
    baseline_probe: list[L.EndpointProbe] | None = None
    launch: L.LaunchMessage | None = None
    thread_ts: str | None = None
    transcript: R.SlackTranscript | None = None
    results: list[Any] = field(default_factory=list)
    slack: SlackSurface | None = None


class ArenaRunner:
    """Runs scenarios end to end. ``slack`` / ``alerts`` / ``shell`` are injectable for offline tests;
    by default the simulator surface is created fresh for every case and real Slack is shared."""

    def __init__(self, cfg: RunnerConfig, *, slack: SlackSurface | None = None, alerts: AlertFeed | None = None,
                 shell: Shell | None = None, printer: Callable[[str], None] = print) -> None:
        self.cfg = cfg
        self.print = printer
        self.shell = shell or Shell(cfg.dry_run, printer=printer)
        self.slack = slack
        self._deps: Any = None
        self.kube_view: dict[str, Any] | None = None     # pre-flight result, recorded in omniops-arena.json
        self.alerts = alerts or AlertFeed(cfg.grafana_url, os.environ.get("GRAFANA_TOKEN"),
                                          bridge_host=cfg.bridge_host, bridge_port=cfg.bridge_port)
        self.progress = Progress(cfg.run_dir / "progress.json", cfg.dry_run)
        self.catalog = arena_catalog(cfg.arena_dir, cfg.suite)
        unknown = [s for s in cfg.scenarios if s not in self.catalog]
        if unknown:
            raise RunnerError(f"unknown {cfg.suite} scenarios: {', '.join(unknown)}")
        if cfg.gitops and cfg.suite != "full":
            raise RunnerError("the GitOps path exists for the full suite only")
        if not cfg.scenarios:
            cfg.scenarios = sorted(self.catalog)

    # -- commands ----------------------------------------------------------------------------

    def bench(self, case: str, *args: str) -> list[str]:
        return [self.cfg.python, "-m", "bench", "--run", str(self.cfg.run_file), "--case", case, *args]

    def gitops_cmd(self, op: str) -> list[str]:
        return [self.cfg.python, "-m", "bench.gitops", "--run", str(self.cfg.run_file), op]

    async def arena(self, argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        return await self.shell.run(argv, cwd=self.cfg.arena_dir, timeout=timeout)

    # -- top level ---------------------------------------------------------------------------

    async def run(self) -> int:
        cfg = self.cfg
        if cfg.dry_run:
            self.print_plan()
            return 0
        self.kube_view = await self.check_kube_view()
        self.prepare()
        if self.slack is None and cfg.real_slack:
            self.slack = await self.make_real_slack()
        await self.alerts.start()
        failures = 0
        try:
            for scenario in cfg.scenarios:
                e = self.progress.entry(scenario)
                if e["status"] == "complete":
                    self.print(f"== {scenario}: complete in {e['case']}, skipping")
                    continue
                try:
                    await self.run_case(scenario)
                except Exception as exc:  # noqa: BLE001 - one scenario's failure must not stop the suite
                    failures += 1
                    self.progress.problem(scenario, f"{type(exc).__name__}: {exc}")
                    self.progress.entry(scenario)["status"] = "failed"
                    self.progress.save()
                    self.print(f"!! {scenario} failed: {exc}")
            await self.summarize()
        finally:
            await self.alerts.close()
            if self.slack is not None:
                await self.slack.close()
        return 1 if failures else 0

    def prepare(self) -> None:
        cfg = self.cfg
        cfg.run_dir.mkdir(parents=True, exist_ok=True)
        if not cfg.run_file.exists():
            cfg.run_file.write_text(json.dumps(arena_run_config(cfg, cfg.scenarios[0]), indent=2) + "\n")
        meta = cfg.run_dir / "omniops-arena.json"
        if not meta.exists():
            meta.write_text(json.dumps(self.metadata(), indent=2, default=str) + "\n")
        if cfg.judge and not os.environ.get(cfg.judge_key_env):
            self.print(f"WARNING: {cfg.judge_key_env} is not set: cases will be archived but not judged")
        self.print(cfg.notice)

    def metadata(self) -> dict[str, Any]:
        cfg = self.cfg
        return {
            "run_id": cfg.run_id, "suite": cfg.suite, "scenarios": cfg.scenarios,
            "deployment": "gitops" if cfg.gitops else "direct", "slack": "real" if cfg.real_slack else "simulator",
            "context": cfg.context, "arena_dir": str(cfg.arena_dir), "arena_commit": _git_head(cfg.arena_dir),
            "judge": {"provider": "openai-compatible", "model": cfg.judge_model, "base_url": cfg.judge_base_url,
                      "notice": cfg.notice},
            "detection": "not_measured: OmniOps is launched by the harness from a Grafana alert or a customer report",
            "final_selection": R.FINAL_SELECTION,
            "access": {**access_record(), "kubernetes_view_check": self.kube_view},
            "started": _now_iso(),
        }

    async def check_kube_view(self) -> dict[str, Any]:
        """List every ConfigMap exactly as OmniOps' kubernetes source would (its kubeconfig, context and
        API URL) and refuse the run when an Arena control namespace is visible: its suite-state ConfigMap
        names the active scenario, which OmniOps' configmaps table and change feed would read (3.16)."""
        base = kube_view_argv(os.environ)
        if base is None:
            return {"checked": False, "note": "kubernetes source not configured (no KUBECONFIG)"}
        argv = [*base, "get", "configmaps", "--all-namespaces", "-o", "jsonpath={.items[*].metadata.namespace}"]
        try:
            proc = await self.shell.run(argv, timeout=60, check=False)
        except (RunnerError, OSError) as exc:
            proc = subprocess.CompletedProcess(argv, 1, "", str(exc))
        if proc.returncode:
            msg = f"OmniOps' Kubernetes view could not be listed: {(proc.stderr or proc.stdout).strip()[-300:]}"
            self.print(f"WARNING: {msg} (investigations will have no Kubernetes data)")
            return {"checked": False, "error": msg}
        visible = sorted(set(proc.stdout.split()) & set(CONTROL_NAMESPACES))
        if visible:
            raise RunnerError(
                f"OmniOps' Kubernetes identity can read namespace {', '.join(visible)}: the Arena's suite-state "
                "ConfigMap there names the active scenario, so the answer would leak into OmniOps' evidence. "
                "Give OmniOps the filtered read-only view: deploy/arena/port-forward.sh start, then "
                f"deploy/arena/omniops-env.sh, and use {self.cfg.env_file or 'its env file'} "
                "(KUBECONFIG / KUBE_CONTEXT / K8S_API_URL).")
        return {"checked": True, "hidden_namespaces": list(CONTROL_NAMESPACES)}

    async def make_real_slack(self) -> RealSlack:
        bot, poster = os.environ.get("SLACK_BOT_TOKEN"), os.environ.get("OMNIOPS_ARENA_POSTER_TOKEN")
        if not bot or not poster or not os.environ.get("OMNIOPS_ARENA_CHANNEL"):
            raise RunnerError("--real-slack needs SLACK_BOT_TOKEN, OMNIOPS_ARENA_POSTER_TOKEN and OMNIOPS_ARENA_CHANNEL "
                              "(see docs/BENCH_ARENA.md, 'Slack app')")
        slack = RealSlack(bot, poster, self.cfg.channel, adopt_grafana=self.cfg.adopt_grafana_posts,
                          alert_token=os.environ.get("OMNIOPS_ARENA_ALERT_TOKEN") or None,
                          mention_grace_s=self.cfg.mention_grace_s)
        await slack.start()
        return slack

    async def open_slack(self) -> SlackSurface:
        """The shared surface (real Slack or an injected one), else a fresh simulator for this case."""
        if self.slack is not None:
            return self.slack
        if self._deps is None:
            self._deps = build_deps(self.cfg.channel)
        return await SimulatorSlack.create(self.cfg.channel, self._deps, mention_grace_s=self.cfg.mention_grace_s)

    # -- one scenario ------------------------------------------------------------------------

    async def run_case(self, scenario: str) -> None:
        case, start = self.progress.resume_case(scenario)
        state = CaseState(scenario, case)
        self.shell.log_path = self.cfg.run_dir / case / "runner.log"
        self.print(f"== {scenario}: case {case}" + (f" (resuming at {STAGES[start]})" if start else ""))
        self.set_run_scenario(scenario)
        try:
            for stage in self.stages(state)[start:]:
                self.print(f"-- {scenario} {stage.name}")
                info = await stage.run() or {}
                self.progress.mark(scenario, stage.name, **info)
        finally:
            if state.slack is not None and state.slack is not self.slack:   # this case's simulator
                await state.slack.close()
        e = self.progress.entry(scenario)
        e["status"] = "complete"
        self.progress.save()

    def set_run_scenario(self, scenario: str) -> None:
        """``bench.gitops fault`` reads the scenario from the run file, so keep it current."""
        run = json.loads(self.cfg.run_file.read_text())
        if run.get("scenario") != scenario:
            run["scenario"] = scenario
            self.cfg.run_file.write_text(json.dumps(run, indent=2) + "\n")

    def stages(self, st: CaseState) -> list[Stage]:
        cfg, s, c = self.cfg, st.scenario, st.case
        direct = not cfg.gitops
        alerts_note = (f"wait <= {cfg.clear_wait_s:.0f} s for Grafana alerts labelled omniops=arena to clear"
                       if self.alerts.enabled or cfg.dry_run else "Grafana not configured: no alert wait")
        if direct:
            baseline = [_show(self.bench(c, "reset", "--confirm-disposable")), alerts_note]
            fault = [_show(self.bench(c, "fault", "--scenario", s))]
            reset = [_show(self.bench(c, "reset", "--confirm-disposable"))]
        else:
            baseline = ["only when a scenario is still active: " + _show(self.gitops_cmd("reset")),
                        *self.push_lines("Retire benchmark workload"), _show(self.gitops_cmd("sync")), alerts_note]
            fault = [f"set scenario={s} in {cfg.run_file}", _show(self.gitops_cmd("fault")),
                     *self.push_lines("Enable benchmark workload"), _show(self.gitops_cmd("sync"))]
            reset = [_show(self.gitops_cmd("reset")), *self.push_lines("Retire benchmark workload"),
                     _show(self.gitops_cmd("sync"))]
        verify = ([_show(self.bench(c, "verify", "--scenario", s, "--timeout", "240"))] if direct and cfg.suite == "full"
                  else ["covered by bench.gitops sync"] if cfg.gitops else ["skipped: automated verify supports full only"])
        slack = "real Slack channel (poster identity)" if cfg.real_slack else "Slack simulator (real Bolt handlers)"
        archive = self.bench(c, "archive", "--scenario", s, "--suite", cfg.suite, "--detection", "not_measured")
        judge_line = (_show(self.bench(c, "judge")) + f"   # {cfg.judge_model}, NON-OFFICIAL" if cfg.judge
                      else "skipped (--no-judge)")
        return [
            Stage("baseline", baseline, lambda: self.do_baseline(st)),
            Stage("probe", [_show(self.probe_argv()[:-1]) + " <read-only probe script>"],
                  lambda: self.do_probe(st, baseline=True)),
            Stage("fault", fault, lambda: self.do_fault(st)),
            Stage("verify", verify, lambda: self.do_verify(st)),
            Stage("detect", [(f"poll {cfg.grafana_url or '$GRAFANA_URL'}/api/alertmanager/grafana/api/v2/alerts "
                              f"(+ webhook bridge :{cfg.bridge_port}) <= {cfg.alert_wait_s:.0f} s; else probe and "
                              "build a customer report from user-visible symptoms; leak-check against judge-only "
                              "truth")],
                  lambda: self.do_detect(st)),
            Stage("launch", [f"post the launch message into the {slack} channel {cfg.channel}",
                             (f"customer report: if OmniOps' own triage has not replied within {cfg.mention_grace_s:.0f} s, "
                              "the on-call user asks '@OmniOps investigate' in the thread")],
                  lambda: self.do_launch(st)),
            Stage("investigate", [f"wait <= {self.timeout_label()} for OmniOps' thread reply; capture every version"],
                  lambda: self.do_investigate(st)),
            Stage("records", [f"write {cfg.run_dir / c}/final.txt, intermediate.txt, actions.txt, transcript.json"],
                  lambda: self.do_records(st)),
            Stage("archive", [_show(archive), "validate archive.json (archive-v1, final_answer == final.txt)"],
                  lambda: self.do_archive(st, archive)),
            Stage("packet", [_show(self.bench(c, "packet"))], lambda: self.do_packet(st)),
            Stage("judge", [judge_line], lambda: self.do_judge(st)),
            Stage("reset", reset, lambda: self.do_reset(st)),
        ]

    def push_lines(self, message: str) -> list[str]:
        if self.cfg.dry_run and not self.cfg.gitops_config.is_file():
            repo, path, rev = "<fault checkout>", "<fault path>", "main"
        else:
            fault = gitops_settings(self.cfg)["fault_repo"]
            repo, path, rev = fault["checkout"], fault["path"], fault["revision"]
        who = "runner (--allow-push)" if self.cfg.allow_push else "operator (runner pauses)"
        # quoted like the commands themselves: the checkouts live under a path with spaces
        return [f"by {who}: " + " && ".join(_show(c) for c in _push_commands(repo, path, rev, message))]

    def timeout_label(self) -> str:
        try:
            return f"{investigation_timeout(self.cfg):.0f} s"
        except Exception:  # noqa: BLE001 - only a label for the plan
            return "investigation + report + watchdog budget"

    def print_plan(self) -> None:
        cfg = self.cfg
        self.print("OmniOps Arena runner - DRY RUN (nothing is executed; no cluster, Slack or API is touched)")
        self.print(f"run {cfg.run_id} | suite {cfg.suite} | deployment {'gitops' if cfg.gitops else 'direct'} | "
                   f"slack {'real' if cfg.real_slack else 'simulator'} | arena {cfg.arena_dir}")
        self.print(f"run file {cfg.run_file} (written on a real run) | product label {cfg.product!r}")
        self.print(cfg.notice)
        self.print(f"pre-flight: list ConfigMaps as OmniOps' Kubernetes identity; refuse the run if "
                   f"{', '.join(CONTROL_NAMESPACES)} (the Arena's suite state) is visible")
        for scenario in cfg.scenarios:
            case = f"{scenario}-a1"
            self.print(f"\n== {scenario} (case {case})")
            for i, stage in enumerate(self.stages(CaseState(scenario, case)), 1):
                for j, line in enumerate(stage.describe):
                    self.print(f"{i:>2}. {stage.name:<12} {line}" if j == 0 else f"    {'':<12} {line}")
        self.print("\n== report")
        self.print(f"    $ cd {shlex.quote(str(cfg.arena_dir))} && "
                   f"{_show([cfg.python, '-m', 'bench', '--run', str(cfg.run_file), 'report', '--summary'])} "
                   "<latest judgment.json per scenario> --records <their archive.json>  > summary.csv")

    # -- stage implementations ---------------------------------------------------------------

    async def do_baseline(self, st: CaseState) -> dict[str, Any]:
        if self.cfg.gitops:
            if await self.gitops_active_scenario():
                await self.gitops_retire()
        else:
            await self.arena(self.bench(st.case, "reset", "--confirm-disposable"), 1800)
        still = await self.alerts.wait_clear(self.cfg.clear_wait_s) if self.alerts.enabled else []
        if still:
            self.progress.problem(st.scenario, f"alerts still firing at baseline (excluded by start time): {still}")
        return {"alerts_still_firing": still}

    async def do_probe(self, st: CaseState, *, baseline: bool) -> dict[str, Any]:
        probes, error = await self.probe()
        if baseline:
            st.baseline_probe = probes
        self.write_json(st, "probe-baseline.json" if baseline else "probe-fault.json",
                        {"probes": [p.__dict__ for p in probes], "error": error})
        return {"endpoints": len(probes), "error": error}

    def probe_argv(self) -> list[str]:
        return probe_argv(self.cfg.context, self.cfg.suite, image=smoke_image(self.cfg.arena_dir))

    async def probe(self) -> tuple[list[L.EndpointProbe], str | None]:
        try:
            proc = await self.shell.run(self.probe_argv(), timeout=120, check=False, stdin="")
            if proc.returncode:
                return [], f"probe exited {proc.returncode}: {proc.stderr.strip()[-300:]}"
            # the JSON line, whatever kubectl prints around it
            rows = json.loads(next((ln for ln in reversed(proc.stdout.strip().splitlines()) if ln.startswith("[")), ""))
            return [L.EndpointProbe(r["path"], int(r["status"]), float(r["latency_ms"]), r.get("error")) for r in rows], None
        except (RunnerError, ValueError, IndexError, KeyError) as exc:
            return [], f"probe failed: {exc}"

    async def do_fault(self, st: CaseState) -> dict[str, Any]:
        st.fault_at = datetime.now(UTC)
        if self.cfg.gitops:
            await self.arena(self.gitops_cmd("fault"), 600)
            await self.push_fault_repo("Enable benchmark workload")
            await self.arena(self.gitops_cmd("sync"), 1800)
        else:
            await self.arena(self.bench(st.case, "fault", "--scenario", st.scenario), 600)
        return {"fault_at": st.fault_at.isoformat()}

    async def do_verify(self, st: CaseState) -> dict[str, Any]:
        if self.cfg.gitops:
            return {"observed": True, "by": "bench.gitops sync"}
        if self.cfg.suite != "full":
            return {"observed": None, "note": "automated verify supports full only"}
        argv = self.bench(st.case, "verify", "--scenario", st.scenario, "--timeout", "240")
        proc = await self.shell.run(argv, cwd=self.cfg.arena_dir, timeout=600, check=False)
        if proc.returncode:
            raise RunnerError("fault was not observed by bench verify: the case is not a valid test "
                              "(Arena: successful application alone does not establish a valid test)")
        return {"observed": True}

    async def do_detect(self, st: CaseState) -> dict[str, Any]:
        since = st.fault_at or datetime.now(UTC)
        selected: list[L.FiringAlert] = []
        excluded: list[dict[str, str]] = []
        if self.alerts.enabled and self.cfg.alert_wait_s > 0:
            selected, excluded = await self.alerts.wait_for(since, wait_s=self.cfg.alert_wait_s,
                                                            settle_s=self.cfg.alert_settle_s)
        await self.do_probe(st, baseline=False)
        fault_probe = json.loads((self.cfg.run_dir / st.case / "probe-fault.json").read_text())
        probes = fault_probe["probes"]
        if fault_probe["error"]:                     # 4.1: no symptoms because the probe broke, not the service
            self.progress.problem(st.scenario, f"user-visible probe failed at fault time: {fault_probe['error']}")
        current = [L.EndpointProbe(**p) for p in probes]
        symptoms = L.user_visible_symptoms(journeys(self.cfg.suite), st.baseline_probe, current)
        msg = L.build_launch(selected, symptoms, since=since, grafana_url=self.cfg.grafana_url or "http://localhost:3000",
                             subject=L.SUBJECTS[self.cfg.suite], excluded=excluded)
        hits = self.leaks(msg)
        st.launch = msg
        self.write_json(st, "launch.json", {**msg.to_dict(), "leak_check": hits, "alert_feed_errors": self.alerts.errors})
        if hits:
            raise RunnerError(f"launch message would leak ground truth ({hits[:3]}); refusing to post")
        if msg.path == "customer_report" and not symptoms:
            self.progress.problem(st.scenario, "no alert and no user-visible symptom: generic customer report used")
        return {"path": msg.path, "alerts": len(selected), "symptoms": len(symptoms)}

    def leaks(self, msg: L.LaunchMessage) -> list[str]:
        """Answer-key sentences and scenario ids must never appear. Alerts legitimately name workloads
        (pod labels) and generic symptoms ("OOM"), so for them only multi-word ids and sentences count."""
        phrases, tokens, names = L.forbidden_from_truths(self.catalog.values())
        if msg.path == "alert":
            return L.leak_check(msg.visible_text(), phrases=phrases, scenario_ids=[n for n in names if "-" in n])
        return L.leak_check(msg.visible_text(), phrases=phrases, tokens=tokens, scenario_ids=names)

    async def do_launch(self, st: CaseState) -> dict[str, Any]:
        if st.launch is None:
            raise RunnerError("no launch message")
        st.slack = await self.open_slack()
        posted = await st.slack.post(st.launch)
        st.thread_ts = posted.ts
        notes = list(getattr(st.slack, "notes", []))
        if notes:
            st.slack.notes.clear()                   # type: ignore[attr-defined]  # RealSlack: per-launch notes
        for note in notes:
            self.progress.problem(st.scenario, note)
        return {"thread_ts": st.thread_ts, "path": st.launch.path, "channel": self.cfg.channel,
                "requested_by": posted.requested_by, "notes": notes}

    async def do_investigate(self, st: CaseState) -> dict[str, Any]:
        if st.slack is None or st.thread_ts is None:
            raise RunnerError("nothing was launched")
        started = time.monotonic()
        st.transcript, st.results = await st.slack.collect(st.thread_ts, timeout_s=investigation_timeout(self.cfg),
                                                           quiet_s=self.cfg.quiet_s)
        self.write_json(st, "transcript.json", st.transcript.to_dict())
        self.write_json(st, "run_results.json", [_result_digest(r) for r in st.results])
        bot = st.transcript.bot_messages()
        if not bot:
            self.progress.problem(st.scenario, "OmniOps posted nothing in the launch thread")
        return {"bot_messages": len(bot), "elapsed_s": round(time.monotonic() - started, 1),
                "capture": st.transcript.capture}

    async def do_records(self, st: CaseState) -> dict[str, Any]:
        final, source = R.render_final(st.transcript, st.results)
        inter = R.render_intermediate(st.transcript)
        actions = R.render_actions(st.results) if st.results else ""
        d = self.cfg.run_dir / st.case
        (d / "final.txt").write_text(final)
        for name, text in (("intermediate.txt", inter), ("actions.txt", actions)):
            if text:                                # missing evidence stays missing (Arena rule)
                (d / name).write_text(text)
        if source != "slack":
            self.progress.problem(st.scenario, f"final answer source is {source!r}, not the Slack thread")
        if not st.results or not any(_get(r, "tool_events") for r in st.results):
            self.progress.problem(st.scenario, "zero tool calls recorded")
        return {"final_source": source, "final_chars": len(final), "intermediate": bool(inter), "actions": bool(actions)}

    async def do_archive(self, st: CaseState, argv: list[str]) -> dict[str, Any]:
        d = self.cfg.run_dir / st.case
        if not (d / "archive.json").exists():
            await self.arena(argv, 300)
        record = json.loads((d / "archive.json").read_text())
        R.validate_archive(record, scenarios=self.catalog, final_text=(d / "final.txt").read_text())
        warnings = R.record_warnings(record)
        for w in warnings:
            self.progress.problem(st.scenario, w)
        return {"warnings": warnings}

    async def do_packet(self, st: CaseState) -> dict[str, Any]:
        if not (self.cfg.run_dir / st.case / "packet.json").exists():
            await self.arena(self.bench(st.case, "packet"), 300)
        return {}

    async def do_judge(self, st: CaseState) -> dict[str, Any]:
        out = self.cfg.run_dir / st.case / "judgment.json"
        if out.exists():
            return {"judged": True}
        if not self.cfg.judge or not os.environ.get(self.cfg.judge_key_env):
            self.progress.problem(st.scenario, f"unscored: judge disabled or {self.cfg.judge_key_env} missing")
            return {"judged": False}
        await self.arena(self.bench(st.case, "judge"), 1200)
        verdicts = {k: v.get("verdict") for k, v in json.loads(out.read_text()).items()
                    if isinstance(v, dict) and "verdict" in v}
        return {"judged": True, "verdicts": verdicts}

    async def do_reset(self, st: CaseState) -> dict[str, Any]:
        if self.cfg.gitops:
            await self.gitops_retire()
        else:
            await self.arena(self.bench(st.case, "reset", "--confirm-disposable"), 1800)
        return {}

    # -- GitOps ------------------------------------------------------------------------------

    async def gitops_active_scenario(self) -> str:
        proc = await self.shell.run(["kubectl", "--context", self.cfg.context, "-n", "benchmark-control", "get",
                                     "configmap", "suite-state", "-o", "jsonpath={.data.scenario}"], check=False)
        return proc.stdout.strip() if proc.returncode == 0 else ""

    async def gitops_retire(self) -> None:
        await self.arena(self.gitops_cmd("reset"), 600)
        await self.push_fault_repo("Retire benchmark workload")
        await self.arena(self.gitops_cmd("sync"), 1800)

    async def push_fault_repo(self, message: str) -> None:
        fault = gitops_settings(self.cfg)["fault_repo"]
        repo, path, rev = fault["checkout"], fault["path"], fault["revision"]
        status = await self.shell.run(["git", "-C", repo, "status", "--porcelain", "--", path])
        if not status.stdout.strip():
            return
        cmds = _push_commands(repo, path, rev, message)
        if not self.cfg.allow_push:
            self.print("The fault repository has a prepared change. Review it, then run:")
            for cmd in cmds:
                self.print("  " + _show(cmd))
            if not sys.stdin.isatty():
                raise RunnerError("GitOps commit needs operator approval: rerun with --allow-push or run interactively")
            await asyncio.to_thread(input, "Press Enter once the commit is pushed... ")
            return
        for cmd in cmds:
            await self.shell.run(cmd, timeout=180)

    # -- summary -----------------------------------------------------------------------------

    async def summarize(self) -> None:
        judgments, archives = [], []
        for scenario in self.cfg.scenarios:
            case = self.progress.entry(scenario).get("case")
            d = self.cfg.run_dir / case if case else None
            if d and (d / "archive.json").exists():
                archives.append(str(d / "archive.json"))
                if (d / "judgment.json").exists():
                    judgments.append(str(d / "judgment.json"))
        if archives:
            argv = [self.cfg.python, "-m", "bench", "--run", str(self.cfg.run_file), "report", "--summary",
                    *judgments, "--records", *archives]
            proc = await self.shell.run(argv, cwd=self.cfg.arena_dir, timeout=300, check=False)
            if proc.returncode == 0:
                (self.cfg.run_dir / "summary.csv").write_text(proc.stdout)
                self.print(proc.stdout)
        self.print(self.cfg.notice)
        for scenario in self.cfg.scenarios:
            e = self.progress.entry(scenario)
            self.print(f"{scenario:<26} {e['status']:<9} {e.get('case', '')}"
                       + (f"  problems: {len(e['problems'])}" if e["problems"] else ""))

    def write_json(self, st: CaseState, name: str, value: Any) -> None:
        d = self.cfg.run_dir / st.case
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(json.dumps(value, indent=2, default=str) + "\n")


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------


def kube_view_argv(env: Any) -> list[str] | None:
    """kubectl as OmniOps' kubernetes source connects: first KUBECONFIG entry, its context, K8S_API_URL."""
    kubeconfig = (env.get("KUBECONFIG") or "").split(os.pathsep)[0]
    if not kubeconfig:
        return None
    argv = ["kubectl", "--kubeconfig", os.path.expanduser(kubeconfig)]
    context = env.get("KUBE_CONTEXT") or env.get("KUBERNETES_CONTEXT")
    if context:
        argv += ["--context", context]
    if env.get("K8S_API_URL"):
        argv += ["--server", env["K8S_API_URL"]]
    return argv


def _csv(name: str) -> list[str]:
    return [x for x in os.environ.get(name, "").split(",") if x]


def access_record() -> dict[str, Any]:
    """Which evidence OmniOps can reach in this run (the Arena asks products to record repository access)."""
    out: dict[str, Any] = {"github_repos": _csv("GITHUB_REPOS"),
                           "fault_repo_access": os.environ.get("OMNIOPS_ARENA_FAULT_REPO_ACCESS", "0") == "1",
                           "note": "configured scope, not an access control: both GitOps repositories are public "
                                   "and Argo CD lists the revisions it applied from each",
                           "kubernetes": {"kubeconfig": (os.environ.get("KUBECONFIG") or "").split(os.pathsep)[0] or None,
                                          "context": os.environ.get("KUBE_CONTEXT") or os.environ.get("KUBERNETES_CONTEXT"),
                                          "api_url": os.environ.get("K8S_API_URL") or None,
                                          "hidden_namespaces": _csv("OMNIOPS_ARENA_KUBE_HIDDEN_NAMESPACES"),
                                          "note": "read-only ServiceAccount behind deploy/arena's namespace-filtering "
                                                  "proxy (GET only; no credential in OmniOps' kubeconfig)"},
                           "argocd_hidden_applications": _csv("OMNIOPS_ARENA_ARGOCD_HIDDEN_APPS")}
    try:
        from omniops.engine.catalog import Catalog

        cat = Catalog.from_env()
        out["enabled_sources"] = sorted(cat.runtimes)
        out["unavailable_sources"] = sorted(cat.unavailable)
    except Exception as exc:  # noqa: BLE001 - an access record must not stop the run
        out["catalog_error"] = f"{type(exc).__name__}: {exc}"
    return out


def _result_digest(r: Any) -> dict[str, Any]:
    stats = _get(r, "stats")
    return {"run_id": _get(r, "run_id"), "kind": _get(r, "kind"), "status": _get(r, "status"),
            "tool_events": len(_get(r, "tool_events", []) or []), "warnings": _get(r, "warnings", []),
            "stats": stats.as_dict() if hasattr(stats, "as_dict") else stats}


def _get(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _git_head(path: Path) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=10, check=False).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> RunnerConfig:
    p = argparse.ArgumentParser(prog="python -m omniops.bench.arena.runner", description=__doc__.split("\n\n")[0])
    p.add_argument("--suite", choices=["full", "smoke"], default="full")
    p.add_argument("--scenarios", default="", help="comma-separated scenarios (default: the whole suite)")
    p.add_argument("--gitops", action="store_true", help="inject and retire faults through Argo CD commits")
    p.add_argument("--allow-push", action="store_true", help="let the runner commit and push the fault repository")
    p.add_argument("--real-slack", action="store_true", help="post into a real Slack channel (OMNIOPS_ARENA_CHANNEL)")
    p.add_argument("--dry-run", action="store_true", help="print the planned command sequence and exit")
    p.add_argument("--run-id", default="", help="resume or name a run (default: a new timestamped id)")
    p.add_argument("--arena-dir", type=Path, default=Path(os.environ.get("ARENA_DIR", DEFAULT_ARENA_DIR)))
    p.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "runs" / "arena")
    p.add_argument("--context", default=os.environ.get("ARENA_CONTEXT", DEFAULT_CONTEXT))
    p.add_argument("--python", default=os.environ.get("ARENA_PYTHON") or sys.executable,
                   help="interpreter for the Arena CLI (Python 3.10+; default: this interpreter)")
    p.add_argument("--env-file", type=Path, default=STATE_DIR / "omniops.env",
                   help="OmniOps source env written by deploy/arena/omniops-env.sh (loaded if present)")
    p.add_argument("--alert-wait", type=float, default=420.0, help="max seconds to wait for Grafana alerts")
    p.add_argument("--alert-settle", type=float, default=45.0, help="seconds to collect co-firing alerts")
    p.add_argument("--mention-grace", type=float, default=90.0,
                   help="seconds to wait for OmniOps' own triage of a customer report before asking it in the thread")
    p.add_argument("--investigation-timeout", type=float, default=None)
    p.add_argument("--no-judge", action="store_true")
    p.add_argument("--judge-model", default=JUDGE_MODEL)
    p.add_argument("--bridge-port", type=int, default=int(os.environ.get("OMNIOPS_ARENA_BRIDGE_PORT", "8787")),
                   help="local Grafana webhook bridge port (0 disables it; polling still works)")
    a = p.parse_args(argv)
    if a.env_file and a.env_file.is_file() and not a.dry_run:
        from dotenv import dotenv_values, load_dotenv

        load_dotenv(a.env_file, override=False)
        values = dotenv_values(a.env_file)
        for key in KUBE_VIEW_ENV:                    # OmniOps' Kubernetes view: the env file wins (3.16)
            if values.get(key):
                os.environ[key] = str(values[key])
    channel = os.environ.get("OMNIOPS_ARENA_CHANNEL", "C0ARENAOPS") if a.real_slack else "C0ARENAOPS"
    return RunnerConfig(
        suite=a.suite, scenarios=[s.strip() for s in a.scenarios.split(",") if s.strip()], gitops=a.gitops,
        real_slack=a.real_slack, dry_run=a.dry_run,
        run_id=a.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:4],
        arena_dir=a.arena_dir.resolve(), output_root=a.output_root.resolve(), context=a.context, python=a.python,
        channel=channel, env_file=a.env_file, alert_wait_s=a.alert_wait, alert_settle_s=a.alert_settle,
        mention_grace_s=a.mention_grace,
        investigation_timeout_s=a.investigation_timeout, judge=not a.no_judge, judge_model=a.judge_model,
        allow_push=a.allow_push, adopt_grafana_posts=os.environ.get("OMNIOPS_ARENA_GRAFANA_POSTS") == "1",
        bridge_port=a.bridge_port, grafana_url=os.environ.get("GRAFANA_URL", ""),
    )


def main(argv: Sequence[str] | None = None) -> int:
    try:
        cfg = parse_args(argv)
        return asyncio.run(ArenaRunner(cfg).run())
    except RunnerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
