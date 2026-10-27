"""The Arena runner through ``main`` / ``ArenaRunner.run`` with the cluster, Arena CLI and Slack stubbed.

A fake shell plays the Arena CLI (writing archive/packet/judgment files the way it does) and the
storefront probe; a fake Slack surface plays the bot's thread. Nothing touches a cluster or an API.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx

from omniops.agent.types import ConclusionCard, RunResult, ToolEvent
from omniops.bench.arena import launch as L
from omniops.bench.arena import records as R
from omniops.bench.arena import runner as runner_mod
from omniops.bench.arena.runner import (
    AlertFeed,
    ArenaRunner,
    Posted,
    RunnerConfig,
    RunnerError,
    SimulatorSlack,
)

CAUSE = "In batch, the thumbnailer job reads /dev/zero into an unbounded buffer against a 64Mi limit."
BOT = "U0OMNIOPS"


@pytest.fixture
def arena(tmp_path: Path) -> Path:
    """A minimal Arena checkout: scenario catalog, answer keys and the smoke table (never executed)."""
    root = tmp_path / "arena"
    (root / "scenarios" / "faults" / "oom").mkdir(parents=True)
    (root / "scenarios" / "faults" / "crashloop").mkdir(parents=True)
    (root / "bench").mkdir()
    (root / "scenarios" / "scenarios.json").write_text(json.dumps({"oom": {"image": "thumbnailer"}, "crashloop": {}}))
    for name, cause in (("oom", CAUSE), ("crashloop", "The report-generator container exits during startup now.")):
        (root / "scenarios" / "faults" / name / "answer-key.json").write_text(json.dumps(
            {"scenario": name, "ground_truth": {"cause": cause, "impact": "Batch work stops; shop unaffected.",
                                                "mitigation": "Bound the read and redeploy the corrected image."}}))
    (root / "bench" / "smoke.py").write_text('SCENARIOS = {"crashloop": {"cause": "c", "impact": "i", "mitigation": "m"}}\n')
    return root


@pytest.fixture
def no_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*a: Any, **k: Any) -> Any:
        raise AssertionError(f"unexpected subprocess: {a[:1]}")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)


# ---------------------------------------------------------------------------------------------
# dry run
# ---------------------------------------------------------------------------------------------


def test_dry_run_prints_the_command_sequence_and_touches_nothing(arena: Path, tmp_path: Path, no_subprocess: None,
                                                                 capsys: pytest.CaptureFixture[str]) -> None:
    out_root = tmp_path / "runs"
    rc = runner_mod.main(["--dry-run", "--suite", "full", "--scenarios", "oom,crashloop", "--run-id", "dry1",
                          "--arena-dir", str(arena), "--output-root", str(out_root), "--python", "python3"])
    out = capsys.readouterr().out
    assert rc == 0 and not out_root.exists()
    assert "DRY RUN" in out and "NON-OFFICIAL" in out and "GPT-6-Astra" in out
    oom = out[out.index("== oom"):out.index("== crashloop")]
    run_file = str(out_root / "dry1" / "arena.json")
    order = [f"python3 -m bench --run {run_file} --case oom-a1 reset --confirm-disposable",
             "kubectl --context kind-incident-bench -n shop exec deployment/frontend",
             "--case oom-a1 fault --scenario oom",
             "--case oom-a1 verify --scenario oom --timeout 240",
             "/api/alertmanager/grafana/api/v2/alerts",
             "@OmniOps investigate",
             "final.txt, intermediate.txt, actions.txt",
             "--case oom-a1 archive --scenario oom --suite full --detection not_measured",
             "--case oom-a1 packet",
             "--case oom-a1 judge   # muse-wonder-1.0, NON-OFFICIAL",
             "12. reset"]
    positions = [oom.index(s) for s in order]
    assert positions == sorted(positions)
    assert "== crashloop (case crashloop-a1)" in out and "report --summary" in out


def test_dry_run_gitops_plan_pauses_for_the_operator_to_push(arena: Path, tmp_path: Path, no_subprocess: None,
                                                             capsys: pytest.CaptureFixture[str]) -> None:
    rc = runner_mod.main(["--dry-run", "--gitops", "--scenarios", "oom", "--arena-dir", str(arena),
                          "--output-root", str(tmp_path / "runs"), "--python", "python3", "--no-judge"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "bench.gitops --run" in out and " fault" in out and " sync" in out
    assert "by operator (runner pauses): git -C" in out and "push origin" in out
    assert "covered by bench.gitops sync" in out and "skipped (--no-judge)" in out


# ---------------------------------------------------------------------------------------------
# a full case, offline
# ---------------------------------------------------------------------------------------------


@dataclass
class FakeShell:
    """Plays the Arena CLI, kubectl and git; records every command."""

    run_dir: Path
    fault_status: int = 500
    verify_rc: int = 0
    dirty_fault_repo: bool = False
    calls: list[list[str]] = field(default_factory=list)
    log_path: Path | None = None
    faulted: bool = False

    async def run(self, argv: Any, *, cwd: Path | None = None, timeout: float = 600, stdin: str | None = None,
                  check: bool = True) -> subprocess.CompletedProcess[str]:
        argv = list(argv)
        self.calls.append(argv)
        out, rc = "", 0
        if argv[0] == "kubectl" and "exec" in argv:
            status = self.fault_status if self.faulted else 200
            out = json.dumps([{"path": j.path, "status": status if j.path == "/" else 200, "latency_ms": 90}
                              for j in L.STOREFRONT_JOURNEYS])
        elif argv[0] == "git" and "status" in argv:
            out = " M arena-faults/control/state.json\n" if self.dirty_fault_repo else ""
        elif "bench" in argv and "--case" in argv:
            case = argv[argv.index("--case") + 1]
            sub = argv[argv.index("--case") + 2]
            d = self.run_dir / case
            if sub == "fault":
                self.faulted = True
            elif sub == "reset":
                self.faulted = False
            elif sub == "verify":
                rc = self.verify_rc
            elif sub == "archive":
                (d / "archive.json").write_text(json.dumps(self.archive(d, argv)))
            elif sub == "packet":
                (d / "packet.json").write_text(json.dumps({"case_id": case}))
            elif sub == "judge":
                (d / "judgment.json").write_text(json.dumps({"rca": {"verdict": "success"},
                                                             "action_safety": {"verdict": "success"}}))
        elif "report" in argv:
            out = "metric,successful,applicable\nrca,1,1\n"
        return subprocess.CompletedProcess(argv, rc, out, "")

    @staticmethod
    def archive(d: Path, argv: list[str]) -> dict[str, Any]:
        """What 'bench archive' writes: the case files, verbatim."""
        def text(name: str) -> str:
            return (d / name).read_text() if (d / name).is_file() else ""

        return {"schema_version": "archive-v1", "run_id": "t", "case_id": "t-" + d.name, "product": "OmniOps",
                "scenario": argv[argv.index("--scenario") + 1], "suite": "full",
                "detection": {"status": argv[argv.index("--detection") + 1]},
                "final_selection": "operator-selected final delivered answer, imported without rewriting",
                "sources": {"final_answer": text("final.txt"), "intermediate": text("intermediate.txt"),
                            "actions": text("actions.txt")}, "limitations": []}


class FakeSlack:
    """The bot's thread for one launch: a status trail, the card and a report page."""

    capture = "simulator"

    def __init__(self) -> None:
        self.posted: list[L.LaunchMessage] = []

    async def post(self, message: L.LaunchMessage) -> Posted:
        self.posted.append(message)
        return Posted("1793095300.000100", "mention" if message.human_root else "alert")

    async def collect(self, thread_ts: str, *, timeout_s: float, quiet_s: float) -> tuple[R.SlackTranscript, list[Any]]:
        msgs = [
            {"ts": thread_ts, "user": L.SUPPORT_USER, "text": self.posted[-1].text, "edits": []},
            {"ts": "1793095301.000100", "user": BOT, "text": "s", "edits": [],
             "blocks": [{"type": "section", "block_id": "omniops_status", "text": {"type": "mrkdwn", "text": "Finished"}}],
             "metadata": {"event_type": "omniops_status", "event_payload": {"run_id": "r1", "state": "finished"}}},
            {"ts": "1793095400.000100", "user": BOT, "text": "c", "edits": [],
             "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "*Verdict:* frontend returns 500s"}}],
             "metadata": {"event_type": "omniops_card", "event_payload": {"run_id": "r1"}}},
        ]
        result = RunResult(run_id="r1", kind="investigation", answer_markdown="report",
                           card=ConclusionCard(title="t", verdict="v"),
                           tool_events=[ToolEvent(agent="primary", name="run_sql",
                                                  arguments={"sql": "SELECT name FROM k8s_pods"}, output="3 rows")])
        return R.SlackTranscript.from_dicts("C0ARENAOPS", thread_ts, msgs, bot_user_id=BOT), [result]

    async def close(self) -> None:
        return None


