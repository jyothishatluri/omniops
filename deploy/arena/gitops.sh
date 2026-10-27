#!/usr/bin/env bash
# The Arena's GitOps path (docs/gitops.md) with two PUBLIC GitHub repositories:
#   <owner>/service-manifests   application: Argo Applications + shop components (path arena-apps)
#   <owner>/platform-config     faults: library, batch-active, flagd-values, control (path arena-faults)
#
#   gitops.sh prepare     clone or init both checkouts under .state/gitops, run 'bench.gitops export',
#                         point the exported collector at the LGTM stack, then STOP and print the
#                         gh / git commands for you to review and run (this script never creates a
#                         repository, commits or pushes: publishing is a human decision)
#   gitops.sh bootstrap   after you pushed: check both checkouts are clean and pushed, then
#                         'bench.gitops bootstrap' + 'sync' and enable the Postgres exporter
#   gitops.sh status
#
# Env: GITHUB_OWNER (default: the gh login), ARENA_APP_REPO=service-manifests,
#      ARENA_FAULT_REPO=platform-config.
source "$(dirname "$0")/lib.sh"

APP_REPO="${ARENA_APP_REPO:-service-manifests}"
FAULT_REPO="${ARENA_FAULT_REPO:-platform-config}"
GITOPS_DIR="$STATE_DIR/gitops"
RUN_FILE="$STATE_DIR/arena-gitops.json"

owner() {
  if [ -n "${GITHUB_OWNER:-}" ]; then
    printf '%s\n' "$GITHUB_OWNER"
    return
  fi
  need gh "Install the GitHub CLI into ~/.local/bin."
  gh api user --jq .login 2>/dev/null || die "Not logged in to GitHub: run 'gh auth login' (or set GITHUB_OWNER)."
}

checkout() {   # owner repo -> prints "created" when the GitHub repository does not exist yet
  local own="$1" name="$2" dir="$GITOPS_DIR/$2"
  if [ -d "$dir/.git" ]; then
    return 0
  fi
  if gh repo view "$own/$name" >/dev/null 2>&1; then
    git clone --quiet "https://github.com/$own/$name.git" "$dir"
  else
    git init --quiet -b main "$dir"
    git -C "$dir" remote add origin "https://github.com/$own/$name.git"
    echo created
  fi
}

prepare() {
  local own missing="" name
  need git "Install git."
  need gh "Install the GitHub CLI into ~/.local/bin, then run 'gh auth login'."
  ensure_state_dir
  mkdir -p "$GITOPS_DIR"
  own="$(owner)"
  for name in "$APP_REPO" "$FAULT_REPO"; do
    if [ "$(checkout "$own" "$name")" = created ]; then missing="$missing $name"; fi
  done
  helper run-config --out "$RUN_FILE" --context "$CONTEXT" --run-id omniops-gitops-setup \
    --output-dir gitops-runs --github-owner "$own" --app-repo "$APP_REPO" --fault-repo "$FAULT_REPO"
  log "Exporting the Arena's desired state into the local checkouts"
  arena_cli bench.gitops --run "$RUN_FILE" export >/dev/null
  helper gitops-collector --resources "$GITOPS_DIR/$APP_REPO/arena-apps/components/telemetry/resources.json" \
    --config "$ARENA_DEPLOY_DIR/observability/collector.yaml"
  log "Review the exported trees, then run these commands yourself (nothing has been published):"
  for name in $missing; do
    note "gh repo create $own/$name --public --description \"Arena GitOps repository for OmniOps\""
  done
  note "git -C '$GITOPS_DIR/$APP_REPO' add -A"
  note "git -C '$GITOPS_DIR/$APP_REPO' commit -m 'Add benchmark application'"
  note "git -C '$GITOPS_DIR/$APP_REPO' push -u origin main"
  note "git -C '$GITOPS_DIR/$FAULT_REPO' add -A"
  note "git -C '$GITOPS_DIR/$FAULT_REPO' commit -m 'Add benchmark fault definitions'"
  note "git -C '$GITOPS_DIR/$FAULT_REPO' push -u origin main"
  note "deploy/arena/gitops.sh bootstrap"
  note ""
  note "OmniOps reads $own/$APP_REPO only. To also give it the fault repository (changes the evidence"
  note "it can see; record that choice), export OMNIOPS_ARENA_FAULT_REPO_ACCESS=1 before omniops-env.sh."
}

pushed() {   # repo dir: clean and HEAD == origin/main
  local dir="$1" head remote
  [ -z "$(git -C "$dir" status --porcelain)" ] || { warn "$dir has uncommitted changes"; return 1; }
  head="$(git -C "$dir" rev-parse HEAD 2>/dev/null)" || { warn "$dir has no commit yet"; return 1; }
  remote="$(git -C "$dir" ls-remote origin refs/heads/main 2>/dev/null | cut -f1)"
  [ "$head" = "$remote" ] || { warn "$dir is not pushed (HEAD $head, origin/main ${remote:-missing})"; return 1; }
}

bootstrap() {
  [ -f "$RUN_FILE" ] || die "Run 'gitops.sh prepare' first."
  argocd_installed || die "Argo CD is not installed: deploy/arena/argocd.sh install"
  pushed "$GITOPS_DIR/$APP_REPO" && pushed "$GITOPS_DIR/$FAULT_REPO" ||
    die "Commit and push both checkouts first (see 'gitops.sh prepare' output)."
  log "Bootstrapping Argo CD applications (bench.gitops bootstrap)"
  arena_cli bench.gitops --run "$RUN_FILE" bootstrap
  log "Waiting for Argo CD to apply the pushed commits (bench.gitops sync)"
  arena_cli bench.gitops --run "$RUN_FILE" sync
  "$ARENA_DEPLOY_DIR/observability.sh" connect-shop --gitops
  "$ARENA_DEPLOY_DIR/omniops-env.sh"
}

status() {
  local name
  for name in "$APP_REPO" "$FAULT_REPO"; do
    if [ -d "$GITOPS_DIR/$name/.git" ]; then
      note "$name: $(git -C "$GITOPS_DIR/$name" log -1 --format='%h %s' 2>/dev/null || echo 'no commits')"
    fi
  done
  if argocd_installed; then kc -n "$ARGOCD_NS" get applications; fi
}

[ -n "$PY" ] || die "Python 3.10+ is required (set ARENA_PYTHON)."
case "${1:-}" in
  prepare) prepare ;;
  bootstrap) bootstrap ;;
  status) status ;;
  *) sed -n '2,17p' "$0"; exit 2 ;;
esac
