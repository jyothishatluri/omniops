#!/usr/bin/env bash
# Run the o11y-bench scripts inside a Linux container that drives this host's Docker.
#
#   scripts/o11ybench_docker.sh scripts/o11ybench_setup.sh
#   scripts/o11ybench_docker.sh scripts/o11ybench_run.sh smoke --judge meta
#
# Use it when o11y-bench's host dependencies cannot be installed natively (Intel Macs: no x86_64 macOS
# wheels for cryptography>=50). The project's parent directory is mounted at the SAME absolute path, so
# the bind mounts Harbor/compose ask the host daemon for (task dirs, jobs dir, the OmniOps wheel)
# resolve on the host. Secrets are passed by name (-e VAR), never printed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PARENT="$(cd "$ROOT/.." && pwd)"
IMAGE="omniops-o11ybench-host:latest"

[ $# -ge 1 ] || { sed -n '2,10p' "$0"; exit 2; }
docker build -q -t "$IMAGE" "$ROOT/deploy/o11ybench" >/dev/null

# Load omniops/.env into this process so the keys can be forwarded by name.
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi

ENV_ARGS=()
for var in MODEL_API_KEY ANTHROPIC_API_KEY OPENROUTER_API_KEY GRADING_MODEL META_ANTHROPIC_BASE_URL \
           O11Y_SCENARIO_TIME_ISO O11Y_STACK_GOMAXPROCS; do
  if [ -n "${!var:-}" ]; then ENV_ARGS+=(-e "$var"); fi
done
while IFS='=' read -r name _; do
  case "$name" in OMNIOPS_*) ENV_ARGS+=(-e "$name") ;; esac
done < <(env)

# The benchmark checkout may be a symlink to a folder outside an iCloud-synced Documents directory (iCloud
# makes "name 2" conflict copies and returns EIO under benchmark write load). Mount its real path at the same
# path too, so both the link and the paths Harbor resolves through it exist inside the container.
EXTRA_MOUNTS=()
BENCH="${O11Y_BENCH_DIR:-$PARENT/vendor/o11y-bench}"
if [ -e "$BENCH" ]; then
  REAL="$(cd "$BENCH" && pwd -P)"
  case "$REAL" in "$PARENT"/*) ;; *) EXTRA_MOUNTS+=(-v "$REAL:$REAL") ;; esac
fi

TTY=()
[ -t 1 ] && TTY=(-t)
exec docker run --rm ${TTY[@]+"${TTY[@]}"} \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$PARENT:$PARENT" -w "$ROOT" \
  ${EXTRA_MOUNTS[@]+"${EXTRA_MOUNTS[@]}"} \
  -v omniops-o11ybench-uv-cache:/root/.cache/uv \
  -e UV_LINK_MODE=copy \
  ${ENV_ARGS[@]+"${ENV_ARGS[@]}"} \
  "$IMAGE" bash "$@"
