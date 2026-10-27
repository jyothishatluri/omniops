# SigNoz source (`signoz`): SigNoz query and management API

Manifest: `src/omniops/sources/manifests/signoz.yaml` (25 tables)
Fixtures: `src/omniops/sources/fixtures/signoz/`
API reference (OpenAPI, SigNoz v0.147.0): https://signoz.io/api-reference/ (per operation:
`https://signoz.io/api-reference/#/operations/<operationId>`); machine-readable spec:
https://signoz.io/api/api-reference-openapi/latest/
Query guides: Logs API https://signoz.io/docs/logs-management/logs-api/overview/, Traces API
https://signoz.io/docs/traces-management/trace-api/overview/, Metrics API
https://signoz.io/docs/metrics-management/query-range-api/.

Checked on 2026-10-27 against the OpenAPI document, the docs pages above and, where the docs are
silent, the official source and tests in https://github.com/SigNoz/signoz (main):
`pkg/types/querybuildertypes/querybuildertypesv5/resp.go` (v5 response JSON), 
`pkg/telemetryschema/tracestelemetryschema/const.go` (span field names),
`tests/integration/tests/querier*` (row shapes, empty results, warnings),
`pkg/query-service/app/http_handler.go` + `pkg/modules/services` (services / top operations /
dependency graph, which back the Services and Service Map pages but are not in the API reference).

## Tables -> endpoints -> reference pages

| SQL table | HTTP | Official reference |
|---|---|---|
| `signoz_logs` | POST `/api/v5/query_range` (builder_query, signal logs, requestType raw) | https://signoz.io/docs/logs-management/logs-api/search-logs/ (+ payload model https://signoz.io/docs/logs-management/logs-api/payload-model/, operation `QueryRangeV5`) |
| `signoz_log_counts` | POST `/api/v5/query_range` (logs, time_series, groupBy) | https://signoz.io/docs/logs-management/logs-api/aggregate-logs/ |
| `signoz_spans` | POST `/api/v5/query_range` (signal traces, raw, selectFields) | https://signoz.io/docs/traces-management/trace-api/search-traces/ |
| `signoz_trace_aggregates` | POST `/api/v5/query_range` (traces, time_series) | https://signoz.io/docs/traces-management/trace-api/aggregate-traces/ |
| `signoz_metrics` | POST `/api/v5/query_range` (signal metrics, time_series) | https://signoz.io/docs/metrics-management/query-range-api/ |
| `signoz_promql` | GET `/prometheus/api/v1/query_range` | https://signoz.io/api-reference/#/operations/PrometheusQueryRange |
| `signoz_trace` | POST `/api/v4/traces/{traceID}/waterfall` | https://signoz.io/api-reference/#/operations/GetWaterfallV4 |
| `signoz_services` | POST `/api/v2/services` | not in the API reference: https://github.com/SigNoz/signoz/blob/main/pkg/modules/services/implservices/module.go |
| `signoz_top_operations` | POST `/api/v2/service/top_operations` | not in the API reference: same source file |
| `signoz_service_map` | POST `/api/v1/dependency_graph` | not in the API reference: https://github.com/SigNoz/signoz/blob/main/pkg/query-service/app/http_handler.go |
| `signoz_alerts` | GET `/api/v1/alerts` | https://signoz.io/api-reference/#/operations/GetAlerts |
| `signoz_rules` | GET `/api/v3/rules` | https://signoz.io/api-reference/#/operations/ListRulesV3 |
| `signoz_rule` | GET `/api/v2/rules/{id}` | https://signoz.io/api-reference/#/operations/GetRuleByID |
| `signoz_rule_history` | GET `/api/v2/rules/{id}/history/timeline` | https://signoz.io/api-reference/#/operations/GetRuleHistoryTimeline and https://signoz.io/docs/alerts-management/migrate-alert-history-api-v1-to-v2/ |
| `signoz_rule_top_contributors` | GET `/api/v2/rules/{id}/history/top_contributors` | https://signoz.io/api-reference/#/operations/GetRuleHistoryTopContributors |
| `signoz_metric_names` | GET `/api/v2/metrics` | https://signoz.io/api-reference/#/operations/ListMetrics |
| `signoz_metric_attributes` | GET `/api/v2/metrics/attributes` | https://signoz.io/api-reference/#/operations/GetMetricAttributes |
| `signoz_field_keys` | GET `/api/v1/fields/keys` | https://signoz.io/api-reference/#/operations/GetFieldsKeys |
| `signoz_field_values` | GET `/api/v1/fields/values` | https://signoz.io/api-reference/#/operations/GetFieldsValues |
| `signoz_hosts` | POST `/api/v2/infra_monitoring/hosts` | https://signoz.io/api-reference/#/operations/ListHosts |
| `signoz_pods` | POST `/api/v2/infra_monitoring/pods` | https://signoz.io/api-reference/#/operations/ListPods |
| `signoz_dashboards` | GET `/api/v2/dashboards` | https://signoz.io/api-reference/#/operations/ListDashboardsV2 and https://signoz.io/docs/dashboards/dashboards-v2-api/ |
| `signoz_downtime_schedules` | GET `/api/v1/downtime_schedules` | https://signoz.io/api-reference/#/operations/ListDowntimeSchedules |
| `signoz_exceptions` | POST `/api/v5/query_range` (clickhouse_sql over `signoz_traces.distributed_signoz_error_index_v2`) | https://signoz.io/docs/userguide/writing-clickhouse-traces-query/ |
| `signoz_ingestion_stats` | GET `/api/v1/stats` | https://signoz.io/docs/telemetry-stats-api/ |

