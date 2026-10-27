#!/usr/bin/env bash
# In-cluster observability for the Arena: grafana/otel-lgtm (Grafana, Prometheus, Loki, Tempo, OTLP),
# kube-state-metrics, Alloy (cAdvisor, Redis/Postgres exporters, pod logs, events), Grafana alert
# rules + contact points, and the shop's collector re-pointed at the stack.
#
#   observability.sh install                 stack + provisioning + OmniOps Grafana token
#   observability.sh connect-shop [--gitops] shop collector -> LGTM (direct mode) + Postgres exporter
#   observability.sh notifications          re-render Slack/webhook contact points from the environment
#   observability.sh selftest                verify the signals the alert rules need are flowing
#   observability.sh uninstall               remove the stack and restore the shop's original collector
#
# Slack contact point (optional): SLACK_WEBHOOK_URL (incoming webhook of a "Grafana" app), or
# GRAFANA_SLACK_TOKEN + GRAFANA_SLACK_CHANNEL (a second app's bot token, never OmniOps' own).
# Simulator runs receive notifications through a webhook to the runner's local bridge
# (OMNIOPS_ARENA_BRIDGE_URL, default http://host.docker.internal:8787/grafana).
source "$(dirname "$0")/lib.sh"

OBS_DIR="$ARENA_DEPLOY_DIR/observability"
BRIDGE_URL="${OMNIOPS_ARENA_BRIDGE_URL:-http://host.docker.internal:$BRIDGE_PORT/grafana}"

apply_stdin() { kc apply -f - >/dev/null; }

render_alloy() {
  local tmp with_pg="${1:-0}"
  tmp="$(mktemp)"
  cat "$OBS_DIR/alloy/base.alloy" >"$tmp"
  if [ "$with_pg" = 1 ]; then cat "$OBS_DIR/alloy/postgres.alloy" >>"$tmp"; fi
  kc -n "$OBS_NS" create configmap alloy-config --from-file=config.alloy="$tmp" \
    --from-file=postgres-queries.yaml="$OBS_DIR/alloy/postgres-queries.yaml" --dry-run=client -o yaml | apply_stdin
  rm -f "$tmp"
}

postgres_enabled() { kc -n "$OBS_NS" get secret alloy-postgres >/dev/null 2>&1; }

notifications() {
  ensure_state_dir
  helper notifications --namespace "$OBS_NS" --bridge-url "$BRIDGE_URL" \
    --mode-file "$STATE_DIR/slack-contact-point" | apply_stdin
}

install() {
  log "Installing the observability stack into namespace $OBS_NS"
  kc apply -f "$OBS_DIR/lgtm.yaml" >/dev/null          # namespace first; the pod waits for its config
  helper gen-password --out "$STATE_DIR/grafana-admin-password"
  kc -n "$OBS_NS" create secret generic grafana-admin \
    --from-file=password="$STATE_DIR/grafana-admin-password" --dry-run=client -o yaml | apply_stdin
  kc -n "$OBS_NS" create configmap grafana-provisioning \
    --from-file=datasources.yaml="$OBS_DIR/grafana/datasources.yaml" \
    --from-file=dashboards.yaml="$OBS_DIR/grafana/dashboards.yaml" \
    --from-file=arena-overview.json="$OBS_DIR/grafana/arena-overview.json" \
    --from-file=plugins.yaml="$OBS_DIR/grafana/plugins.yaml" \
    --from-file=alert-rules.yaml="$OBS_DIR/grafana/alert-rules.yaml" --dry-run=client -o yaml | apply_stdin
  notifications
  kc apply -f "$OBS_DIR/kube-state-metrics.yaml" >/dev/null
  if postgres_enabled; then render_alloy 1; else render_alloy 0; fi
  kc apply -f "$OBS_DIR/alloy.yaml" >/dev/null
  # provisioning is read at start: restart so changed rules/contact points take effect
  kc -n "$OBS_NS" rollout restart deployment/lgtm deployment/alloy >/dev/null
  log "Waiting for LGTM, kube-state-metrics and Alloy (first start pulls ~1 GB of images)"
  kc -n "$OBS_NS" rollout status deployment/kube-state-metrics --timeout=600s
  kc -n "$OBS_NS" rollout status deployment/lgtm --timeout=900s
  kc -n "$OBS_NS" rollout status deployment/alloy --timeout=600s
  grafana_token
}

