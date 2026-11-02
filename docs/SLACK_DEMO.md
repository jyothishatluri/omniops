# Live Slack demo: real alerts, real investigations

Grafana's own alert rules fire on the Arena shop (the OpenTelemetry demo on a local kind cluster) and
post to `#omniops-alerts` through a separate **Grafana Alerts** Slack app. OmniOps watches that channel,
so each alert is investigated and the findings are posted in the alert's thread. Nothing is simulated:
the alert text is Grafana's, and the investigation runs SQL over the cluster's Grafana, Prometheus,
Loki, Tempo and Kubernetes APIs.

## One-time setup

| Slack app | Manifest | What goes in `omniops/.env` |
|---|---|---|
| OmniOps (the investigator, Socket Mode) | [slack_app_manifest.yaml](../slack_app_manifest.yaml) | `SLACK_BOT_TOKEN` (Install App page), `SLACK_APP_TOKEN` (Basic Information > App-Level Tokens) |
| Grafana Alerts (the alert source) | [deploy/arena/slack_grafana_app_manifest.yaml](../deploy/arena/slack_grafana_app_manifest.yaml) | `SLACK_WEBHOOK_URL` (Features > Incoming Webhooks > Copy) |

Invite OmniOps to the alert channel (`/invite @OmniOps`). The Grafana Alerts webhook is bound to the
channel picked at install time. They must be two apps: OmniOps ignores its own posts.

## Bring up the cluster with Slack notifications

From the `omniops` directory, with your channel id (`C0CCU715ACD` is `#omniops-alerts` in the test
workspace):

```sh
set -a; source .env; set +a
OMNIOPS_ARENA_CHANNEL=C0CCU715ACD deploy/arena/up.sh
```

`up.sh` is idempotent: on a cluster that is already partly up it only does the missing steps. It
configures Grafana's Slack contact point from `SLACK_WEBHOOK_URL` and writes
`deploy/arena/.state/omniops.env`, which sets `OMNIOPS_ALERT_CHANNELS` to the channel. To change only
the Slack contact point later: `deploy/arena/observability.sh notifications`.

## Start OmniOps

```sh
set -a; source deploy/arena/.state/omniops.env; set +a
OMNIOPS_MEMORY_PATH=.omniops/demo/memory.db OMNIOPS_CASE_DIR=.omniops/demo/cases \
  uv run --no-sync omniops serve
```

The demo keeps its own incident memory, so demo investigations never show up as "prior incidents" in
Arena benchmark runs.

## Fire alerts

```sh
deploy/arena/demo-alerts.sh list
deploy/arena/demo-alerts.sh fire bad-rollout        # currency: image tag that does not exist
deploy/arena/demo-alerts.sh fire memory-squeeze     # recommendation: memory limit cut to 48Mi
deploy/arena/demo-alerts.sh fire dependency-down    # product-catalog scaled to 0
deploy/arena/demo-alerts.sh status
deploy/arena/demo-alerts.sh revert                  # undo everything that is active
```

Each fault is an ordinary operator mistake made with kubectl, not one of the Arena's scored scenarios.
Grafana needs 1-4 minutes (pending period plus evaluation and grouping) before an alert reaches Slack;
OmniOps then reacts with a status message in the thread and posts its conclusion when the
investigation finishes. Grafana posts a "resolved" message after `revert`.

| Fault | Alert rules it is meant to trip |
|---|---|
| `bad-rollout` | Containers stuck waiting to start |
| `memory-squeeze` | Container OOMKilled, Container restarts increasing |
| `dependency-down` | Service error ratio above 5% (callers of product-catalog) |

`dependency-down` needs the shop's span metrics (`observability.sh connect-shop`, part of `up.sh`). Without them the error-ratio rule has no data and no alert fires.

Revert before running the Arena benchmark: its baseline check treats a leftover change as residual
fault state. The script refuses to fire a fault that is already active and records what it changed
under `deploy/arena/.state/demo-alerts/`.