Search syntax used by every `filter.expression`: https://signoz.io/docs/userguide/search-syntax/
and full-text search https://signoz.io/docs/userguide/full-text-search/.

## Roles bound

| Role | Table | Default filters / notes |
|---|---|---|
| `metrics` | `signoz_metrics` (service), `signoz_promql` | metric / PromQL required |
| `logs` | `signoz_logs` | service, env, host, namespace, pod, error_only, query |
| `log_counts` | `signoz_log_counts` | group_by severity_text for a service, else service.name |
| `traces` | `signoz_spans` | service, env, error_only |
| `trace_detail` | `signoz_trace` | trace_id |
| `services` | `signoz_services` | |
| `service_map` | `signoz_service_map` | |
| `hosts` | `signoz_hosts` | host |
| `k8s_state` | `signoz_pods` | namespace, pod (phase = kubectl-style status) |
| `active_alerts` | `signoz_alerts` | `state = active` |
| `alert_rules` | `signoz_rule` (rule UUID = the alert's ruleId label), `signoz_rules` | |
| `alert_history` | `signoz_rule_history` | rule_id |
| `errors` | `signoz_exceptions` | service |
| `dashboards` | `signoz_dashboards` | |

## Auth

- Service-account API key (Settings > Service Accounts > Keys; Admins create keys, a
  `signoz-viewer` role is enough to read) in the header `SIGNOZ-API-KEY: <key>` (the OpenAPI names
  the scheme `SigNoz-Api-Key`; headers are case-insensitive). Session bearer tokens also work but
  are not used. Keys can expire (optional expiration date).

## Base URLs

- SigNoz Cloud: `https://<tenant>.<region>.signoz.cloud` (the UI URL; NOT the
  `ingest.<region>.signoz.cloud` OTLP endpoint).
- Self-hosted: the SigNoz UI/API address (default port 8080), e.g. `http://signoz:8080`.
- `SIGNOZ_URL` excludes `/api`.

## Time formats (the #1 trap)

| API | start / end | other units |
|---|---|---|
| `/api/v5/query_range` | epoch MILLISECONDS (integers) | `stepInterval` seconds (or "60s"); response timestamps epoch ms; raw rows carry RFC 3339 `timestamp` |
| `/api/v2/services`, `/api/v2/service/top_operations`, `/api/v1/dependency_graph` | epoch NANOSECONDS as strings | p50..p99 / avgDuration in nanoseconds; error rates in percent; callRate per second |
| rule history (`/api/v2/rules/{id}/history/*`) | `start` / `end` epoch ms | `unixMilli` in items |
| fields / metric discovery | `startUnixMilli` / `endUnixMilli` (fields), `start` / `end` (metrics) epoch ms | |
| infra monitoring (hosts, pods) | epoch ms in the body | metric fields -1 = no data |
| `/prometheus/api/v1/query_range` | RFC 3339 or unix seconds (tables send seconds) | `step` seconds |
| waterfall | none (trace id) | `time_unix`, `duration_nano` in nanoseconds |

## Pagination and limits

- v5 raw queries page with `offset`/`limit` (or `cursor`) INSIDE `compositeQuery.queries[0].spec`;
  the engine cannot target a list element of the body, so `signoz_logs` / `signoz_spans` fetch one
  page (SQL LIMIT, default 500, capped at 5,000; the server max is 10,000).
- time_series group-bys keep the top `limit` groups ordered by the aggregation (tables send top 50).
- Rule list (`/api/v3/rules`) and dashboards: `limit`/`offset` (engine offset pagination).
- Rule history timeline: cursor (`nextCursor` -> `cursor`).
- Infra hosts/pods: `offset`/`limit` in the body (engine offset pagination, `total`).
- Waterfall returns every span up to 10,000 (server default), then windows (`hasMore`).

## Rate limits

- No published query-API rate limit (the documented 100 RPS limit concerns trial-account
  ingestion). Queries run on the tenant's ClickHouse; keep windows short. The manifest enforces
  120 requests/min and concurrency 4.
- `/api/v1/stats` runs full row counts and is aborted after 10 s on large installs.

## Retention

- Defaults: logs and traces 15 days, metrics 30 days. Cloud options: logs/traces 15, 30, 90, 180
  days or 1 year; metrics 1, 3, 6 or 13 months (https://signoz.io/docs/userguide/retention-period/).
  Self-hosted retention is set in Settings.

## Versions

- Query Builder v5 / `/api/v5/query_range`: SigNoz v0.92.1+ (August 2025 changelog). Older
  instances expose `/api/v3/query_range` (logs, traces) and `/api/v4/query_range` (metrics) with a
  different payload; not implemented here.
- Alert history v2 (`GET /api/v2/rules/{id}/history/*`): v0.118.0+; the v1 POST endpoints are
  deprecated and being removed.
- Services v2 / top operations v2 exist since the services module moved to QBv5; very old
  instances only have `POST /api/v1/services` (bare-array response).

## Pitfalls (also in the manifest)

- Filter expression strings need single quotes; a bare quoted string is a log-body full-text search
  evaluated as an RE2 regex when it compiles.
- Unknown keys return 200 + 0 rows + a "not found" warning (`data.warning`) on current versions
  (v0.92-era versions answered 400 with suggestions): discover names with `signoz_field_keys`,
  `signoz_field_values`, `signoz_metric_names`, `signoz_metric_attributes`.
- Ambiguous keys (same name as string and number, or as attribute and resource) need a context
  prefix (`resource.service.name`, `attribute.http.status_code`).
- `status_code_string` values are `STATUS_CODE_UNSET | STATUS_CODE_OK | STATUS_CODE_ERROR`;
  `kind_string` values `Server, Client, Internal, Producer, Consumer, Unspecified`.
- Root spans: `parent_span_id = ''`; entry-point spans: `isEntryPoint = true` (services page uses
  `isRoot = true OR isEntryPoint = true`).
- Metric names are OTel dotted names; in PromQL they must be written as `{__name__="a.b.c"}`.
  Counters need `time_aggregation = rate`; histogram percentiles need the `.bucket` metric.
- Span-derived metrics (`signoz_calls_total`, `signoz_latency.bucket`) exist only with the
  span-metrics processor; prefer `signoz_trace_aggregates` / `signoz_services` otherwise.
- `data.warning` is the only place SigNoz says why a result is empty; the engine has no non-fatal
  warning channel, so the tables expose it as a `warning` column (visible only when rows exist).

## Fixtures provenance

| Fixture | Source |
|---|---|
| `logs_raw.json` | RawData schema; row contents from tests/integration/tests/querierlogs/01_list.py (values) and 07_list_expectations.py (key set) |
| `logs_empty.json` | `rows: null` empty result as asserted in querierlogs/07_list_expectations.py |
| `query_error.json` | RenderErrorResponse schema (`status: error`) - proves the 2xx error rule |
| `log_counts_ts.json`, `trace_aggregates_ts.json`, `metrics_ts.json` | TimeSeriesData schema (resp.go JSON tags); requests from the docs/OpenAPI examples; warning text from queriermetrics/02_warnings.py |
| `spans_raw.json` | RawData schema; key set asserted in queriertraces/01_list.py |
| `promql_range.json` | upstream Prometheus docs example (SigNoz's OpenAPI states the Prometheus contract) |
| `trace_waterfall.json` | GetWaterfallV4 schema + `pkg/types/spantypes/waterfall_*.go` JSON tags |
| `services.json` | frontend mock `frontend/src/mocks-server/__mockdata__/services.ts` (verbatim values) |
| `top_operations.json` | frontend mock `frontend/src/container/MetricsApplication/__mocks__/getTopOperation.ts` |
| `dependency_graph.json` | frontend story mock `modules/Servicemap/stories/__story_mockdata__/serviceMap.ts` + `model.ServiceMapDependencyResponseItem` |
| `alerts.json` | frontend fixture `mocks-server/__mockdata__/triggered_alerts.ts` (GetAlerts DTO) |
| `rules_v3.json` | ListRulesV3 schema with values from `mocks-server/__mockdata__/alert_rules.ts` |
| `rule_detail.json`, `rule_timeline.json`, `rule_top_contributors.json`, `metric_names.json`, `metric_attributes.json`, `field_keys.json`, `field_values.json`, `hosts.json`, `pods.json`, `dashboards.json`, `downtime_schedules.json`, `exceptions_raw.json` | built strictly from the OpenAPI 200 schemas (values illustrative; label shape of rule history verbatim from the v1->v2 migration doc) |
| `stats.json` | verbatim example of the Stats API docs page |
