#!/usr/bin/env bash
# Bring up the Edge Delta AI SRE Arena (full suite) on a local kind cluster for OmniOps.
#
#   deploy/arena/up.sh [--check] [--gitops] [--with-argocd] [--skip-images]
#
#   --check        prerequisites only (Docker version and memory, tools, Python, Arena checkout)
#   --gitops       install Argo CD and stop before deploying the shop: continue with gitops.sh
#   --with-argocd  direct deployment, but also install Argo CD (OmniOps can then query it)
#   --skip-images  do not build/load the fault images (they are detected in the kind node anyway)
#
# Steps: kind cluster from the Arena's infra/kind/cluster.yaml -> Cilium (NetworkPolicy enforcement for
# netpol-isolation) -> build + load the fault images -> observability stack -> 'bench deploy' (direct)
# -> shop collector + Postgres exporter -> port-forwards -> .state/omniops.env -> telemetry self-test.
# Every step is idempotent: rerun after a failure.
source "$(dirname "$0")/lib.sh"

CHECK_ONLY=0 GITOPS=0 WITH_ARGOCD=0 SKIP_IMAGES=0
for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=1 ;;
    --gitops) GITOPS=1 ;;
    --with-argocd) WITH_ARGOCD=1 ;;
    --skip-images) SKIP_IMAGES=1 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) die "unknown option $arg (see --help)" ;;
  esac
done

create_cluster() {
  if cluster_exists; then
    note "kind cluster '$CLUSTER_NAME' exists"
    return
  fi
  log "Creating kind cluster '$CLUSTER_NAME' (default CNI disabled; Cilium follows)"
  kind create cluster --name "$CLUSTER_NAME" --config "$ARENA_DIR/infra/kind/cluster.yaml" --wait 0s
}

install_cilium() {
  if helm status cilium --namespace kube-system --kube-context "$CONTEXT" >/dev/null 2>&1; then
    note "Cilium installed"
  else
    log "Installing Cilium $CILIUM_VERSION (the Arena's kind guide)"
    helm upgrade --install cilium cilium --repo https://helm.cilium.io --version "$CILIUM_VERSION" \
      --kube-context "$CONTEXT" --namespace kube-system \
      --set ipam.mode=kubernetes --set operator.replicas=1 --wait --timeout 10m
  fi
  kc wait --for=condition=Ready nodes --all --timeout=300s >/dev/null
}

# Every fault image in scenarios.json, not just one: build-images loads them one at a time, so a rerun
# after a failure part-way must not skip the images that were never loaded.
images_loaded() {
  local names loaded name
  names="$("$PY" -c 'import json, sys
print(" ".join(sorted({e["image"] for e in json.load(open(sys.argv[1])).values() if e.get("image")})))' \
    "$ARENA_DIR/scenarios/scenarios.json")" && [ -n "$names" ] || return 1
  loaded="$(docker exec "$CLUSTER_NAME-worker" crictl images 2>/dev/null)" || return 1
  for name in $names; do
    printf '%s\n' "$loaded" | grep -Eq "^fixture\.local/$name[[:space:]]+v1[[:space:]]" || {
      note "fault image fixture.local/$name:v1 is not loaded in the kind node"
      return 1
    }
  done
}

build_images() {
  if [ "$SKIP_IMAGES" = 1 ] || images_loaded; then
    note "fault images present in the kind node"
    return
  fi
  log "Building the full-suite fault images (linux/amd64) and loading them into kind (10-20 min)"
  arena_cli bench.scenarios build-images --kind-name "$CLUSTER_NAME"
}

deploy_shop() {
  if kc -n benchmark-control get configmap suite-state >/dev/null 2>&1; then
    note "Arena shop already deployed"
    return
  fi
  log "Deploying the Arena shop (bench deploy; waits for a healthy end-to-end request)"
  helper run-config --out "$STATE_DIR/arena-setup.json" --context "$CONTEXT"
  arena_cli bench --run "$STATE_DIR/arena-setup.json" --case setup deploy >/dev/null
}

check_prereqs
[ "$CHECK_ONLY" = 1 ] && { log "Prerequisites OK"; exit 0; }
ensure_state_dir
create_cluster
install_cilium
build_images
"$ARENA_DEPLOY_DIR/observability.sh" install
if [ "$GITOPS" = 1 ]; then
  "$ARENA_DEPLOY_DIR/argocd.sh" install
  "$ARENA_DEPLOY_DIR/omniops-env.sh" >/dev/null
  log "Cluster, observability and Argo CD are up. The shop is deployed through Git next:"
  note "deploy/arena/gitops.sh prepare      # export into local checkouts, prints the gh/git commands"
  note "(review + run the printed commands)  # creates the public repos and pushes - your decision"
  note "deploy/arena/gitops.sh bootstrap    # Argo CD deploys the shop from GitHub"
  exit 0
fi
deploy_shop
"$ARENA_DEPLOY_DIR/observability.sh" connect-shop
if [ "$WITH_ARGOCD" = 1 ]; then "$ARENA_DEPLOY_DIR/argocd.sh" install; fi
"$ARENA_DEPLOY_DIR/port-forward.sh" start
"$ARENA_DEPLOY_DIR/omniops-env.sh"
log "Waiting 3 minutes for the first telemetry before the self-test"
sleep 180
"$ARENA_DEPLOY_DIR/observability.sh" selftest || warn "Some signals are missing (see above); rerun 'deploy/arena/observability.sh selftest' in a few minutes."
log "Arena is up. Next: docs/BENCH_ARENA.md 'Run the benchmark' (dry run: uv run --no-sync python -m omniops.bench.arena.runner --dry-run --scenarios oom)"
