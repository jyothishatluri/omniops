# Shared settings and helpers for deploy/arena/*.sh (sourced, never executed).
#
# Bash 3.2 compatible (macOS /bin/bash). Secrets (Grafana admin password, service tokens, Slack
# credentials, database passwords) only ever travel through files under $STATE_DIR (mode 600),
# Kubernetes Secrets or stdin pipes; they are never echoed, put on a command line or logged.

set -euo pipefail

ARENA_DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OMNIOPS_ROOT="$(cd "$ARENA_DEPLOY_DIR/../.." && pwd)"
STATE_DIR="${ARENA_STATE_DIR:-$ARENA_DEPLOY_DIR/.state}"
ARENA_DIR="${ARENA_DIR:-$(cd "$OMNIOPS_ROOT/.." && pwd)/vendor/project-arena}"
HELPER="$ARENA_DEPLOY_DIR/helper.py"

CLUSTER_NAME="${ARENA_CLUSTER_NAME:-incident-bench}"
CONTEXT="${ARENA_CONTEXT:-kind-$CLUSTER_NAME}"
OBS_NS="observability"
ARGOCD_NS="argocd"

# Pinned versions (each released at least two weeks before this adapter was written).
CILIUM_VERSION="1.20.2"            # the Arena's infra/kind guide
ARGOCD_CHART_VERSION="10.4.0"      # the Arena's docs/gitops.md

# Laptop sizing (Intel Mac, 16 GB): the full suite + LGTM + Argo CD fit in ~10 GB of Docker memory.
MIN_DOCKER_SERVER_MAJOR=24         # Docker Desktop 4.11 (Engine 20.10) is too old for kind v0.33 + Cilium
REC_DOCKER_MEM_MIB=9500            # Docker Desktop "Memory: 10 GB" reports ~9.7 GiB
MIN_DOCKER_MEM_MIB=6000
REC_DOCKER_CPUS=4

GRAFANA_PORT="${GRAFANA_PORT:-3000}"
PROMETHEUS_PORT="${PROMETHEUS_PORT:-9090}"
LOKI_PORT="${LOKI_PORT:-3100}"
TEMPO_PORT="${TEMPO_PORT:-3200}"
ARGOCD_PORT="${ARGOCD_PORT:-8080}"
KUBE_PROXY_PORT="${KUBE_PROXY_PORT:-8001}"
BRIDGE_PORT="${OMNIOPS_ARENA_BRIDGE_PORT:-8787}"

# OmniOps' Kubernetes view (port-forward.sh): a read-only ServiceAccount behind helper.py's kube-proxy,
# which hides the Arena's control namespace (ConfigMap suite-state names the active scenario: 3.16).
OMNIOPS_KUBE_CONTEXT="omniops-arena"
KUBE_HIDDEN_NAMESPACES="benchmark-control"
ARGOCD_HIDDEN_APPS="arena-control"     # argocd/values.yaml denies it to the omniops account

# Tools installed by the operator live in ~/.local/bin (kind, kubectl, helm, gh); prefer them.
PATH="${ARENA_BIN_DIR:-$HOME/.local/bin}:$PATH"
export PATH