grafana_token() {
  "$ARENA_DEPLOY_DIR/port-forward.sh" start >/dev/null
  helper wait-http "http://localhost:$GRAFANA_PORT/api/health" --timeout 120
  helper grafana-token --url "http://localhost:$GRAFANA_PORT" \
    --password-file "$STATE_DIR/grafana-admin-password" --out "$STATE_DIR/grafana-token"
}

connect_shop() {
  local gitops=0 password
  if [ "${1:-}" = "--gitops" ]; then gitops=1; fi
  kc -n benchmark-control get configmap suite-state >/dev/null 2>&1 ||
    die "The Arena shop is not deployed yet (bench deploy, or gitops.sh bootstrap)."
  if [ "$gitops" = 0 ]; then
    log "Pointing the shop's OpenTelemetry collector at the LGTM stack"
    local patch
    patch="$(mktemp)"
    helper collector-patch --config "$OBS_DIR/collector.yaml" >"$patch"
    kc -n telemetry patch configmap otel-collector --type merge --patch-file "$patch" >/dev/null
    rm -f "$patch"
    kc -n telemetry rollout restart deployment/otel-collector >/dev/null
    kc -n telemetry rollout status deployment/otel-collector --timeout=300s
  else
    note "GitOps mode: the collector change is committed in the application repository (gitops.sh)"
  fi
  log "Enabling the PostgreSQL exporter"
  # GitOps bootstrap generates MONITORING_PASSWORD; the direct deploy keeps the shop's init default.
  password="$(kc -n datastore get secret postgres -o jsonpath='{.data.MONITORING_PASSWORD}' 2>/dev/null | base64 -d 2>/dev/null || true)"
  [ -n "$password" ] || password="monitoring_password"
  if printf '%s' "$password" | kc -n datastore exec -i statefulset/postgres -- sh -c \
       'PGPASSWORD="$(cat)" psql -h 127.0.0.1 -U monitoring_user -d shop_db -tAc "select 1"' 2>/dev/null | grep -qx 1; then
    printf '%s' "$password" | helper postgres-secret --namespace "$OBS_NS" | apply_stdin
    render_alloy 1
    kc -n "$OBS_NS" rollout restart deployment/alloy >/dev/null
    kc -n "$OBS_NS" rollout status deployment/alloy --timeout=300s
  else
    warn "The monitoring_user credential could not be verified: the PostgreSQL exporter stays off (lock-wait and long-transaction alerts will not fire). This failed check wrote one authentication failure to the Postgres log; the Arena baseline check ignores it after 2 minutes."
  fi
  password=""
}

selftest() {
  "$ARENA_DEPLOY_DIR/port-forward.sh" start >/dev/null
  log "Checking that telemetry is flowing (verify the effect, not the config)"
  helper selftest --prometheus "http://localhost:$PROMETHEUS_PORT" --loki "http://localhost:$LOKI_PORT" \
    --tempo "http://localhost:$TEMPO_PORT" --grafana "http://localhost:$GRAFANA_PORT" \
    --token-file "$STATE_DIR/grafana-token"
}

uninstall() {
  log "Removing the observability stack"
  if kc -n telemetry get configmap otel-collector >/dev/null 2>&1 && ! argocd_installed; then
    local patch
    patch="$(mktemp)"
    helper collector-patch --config "$ARENA_DIR/scenarios/shop/collector.yaml" >"$patch"
    kc -n telemetry patch configmap otel-collector --type merge --patch-file "$patch" >/dev/null
    rm -f "$patch"
    kc -n telemetry rollout restart deployment/otel-collector >/dev/null
  fi
  kc delete namespace "$OBS_NS" --ignore-not-found
  kc delete clusterrole,clusterrolebinding omniops-alloy omniops-kube-state-metrics --ignore-not-found
  rm -f "$STATE_DIR/grafana-token"
}

[ -n "$PY" ] || die "Python 3.10+ is required (set ARENA_PYTHON)."
case "${1:-}" in
  install) ensure_state_dir; install ;;
  connect-shop) shift; connect_shop "$@" ;;
  notifications) notifications; kc -n "$OBS_NS" rollout restart deployment/lgtm >/dev/null; note "Grafana restarting with the new contact points" ;;
  selftest) selftest ;;
  uninstall) uninstall ;;
  *) sed -n '2,16p' "$0"; exit 2 ;;
esac