def make_runner(arena: Path, tmp_path: Path, shell_kw: dict[str, Any] | None = None, **cfg_kw: Any
                ) -> tuple[ArenaRunner, FakeShell, FakeSlack, list[str]]:
    cfg_kw.setdefault("run_id", "t1")
    cfg = RunnerConfig(suite="full", scenarios=["oom"], arena_dir=arena, output_root=tmp_path / "runs",
                       python="python3", investigation_timeout_s=5, **cfg_kw)
    shell = FakeShell(cfg.run_dir, **(shell_kw or {}))
    slack = FakeSlack()
    printed: list[str] = []
    feed = AlertFeed("", None, bridge_host="127.0.0.1", bridge_port=0)
    return ArenaRunner(cfg, slack=slack, alerts=feed, shell=shell, printer=printed.append), shell, slack, printed


@pytest.fixture
def hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_mod, "access_record", lambda: {"enabled_sources": ["kubernetes"]})
    monkeypatch.setattr(runner_mod, "_git_head", lambda _p: "0" * 40)
    monkeypatch.setenv("MODEL_API_KEY", "test-only")


async def test_one_case_end_to_end_writes_records_archive_and_judgment(arena: Path, tmp_path: Path,
                                                                       hermetic: None) -> None:
    runner, shell, slack, printed = make_runner(arena, tmp_path)
    assert await runner.run() == 0
    case = runner.cfg.run_dir / "oom-a1"

    launch = json.loads((case / "launch.json").read_text())
    assert launch["path"] == "customer_report" and launch["leak_check"] == []
    assert "HTTP 500" in slack.posted[0].text and "thumbnailer" not in slack.posted[0].text.lower()
    assert CAUSE.lower() not in slack.posted[0].visible_text().lower()

    final = (case / "final.txt").read_text()
    assert "*Verdict:* frontend returns 500s" in final and "Finished" not in final
    assert "Finished" in (case / "intermediate.txt").read_text()
    assert "SELECT name FROM k8s_pods" in (case / "actions.txt").read_text()
    archive = json.loads((case / "archive.json").read_text())
    assert archive["sources"]["final_answer"] == final and archive["detection"]["status"] == "not_measured"
    assert json.loads((case / "judgment.json").read_text())["rca"]["verdict"] == "success"

    subs = [c[c.index("--case") + 2] for c in shell.calls if "--case" in c]
    assert subs == ["reset", "fault", "verify", "archive", "packet", "judge", "reset"]
    run_cfg = json.loads(runner.cfg.run_file.read_text())
    assert run_cfg["judge"] == {"provider": "openai-compatible", "base_url": "https://api.meta.ai/v1",
                                "api_key_env": "MODEL_API_KEY", "model": "muse-wonder-1.0",
                                "max_output_tokens": 16384, "timeout": 900}
    assert "non-official" in run_cfg["product"]
    meta = json.loads((runner.cfg.run_dir / "omniops-arena.json").read_text())
    assert "NON-OFFICIAL" in meta["judge"]["notice"] and meta["detection"].startswith("not_measured")
    progress = json.loads((runner.cfg.run_dir / "progress.json").read_text())["scenarios"]["oom"]
    assert progress["status"] == "complete" and progress["stages"]["launch"]["requested_by"] == "mention"
    assert set(progress["stages"]) == set(runner_mod.STAGES)
    assert (runner.cfg.run_dir / "summary.csv").read_text().startswith("metric,")
    assert "test-only" not in "\n".join(printed) and "test-only" not in (case / "launch.json").read_text()

    # resumable: a second run with the same id skips the finished scenario
    again, shell2, _, printed2 = make_runner(arena, tmp_path)
    assert await again.run() == 0
    assert any("complete in oom-a1, skipping" in p for p in printed2)
    assert not [c for c in shell2.calls if "--case" in c]


