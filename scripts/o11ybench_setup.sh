#!/usr/bin/env bash
# Prepare Grafana o11y-bench to run OmniOps (Slack DM entry, SQL over HTTP, no MCP).
#
#   scripts/o11ybench_setup.sh [--skip-sync] [--skip-preflight]
#
# 1. builds the OmniOps wheel (uv build) and a locked requirements.txt (uv export --frozen)
# 2. installs the Harbor agent: src/omniops/bench/o11ybench/harbor_agent.py ->
#    <o11y-bench>/agents/omniops_agent.py, wheel + requirements -> <o11y-bench>/agents/dist/omniops/
# 3. prepares o11y-bench: mise install (if mise exists), uv sync, task generation, agent import check
# 4. preflight: Docker reachable, o11y-bench's harbor_preflight.sh (shared images), keys present
#
# Env: O11Y_BENCH_DIR (default ../vendor/o11y-bench next to this repo). Never prints key values.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BENCH_DIR="${O11Y_BENCH_DIR:-$ROOT/../vendor/o11y-bench}"
AGENT_SRC="$ROOT/src/omniops/bench/o11ybench/harbor_agent.py"
# uv / mise from their usual user install dirs, after the caller's own PATH.
for d in "$HOME/.local/bin" "$HOME/.cargo/bin" "$HOME"/Library/Python/*/bin; do
  [ -d "$d" ] && PATH="$PATH:$d"
done
export PATH

SKIP_SYNC=0
SKIP_PREFLIGHT=0
for arg in "$@"; do
  case "$arg" in
    --skip-sync) SKIP_SYNC=1 ;;
    --skip-preflight) SKIP_PREFLIGHT=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n== %s\n' "$*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

[ -d "$BENCH_DIR/agents" ] || die "o11y-bench not found at $BENCH_DIR (set O11Y_BENCH_DIR)"
BENCH_DIR="$(cd "$BENCH_DIR" && pwd)"
command -v uv >/dev/null || die "uv is required (https://docs.astral.sh/uv/)"

step "Build the OmniOps wheel"
DIST="$ROOT/dist"
rm -rf "$DIST"
(cd "$ROOT" && uv build --wheel --out-dir "$DIST")
(cd "$ROOT" && uv export --frozen --no-dev --no-emit-project --no-hashes -o "$DIST/requirements.txt" >/dev/null)
WHEEL="$(ls -t "$DIST"/omniops-*.whl | head -n 1)"
[ -f "$WHEEL" ] || die "uv build produced no wheel"
echo "wheel: $WHEEL"

step "Install the Harbor agent into o11y-bench"
# Harbor imports the agent on the host inside o11y-bench's env: it must not import omniops.
if grep -Eq '^[[:space:]]*(from|import)[[:space:]]+omniops' "$AGENT_SRC"; then
  die "$AGENT_SRC imports omniops; Harbor loads it where omniops is not installed"
fi
AGENT_DIST="$BENCH_DIR/agents/dist/omniops"
mkdir -p "$AGENT_DIST"
rm -f "$AGENT_DIST"/omniops-*.whl "$AGENT_DIST/requirements.txt"
cp "$WHEEL" "$DIST/requirements.txt" "$AGENT_DIST/"
cp "$AGENT_SRC" "$BENCH_DIR/agents/omniops_agent.py"
echo "agent: $BENCH_DIR/agents/omniops_agent.py (agents.omniops_agent:OmniOpsHarborAgent)"

if [ "$SKIP_SYNC" -eq 0 ]; then
  step "Prepare o11y-bench (toolchain, environment, tasks)"
  if command -v mise >/dev/null; then
    (cd "$BENCH_DIR" && mise install)
  else
    echo "mise not found: relying on uv-managed Python 3.14 (uv sync downloads it)"
  fi
  (cd "$BENCH_DIR" && uv sync && uv run python -m scripts.sync_tasks)
fi

step "Check that Harbor can load the agent"
(cd "$BENCH_DIR" && uv run python -c \
  'from agents.omniops_agent import OmniOpsHarborAgent as A, find_wheel; print(A.name(), find_wheel().name)')

if [ "$SKIP_PREFLIGHT" -eq 0 ]; then
  step "Preflight"
  docker info >/dev/null 2>&1 || die "Docker is not reachable"
  (cd "$BENCH_DIR" && bash scripts/harbor_preflight.sh)
fi

step "Keys (values are never printed)"
[ -n "${MODEL_API_KEY:-}" ] && echo "MODEL_API_KEY: set" || echo "WARNING: MODEL_API_KEY is not set: the agent cannot reach the model"
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "ANTHROPIC_API_KEY: set (official judge available)"
else
  echo "ANTHROPIC_API_KEY: not set - use: scripts/o11ybench_run.sh <mode> --judge meta  (NON-OFFICIAL scores)"
fi
echo
echo "Ready. Next: scripts/o11ybench_run.sh smoke"
