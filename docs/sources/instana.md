# IBM Instana source (`instana`): Instana REST API

Manifest: `src/omniops/sources/manifests/instana.yaml` (20 tables)
Fixtures: `src/omniops/sources/fixtures/instana/`
API reference (IBM API Hub, "Instana REST API", OpenAPI 1.328.1128):
https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation
(operation anchors are `#<operationId>`). Developer guides with tutorials:
https://developer.ibm.com/apis/catalog/instana--instana-rest-api/Applications and
https://developer.ibm.com/apis/catalog/instana--instana-rest-api/Infrastructure.
Getting started (auth, rate limits):
https://www.ibm.com/docs/en/SSO4JPD/src/pages/api/instana_rest_api/getting_started/
The former https://instana.github.io/openapi/ site is gone (404).

Checked on 2026-10-27. Fixtures are the OpenAPI 200 examples verbatim unless the fixture note says
otherwise (two tutorial responses, one schema-built infrastructure-metrics fixture, one
schema-built scoped release, one single-object Smart Alert config).

## Tables -> endpoints -> reference pages

Base for every anchor: `https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation`

| SQL table | HTTP | Operation (anchor) |
|---|---|---|
| `instana_applications` | GET `/api/application-monitoring/applications` | `#getApplications` |
| `instana_services` | GET `/api/application-monitoring/services` | `#getServices` |
| `instana_service_metrics` | POST `/api/application-monitoring/metrics/services` | `#getServicesMetrics` |
| `instana_application_metrics` | POST `/api/application-monitoring/metrics/applications` | `#getApplicationMetrics` |
| `instana_endpoint_metrics` | POST `/api/application-monitoring/metrics/endpoints` | `#getEndpointsMetrics` |
| `instana_metrics` | POST `/api/application-monitoring/v2/metrics` | `#getApplicationDataMetricsV2` |
| `instana_metric_catalog` | GET `/api/application-monitoring/catalog/metrics` | `#getApplicationCatalogMetrics` |
| `instana_call_groups` | POST `/api/application-monitoring/analyze/call-groups` | `#getCallGroup` |
| `instana_traces` | POST `/api/application-monitoring/analyze/traces` | `#getTraces` |
| `instana_trace_groups` | POST `/api/application-monitoring/analyze/trace-groups` | `#getTraceGroups` |
| `instana_trace_calls` | GET `/api/application-monitoring/v2/analyze/traces/{traceId}` | `#getTraceDownload` |
| `instana_service_map` | GET `/api/application-monitoring/topology/services` | `#getServicesMap` |
| `instana_events` | GET `/api/events` | `#getEvents` |
| `instana_event_detail` | GET `/api/events/{eventId}` | `#getEvent` |
| `instana_snapshots` | GET `/api/infrastructure-monitoring/snapshots` | `#getSnapshots` |
| `instana_infrastructure_metrics` | POST `/api/infrastructure-monitoring/metrics` | `#getInfrastructureMetrics` |
| `instana_releases` | GET `/api/releases` | `#getAllReleases` |
| `instana_logs` | POST `/api/logging/logs/getLogs/v1` | `#getLogs` |
| `instana_smart_alert_configs` | GET `/api/events/settings/application-alert-configs?applicationId=` | `#findAllActiveApplicationAlertConfigs` |
| `instana_smart_alert_config` | GET `/api/events/settings/application-alert-configs/{id}` | `#findApplicationAlertConfig` |

## Roles bound

| Role | Table | Default filters / notes |
|---|---|---|
| `metrics` | `instana_service_metrics` (errors.mean, latency.p95, calls.sum for the service), `instana_infrastructure_metrics` | query languages `instana_metric`, `instana_dfq` |
| `services` | `instana_services` | |
| `hosts` | `instana_snapshots` | `plugin = host` |
| `service_map` | `instana_service_map` | |
| `traces` | `instana_traces` | `service`, `error_only` |
| `trace_detail` | `instana_trace_calls` | |
| `logs` | `instana_logs` | `service`, `level = ERROR` when error_only, `host`, `query` |
| `active_alerts` | `instana_events` | `type IN (incident, issue)`, `state = open` |
| `alert_history` | `instana_events` | `type IN (incident, issue)` |
| `incidents` | `instana_events` | `type = incident` |
| `change_events` | `instana_releases`, `instana_events` (`type = change`) | |
| `alert_rules` | `instana_smart_alert_config` | `alert_id` = the alert's rule id (application Smart Alerts) |