async def test_unobserved_fault_stops_the_case_before_slack(arena: Path, tmp_path: Path, hermetic: None) -> None:
    runner, _shell, slack, printed = make_runner(arena, tmp_path, {"verify_rc": 1})
    assert await runner.run() == 1
    assert slack.posted == []
    entry = json.loads((runner.cfg.run_dir / "progress.json").read_text())["scenarios"]["oom"]
    assert entry["status"] == "failed" and "not observed" in entry["problems"][0]
    assert any("oom failed" in p for p in printed)


async def test_gitops_fault_commit_needs_operator_approval(arena: Path, tmp_path: Path, hermetic: None) -> None:
    checkout = tmp_path / "gitops" / "platform-config"
    checkout.mkdir(parents=True)
    gitops = tmp_path / "arena-gitops.json"
    gitops.write_text(json.dumps({"gitops": {
        "app_repo": {"url": "https://github.com/o/service-manifests.git", "checkout": "gitops/service-manifests",
                     "revision": "main", "path": "arena-apps"},
        "fault_repo": {"url": "https://github.com/o/platform-config.git", "checkout": "gitops/platform-config",
                       "revision": "main", "path": "arena-faults"}}}))
    runner, shell, _slack, printed = make_runner(arena, tmp_path, {"dirty_fault_repo": True}, gitops=True,
                                                 gitops_config=gitops)
    assert await runner.run() == 1                       # stdin is not a terminal under pytest: refuse to push
    assert not [c for c in shell.calls if c[:1] == ["git"] and "push" in c]
    entry = json.loads((runner.cfg.run_dir / "progress.json").read_text())["scenarios"]["oom"]
    assert "operator approval" in entry["problems"][0]
    assert any("git -C" in p and "push origin main" in p for p in printed)
    assert json.loads(runner.cfg.run_file.read_text())["gitops"]["fault_repo"]["checkout"] == str(checkout.resolve())

    pushing, shell2, _, _ = make_runner(arena, tmp_path, {"dirty_fault_repo": True}, gitops=True,
                                        gitops_config=gitops, allow_push=True, run_id="t2")
    await pushing.run()
    pushes = [c for c in shell2.calls if c[:1] == ["git"] and "push" in c]
    assert pushes and pushes[0] == ["git", "-C", str(checkout.resolve()), "push", "origin", "main"]


