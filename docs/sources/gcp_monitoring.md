# Google Cloud Monitoring (`gcp_monitoring`)

Manifest: `src/omniops/sources/manifests/gcp_monitoring.yaml`. Fixtures: `src/omniops/sources/fixtures/gcp_monitoring/`.
PromQL over Cloud Monitoring / Managed Prometheus is a separate source (`google_prometheus`).

Reference: [Cloud Monitoring API v3 REST](https://docs.cloud.google.com/monitoring/api/ref_v3/rest) ·
[Monitoring filters](https://docs.cloud.google.com/monitoring/api/v3/filters) ·
[Sorting and filtering (policies, uptime)](https://docs.cloud.google.com/monitoring/api/v3/sorting-and-filtering) ·
[Retrieve time-series data](https://docs.cloud.google.com/monitoring/custom-metrics/reading-metrics) ·
[Structure of time series](https://docs.cloud.google.com/monitoring/api/v3/metrics-details) ·
[SLO time-series selectors](https://docs.cloud.google.com/stackdriver/docs/solutions/slo-monitoring/api/timeseries-selectors) ·
[Quotas and limits](https://docs.cloud.google.com/monitoring/quotas) ·
[Incidents (auto-close)](https://docs.cloud.google.com/monitoring/alerts/incidents-events)

(`cloud.google.com/monitoring/...` URLs now 301-redirect to `docs.cloud.google.com/monitoring/...`.)

## Tables

Paths are relative to `https://monitoring.googleapis.com`; `{p}` = `GCP_PROJECT_ID` (or the `project` filter).

| SQL table | HTTP | Official doc |
|---|---|---|
| `gcp_monitoring_time_series` | GET `/v3/projects/{p}/timeSeries` (`filter`, `interval.startTime`, `interval.endTime`, `aggregation.*`, `view=FULL`, `pageSize`, `pageToken`) | https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.timeSeries/list |
| `gcp_monitoring_time_series_headers` | GET `/v3/projects/{p}/timeSeries` (`view=HEADERS`) | https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.timeSeries/list |
| `gcp_monitoring_metric_descriptors` | GET `/v3/projects/{p}/metricDescriptors` (`filter`, `activeOnly`) | https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.metricDescriptors/list |
| `gcp_monitoring_alert_policies` | GET `/v3/projects/{p}/alertPolicies` (`filter`, `pageSize`) | https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.alertPolicies/list |
| `gcp_monitoring_alerts` | GET `/v3/projects/{p}/alerts` (`filter`, `pageSize` <= 1000) | https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.alerts/list |
| `gcp_monitoring_uptime_checks` | GET `/v3/projects/{p}/uptimeCheckConfigs` (`filter`) | https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.uptimeCheckConfigs/list |
| `gcp_monitoring_slos` | GET `/v3/workspaces/{p}/services/-/serviceLevelObjectives` (all services) or `/v3/projects/{p}/services/{service_id}/serviceLevelObjectives`; `view=EXPLICIT` | https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/services.serviceLevelObjectives/list |
| `gcp_monitoring_services` | GET `/v3/projects/{p}/services` (`filter` e.g. `identifier_case = "CLOUD_RUN"`) | https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/services/list |
| `gcp_monitoring_snoozes` | GET `/v3/projects/{p}/snoozes` (`filter` on `interval.start_time` / `interval.end_time`, pushed as an overlap with the window) | https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.snoozes/list |

Roles: `metrics` and `log_counts` (metric `logging.googleapis.com/log_entry_count`, labels `log`, `severity`) →
`gcp_monitoring_time_series`; `alert_rules` → `gcp_monitoring_alert_policies` (id = numeric policy id, the
`rule_id` parsed from the Slack notification's policy link; name = display name); `active_alerts`
(`state = 'OPEN'`) and `alert_history` → `gcp_monitoring_alerts`; `slo` → `gcp_monitoring_slos`; `services` →
`gcp_monitoring_services` (auto-detected Cloud Run / GKE / App Engine / Istio services and custom SLO services).
`gcp_monitoring_snoozes` has no role: it answers "why did the expected alert not fire / notify?".

## Auth

`auth.type: gcp_service_account`: OAuth2 bearer from a service-account key (`GCP_CREDENTIALS_FILE`) or
Application Default Credentials (needs `omniops[gcp]`). Scope `https://www.googleapis.com/auth/monitoring.read`
(`monitoring` and `cloud-platform` are also accepted by every method used). IAM: `roles/monitoring.viewer`
on the project (on the scoping project to see the whole metrics scope).

## Base URL / regions

Single global endpoint `https://monitoring.googleapis.com` (override with `GCP_MONITORING_BASE_URL`). The
`name`/`parent` may be `projects/<id or number>`; timeSeries.list also accepts `organizations/` and `folders/`.
Querying the scoping project of a metrics scope returns data of every monitored project.

## Time formats

- `interval.startTime` / `interval.endTime`: RFC 3339 (`2024-03-01T12:34:56.992Z`). Data returned is the
  half-open interval `(start, end]`; start = end (or no start) reads one instant.
- Durations (`aggregation.alignmentPeriod`, condition `duration`, uptime `period`/`timeout`, SLO `rollingPeriod`)
  are strings `"<seconds>s"` (`"600s"`); minimum alignment period 60 s.
- Point values: `doubleValue` (number, `"NaN"`/`"Infinity"` as strings), `int64Value` (**JSON string**),
  `boolValue`, `stringValue`, `distributionValue` (`count` and `bucketCounts` are int64 strings).
- Points are returned **newest first**; there is no override (`orderBy` is "Unsupported: must be left blank").

## Pagination

`pageSize` + `pageToken` → `nextPageToken` (empty = done) everywhere (snoozes: 1-1000). timeSeries.list: `pageSize` counts
**points** in `view=FULL` and series in `view=HEADERS`; empty or > 100,000 means 100,000. metricDescriptors:
max 10,000. alerts: default 50, max 1000 (tokens valid 72 h). Policies, uptime checks, SLOs: no documented max.

## Rate limits

Per-project Cloud Monitoring API read quotas (time-series queries per minute) shown in the console Quotas
page; exceeding returns HTTP 429 `RESOURCE_EXHAUSTED`. Responses over 200 MB fail with `RESOURCE_EXHAUSTED`.
Alerting limits: 2,000 policies per metrics scope (default), 6 conditions per policy, 1,000 open alerts per
policy, 100 uptime checks per metrics scope. Engine: 600 req/min, concurrency 4.

## Retention

- 24 months: `custom.googleapis.com`, `external.googleapis.com`, `agent.googleapis.com`,
  `workload.googleapis.com`, `prometheus.googleapis.com` and the Compute Engine, GKE, Cloud Storage,
  BigQuery, Cloud SQL and internal/HTTPS/L7 load-balancer system metrics (raw for 6 weeks, then 10-minute
  points; Prometheus data: 1 week raw, then 1-minute for 5 weeks, then 10-minute).
- 6 weeks: all other Google Cloud metrics, Istio, AWS and **log-based metrics**.
- Alerts: closed alerts kept 13 months; metric alerts with no new data auto-close after 7 days.

## Error handling

- Non-2xx: `{"error": {"code", "message", "status"}}` (429 RESOURCE_EXHAUSTED, 403 PERMISSION_DENIED, 400
  INVALID_ARGUMENT for bad filters or an aligner that does not fit the metric kind).
- 2xx with `executionErrors[]` (timeSeries.list): "query errors that may have made the returned time series
  data incomplete" → declared as an error rule (loud failure, fixture `time_series_execution_error.json`).
- ListTimeSeriesResponse also carries `unreachable[]` (cloud regions that could not be reached; data may be
  incomplete) - documented as a pitfall, not a hard failure.
- Source-level rule on `$.error.message` (proxies answering 200 with a Google error body).

## Filter syntax (timeSeries.list)

`metric.type = "..."` (exactly one metric type per request) `AND resource.type = "..." AND
metric.labels.KEY = "..." AND resource.labels.KEY = starts_with("...")`. Operators `= != : > < >= <=`;
functions `starts_with`, `ends_with`, `has_substring` (one-argument form only), `one_of` (<= 100 strings),
`monitoring.regex.full_match` (RE2). Double-quoted strings, uppercase `AND`/`OR`, **OR binds tighter than
AND**, `NOT` only in group definitions. `group.id` requires an aligner. SLO data: `select_slo_health(...)`,
`select_slo_compliance(...)`, `select_slo_budget(...)`, `select_slo_budget_fraction(...)`,
`select_slo_burn_rate("<slo name>", "3600s")`, `select_slo_counts(...)`.

Alert policies filter/orderBy fields: `name`, `display_name`, `documentation.content`, `documentation.mime_type`,
`user_labels`, `conditions.size`, `combiner`, `enabled`, `notification_channels` (snake_case; camelCase also
accepted). Uptime checks filter fields: `display_name`, `user_labels`, `selected_regions`, `http_check.path`,
`http_check.headers`, `http_check.port`, `tcp_check.port`, `monitored_resource.type`, `monitored_resource.labels`.

## Pitfalls

- Unknown metric type / label = 0 series, not an error: use `gcp_monitoring_metric_descriptors` and
  `gcp_monitoring_time_series_headers` to discover the real names and values first.
- Wrong aligner for the metric kind is a 400: DELTA/CUMULATIVE → `ALIGN_RATE` (per second) or
  `ALIGN_DELTA`/`ALIGN_SUM`; GAUGE → `ALIGN_MEAN`/`ALIGN_MAX`; DISTRIBUTION → `ALIGN_PERCENTILE_99/95/50`
  or `ALIGN_DELTA` + `REDUCE_PERCENTILE_99`. A reducer needs an aligner; `resource.type` is always kept.
- Raw (unaligned) queries return every sampled point; fleets × hours need aligner + reducer + group_by.
- Ingest delay: many system metrics appear minutes after the fact - an empty tail is not recovery.
- Alert policies: one row per condition; PromQL/MQL/SQL thresholds live inside `query`.
- `projects.alerts.list` filter grammar is documented only as "match on any fields of the alert"; the
  manifest therefore filters `state`/`title`/`ts` in SQL (server default order is open time desc).
- Uptime results are metrics (`monitoring.googleapis.com/uptime_check/check_passed`, label `check_id`), not
  part of the config listing.
- `services/-` (all services) is documented on the `workspaces/{host project}` parent form; the manifest uses
  it when no `service_id` is given.
- Snoozes: the filter grammar only supports `interval.start_time` / `interval.end_time`; the manifest sends the
  overlap `interval.start_time < END AND interval.end_time > START` and does not re-apply the ts predicate, so a
  snooze that started before the incident window is still returned.
- Services: exactly one identifier oneof (`cloudRun`, `gkeService`, `gkeWorkload`, `gkeNamespace`, `appEngine`,
  `meshIstio`, `clusterIstio`, `istioCanonicalService`, `custom`) is set per service; `service`, `namespace`,
  `cluster` and `location` are derived from whichever is present.

## Fixture provenance

| Fixture | Source |
|---|---|
| `time_series_aligned.json` | Docs "Retrieve time-series data", ALIGN_MEAN 600 s example output (field-masked: metric + points). |
| `time_series_headers.json` | Same page, list-time-series (`view=HEADERS`) example output. |
| `time_series_reduced.json` | Same page, ALIGN_MEAN + REDUCE_MEAN example output (one unlabeled series). |
| `time_series_distribution.json` | Schema-built (TypedValue.distributionValue: int64-string `count`/`bucketCounts`, `mean`) + response-level `unit`. |
| `time_series_log_entry_count.json` | Docs "Structure of time series" example series (int64Value `"20"`), in the ListTimeSeriesResponse envelope. |
| `time_series_execution_error.json` | Schema-built (`executionErrors[]` google.rpc.Status) - proves the 2xx error rule. |
| `metric_descriptors.json`, `metric_descriptor_full.json` | Docs "List metric and resource types" list and get examples. |
| `alert_policies.json` | Docs "Sample policies in JSON" (Very high CPU usage / Data ingestion functioning) + output-only fields per schema. |
| `alerts.json`, `uptime_checks.json`, `slos.json`, `services.json`, `services_gke_workload.json`, `snoozes.json` | Schema-built (no documented example response). |
| `google_error.json` | Google error envelope - proves the source-level error rule. |
