#!/usr/bin/env bash
# Expose the in-cluster Grafana, Prometheus, Loki, Tempo and Argo CD on localhost for OmniOps, plus
# OmniOps' Kubernetes view.
#
#   port-forward.sh start | stop | status
#
# Ports: GRAFANA_PORT=3000 PROMETHEUS_PORT=9090 LOKI_PORT=3100 TEMPO_PORT=3200 ARGOCD_PORT=8080
# KUBE_PROXY_PORT=8001. Each forward runs in a restart loop (a pod restart ends a kubectl
# port-forward), logs to .state/port-forward.log and is stopped by 'stop'.
#
# Kubernetes: OmniOps never gets a cluster credential. 'start' applies kubernetes/omniops-reader.yaml
# (a read-only ServiceAccount), keeps its token in .state/kube-upstream.json (mode 600) and runs
# helper.py kube-proxy on localhost:KUBE_PROXY_PORT: GET only, the benchmark-control namespace (the
# Arena's suite state, which names the active scenario) hidden from every path and list.
source "$(dirname "$0")/lib.sh"

PIDS="$STATE_DIR/port-forward.pids"
LOG="$STATE_DIR/port-forward.log"

running() {
  local pid
  [ -f "$PIDS" ] || return 1
  while read -r pid; do
    kill -0 "$pid" 2>/dev/null && return 0
  done <"$PIDS"
  return 1
}

supervise() {   # command...: run it in a restart loop in the background, recorded for 'stop'
  (
    child=""
    trap 'kill ${child:-} 2>/dev/null; exit 0' TERM INT
    while true; do
      "$@" &
      child=$!
      wait "$child" || true
      sleep 2
    done
  ) </dev/null >>"$LOG" 2>&1 &
  echo $! >>"$PIDS"
}

forward() {   # namespace target port-mappings...
  local ns="$1" target="$2"
  shift 2
  supervise kubectl --context "$CONTEXT" -n "$ns" port-forward "$target" "$@"
}

# OmniOps' Kubernetes view: the read-only identity (its token straight from the Secret into the
# helper's stdin), then the namespace-filtering proxy in front of the API server.
kube_view() {
  local upstream="$STATE_DIR/kube-upstream.json" server i
  rm -f "$upstream"                    # never a stale token from a deleted cluster
  kc apply -f "$ARENA_DEPLOY_DIR/kubernetes/omniops-reader.yaml" >/dev/null
  server="$(kc config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    kc -n "$OBS_NS" get secret omniops-arena-token -o json 2>/dev/null |
      helper kube-upstream --server "$server" --out "$upstream" 2>/dev/null && break
    sleep 2
  done
  [ -s "$upstream" ] || die "the omniops-arena ServiceAccount token was not issued (kubectl -n $OBS_NS get secret omniops-arena-token)."
  supervise "$PY" "$HELPER" kube-proxy --upstream "$upstream" --port "$KUBE_PROXY_PORT" \
    --hide-namespaces "$KUBE_HIDDEN_NAMESPACES"
}

start() {
  ensure_state_dir
  if running; then
    note "port-forwards already running (port-forward.sh stop to end them)"
    return 0
  fi
  : >"$PIDS"
  kc -n "$OBS_NS" get service lgtm >/dev/null 2>&1 || die "The observability stack is not installed (observability.sh install)."
  forward "$OBS_NS" svc/lgtm "$GRAFANA_PORT:3000" "$PROMETHEUS_PORT:9090" "$LOKI_PORT:3100" "$TEMPO_PORT:3200"
  if argocd_installed; then
    forward "$ARGOCD_NS" svc/argo-cd-argocd-server "$ARGOCD_PORT:80"
  fi
  kube_view
  helper wait-http "http://localhost:$KUBE_PROXY_PORT/version" --timeout 30 ||
    warn "OmniOps' Kubernetes view is not answering on localhost:$KUBE_PROXY_PORT yet (see $LOG)"
  helper wait-http "http://localhost:$GRAFANA_PORT/api/health" --timeout 60 ||
    warn "Grafana is not answering on localhost:$GRAFANA_PORT yet (see $LOG)"
  status
}

stop() {
  local pid
  if [ -f "$PIDS" ]; then
    while read -r pid; do
      kill "$pid" 2>/dev/null || true
    done <"$PIDS"
    rm -f "$PIDS"
  fi
  note "port-forwards stopped"
}

status() {
  if running; then
    note "Grafana     http://localhost:$GRAFANA_PORT"
    note "Prometheus  http://localhost:$PROMETHEUS_PORT"
    note "Loki        http://localhost:$LOKI_PORT"
    note "Tempo       http://localhost:$TEMPO_PORT"
    if argocd_installed; then note "Argo CD     http://localhost:$ARGOCD_PORT"; fi
    note "Kubernetes  http://localhost:$KUBE_PROXY_PORT (OmniOps' read-only view; hidden: $KUBE_HIDDEN_NAMESPACES)"
  else
    note "no port-forwards running"
  fi
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) sed -n '2,8p' "$0"; exit 2 ;;
esac
