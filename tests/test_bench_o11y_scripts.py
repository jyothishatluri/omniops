"""scripts/o11ybench_run.sh and o11ybench_setup.sh: syntax, task selection and judge wiring (dry runs)."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "scripts" / "o11ybench_run.sh"
SETUP = ROOT / "scripts" / "o11ybench_setup.sh"
SECRET = "sk-meta-secret-value"


@pytest.fixture
def bench(tmp_path: Path) -> Path:
    return make_bench(tmp_path)


def make_bench(root: Path) -> Path:
    """A minimal o11y-bench checkout: tasks-spec/<category>/<task>.yaml."""
    specs = {"prometheus_query": ["promql-error-rate", "query-cpu-metrics"], "grafana_api": ["get-datasource-details"],
             "dashboarding": ["dashboard-add-cache-lag-panels", "dashboard-add-deployment-annotation"]}
    for category, ids in specs.items():
        d = root / "tasks-spec" / category
        d.mkdir(parents=True)
        for task_id in ids:
            (d / f"{task_id}.yaml").write_text(f"id: {task_id}\ncategory: {category}\nstatement: x\n")
    (root / "agents").mkdir()
    return root


def run_script(bench: Path, *args: str, **env: str) -> str:
    full_env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "GRADING_MODEL")}
    full_env.update({"O11Y_BENCH_DIR": str(bench), "MODEL_API_KEY": SECRET, **env})
    argv = list(args)
    argv.insert(argv.index("--") if "--" in argv else len(argv), "--dry-run")
    proc = subprocess.run(["bash", str(RUN), *argv], capture_output=True, text=True, env=full_env, check=True)
    assert SECRET not in proc.stdout + proc.stderr            # key values are never printed
    return proc.stdout


def job_line(out: str) -> str:
    return next(line for line in out.splitlines() if " o11y_bench job " in line)


@pytest.mark.parametrize("script", [RUN, SETUP])
def test_scripts_parse(script: Path) -> None:
    subprocess.run(["bash", "-n", str(script)], check=True)


def test_smoke_runs_one_task_per_category_once(bench: Path) -> None:
    line = job_line(run_script(bench, "smoke"))
    assert "--agent-import-path agents.omniops_agent:OmniOpsHarborAgent" in line
    assert "--model meta/muse-wonder-1.0" in line and "--n-attempts 1" in line
    tasks = line.split("--task-name ")[1:]
    assert sorted(t.strip() for t in tasks) == ["dashboard-add-deployment-annotation", "get-datasource-details",
                                                "query-cpu-metrics"]      # preferred pick, else the first


def test_full_runs_every_task_three_times(bench: Path) -> None:
    out = run_script(bench, "full", "--model", "meta/muse-spark-1.3", "--n-concurrent", "2")
    assert "5 task(s) x 3 attempt(s)" in out
    line = job_line(out)
    assert "--n-attempts 3" in line and "--n-concurrent 2" in line and "--task-name" not in line
    assert "--job-name omniops-meta-muse-spark-1-3-full-k3" in line


def test_official_judge_keeps_the_task_judge(bench: Path) -> None:
    out = run_script(bench, "task", "promql-error-rate", "--", "--reasoning-effort", "high")
    line = job_line(out)
    assert "ANTHROPIC" not in out and "regrade" not in out
    assert line.rstrip().endswith("--task-name promql-error-rate --reasoning-effort high")


def test_meta_judge_disables_the_container_judge_and_regrades_on_the_host(bench: Path) -> None:
    out = run_script(bench, "smoke", "--judge", "meta", GRADING_MODEL="muse-spark-1.3")
    line = job_line(out)
    # the in-container verifier only ever sees a placeholder, never a real key
    assert "env ANTHROPIC_API_KEY=omniops-official-judge-disabled" in line
    assert "--job-name omniops-meta-muse-wonder-1-0-smoke-k1-metajudge" in line
    regrade = next(x for x in out.splitlines() if " o11y_bench regrade " in x)
    assert "ANTHROPIC_BASE_URL=https://api.meta.ai" in regrade and "ANTHROPIC_API_KEY=$MODEL_API_KEY" in regrade
    assert "GRADING_MODEL=muse-spark-1.3 " in regrade and regrade.rstrip().endswith("--offline")


def test_setup_installs_the_agent_under_its_import_path() -> None:
    text = SETUP.read_text()
    assert '"$BENCH_DIR/agents/omniops_agent.py"' in text
    assert "uv build --wheel" in text and "uv export --frozen" in text


def job_name(out: str) -> str:
    return job_line(out).split("--job-name ")[1].split()[0]


def test_task_mode_job_names_depend_on_the_task_list(bench: Path) -> None:
    """Another task list must not land in (and resume) the first list's job directory."""
    cpu = job_name(run_script(bench, "task", "query-cpu-metrics"))
    other = job_name(run_script(bench, "task", "promql-error-rate"))
    both = job_name(run_script(bench, "task", "query-cpu-metrics", "promql-error-rate"))
    assert len({cpu, other, both}) == 3 and cpu.startswith("omniops-meta-muse-wonder-1-0-task-t")
    assert job_name(run_script(bench, "task", "promql-error-rate", "query-cpu-metrics")) == both   # order-free


