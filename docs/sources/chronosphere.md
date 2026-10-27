# Chronosphere / Palo Alto Networks Cortex XCOR (`chronosphere`)

Manifest: `src/omniops/sources/manifests/chronosphere.yaml` (extends `templates/prometheus.yaml`; drops
metadata, rules, alerts and targets because Chronosphere's Prometheus API supports only instant query, range
query, series and labels). Fixtures: `src/omniops/sources/fixtures/chronosphere/`.

Chronosphere's docs moved to `docs-xcor.paloaltonetworks.com` (Chronosphere is now Cortex XCOR); the
`docs.chronosphere.io` links redirect.

Reference: [Prometheus API overview](https://docs.chronosphere.io/tooling/prometheus-api) ·
[Get started with the API (auth, pagination)](https://docs.chronosphere.io/tooling/api-info) ·
[Config API (swagger)](https://docs-xcor.paloaltonetworks.com/openapi/api_v1_config_swagger.json) ·
[Data API (swagger)](https://docs-xcor.paloaltonetworks.com/openapi/api_v1_data_swagger.json) ·
[Alert metrics (ALERTS)](https://docs-xcor.paloaltonetworks.com/investigate/querying/metrics/alert-metrics)

## Tables

| SQL table | HTTP | Official doc |
|---|---|---|
| `chronosphere_query_range` | GET `/data/metrics/api/v1/query_range` | https://docs.chronosphere.io/tooling/prometheus-api |
| `chronosphere_query` | GET `/data/metrics/api/v1/query` | https://docs.chronosphere.io/tooling/prometheus-api |
| `chronosphere_series` | GET `/data/metrics/api/v1/series` | https://docs.chronosphere.io/tooling/prometheus-api |
| `chronosphere_label_values` | GET `/data/metrics/api/v1/label/{name}/values` | https://docs.chronosphere.io/tooling/prometheus-api |
| `chronosphere_labels` | GET `/data/metrics/api/v1/labels` | https://docs.chronosphere.io/tooling/prometheus-api |
| `chronosphere_alerts` | POST `/api/v1/data/alerts` (ListAlerts; `active_time_range` by default or `trigger_time_range`; cursor `page.token` in the body) | https://docs-xcor.paloaltonetworks.com/tooling/api-info/data_definition/operations/ListAlerts |
| `chronosphere_metric_metadata` | GET `/api/v1/data/metrics:metadata/{metric_name}` (ReadMetricMetadata) | https://docs-xcor.paloaltonetworks.com/tooling/api-info/data_definition/operations/ReadMetricMetadata |
| `chronosphere_change_events` | GET `/api/v1/data/events` (ListEvents; cursor `page.token`) | https://docs-xcor.paloaltonetworks.com/tooling/api-info/data_definition/operations/ListEvents |
| `chronosphere_monitors` | GET `/api/v1/config/monitors` (ListMonitors; cursor `page.token`) | https://docs-xcor.paloaltonetworks.com/tooling/api-info/definition/operations/ListMonitors |

Roles: `metrics` → `chronosphere_query_range`; `active_alerts` → `chronosphere_alerts` (state = FIRING);
`alert_history` → `chronosphere_alerts`; `alert_rules` → `chronosphere_monitors`; `change_events` →
`chronosphere_change_events`.

Not modelled (documented but out of this pass): Data API `logs:range` (QueryLogsRange: grid rows of typed
`{string_value|float_value|bool_value}` cells whose columns depend on the query, which the declarative engine can
only flatten generically, and no example body) and `traces` (ListTraces),
state APIs (metric usage), remote-read JSON debug endpoint, the many Config API object types (dashboards,
SLOs, notification policies, muting rules). The `M3-Restrict-By-Tags-JSON` header extension is not exposed.

## Auth

API token from a service account (or a temporary personal access token):
- Prometheus API: `Authorization: Bearer <token>` (manifest auth).
- Config/Data APIs: `API-Token: <token>` header (added per table). Many Config API list calls need an
  unrestricted service account.

## Base URL

`https://<CHRONOSPHERE_DOMAIN>.chronosphere.io`; Prometheus routes under `/data/metrics`. `CHRONOSPHERE_DOMAIN`
may be the subdomain (`acme`) or a full host.

## Rate limits

Not published as fixed numbers; per-tenant query limits (series / samples / concurrency) return 4xx/429. List
APIs are paginated server-side (`page.max_size`, `page.next_token`); the client must keep filters constant
while paging.

## Retention

Per tenant contract (raw and downsampled resolutions). Change events can only be created within ±24h of now.

## Time formats

Prometheus API: Unix seconds or RFC 3339 (manifest: epoch seconds). Data API: RFC 3339 (`trigger_time_range`,
`happened_after`/`happened_before`, `triggered_at`, `happened_at`). ListEvents defaults to the last hour when
both bounds are omitted (the manifest always sends them).

## Pitfalls

- No Prometheus metadata / rules / alerts / targets endpoints: use the Data and Config API tables (or the
  `ALERTS{alertstate="firing"}` metric through PromQL).
- ListAlerts has two mutually exclusive windows: `trigger_time_range` (alerts that started in the window) and
  `active_time_range` (alerts firing at any point in it). The manifest defaults to `active_time_range` so a
  long-running alert that started before the incident window is not missed (`window_mode = 'triggered'` switches);
  time predicates are therefore not re-applied in SQL (`time.reapply: false`) and `ts` (= `triggered_at`) can be
  earlier than the window. Without either range the API uses the last hour; too-wide windows are rejected, not
  truncated.
- ListAlerts enums are uppercase (`FIRING`/`RESOLVED`, `WARN`/`CRITICAL`, `MUTED`/`NOT_MUTED`, `MONITOR`/`SLO`).
- `notifier_slugs` and `external_connections` filters are OR'ed with each other; failed sends still match.
- ReadMetricMetadata answers 404 for unknown metrics (mapped to 0 rows); types are Chronosphere's
  (`CUMULATIVE_COUNTER`, `GAUGE`, `DELTA_COUNTER`, `MEASUREMENT`, exponential histograms) plus the ingest `source`.
- ListAlerts `monitor_slugs`, `monitor_name_contains`, `slo_*`, `alert_title_contains` are OR'ed with each other.
- Change-event categories are case-sensitive.
- Aggregation rules can drop labels from stored metrics.
- Fixtures for the Data/Config tables are built from the swagger schemas (the docs give no example bodies).