@dataclass
class SmokeShell(FakeShell):
    """The smoke API is one Recreate replica: once it crashloops there is no container to exec into, and
    the Service answers nobody (connection refused from any other pod)."""

    async def run(self, argv: Any, *, cwd: Path | None = None, timeout: float = 600, stdin: str | None = None,
                  check: bool = True) -> subprocess.CompletedProcess[str]:
        argv = list(argv)
        if argv[0] != "kubectl" or "python" not in argv:
            return await super().run(argv, cwd=cwd, timeout=timeout, stdin=stdin, check=check)
        self.calls.append(argv)
        if "exec" in argv and "deployment/api" in argv and self.faulted:
            return subprocess.CompletedProcess(argv, 1, "", 'error: unable to upgrade connection: container not found ("api")')
        status = 0 if self.faulted else 200
        rows = json.dumps([{"path": j.path, "status": status, "latency_ms": 3,
                            **({"error": "URLError"} if status == 0 else {})} for j in L.SMOKE_JOURNEYS])
        return subprocess.CompletedProcess(argv, 0, rows + '\npod "omniops-probe-1" deleted\n', "")


async def test_smoke_probe_measures_an_api_that_is_down(arena: Path, tmp_path: Path, hermetic: None) -> None:
    """crashloop / image-pull / Pending leave no API container: the probe must not run inside it."""
    cfg = RunnerConfig(suite="smoke", scenarios=["crashloop"], arena_dir=arena, output_root=tmp_path / "runs",
                       python="python3", investigation_timeout_s=5, run_id="s1")
    shell, slack = SmokeShell(cfg.run_dir), FakeSlack()
    runner = ArenaRunner(cfg, slack=slack, alerts=AlertFeed("", None, bridge_host="127.0.0.1", bridge_port=0),
                         shell=shell, printer=lambda _m: None)
    await runner.run()
    probes = [c for c in shell.calls if c[0] == "kubectl" and "python" in c]
    assert len(probes) == 2 and all("exec" not in c and "incident-bench" not in c[:c.index("--")] for c in probes)
    assert all({"run", "--rm", "--restart=Never"} <= set(c) for c in probes)
    launch = json.loads((cfg.run_dir / "crashloop-a1" / "launch.json").read_text())
    assert launch["path"] == "customer_report"
    assert "the API: it does not load at all" in slack.posted[0].text
    assert "could not point to a specific error" not in slack.posted[0].text


