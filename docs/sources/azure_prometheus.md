# Azure Monitor managed service for Prometheus (`azure_prometheus`)

Manifest: `src/omniops/sources/manifests/azure_prometheus.yaml` (extends `templates/prometheus.yaml`; drops
label values (except `__name__`), metadata, rules, alerts and targets - unsupported). Fixtures:
`src/omniops/sources/fixtures/azure_prometheus/` (Azure's page defers response formats to the Prometheus docs).

Reference: [Query Prometheus metrics using the API and PromQL](https://learn.microsoft.com/en-us/azure/azure-monitor/metrics/prometheus-api-promql) ·
[Service limits - Prometheus metrics](https://learn.microsoft.com/en-us/azure/azure-monitor/fundamentals/service-limits#prometheus-metrics)

## Tables

| SQL table | HTTP | Official doc |
|---|---|---|
| `azure_prometheus_query_range` | GET `/api/v1/query_range` | https://learn.microsoft.com/en-us/azure/azure-monitor/metrics/prometheus-api-promql#range-queries |
| `azure_prometheus_query` | GET `/api/v1/query` | https://learn.microsoft.com/en-us/azure/azure-monitor/metrics/prometheus-api-promql#instant-queries |
| `azure_prometheus_series` | GET `/api/v1/series` (≤ 12h) | https://learn.microsoft.com/en-us/azure/azure-monitor/metrics/prometheus-api-promql#series |
| `azure_prometheus_labels` | GET `/api/v1/labels` | https://learn.microsoft.com/en-us/azure/azure-monitor/metrics/prometheus-api-promql#labels |
| `azure_prometheus_metric_names` | GET `/api/v1/label/__name__/values` | https://learn.microsoft.com/en-us/azure/azure-monitor/metrics/prometheus-api-promql#label-values |

Roles: `metrics` → `azure_prometheus_query_range`.

## Auth

Microsoft Entra ID client credentials (`azure_ad`): token from
`https://login.microsoftonline.com/<AZURE_TENANT_ID>/oauth2/v2.0/token` with scope
`https://prometheus.monitor.azure.com/.default` (the docs show the v1 endpoint with
`resource=https://prometheus.monitor.azure.com`; same audience). The app needs the **Monitoring Data Reader**
role on the Azure Monitor workspace. Managed identity is not supported by the engine's `azure_ad` auth.

## Base URL

The workspace query endpoint from its Overview page:
`https://<workspace>-<id>.<region>.prometheus.monitor.azure.com`.

## Limits

32-day max range (including range selectors), 500,000 series per metric, 50M samples per query, step ≥ 60s
when the range ≥ 48h, max query cost 15,000; throttling by "query hours" per 30-second window (30,000 per
user, 60,000 per workspace pre-parse; 2,000,000 post-parse). `/series` searches at most 12 hours.

## Retention

18 months (not configurable).

## Time formats

RFC 3339 or Unix seconds (manifest: epoch seconds); `step` duration or seconds. `start`/`end` are ignored by
`/labels` and `/label/__name__/values` (all retained data).

## Pitfalls

- Every series-fetching selector needs exactly one `__name__` matcher: `metric{...}`.
- `/series` does not support regex matchers.
- Case-insensitive storage: series differing only by case are merged; returned casing is unpredictable.
- Duplicate series across clusters → HTTP 422 (add a unique `cluster` label).
- No metadata, exemplars, rules, alerts or targets endpoints; Prometheus rule groups are ARM resources
  (`Microsoft.AlertsManagement/prometheusRuleGroups`) behind a different token audience.
- Throttling and cost limits answer 4xx/429 with the Prometheus error envelope; a 2xx `status=error` body is
  caught by the error rule (proven by `query_error.json`, built from the documented envelope).
- `query_vector.json` / `series.json` are not copied: the engine falls back to `fixtures/prometheus/`.
