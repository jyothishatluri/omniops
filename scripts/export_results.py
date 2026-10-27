"""Export benchmark results and coverage figures to CSV for the results dashboard.

    uv run --no-sync python scripts/export_results.py [--o11y-job DIR]

Writes results/o11ybench_tasks.csv (one row per task of an o11y-bench job, regraded scores) and
results/coverage.csv (sources, tables, offline manifest checks, tests).
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import statistics
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT.parent / "vendor" / "o11y-bench"
OUT = ROOT / "results"


def o11y_tasks(job: Path) -> list[dict]:
    categories = {Path(f).stem: Path(f).parent.name for f in glob.glob(str(BENCH / "tasks-spec" / "*" / "*.yaml"))}
    trials: dict[str, list[dict]] = {}
    for d in sorted(job.glob("*__*")):
        prefix = d.name.rsplit("__", 1)[0]
        task = prefix if prefix in categories else next((t for t in categories if t.startswith(prefix)), prefix)
        gd = d / "verifier" / "grading_details.json"
        g = json.loads(gd.read_text()) if gd.exists() else {}
        fm = {}
        tj = d / "agent" / "trajectory.json"
        if tj.exists():
            fm = json.loads(tj.read_text()).get("final_metrics", {})
        trials.setdefault(task, []).append({
            "score": float(g.get("score") or 0.0), "passed": g.get("status") == "passed",
            "cost": float(fm.get("total_cost_usd") or 0.0),
            "seconds": float((fm.get("extra") or {}).get("elapsed_seconds") or 0.0),
            "tool_calls": int((fm.get("extra") or {}).get("total_tool_calls") or 0),
        })
    rows = []
    for task, ts in sorted(trials.items()):
        rows.append({
            "task": task,
            "category": categories.get(task, "unknown"),
            "attempts": len(ts),
            "attempts_passed": sum(t["passed"] for t in ts),
            "mean_score": round(statistics.mean(t["score"] for t in ts), 4),
            "pass_all": int(all(t["passed"] for t in ts)),
            "pass_any": int(any(t["passed"] for t in ts)),
            "cost_usd": round(sum(t["cost"] for t in ts), 4),
            "median_seconds": round(statistics.median(t["seconds"] for t in ts), 1),
            "mean_tool_calls": round(statistics.mean(t["tool_calls"] for t in ts), 1),
        })
    return rows


def coverage() -> list[dict]:
    from omniops.engine.loader import load_all_manifests
    from omniops.engine.manifest_check import check_manifest

    manifests, _ = load_all_manifests(strict=False)
    checks = [r for m in manifests.values() for r in check_manifest(m, manifests)]
    collected = subprocess.run(["uv", "run", "--no-sync", "pytest", "--collect-only", "-q"], check=True, cwd=ROOT,
                               capture_output=True, text=True).stdout
    tests = sum(1 for line in collected.splitlines() if "::" in line)
    return [
        {"metric": "Platform sources", "value": len(manifests)},
        {"metric": "SQL tables", "value": sum(len(m.tables) for m in manifests.values())},
        {"metric": "Offline manifest checks passing", "value": sum(r.ok for r in checks)},
        {"metric": "Offline manifest checks", "value": len(checks)},
        {"metric": "Automated tests", "value": tests},
    ]


def write(name: str, rows: list[dict]) -> None:
    OUT.mkdir(exist_ok=True)
    with open(OUT / name, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {OUT / name} ({len(rows)} rows)")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--o11y-job", default=str(BENCH / "jobs" / "omniops-meta-muse-wonder-1-0-full-k3-metajudge"))
    a = p.parse_args()
    write("o11ybench_tasks.csv", o11y_tasks(Path(a.o11y_job)))
    write("coverage.csv", coverage())


if __name__ == "__main__":
    main()
