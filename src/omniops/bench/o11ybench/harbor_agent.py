"""Harbor agent that runs OmniOps on o11y-bench tasks: Slack DM in, SQL over HTTP, no MCP.

Installed by ``scripts/o11ybench_setup.sh`` as ``<o11y-bench>/agents/omniops_agent.py`` and run with

    uv run python -m o11y_bench job --model meta/muse-wonder-1.0 \
        --agent-import-path agents.omniops_agent:OmniOpsHarborAgent

Harbor imports this file on the host inside o11y-bench's environment, so it must not import
omniops (only the standard library and harbor). OmniOps runs inside the task container: ``setup``
uploads the wheel built on the host (plus the locked requirements) and installs it with uv into a
venv on the container's Python 3.14; ``run`` DMs the task to the bot through the Slack simulator
(``python -m omniops.bench.o11ybench.runner``) and collects the ATIF trajectory.

Retrospective lessons encoded (OpsIntelligence):
- D5: no MCP. The agent declares ``mcp_servers`` support only because Harbor refuses MCP-configured
  tasks otherwise; the configured servers are dropped and never used.
- 4.1 "verify the effect, not the config": setup imports the installed runner before declaring
  success, and a non-zero runner exit (infrastructure failure) fails the trial loudly.
- 4.6 / 4.8: credentials are passed by name to the container env only, never logged; host paths
  and the host's own stack URLs are not forwarded (every task seeds its own o11y-stack sidecar, and
  the container env already points at it; ``--ak extra_env=...`` is the explicit override).
- 3.13: budgets nest - chat budget + watchdog < runner timeout < Harbor's 600 s agent timeout.
"""

import json
import os
import shlex
from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

DIST_DIR = Path(__file__).parent / "dist" / "omniops"     # filled by scripts/o11ybench_setup.sh
CONTAINER_DIR = "/app/omniops"
VENV = "/opt/omniops-venv"
INSTRUCTION_PATH = "/app/instruction.txt"
TRAJECTORY_PATH = "/logs/agent/trajectory.json"
STDOUT_PATH = "/logs/agent/command-0/stdout.txt"
CONTAINER_PATH = "/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin"
# OMNIOPS_* variables that hold host paths (or host-only settings) are not forwarded.
HOST_ONLY_ENV = frozenset({"OMNIOPS_CONFIG", "OMNIOPS_MANIFEST_DIRS", "OMNIOPS_MEMORY_PATH", "OMNIOPS_CASE_DIR",
                           "OMNIOPS_WHEEL"})
# Budgets nest under Harbor's 600 s agent timeout (3.13): chat 360 s + watchdog 120 s < runner 540 s.
# Host OMNIOPS_* values override these defaults.
DEFAULT_ENV = {"OMNIOPS_CHAT_BUDGET_S": "360", "OMNIOPS_BENCH_TIMEOUT_S": "540"}
# The container's stack env says where Grafana & co. live; the run is always a benchmark with staged
# writes. Case files go under /logs/agent/omniops/ (a sub-directory: the grader parses only
# /logs/agent/*.json, which must stay the trajectory alone).
FIXED_ENV = {"OMNIOPS_BENCHMARK_MODE": "1", "OMNIOPS_WRITE_MODE": "stage", "PYTHONUNBUFFERED": "1",
             "OMNIOPS_CASE_DIR": "/logs/agent/omniops/cases"}


def omniops_model(model_name: str | None) -> str | None:
    """Harbor's ``provider/model`` (o11y-bench requires a provider) -> the OmniOps heavy model id."""
    if not model_name:
        return None
    return model_name.split("/", 1)[1] if "/" in model_name else model_name


def find_wheel(explicit: str | None = None, dist_dir: Path | None = None) -> Path:
    """The wheel to install: ``wheel=`` agent kwarg, then ``$OMNIOPS_WHEEL``, then the newest in dist."""
    dist_dir = dist_dir or DIST_DIR
    for candidate in (explicit, os.environ.get("OMNIOPS_WHEEL")):
        if candidate:
            path = Path(candidate).expanduser()
            if not path.is_file():
                raise FileNotFoundError(f"OmniOps wheel not found: {path}")
            return path
    wheels = sorted(dist_dir.glob("omniops-*.whl"), key=lambda p: p.stat().st_mtime)
    if not wheels:
        raise FileNotFoundError(f"no omniops-*.whl in {dist_dir}: run omniops/scripts/o11ybench_setup.sh first")
    return wheels[-1]


def container_env(model_name: str | None, host_env: dict[str, str], extra_env: dict[str, str]) -> dict[str, str]:
    """Environment for the in-container runner.
    Precedence: defaults < host passthrough < --model < fixed < extra_env (``--ak extra_env=...``).

    Stack URLs (``GRAFANA_URL`` ...) never come from the host: an OmniOps user's own exported config
    would silently point every trial at their stack instead of the task's seeded sidecar."""
    env = {"PATH": CONTAINER_PATH, **DEFAULT_ENV}
    # The task's compose file already sets the scenario clock; an empty value must not mask it.
    if host_env.get("O11Y_SCENARIO_TIME_ISO"):
        env["O11Y_SCENARIO_TIME_ISO"] = host_env["O11Y_SCENARIO_TIME_ISO"]
    key_env = host_env.get("OMNIOPS_LLM_API_KEY_ENV") or "MODEL_API_KEY"
    for name in {"MODEL_API_KEY", key_env}:
        if host_env.get(name):
            env[name] = host_env[name]
    env.update({k: v for k, v in host_env.items() if k.startswith("OMNIOPS_") and v and k not in HOST_ONLY_ENV})
    model = omniops_model(model_name)
    if model:
        env["OMNIOPS_MODEL_HEAVY"] = model
    env.update(FIXED_ENV)
    env.update(extra_env)
    return env