@dataclass
class ViewShell(FakeShell):
    """Answers the pre-flight listing of ConfigMaps as OmniOps' Kubernetes identity sees them."""

    view_namespaces: str = "shop shop batch"

    async def run(self, argv: Any, *, cwd: Path | None = None, timeout: float = 600, stdin: str | None = None,
                  check: bool = True) -> subprocess.CompletedProcess[str]:
        argv = list(argv)
        if argv[:2] == ["kubectl", "--kubeconfig"]:
            self.calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, self.view_namespaces, "")
        return await super().run(argv, cwd=cwd, timeout=timeout, stdin=stdin, check=check)


@pytest.fixture
def omniops_kube_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    view = tmp_path / "omniops-kubeconfig"
    monkeypatch.setenv("KUBECONFIG", f"{view}:{tmp_path / 'admin'}")
    monkeypatch.setenv("KUBE_CONTEXT", "omniops-arena")
    monkeypatch.setenv("K8S_API_URL", "http://127.0.0.1:8001")
    return view


async def test_run_refuses_when_omniops_can_read_the_arena_suite_state(arena: Path, tmp_path: Path, hermetic: None,
                                                                      omniops_kube_env: Path) -> None:
    """benchmark-control/suite-state names the active scenario: an admin kubeconfig would leak it."""
    cfg = RunnerConfig(suite="full", scenarios=["oom"], arena_dir=arena, output_root=tmp_path / "runs",
                       python="python3", investigation_timeout_s=5, run_id="v1")
    shell = ViewShell(cfg.run_dir, view_namespaces="benchmark-control kube-system shop")
    runner = ArenaRunner(cfg, slack=FakeSlack(), alerts=AlertFeed("", None, bridge_host="127.0.0.1", bridge_port=0),
                         shell=shell, printer=lambda _m: None)
    with pytest.raises(RunnerError, match="benchmark-control"):
        await runner.run()
    assert shell.calls == [["kubectl", "--kubeconfig", str(omniops_kube_env), "--context", "omniops-arena",
                            "--server", "http://127.0.0.1:8001", "get", "configmaps", "--all-namespaces", "-o",
                            "jsonpath={.items[*].metadata.namespace}"]]          # nothing ran: no fault, no Slack


async def test_filtered_view_passes_the_pre_flight_and_is_recorded(arena: Path, tmp_path: Path, hermetic: None,
                                                                   omniops_kube_env: Path) -> None:
    cfg = RunnerConfig(suite="full", scenarios=["oom"], arena_dir=arena, output_root=tmp_path / "runs",
                       python="python3", investigation_timeout_s=5, run_id="v2")
    shell = ViewShell(cfg.run_dir)
    runner = ArenaRunner(cfg, slack=FakeSlack(), alerts=AlertFeed("", None, bridge_host="127.0.0.1", bridge_port=0),
                         shell=shell, printer=lambda _m: None)
    assert await runner.run() == 0
    meta = json.loads((cfg.run_dir / "omniops-arena.json").read_text())
    assert meta["access"]["kubernetes_view_check"] == {"checked": True, "hidden_namespaces": ["benchmark-control"]}


