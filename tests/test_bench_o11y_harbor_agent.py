"""The Harbor agent class as o11y-bench loads it: a standalone file next to its own agents.

Harbor and the task container are faked; the agent file is loaded by path (as the copied
``agents/omniops_agent.py`` would be) and must not import omniops.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import logging
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

AGENT_FILE = Path(__file__).resolve().parents[1] / "src" / "omniops" / "bench" / "o11ybench" / "harbor_agent.py"


def test_agent_file_imports_only_stdlib_and_harbor() -> None:
    tree = ast.parse(AGENT_FILE.read_text())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative imports break once the file is copied into o11y-bench"
            roots.add((node.module or "").split(".")[0])
        elif isinstance(node, ast.Call) and getattr(node.func, "attr", "") in ("import_module", "__import__"):
            raise AssertionError("dynamic imports are not allowed in the Harbor agent")
    assert "omniops" not in roots
    assert roots - {"harbor"} <= set(sys.stdlib_module_names), roots


# ---------------------------------------------------------------------------------------------
# Fake Harbor
# ---------------------------------------------------------------------------------------------


class FakeBaseAgent:
    def __init__(self, logs_dir: Path, model_name: str | None = None, logger: Any = None,
                 mcp_servers: list[Any] | None = None, **kwargs: Any) -> None:
        self.logs_dir = logs_dir
        self.model_name = model_name
        self.logger = logger or logging.getLogger("fake-harbor")
        self.mcp_servers = mcp_servers or []


@dataclass
class FakeCapabilities:
    mcp_servers: bool = False


class FakeNonZeroExit(RuntimeError):
    pass


@dataclass
class FakeContext:
    n_input_tokens: int | None = None
    n_output_tokens: int | None = None
    n_cache_tokens: int | None = None
    cost_usd: float | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class ExecResult:
    return_code: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class FakeEnvironment:
    """The task container: records commands and holds the files the runner would write."""

    container_files: dict[str, str] = field(default_factory=dict)
    uploads: dict[str, str] = field(default_factory=dict)
    execs: list[tuple[str, dict[str, str] | None]] = field(default_factory=list)
    codes: list[int] = field(default_factory=list)

    async def exec(self, command: str, env: dict[str, str] | None = None, **_: Any) -> ExecResult:
        self.execs.append((command, env))
        return ExecResult(self.codes.pop(0) if self.codes else 0, stdout="installed", stderr="")

    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        self.uploads[target_path] = Path(source_path).name

    async def download_file(self, source_path: str, target_path: Path | str) -> None:
        Path(target_path).write_text(self.container_files[source_path])


@pytest.fixture
def agent_mod(monkeypatch: pytest.MonkeyPatch) -> Any:
    modules = {name: types.ModuleType(name) for name in (
        "harbor", "harbor.agents", "harbor.agents.base", "harbor.agents.capabilities", "harbor.agents.installed",
        "harbor.agents.installed.base", "harbor.environments", "harbor.environments.base", "harbor.models",
        "harbor.models.agent", "harbor.models.agent.context")}
    modules["harbor.agents.base"].BaseAgent = FakeBaseAgent  # type: ignore[attr-defined]
    modules["harbor.agents.capabilities"].AgentCapabilities = FakeCapabilities  # type: ignore[attr-defined]
    modules["harbor.agents.installed.base"].NonZeroAgentExitCodeError = FakeNonZeroExit  # type: ignore[attr-defined]
    modules["harbor.environments.base"].BaseEnvironment = FakeEnvironment  # type: ignore[attr-defined]
    modules["harbor.models.agent.context"].AgentContext = FakeContext  # type: ignore[attr-defined]
    for name, mod in modules.items():
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location("omniops_agent_under_test", AGENT_FILE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    before = set(sys.modules)
    spec.loader.exec_module(mod)
    assert not any(m == "omniops" or m.startswith("omniops.") for m in set(sys.modules) - before)
    return mod


@pytest.fixture
def wheel_dir(tmp_path: Path) -> Path:
    d = tmp_path / "dist" / "omniops"
    d.mkdir(parents=True)
    (d / "omniops-0.1.0-py3-none-any.whl").write_bytes(b"PK")
    (d / "requirements.txt").write_text("httpx==0.28.1\n")
    return d


def test_container_env_maps_model_and_filters_host_env(agent_mod: Any) -> None:
    host = {"MODEL_API_KEY": "k", "ANTHROPIC_API_KEY": "judge-key", "OMNIOPS_LLM_PROTOCOL": "chat",
            "OMNIOPS_MEMORY_PATH": "/Users/me/.omniops/memory.db", "OMNIOPS_CHAT_BUDGET_S": "300",
            "OMNIOPS_WRITE_MODE": "auto", "GRAFANA_URL": "http://localhost:3000",
            "PROMETHEUS_URL": "https://prom.example.net", "O11Y_SCENARIO_TIME_ISO": "2026-10-26T12:00:00Z",
            "HOME": "/Users/me"}
    env = agent_mod.container_env("meta/muse-wonder-1.0", host, {"OMNIOPS_SPECIALISTS": "code_change"})
    assert env["OMNIOPS_MODEL_HEAVY"] == "muse-wonder-1.0"
    assert env["MODEL_API_KEY"] == "k" and "ANTHROPIC_API_KEY" not in env   # the judge key never enters
    assert env["OMNIOPS_LLM_PROTOCOL"] == "chat" and env["OMNIOPS_CHAT_BUDGET_S"] == "300"
    assert env["OMNIOPS_BENCH_TIMEOUT_S"] == "540"
    assert "OMNIOPS_MEMORY_PATH" not in env and "HOME" not in env
    assert "GRAFANA_URL" not in env and "PROMETHEUS_URL" not in env   # the task's sidecar URLs stay in force
    assert env["OMNIOPS_BENCHMARK_MODE"] == "1" and env["OMNIOPS_WRITE_MODE"] == "stage"
    assert env["O11Y_SCENARIO_TIME_ISO"] == "2026-10-26T12:00:00Z"
    assert env["OMNIOPS_SPECIALISTS"] == "code_change"
    # without a host clock the container's own compose value stays in force (never masked by "")
    assert "O11Y_SCENARIO_TIME_ISO" not in agent_mod.container_env(None, {"O11Y_SCENARIO_TIME_ISO": ""}, {})
    assert agent_mod.omniops_model("muse-spark-1.3") == "muse-spark-1.3"
    assert agent_mod.omniops_model(None) is None


def test_host_stack_urls_never_override_the_task_sidecar(agent_mod: Any) -> None:
    """A user's exported OmniOps config (a remote Grafana, Prometheus ...) must not redirect every trial
    away from the task's seeded o11y-stack sidecar; only an explicit extra_env may."""
    host = {"GRAFANA_URL": "https://acme.grafana.net", "GRAFANA_TOKEN": "glsa_x", "PROMETHEUS_URL": "https://prom.corp",
            "LOKI_URL": "http://loki.corp:3100", "TEMPO_URL": "https://tempo.corp", "MODEL_API_KEY": "k"}
    env = agent_mod.container_env("meta/muse-wonder-1.0", host, {})
    assert not {"GRAFANA_URL", "GRAFANA_TOKEN", "PROMETHEUS_URL", "LOKI_URL", "TEMPO_URL"} & set(env)
    explicit = agent_mod.container_env(None, host, {"LOKI_URL": "http://loki.override:3100"})
    assert explicit["LOKI_URL"] == "http://loki.override:3100" and "GRAFANA_URL" not in explicit