log() { printf '\033[1m==>\033[0m %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
warn() { printf '\033[33mWARNING:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

kc() { kubectl --context "$CONTEXT" "$@"; }

need() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is not on PATH. $2"
}

ensure_state_dir() {
  mkdir -p "$STATE_DIR"
  chmod 700 "$STATE_DIR"
}

# Writes stdin to a mode-600 file (secrets).
write_private() {
  ensure_state_dir
  (umask 077 && cat >"$1")
}

# A Python 3.10+ interpreter for the Arena CLI and helper.py (the Arena needs 3.10+; macOS ships 3.9).
arena_python() {
  local candidate check='import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
  if [ -n "${ARENA_PYTHON:-}" ]; then
    "$ARENA_PYTHON" -c "$check" 2>/dev/null && printf '%s\n' "$ARENA_PYTHON"
    return
  fi
  for candidate in python3.13 python3.12 python3.11 python3.10 python3 "$OMNIOPS_ROOT/venv/bin/python"; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "$check" 2>/dev/null; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

PY="$(arena_python || true)"

helper() { "$PY" "$HELPER" "$@"; }

# Runs the Arena CLI from its checkout (it resolves scenarios relative to its own tree).
arena_cli() { (cd "$ARENA_DIR" && "$PY" -m "$@"); }

cluster_exists() { kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; }

argocd_installed() { kc -n "$ARGOCD_NS" get service argo-cd-argocd-server >/dev/null 2>&1; }

# Prerequisites with actionable messages. Exits non-zero on a hard failure.
check_prereqs() {
  local failed=0 server major mem_bytes mem_mib cpus arch
  log "Checking prerequisites"
  [ -n "$PY" ] || { warn "Python 3.10+ not found (the Arena CLI needs it). Install python3.12 or set ARENA_PYTHON."; failed=1; }
  [ -d "$ARENA_DIR/bench" ] || { warn "Arena checkout not found at $ARENA_DIR (set ARENA_DIR)."; failed=1; }
  for tool in kind kubectl helm; do
    command -v "$tool" >/dev/null 2>&1 || { warn "'$tool' not found; install it into ~/.local/bin (kind v0.33, kubectl v1.37, helm v4)."; failed=1; }
  done
  if ! command -v docker >/dev/null 2>&1; then
    warn "Docker CLI not found. Install Docker Desktop (current release) and start it."
    failed=1
  elif ! server="$(docker version --format '{{.Server.Version}}' 2>/dev/null)" || [ -z "$server" ]; then
    warn "The Docker daemon is not reachable. Start Docker Desktop; if it does not start (Docker Desktop 4.11 is known not to), upgrade Docker Desktop to the current release first."
    failed=1
  else
    major="${server%%.*}"
    if [ "$major" -lt "$MIN_DOCKER_SERVER_MAJOR" ] 2>/dev/null; then
      warn "Docker Engine $server is too old (Docker Desktop 4.11 era). Upgrade Docker Desktop to the current release (Engine >= $MIN_DOCKER_SERVER_MAJOR), restart it, then rerun."
      failed=1
    fi
    mem_bytes="$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)"
    mem_mib=$((mem_bytes / 1048576))
    cpus="$(docker info --format '{{.NCPU}}' 2>/dev/null || echo 0)"
    arch="$(docker info --format '{{.Architecture}}' 2>/dev/null || echo unknown)"
    if [ "$mem_mib" -lt "$MIN_DOCKER_MEM_MIB" ]; then
      warn "Docker has ${mem_mib} MiB of memory; the full suite needs >= 10 GB. Docker Desktop > Settings > Resources > Memory: 10 GB."
      [ "${ARENA_FORCE:-0}" = 1 ] || failed=1
    elif [ "$mem_mib" -lt "$REC_DOCKER_MEM_MIB" ]; then
      warn "Docker has ${mem_mib} MiB of memory; 10 GB is recommended (Docker Desktop > Settings > Resources). Pods may be OOM-killed or evicted for reasons unrelated to the injected fault."
    fi
    [ "$cpus" -ge "$REC_DOCKER_CPUS" ] 2>/dev/null || warn "Docker has $cpus CPUs; $REC_DOCKER_CPUS or more are recommended."
    case "$arch" in
      x86_64|amd64) ;;
      *) warn "Docker reports architecture '$arch': the full suite's prebuilt shop images are linux/amd64 only."; failed=1 ;;
    esac
  fi
  [ "$failed" = 0 ] || die "Prerequisites are not met (see the warnings above, and docs/BENCH_ARENA.md 'Before you start')."
  note "Docker $server, ${mem_mib} MiB, $cpus CPUs, $arch; Python $("$PY" -c 'import platform; print(platform.python_version())')"
}
