# Google Cloud Managed Service for Prometheus (`google_prometheus`)

Manifest: `src/omniops/sources/manifests/google_prometheus.yaml` (extends `templates/prometheus.yaml`; drops
rules, alerts and targets - not served by this API). Fixtures: `src/omniops/sources/fixtures/google_prometheus/`.

Reference: [Query using the Prometheus API or UI (API compatibility)](https://cloud.google.com/stackdriver/docs/managed-prometheus/query-api-ui) ·
[REST reference: projects.location.prometheus.api.v1](https://cloud.google.com/monitoring/api/ref_v3/rest/v1/projects.location.prometheus.api.v1) ·
[PromQL for Cloud Monitoring metrics](https://cloud.google.com/monitoring/promql/promql-mapping)

## Tables

Paths are relative to `https://monitoring.googleapis.com/v1/projects/<GMP_PROJECT_ID>/location/global/prometheus`.

| SQL table | HTTP | Official doc |
|---|---|---|
| `google_prometheus_query_range` | GET `/api/v1/query_range` | https://cloud.google.com/monitoring/api/ref_v3/rest/v1/projects.location.prometheus.api.v1/query_range |
| `google_prometheus_query` | GET `/api/v1/query` | https://cloud.google.com/monitoring/api/ref_v3/rest/v1/projects.location.prometheus.api.v1/query |
| `google_prometheus_series` | GET `/api/v1/series` (GET only, `match[]` required) | https://cloud.google.com/monitoring/api/ref_v3/rest/v1/projects.location.prometheus.api.v1/series |
| `google_prometheus_labels` | GET `/api/v1/labels` | https://cloud.google.com/monitoring/api/ref_v3/rest/v1/projects.location.prometheus.api.v1/labels |
| `google_prometheus_label_values` | GET `/api/v1/label/{name}/values` (needs `__name__`) | https://cloud.google.com/monitoring/api/ref_v3/rest/v1/projects.location.prometheus.api.v1.label/values |
| `google_prometheus_metric_metadata` | GET `/api/v1/metadata` | https://cloud.google.com/monitoring/api/ref_v3/rest/v1/projects.location.prometheus.api.v1.metadata/list |
| `google_prometheus_exemplars` | GET `/api/v1/query_exemplars` | https://cloud.google.com/monitoring/api/ref_v3/rest/v1/projects.location.prometheus.api.v1/query_exemplars |

Roles: `metrics` → `google_prometheus_query_range`.

## Auth

OAuth2 bearer from a service account (`GMP_CREDENTIALS_FILE`) or Application Default Credentials (needs
`omniops[gcp]`), scope `https://www.googleapis.com/auth/monitoring.read` (`monitoring` or `cloud-platform`
also accepted). IAM: `roles/monitoring.viewer` on the scoping project.

## Base URL

Single global endpoint; `PROJECT_ID` is the scoping project of a metrics scope, so data from every project in
the scope is returned; location must be `global`.

## Rate limits

Cloud Monitoring API read quotas per project (time-series queries per minute); 429 `RESOURCE_EXHAUSTED`.

## Retention

24 months (Monarch).

## Time formats

`start`/`end`/`time`: floating-point Unix seconds or RFC 3339 (manifest: epoch seconds). `step`/`timeout`:
Prometheus duration or float seconds. Responses: Unix seconds.

## Pitfalls

- `/label/<name>/values` works only for `__name__` or with an exact `__name__` matcher in `match[]`
  (Grafana's `label_values($label)` fails for the same reason).
- `/series` is GET-only and rejects regex matching on `__name__`.
- Google API front ends reject unknown query parameters; the manifest only sends documented ones (no `limit`
  on series).
- Cloud Monitoring system metrics are queryable with PromQL after name mapping
  (`kubernetes.io/container/cpu/core_usage_time` → `kubernetes_io:container_cpu_core_usage_time`, add
  `monitored_resource`).
- Exemplars are only ingested on histogram metrics.
- No rules/alerts/targets here: managed rule evaluation results are metrics; Cloud Monitoring alerting policies
  are a different API.
- Errors: PromQL errors use the Prometheus `status=error` envelope and Google front-end errors the
  `{"error": {"code", "message", "status"}}` envelope; both have 2xx error rules (the first is proven by
  `query_error.json`, built from the documented Prometheus envelope).
- `series.json` / `metadata.json` are not copied: the engine falls back to `fixtures/prometheus/`.
