#!/usr/bin/env bash
# Tear down the local Arena: stop port-forwards and delete the kind cluster (the Arena's
# 'bench cluster delete --confirm-delete'). Credentials minted for the deleted cluster are removed;
# the GitOps checkouts and their run configuration are kept unless --purge.
#
#   deploy/arena/down.sh --yes [--purge]
#
# GitHub repositories are never deleted by this script. Remove them yourself if you want:
#   gh repo delete <owner>/service-manifests ; gh repo delete <owner>/platform-config
source "$(dirname "$0")/lib.sh"

YES=0 PURGE=0
for arg in "$@"; do
  case "$arg" in
    --yes) YES=1 ;;
    --purge) PURGE=1 ;;
    *) sed -n '2,9p' "$0"; exit 2 ;;
  esac
done
[ "$YES" = 1 ] || { sed -n '2,9p' "$0"; die "Deleting the cluster needs --yes."; }

"$ARENA_DEPLOY_DIR/port-forward.sh" stop >/dev/null 2>&1 || true
if cluster_exists; then
  log "Deleting kind cluster '$CLUSTER_NAME'"
  arena_cli bench cluster delete --confirm-delete --name "$CLUSTER_NAME"
else
  note "kind cluster '$CLUSTER_NAME' does not exist"
fi
rm -f "$STATE_DIR/grafana-token" "$STATE_DIR/argocd-token" "$STATE_DIR/grafana-admin-password" \
      "$STATE_DIR/kube-upstream.json" "$STATE_DIR/omniops-kubeconfig" \
      "$STATE_DIR/omniops.env" "$STATE_DIR/slack-contact-point" "$STATE_DIR/port-forward.log"
if [ "$PURGE" = 1 ]; then
  rm -rf "$STATE_DIR"
  note "removed $STATE_DIR (local GitOps checkouts included; the GitHub repositories still exist)"
fi
log "Done. Benchmark results under runs/arena/ are kept."