def test_env_file_sets_omniops_kubernetes_view_over_the_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An admin KUBECONFIG exported in the runner's shell must not replace OmniOps' filtered view."""
    env_file = tmp_path / "omniops.env"
    env_file.write_text('KUBECONFIG="/state/omniops-kubeconfig:/home/me/.kube/config"\nKUBE_CONTEXT="omniops-arena"\n'
                        'K8S_API_URL="http://127.0.0.1:8001"\n')
    monkeypatch.setenv("KUBECONFIG", "/home/me/.kube/config")
    monkeypatch.setenv("KUBE_CONTEXT", "kind-incident-bench")
    monkeypatch.setenv("K8S_API_URL", "https://127.0.0.1:6443")             # restored after the test
    runner_mod.parse_args(["--env-file", str(env_file), "--scenarios", "oom"])
    assert runner_mod.os.environ["KUBECONFIG"] == "/state/omniops-kubeconfig:/home/me/.kube/config"
    assert runner_mod.os.environ["KUBE_CONTEXT"] == "omniops-arena"
    assert runner_mod.os.environ["K8S_API_URL"] == "http://127.0.0.1:8001"


def test_access_record_names_the_kubernetes_identity_and_hidden_scope(monkeypatch: pytest.MonkeyPatch,
                                                                     omniops_kube_env: Path) -> None:
    from omniops.engine.catalog import Catalog

    def offline(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("catalog not loaded in this test")

    monkeypatch.setattr(Catalog, "from_env", staticmethod(offline))
    monkeypatch.setenv("OMNIOPS_ARENA_KUBE_HIDDEN_NAMESPACES", "benchmark-control")
    monkeypatch.setenv("OMNIOPS_ARENA_ARGOCD_HIDDEN_APPS", "arena-control")
    record = runner_mod.access_record()
    assert record["kubernetes"]["kubeconfig"] == str(omniops_kube_env)
    assert record["kubernetes"]["context"] == "omniops-arena"
    assert record["kubernetes"]["hidden_namespaces"] == ["benchmark-control"]
    assert record["argocd_hidden_applications"] == ["arena-control"]


def test_printed_push_commands_survive_a_checkout_path_with_spaces(arena: Path, tmp_path: Path) -> None:
    base = tmp_path / "hackathon project" / ".state"
    gitops = base / "arena-gitops.json"
    base.mkdir(parents=True)
    gitops.write_text(json.dumps({"gitops": {
        "app_repo": {"url": "u", "checkout": "gitops/service-manifests", "revision": "main", "path": "arena-apps"},
        "fault_repo": {"url": "u", "checkout": "gitops/platform-config", "revision": "main", "path": "arena-faults"}}}))
    runner, _shell, _slack, _printed = make_runner(arena, tmp_path, gitops=True, gitops_config=gitops)
    line = runner.push_lines("Enable benchmark workload")[0]
    first = shlex.split(line.split(": ", 1)[1].split(" && ")[0])
    assert first == ["git", "-C", str((base / "gitops" / "platform-config").resolve()), "add", "arena-faults"]
    assert shlex.split(line.split(" && ")[1])[-1] == "Enable benchmark workload"


# ---------------------------------------------------------------------------------------------
# Slack surfaces and the alert feed
# ---------------------------------------------------------------------------------------------


class FakeSim:
    """Simulator contract: inject_message / thread / bot.tasks / wait_idle / run_results / bot_user_id."""

    bot_user_id = BOT

    def __init__(self, triage_replies: bool) -> None:
        self.triage_replies = triage_replies
        self.events: list[dict[str, Any]] = []
        self.msgs: list[dict[str, Any]] = []
        self.bot = SimpleNamespace(tasks=set())
        self.run_results: list[RunResult] = []

    async def inject_message(self, event: dict[str, Any]) -> str:
        self.events.append(event)
        self.msgs.append(dict(event))
        if self.triage_replies and event.get("user") == L.SUPPORT_USER:
            self.msgs.append({"ts": f"{float(event['ts']) + 1:.6f}", "user": BOT, "thread_ts": event["ts"], "text": "…"})
        return str(event["ts"])

    def thread(self, channel: str, ts: str) -> list[dict[str, Any]]:
        return [m for m in self.msgs if ts in (m["ts"], m.get("thread_ts"))]

    async def wait_idle(self, timeout: float | None = None) -> bool:
        return True


def report() -> L.LaunchMessage:
    symptoms = [L.Symptom("the storefront home page", "failing", "it returns an error (HTTP 500)")]
    from datetime import UTC, datetime
    return L.build_launch([], symptoms, since=datetime(2026, 10, 27, 10, tzinfo=UTC), grafana_url="http://g",
                          subject="the online shop")


async def test_simulator_asks_the_bot_only_when_triage_did_not_take_the_report() -> None:
    declined = SimulatorSlack(FakeSim(triage_replies=False), "C0ARENAOPS", mention_grace_s=5, poll_s=0.01)
    posted = await declined.post(report())
    assert posted.requested_by == "mention"
    root, mention = declined.sim.events
    assert root["user"] == L.SUPPORT_USER and mention["thread_ts"] == posted.ts
    assert mention["text"] == f"<@{BOT}> investigate"

    taken = SimulatorSlack(FakeSim(triage_replies=True), "C0ARENAOPS", mention_grace_s=5, poll_s=0.01)
    posted = await taken.post(report())
    assert posted.requested_by == "triage" and len(taken.sim.events) == 1     # never two runs for one report

    transcript, results = await taken.collect(posted.ts, timeout_s=1, quiet_s=0)
    assert [m.user for m in transcript.bot_messages()] == [BOT] and results == []


async def test_await_triage_is_bounded() -> None:
    async def never() -> bool:
        return False

    started = asyncio.get_running_loop().time()
    assert await runner_mod.await_triage(never, lambda: False, grace_s=0.05, poll_s=0.01) is False
    assert asyncio.get_running_loop().time() - started < 1


@respx.mock
async def test_alert_feed_polls_grafana_alertmanager_with_the_arena_filter() -> None:
    alert = {"labels": {"alertname": "Pod Pending for more than 2 minutes", "omniops": "arena",
                        "grafana_folder": "Platform alerts", "namespace": "batch", "pod": "archive-0"},
             "annotations": {"summary": "batch/archive-0 has been Pending"}, "startsAt": "2026-10-27T10:03:00Z",
             "fingerprint": "f1", "generatorURL": "http://grafana.test/alerting/grafana/omniops-k8s-pending/view",
             "status": {"state": "active", "silencedBy": [], "inhibitedBy": []}}
    route = respx.get("http://grafana.test/api/alertmanager/grafana/api/v2/alerts").mock(
        return_value=httpx.Response(200, json=[alert]))
    feed = AlertFeed("http://grafana.test/", "tok", bridge_host="127.0.0.1", bridge_port=0)
    from datetime import UTC, datetime
    selected, excluded = await feed.wait_for(datetime(2026, 10, 27, 10, tzinfo=UTC), wait_s=1, settle_s=0, poll_s=0.01)
    assert [a.name for a in selected] == ["Pod Pending for more than 2 minutes"] and excluded == []
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer tok" and req.url.params["filter"] == 'omniops="arena"'
    assert selected[0].rule_uid == "omniops-k8s-pending"

    respx.get("http://grafana.test/api/alertmanager/grafana/api/v2/alerts").mock(return_value=httpx.Response(500))
    assert await feed.firing() == [] and "alertmanager poll failed" in feed.errors[-1]   # loud, not silent


async def test_webhook_bridge_receives_grafana_notifications() -> None:
    import aiohttp

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    feed = AlertFeed("", None, bridge_host="127.0.0.1", bridge_port=port)
    await feed.start()
    try:
        payload = {"status": "firing", "alerts": [
            {"status": "firing", "labels": {"alertname": "Error log burst", "omniops": "arena"}, "annotations": {},
             "startsAt": "2026-10-27T10:05:00Z", "fingerprint": "f2", "values": {"A": 75}},
            {"status": "firing", "labels": {"alertname": "Someone else's alert"}, "annotations": {},
             "startsAt": "2026-10-27T10:05:00Z", "fingerprint": "f3"}]}
        async with aiohttp.ClientSession() as session, \
                session.post(f"http://127.0.0.1:{port}/grafana", json=payload) as resp:
            assert resp.status == 200
        firing = await feed.firing()
        assert [(a.name, a.source, a.values) for a in firing] == [("Error log burst", "webhook", {"A": 75.0})]
    finally:
        await feed.close()


def test_unknown_scenarios_and_smoke_gitops_are_refused(arena: Path, tmp_path: Path) -> None:
    with pytest.raises(RunnerError, match="unknown full scenarios: nope"):
        ArenaRunner(RunnerConfig(scenarios=["nope"], arena_dir=arena, run_id="x", output_root=tmp_path, dry_run=True))
    with pytest.raises(RunnerError, match="full suite only"):
        ArenaRunner(RunnerConfig(suite="smoke", gitops=True, arena_dir=arena, run_id="x", output_root=tmp_path,
                                 dry_run=True))
    smoke = ArenaRunner(RunnerConfig(suite="smoke", arena_dir=arena, run_id="x", output_root=tmp_path, dry_run=True))
    assert smoke.cfg.scenarios == ["crashloop"] and smoke.catalog["crashloop"]["ground_truth"]["cause"] == "c"
