#!/usr/bin/env bash
# Argo CD for the Arena's GitOps path (chart argo-cd 10.4.0, pinned by the Arena's docs/gitops.md),
# plus a read-only API token for OmniOps (account 'omniops', role:readonly without the Arena's
# arena-control application, whose ConfigMap names the active scenario).
#
#   argocd.sh install    helm install/upgrade + token (.state/argocd-token, mode 600)
#   argocd.sh token      (re)create the read-only token only
#   argocd.sh uninstall
source "$(dirname "$0")/lib.sh"

install() {
  need helm "Install helm v4 into ~/.local/bin."
  log "Installing Argo CD (chart argo-cd $ARGOCD_CHART_VERSION) into namespace $ARGOCD_NS"
  helm repo add argo https://argoproj.github.io/argo-helm --force-update >/dev/null
  helm repo update argo >/dev/null
  helm upgrade --install argo-cd argo/argo-cd --version "$ARGOCD_CHART_VERSION" \
    --namespace "$ARGOCD_NS" --create-namespace --kube-context "$CONTEXT" \
    -f "$ARENA_DEPLOY_DIR/argocd/values.yaml" --wait --timeout 15m
  token
}

token() {
  ensure_state_dir
  argocd_installed || die "Argo CD is not installed (argocd.sh install)."
  "$ARENA_DEPLOY_DIR/port-forward.sh" stop >/dev/null
  "$ARENA_DEPLOY_DIR/port-forward.sh" start >/dev/null
  helper wait-http "http://localhost:$ARGOCD_PORT/healthz" --timeout 90
  # The initial admin password goes from the Secret straight into the helper's stdin.
  kc -n "$ARGOCD_NS" get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d |
    helper argocd-token --url "http://localhost:$ARGOCD_PORT" --account omniops --out "$STATE_DIR/argocd-token"
}

uninstall() {
  helm uninstall argo-cd --namespace "$ARGOCD_NS" --kube-context "$CONTEXT" || true
  kc delete namespace "$ARGOCD_NS" --ignore-not-found
  rm -f "$STATE_DIR/argocd-token"
}

[ -n "$PY" ] || die "Python 3.10+ is required (set ARENA_PYTHON)."
case "${1:-}" in
  install) install ;;
  token) token ;;
  uninstall) uninstall ;;
  *) sed -n '2,7p' "$0"; exit 2 ;;
esac