## Auth

- Header `Authorization: apiToken <token>` (Settings > Security & access > API tokens). The token
  needs the application, infrastructure, events and logs read permissions the tables use.

## Base URLs

- SaaS: `https://<unit>-<tenant>.instana.io` (the UI host, e.g. `https://prod-acme.instana.io`);
  self-hosted: the backend URL. `INSTANA_BASE_URL` excludes `/api`.

## Time formats

- All times are epoch MILLISECONDS. GET endpoints take `to` + `windowSize` (ms) or `from`/`to`
  (events, releases); POST bodies use `timeFrame: {to, windowSize}`. `windowSize` max 31 days.
- Metric `granularity` is in SECONDS; at most 600 points per series. Without granularity the
  metrics endpoints return ONE value for the whole window. The tables send ~300-point granularity
  by default (call-groups / trace-groups default to one value per group).
- Infrastructure `rollup` is in seconds: 1, 5, 60, 300 or 3600 (the table picks the smallest giving
  <= 600 points, >= 60 s for windows older than a day).
- Windows are adjusted to whole buckets (`adjustedTimeframe`).

## Pagination

- `page` / `pageSize` on applications/services (engine page pagination, 200 per page).
- Analyze endpoints (call-groups, traces, trace-groups) use `pagination.retrievalSize` (max 200)
  plus an ingestion-time/offset cursor; only the first page is fetched (`can_load_more` says if
  more exist).
- Logs: `retrievalSize` max 200, `offset` up to 2,000 (engine offset pagination, 5 pages),
  `canLoadMore` gates continuation; `requestedTags` is limited to 10 tags.

## Rate limits

- 10,000 REST calls per hour per API token (`X-RateLimit-Limit/Remaining/Reset`, HTTP 429 when
  exceeded); infrastructure search, snapshot and metric queries are limited to 1,000 calls per hour.
  The manifest caps the source at 150 requests/minute.

## Retention

- Calls and traces: 7 days (a subset kept for 1 year).
- Application metrics: accurate 31 days, approximate 1 year.
- Infrastructure metrics: 1 s rollups ~24 h, 1 min ~1 month, 1 h ~13 months.

## Pitfalls (also in the manifest)

- Metric names are `<metricId>.<aggregation>` with the allowed aggregations per metric
  (calls: SUM/PER_SECOND, errors: MEAN, latency: MEAN/P25..P99/MAX/MIN); a wrong pair is HTTP 400.
- `nameFilter` matches with "contains" semantics; SQL then keeps exact names.
- Tag filters need tags from the application tag catalog (service.name, endpoint.name,
  call.http.status, ...); an unknown tag or value silently matches nothing.
- `group_entity` DESTINATION groups by callee, SOURCE by caller.
- Release markers exist only when pipelines post them; also check `instana_events` changes.
- Log search covers only logs ingested by Instana log management.

## Fixtures provenance

| Fixture | Source |
|---|---|
| `applications.json`, `services.json`, `service_metrics.json`, `application_metrics.json`, `endpoint_metrics.json`, `metrics_v2.json`, `catalog_metrics.json`, `call_groups.json`, `traces.json`, `trace_groups.json`, `trace_download.json`, `services_map.json`, `events.json`, `event.json`, `snapshots.json`, `releases.json`, `logs.json`, `smart_alert_configs.json` | verbatim OpenAPI 200 examples |
| `service_metrics_error_rate.json`, `call_groups_services.json` | verbatim tutorial responses (Applications developer guide) |
| `infrastructure_metrics_schema.json` | built from the InfrastructureMetricResult schema (no 200 example) |
| `releases_scoped.json` | built from the ReleaseWithMetadata schema |
| `smart_alert_config.json` | the object of the verbatim `findAllActiveApplicationAlertConfigs` example (same schema as the by-id response) |