def install_command(wheel_name: str, with_requirements: bool) -> str:
    reqs = f"-r {CONTAINER_DIR}/requirements.txt " if with_requirements else ""
    script = (
        "set -euo pipefail; "
        f'uv venv --allow-existing --python "$(command -v python3)" {VENV}; '
        f"uv pip install --python {VENV}/bin/python {reqs}{CONTAINER_DIR}/{wheel_name}; "
        f"{VENV}/bin/python -c 'import omniops.bench.o11ybench.runner'"
    )
    return f"bash -lc {shlex.quote(script)}"


def runner_command() -> str:
    script = (
        "set -o pipefail; mkdir -p /logs/agent/command-0; "
        f"{VENV}/bin/python -m omniops.bench.o11ybench.runner --instruction-file {INSTRUCTION_PATH} "
        f'--out {TRAJECTORY_PATH} 2>&1 | tee "{STDOUT_PATH}"'
    )
    return f"bash -lc {shlex.quote(script)}"


def _tail(text: str | None, limit: int = 2000) -> str:
    text = text or ""
    return text if len(text) <= limit else f"...[{len(text) - limit} characters omitted]...{text[-limit:]}"


class OmniOpsHarborAgent(BaseAgent):
    """OmniOps inside the Harbor task container, entered through the Slack simulator."""

    # Harbor refuses tasks that configure MCP servers unless the agent declares support. OmniOps
    # never uses them: data access is SQL over the stack's HTTP APIs.
    capabilities = AgentCapabilities(mcp_servers=True)

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        reasoning_effort: str = "off",
        extra_env: dict[str, str] | None = None,
        wheel: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        self.mcp_servers = []
        # Recorded only: OmniOps sets effort per role (verdicts high, loops medium), not per job.
        self.reasoning_effort = reasoning_effort
        self._extra_env = dict(extra_env or {})
        self._wheel_arg = wheel
        self._wheel: Path | None = None

    @staticmethod
    def name() -> str:
        return "omniops"

    def version(self) -> str:
        try:
            wheel = self._wheel or find_wheel(self._wheel_arg)
        except FileNotFoundError:
            return "unknown"
        return wheel.name.split("-")[1]

    async def setup(self, environment: BaseEnvironment) -> None:
        self._wheel = find_wheel(self._wheel_arg)
        reqs = self._wheel.parent / "requirements.txt"
        await environment.exec(command=f"mkdir -p {CONTAINER_DIR}")
        await environment.upload_file(source_path=self._wheel, target_path=f"{CONTAINER_DIR}/{self._wheel.name}")
        if reqs.is_file():
            await environment.upload_file(source_path=reqs, target_path=f"{CONTAINER_DIR}/requirements.txt")
        result = await environment.exec(command=install_command(self._wheel.name, reqs.is_file()),
                                        env={"PATH": CONTAINER_PATH})
        if result.return_code != 0:
            output = _tail(f"{getattr(result, 'stdout', '') or ''}\n{getattr(result, 'stderr', '') or ''}")
            raise NonZeroAgentExitCodeError(f"OmniOps install failed (exit {result.return_code}): {output}")
        self.logger.info(f"Installed {self._wheel.name} into {VENV}")

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        instruction_path = self.logs_dir / "instruction.txt"
        instruction_path.write_text(instruction)
        await environment.upload_file(source_path=instruction_path, target_path=INSTRUCTION_PATH)

        env = container_env(self.model_name, dict(os.environ), self._extra_env)
        model = env.get("OMNIOPS_MODEL_HEAVY", "default")
        self.logger.info(f"Running OmniOps (model={model}) via Slack DM; env keys: {sorted(env)}")
        result = await environment.exec(command=runner_command(), env=env)

        try:
            await environment.download_file(source_path=TRAJECTORY_PATH,
                                            target_path=self.logs_dir / "trajectory.json")
            trajectory = json.loads((self.logs_dir / "trajectory.json").read_text())
            fm = trajectory.get("final_metrics") or {}
            context.n_input_tokens = fm.get("total_prompt_tokens")
            context.n_output_tokens = fm.get("total_completion_tokens")
            context.n_cache_tokens = fm.get("total_cached_tokens")
            context.cost_usd = fm.get("total_cost_usd")
        except Exception as e:  # noqa: BLE001 - a missing trajectory must not hide the exit code below
            self.logger.warning(f"Could not read trajectory metrics: {e}")

        context.metadata = {
            **(context.metadata or {}),
            "model": model,
            "requested_model": self.model_name,
            "reasoning_effort": self.reasoning_effort,
            "entry_point": "slack_dm",
            "data_access": "sql_over_http",
        }
        if result.return_code != 0:
            message = f"OmniOps runner exited with code {result.return_code} (infrastructure failure)"
            self.logger.error(message)
            raise NonZeroAgentExitCodeError(message)
