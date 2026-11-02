#!/usr/bin/env bash
# Trigger real alerts in the Arena shop for a live Slack demo of OmniOps, and put everything back.
#
#   deploy/arena/demo-alerts.sh list                 the faults this script can make
#   deploy/arena/demo-alerts.sh fire NAME            make one (bad-rollout | memory-squeeze | dependency-down)
#   deploy/arena/demo-alerts.sh status               active faults and their pods
#   deploy/arena/demo-alerts.sh revert [NAME|all]    undo one, or every active one (default: all)
#
# Each fault is an ordinary operator mistake made with kubectl on the shop (direct deployment: in GitOps
# mode Argo CD would heal it). Grafana's own alert rules fire and post to the alert channel through the
# "Grafana Alerts" Slack app; OmniOps (`omniops serve`, watching that channel) investigates in the thread.
# These are not the Arena's scored scenarios. Revert before running the benchmark: its baseline check
# treats a leftover change as residual fault state.
source "$(dirname "$0")/lib.sh"

NS="shop"
DEMO_DIR="$STATE_DIR/demo-alerts"
NAMES="bad-rollout memory-squeeze dependency-down"

describe() {
  case "$1" in
    bad-rollout) echo "currency: roll out an image tag that does not exist (new pod stuck in ImagePullBackOff)" ;;
    memory-squeeze) echo "recommendation: cut the memory limit to 48Mi (new pod OOMKilled at start)" ;;
    dependency-down) echo "product-catalog: scale to 0 replicas (frontend requests fail)" ;;
    *) return 1 ;;
  esac
}

target() {
  case "$1" in
    bad-rollout) echo currency ;;
    memory-squeeze) echo recommendation ;;
    dependency-down) echo product-catalog ;;
  esac
}

revision() { kc -n "$NS" get deploy "$1" -o jsonpath='{.metadata.annotations.deployment\.kubernetes\.io/revision}'; }
state_get() { sed -n "s/^$2=//p" "$DEMO_DIR/$1"; }

fire() {
  local name="$1" deploy image last
  describe "$name" >/dev/null || die "unknown fault '$name' (one of: $NAMES)"
  deploy="$(target "$name")"
  [ -f "$DEMO_DIR/$name" ] && die "'$name' is already active: deploy/arena/demo-alerts.sh revert $name"
  kc -n "$NS" get deploy "$deploy" >/dev/null 2>&1 \
    || die "deployment $NS/$deploy not found: is the shop deployed (deploy/arena/up.sh)?"
  mkdir -p "$DEMO_DIR"
  log "Firing '$name': $(describe "$name")"
  case "$name" in
    bad-rollout)
      image="$(kc -n "$NS" get deploy "$deploy" -o jsonpath='{.spec.template.spec.containers[0].image}')"
      image="${image%%@*}"                       # drop a digest pin so the tag is what gets pulled
      last="${image##*/}"
      if [[ "$last" == *:* ]]; then image="$image-rc1"; else image="$image:rc1"; fi
      printf 'revision=%s\n' "$(revision "$deploy")" > "$DEMO_DIR/$name"
      kc -n "$NS" set image "deploy/$deploy" "$deploy=$image" >/dev/null
      note "image -> $image"
      ;;
    memory-squeeze)
      printf 'revision=%s\n' "$(revision "$deploy")" > "$DEMO_DIR/$name"
      kc -n "$NS" set resources "deploy/$deploy" -c "$deploy" --requests=memory=48Mi --limits=memory=48Mi >/dev/null
      note "memory request/limit -> 48Mi"
      ;;
    dependency-down)
      printf 'replicas=%s\n' "$(kc -n "$NS" get deploy "$deploy" -o jsonpath='{.spec.replicas}')" > "$DEMO_DIR/$name"
      kc -n "$NS" scale "deploy/$deploy" --replicas=0 >/dev/null
      note "replicas -> 0"
      ;;
  esac
  note "Grafana's rules need 1-4 minutes (pending period + evaluation) before the alert reaches Slack."
}

revert_one() {
  local name="$1" deploy
  describe "$name" >/dev/null || die "unknown fault '$name' (one of: $NAMES)"
  [ -f "$DEMO_DIR/$name" ] || { note "'$name' is not active"; return 0; }
  deploy="$(target "$name")"
  log "Reverting '$name' ($NS/$deploy)"
  case "$name" in
    bad-rollout|memory-squeeze)
      kc -n "$NS" rollout undo "deploy/$deploy" --to-revision="$(state_get "$name" revision)" >/dev/null ;;
    dependency-down)
      kc -n "$NS" scale "deploy/$deploy" --replicas="$(state_get "$name" replicas)" >/dev/null ;;
  esac
  kc -n "$NS" rollout status "deploy/$deploy" --timeout=300s >/dev/null || warn "$deploy did not become ready within 5 minutes"
  rm -f "$DEMO_DIR/$name"
  note "done"
}

status() {
  local name deploy active=0
  for name in $NAMES; do
    [ -f "$DEMO_DIR/$name" ] || continue
    active=1
    deploy="$(target "$name")"
    log "$name: $(describe "$name")"
    kc -n "$NS" get pods --no-headers 2>/dev/null | awk -v d="$deploy-" 'index($1, d) == 1 { print "    " $0 }'
  done
  [ "$active" = 1 ] || log "No demo faults are active"
}

cmd="${1:-}"
case "$cmd" in
  list) for n in $NAMES; do printf '%-16s %s\n' "$n" "$(describe "$n")"; done ;;
  fire) [ -n "${2:-}" ] || die "usage: demo-alerts.sh fire NAME (one of: $NAMES)"; fire "$2" ;;
  status) status ;;
  revert)
    if [ -z "${2:-}" ] || [ "$2" = all ]; then
      for n in $NAMES; do revert_one "$n"; done
    else
      revert_one "$2"
    fi
    ;;
  *) sed -n '2,7p' "$0"; exit 2 ;;
esac