def test_existing_job_with_another_task_list_is_refused(bench: Path) -> None:
    """Resuming reruns the saved config.json filter: a new task would silently never run."""
    job = bench / "jobs" / "mine"
    job.mkdir(parents=True)
    (job / "config.json").write_text(json.dumps({"job_name": "mine",
                                                 "datasets": [{"path": "tasks", "task_names": ["query-cpu-metrics"]}]}))
    env = {**{k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}, "O11Y_BENCH_DIR": str(bench)}
    refused = subprocess.run(["bash", str(RUN), "task", "promql-error-rate", "--job-name", "mine", "--dry-run"],
                             capture_output=True, text=True, env=env, check=False)
    assert refused.returncode != 0 and "another task list" in refused.stderr
    full = subprocess.run(["bash", str(RUN), "full", "--job-name", "mine", "--dry-run"],
                          capture_output=True, text=True, env=env, check=False)
    assert full.returncode != 0 and "another task list" in full.stderr
    same = run_script(bench, "task", "query-cpu-metrics", "--job-name", "mine")       # a genuine resume
    assert "--job-name mine" in job_line(same)


def test_saved_official_regrade_command_survives_a_path_with_spaces(tmp_path: Path) -> None:
    bench = make_bench(tmp_path / "hackathon project")
    dist = bench / "agents" / "dist" / "omniops"
    dist.mkdir(parents=True)
    (dist / "omniops-0.1.0-py3-none-any.whl").write_bytes(b"PK")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"       # plays uv: the judge probe passes, the job creates its directory
    uv.write_text("""#!/bin/sh
jobs="" name=""
while [ $# -gt 0 ]; do
  case "$1" in --jobs-dir) jobs="$2"; shift ;; --job-name) name="$2"; shift ;; esac
  shift
done
if [ -n "$jobs" ] && [ -n "$name" ]; then mkdir -p "$jobs/$name"; fi
exit 0
""")
    uv.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "GRADING_MODEL")}
    env.update({"O11Y_BENCH_DIR": str(bench), "MODEL_API_KEY": SECRET, "PATH": f"{bin_dir}:{env['PATH']}"})
    proc = subprocess.run(["bash", str(RUN), "task", "query-cpu-metrics", "--judge", "meta"], capture_output=True,
                          text=True, env=env, check=False)
    assert proc.returncode == 0, proc.stderr
    assert SECRET not in proc.stdout + proc.stderr
    notes = next((bench / "jobs").glob("*/NON_OFFICIAL_JUDGE.txt"))
    line = next(x for x in notes.read_text().splitlines() if " regrade " in x)
    argv = shlex.split(line)
    assert argv[argv.index("cd") + 1] == str(bench)
    assert argv[argv.index("--jobs-dir") + 1] == str(bench / "jobs")
    assert argv[argv.index("--job-name") + 1] == notes.parent.name
