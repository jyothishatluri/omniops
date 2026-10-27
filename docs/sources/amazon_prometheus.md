# Amazon Managed Service for Prometheus (`amazon_prometheus`)

Manifest: `src/omniops/sources/manifests/amazon_prometheus.yaml` (extends `templates/prometheus.yaml`; drops
targets - AMP has no scrape-target API). Fixtures: `src/omniops/sources/fixtures/amazon_prometheus/` (all
from the AMP API reference sample responses except `query_range.json`).

Reference: [Prometheus-compatible APIs](https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-Prometheus-Compatible-Apis.html) ·
[Service quotas](https://docs.aws.amazon.com/prometheus/latest/userguide/AMP_quotas.html) ·
[Workspace configuration (retention)](https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-workspace-configuration.html)

## Tables

Paths are relative to `https://aps-workspaces.<AMP_REGION>.amazonaws.com/workspaces/<AMP_WORKSPACE_ID>`.

| SQL table | HTTP | Official doc |
|---|---|---|
| `amazon_prometheus_query_range` | GET `/api/v1/query_range` (QueryMetrics; + `max_samples_processed_error_threshold` / `_warning_threshold`) | https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-QueryMetrics.html |
| `amazon_prometheus_query` | GET `/api/v1/query` (QueryMetrics) | https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-QueryMetrics.html |
| `amazon_prometheus_series` | GET `/api/v1/series` (GetSeries) | https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-GetSeries.html |
| `amazon_prometheus_labels` | GET `/api/v1/labels` (GetLabels) | https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-GetLabels.html |
| `amazon_prometheus_label_values` | GET `/api/v1/label/{name}/values` (GetLabels) | https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-GetLabels.html |
| `amazon_prometheus_metric_metadata` | GET `/api/v1/metadata` (GetMetricMetadata) | https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-GetMetricMetadata.html |
| `amazon_prometheus_alerts` | GET `/api/v1/alerts` (ListAlerts) | https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-ListAlerts.html |
| `amazon_prometheus_rules` | GET `/api/v1/rules` (ListRules; `group_limit` + `group_next_token`) | https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-ListRules.html |
| `amazon_prometheus_alertmanager_alerts` | GET `/alertmanager/api/v2/alerts` (ListAlertManagerAlerts) | https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-ListAlertManagerAlerts.html |
| `amazon_prometheus_alert_groups` | GET `/alertmanager/api/v2/alerts/groups` (ListAlertManagerAlertGroups) | https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-ListAlertManagerAlertGroups.html |
| `amazon_prometheus_silences` | GET `/alertmanager/api/v2/silences` (ListAlertManagerSilences) | https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-ListAlertManagerSilences.html |

Roles: `metrics` → `amazon_prometheus_query_range`; `active_alerts` → `amazon_prometheus_alerts` and
`amazon_prometheus_alertmanager_alerts`; `alert_rules` → `amazon_prometheus_rules`.

Not modelled: write APIs (RemoteWrite, CreateAlertManagerAlerts, PutAlertManagerSilences, DeleteSilence),
GetAlertManagerStatus/Silence by id, ListAlertManagerReceivers, and the control-plane API on
`aps.<region>.amazonaws.com` (workspaces, rule groups namespaces, scrapers).

## Auth

AWS SigV4, service `aps`, region `AMP_REGION`; credentials from the standard AWS chain (env, profile, SSO,
instance/IRSA role; needs `omniops[aws]`). IAM actions: `aps:QueryMetrics`, `aps:GetSeries`, `aps:GetLabels`,
`aps:GetMetricMetadata`, `aps:ListRules`, `aps:ListAlerts`, `aps:ListAlertManagerAlerts`,
`aps:ListAlertManagerAlertGroups`, `aps:ListAlertManagerSilences` (e.g. `AmazonPrometheusQueryAccess`).

## Base URL / regions

`https://aps-workspaces.<region>.amazonaws.com/workspaces/<workspace-id>` in any AMP region.

## Rate limits (per workspace)

| API | Quota |
|---|---|
| QueryMetrics | 300 TPS (adjustable) |
| GetSeries + GetLabels + GetMetricMetadata | 10 TPS (not adjustable) |
| other Prometheus-compatible APIs | 100 TPS |
| per query | 50M samples, 12M series, 5 GB per 24h of range; 95-day max time range; 1 MB request |
| Alertmanager | 1,000 alerts per workspace |

## Retention

150 days by default; configurable up to 1,095 days per workspace.

## Time formats

`start`/`end`/`time`: RFC 3339 or Unix timestamp (manifest: epoch seconds); `step`: duration or float seconds.
Responses: Unix seconds for samples; RFC 3339 for `activeAt`, `lastEvaluation`, `startsAt`/`endsAt`.

## Pitfalls

- 403 is IAM/SigV4 (wrong region or missing action), never the query.
- Billing is by query samples processed (QSP); `max_samples_error` (→ `max_samples_processed_error_threshold`)
  rejects expensive queries for free. QSP warnings show up in the `warnings` column.
- GetSeries/GetLabels/GetMetricMetadata share a 10 TPS budget: avoid fan-outs over many labels.
- No targets API; scrape health lives in the collector (AMP managed scraper / ADOT / Prometheus agent).
- Throttling: the manifest retries 429s and 4xx bodies containing `ThrottlingException` / `Rate exceeded`
  (`rate_limit.retry_body_patterns`) and caps the source at 600 requests/minute (enforced by the engine).
- ListRules pagination: `group_next_token` must be sent together with `group_limit` (the manifest always sends
  `group_limit=500`); a single response holds at most 100,000 rules and never splits a group.
- Error envelope: PromQL / quota errors use the Prometheus `status=error` envelope (normally with 4xx); the 2xx
  rule is proven by `query_error.json` (built from the documented envelope).
- Alertmanager receivers are SNS topics: "notified" means published to SNS.