def test_agent_declares_mcp_support_but_drops_servers(agent_mod: Any, tmp_path: Path) -> None:
    agent = agent_mod.OmniOpsHarborAgent(tmp_path, model_name="meta/muse-wonder-1.0", mcp_servers=["mcp-grafana"])
    assert agent_mod.OmniOpsHarborAgent.capabilities.mcp_servers is True
    assert agent.mcp_servers == []
    assert agent_mod.OmniOpsHarborAgent.name() == "omniops"


async def test_setup_uploads_and_installs_the_wheel(agent_mod: Any, tmp_path: Path, wheel_dir: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_mod, "DIST_DIR", wheel_dir)
    monkeypatch.delenv("OMNIOPS_WHEEL", raising=False)
    agent = agent_mod.OmniOpsHarborAgent(tmp_path)
    env = FakeEnvironment()
    await agent.setup(env)
    assert env.uploads == {"/app/omniops/omniops-0.1.0-py3-none-any.whl": "omniops-0.1.0-py3-none-any.whl",
                           "/app/omniops/requirements.txt": "requirements.txt"}
    install = env.execs[-1][0]
    assert "uv pip install" in install and "-r /app/omniops/requirements.txt" in install
    assert "import omniops.bench.o11ybench.runner" in install         # verify the effect, not the config
    assert agent.version() == "0.1.0"

    failing = FakeEnvironment(codes=[0, 1])
    with pytest.raises(FakeNonZeroExit, match="install failed"):
        await agent.setup(failing)


async def test_run_executes_the_runner_and_reads_the_trajectory(agent_mod: Any, tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_API_KEY", "k")
    agent = agent_mod.OmniOpsHarborAgent(tmp_path, model_name="meta/muse-wonder-1.0")
    trajectory = {"schema_version": "ATIF-v1.7", "steps": [], "final_metrics": {
        "total_prompt_tokens": 1200, "total_completion_tokens": 300, "total_cached_tokens": 800,
        "total_cost_usd": 0.0123}}
    env = FakeEnvironment(container_files={"/logs/agent/trajectory.json": json.dumps(trajectory)})
    ctx = FakeContext()
    await agent.run("Which datasources?", env, ctx)

    assert env.uploads["/app/instruction.txt"] == "instruction.txt"
    assert (tmp_path / "instruction.txt").read_text() == "Which datasources?"
    command, run_env = env.execs[-1]
    assert "-m omniops.bench.o11ybench.runner --instruction-file /app/instruction.txt" in command
    assert 'tee "/logs/agent/command-0/stdout.txt"' in command and "pipefail" in command
    assert run_env is not None and run_env["OMNIOPS_MODEL_HEAVY"] == "muse-wonder-1.0"
    assert (ctx.n_input_tokens, ctx.n_output_tokens, ctx.n_cache_tokens, ctx.cost_usd) == (1200, 300, 800, 0.0123)
    assert ctx.metadata is not None and ctx.metadata["entry_point"] == "slack_dm"
    assert sorted(p.name for p in tmp_path.glob("*.json")) == ["trajectory.json"]

    failing = FakeEnvironment(codes=[3], container_files=env.container_files)
    with pytest.raises(FakeNonZeroExit, match="exited with code 3"):
        await agent.run("Which datasources?", failing, FakeContext())


def test_missing_wheel_points_to_the_setup_script(agent_mod: Any, tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMNIOPS_WHEEL", raising=False)
    with pytest.raises(FileNotFoundError, match="o11ybench_setup.sh"):
        agent_mod.find_wheel(None, tmp_path)
