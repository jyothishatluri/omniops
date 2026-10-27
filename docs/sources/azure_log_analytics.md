# Azure Monitor Logs: Log Analytics + workspace-based Application Insights (`azure_log_analytics`)

Manifest: `src/omniops/sources/manifests/azure_log_analytics.yaml`. Fixtures:
`src/omniops/sources/fixtures/azure_log_analytics/`. ARM-side Azure Monitor (metrics, alerts, activity log,
Resource Graph, Service Health) is the `azure_monitor` source.

Reference: [Logs query API overview](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/overview) ·
[Query - Execute (REST)](https://learn.microsoft.com/en-us/rest/api/logsquery/query/execute?view=rest-logsquery-v1) ·
[Request format](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/request-format) ·
[Response format](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/response-format) ·
[Access and authentication](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/access-api) ·
[Basic and Auxiliary table queries](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/basic-logs-query) ·
[Service limits (query API, throttling)](https://learn.microsoft.com/en-us/azure/azure-monitor/fundamentals/service-limits) ·
[Data-plane OpenAPI spec](https://github.com/Azure/azure-rest-api-specs/blob/main/specification/monitor/data-plane/OperationalInsights/stable/v1/OperationalInsights.json) ·
[Table reference](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables-index)

## Tables

Host `https://api.loganalytics.azure.com` (the older `api.loganalytics.io` keeps working). `{ws}` =
`AZURE_LOG_ANALYTICS_WORKSPACE_ID` (workspace GUID) or the `workspace_id` filter. Every table except the schema
is a `POST .../query` with body `{"query": "<KQL>", "timespan": "<start>/<end>"}`; the dedicated tables build the
KQL from their filters (`| where Col in~ (...)`, `contains` for LIKE, numeric comparisons) and summarize
server-side where noted.

| SQL table | HTTP / KQL | Official doc |
|---|---|---|
| `azure_log_analytics_query` | POST `/v1/workspaces/{ws}/query` (any KQL; `workspaces` for cross-workspace) or POST `/v1/workspaces/{ws}/search?timespan=` for Basic/Auxiliary plans | https://learn.microsoft.com/en-us/rest/api/logsquery/query/execute?view=rest-logsquery-v1 · https://learn.microsoft.com/en-us/azure/azure-monitor/logs/basic-logs-query |
| `azure_log_analytics_resource_query` | POST `/v1/{resourceId}/query` (resource-centric) | https://learn.microsoft.com/en-us/rest/api/logsquery/query/execute-with-resource-id?view=rest-logsquery-v1 |
| `azure_log_analytics_app_requests` | `AppRequests` raw rows (newest 5000) | https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/apprequests |
| `azure_log_analytics_app_request_stats` | `AppRequests` summarize `sum(ItemCount)`, failures, percentiles by `bin(TimeGenerated)`, `AppRoleName` [, dimension] | https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/apprequests |
| `azure_log_analytics_app_dependencies` | `AppDependencies` raw rows | https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/appdependencies |
| `azure_log_analytics_app_dependency_map` | `AppDependencies` summarize by caller, type, target | https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/appdependencies |
| `azure_log_analytics_app_exceptions` | `AppExceptions` raw rows (optional `Details`) | https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/appexceptions |
| `azure_log_analytics_app_exception_groups` | `AppExceptions` summarize by `ProblemId`, type, role | https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/appexceptions |
| `azure_log_analytics_app_traces` | `AppTraces` raw rows | https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/apptraces |
| `azure_log_analytics_log_counts` | `AppTraces` summarize count by bin, role, severity | https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/apptraces |
| `azure_log_analytics_trace` | `union` of AppRequests / AppDependencies / AppExceptions / AppTraces by `OperationId` | https://learn.microsoft.com/en-us/azure/azure-monitor/app/transaction-search-and-diagnostics |
| `azure_log_analytics_app_services` | `AppRequests` summarize by `AppRoleName` | https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/apprequests |
| `azure_log_analytics_app_availability` | `AppAvailabilityResults` summarize by bin, test, location | https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/appavailabilityresults |
| `azure_log_analytics_container_logs` | `ContainerLogV2` raw rows | https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/containerlogv2 |
| `azure_log_analytics_kube_events` | `KubeEvents` raw rows | https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/kubeevents |
| `azure_log_analytics_kube_pod_inventory` | `KubePodInventory` latest record per container + restarts in window (`arg_max`) | https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/kubepodinventory |
| `azure_log_analytics_heartbeat` | `Heartbeat` latest per `Computer` | https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/heartbeat |
| `azure_log_analytics_table_usage` | `Usage` per hour and `DataType` | https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/usage |
| `azure_log_analytics_schema` | GET `/v1/workspaces/{ws}/metadata` | https://learn.microsoft.com/en-us/rest/api/logsquery/metadata/get?view=rest-logsquery-v1 |

Roles: `logs` → `azure_log_analytics_app_traces` (severity >= 3 for errors) and
`azure_log_analytics_container_logs`; `log_counts` → `azure_log_analytics_log_counts`; `traces` →
`azure_log_analytics_app_requests`, `azure_log_analytics_app_dependencies`; `trace_detail` →
`azure_log_analytics_trace`; `service_map` → `azure_log_analytics_app_dependency_map`; `services` →
`azure_log_analytics_app_services`; `errors` → `azure_log_analytics_app_exception_groups`; `k8s_logs` →
`azure_log_analytics_container_logs`; `k8s_events` → `azure_log_analytics_kube_events`; `k8s_state` →
`azure_log_analytics_kube_pod_inventory` (restarts = restarts inside the window); `hosts` →
`azure_log_analytics_heartbeat`.

## Auth

`auth.type: azure_ad` = OAuth2 client credentials (Microsoft Entra ID):
`POST https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token`, `grant_type=client_credentials`,
`scope=https://api.loganalytics.io/.default` (the documented token audience, also for the
`api.loganalytics.azure.com` host). RBAC: **Log Analytics Reader** (or Reader) on the workspace; for
resource-centric queries, read access to the resource (resource-context RBAC). The app registration needs the
Log Analytics API `Data.Read` permission only for delegated flows; client credentials use RBAC.

## Base URLs / sovereign clouds

| Cloud | Query endpoint (`AZURE_LOG_ANALYTICS_ENDPOINT`) | Token scope (`AZURE_LOG_ANALYTICS_SCOPE`) |
|---|---|---|
| Public | `https://api.loganalytics.azure.com` (legacy `https://api.loganalytics.io`) | `https://api.loganalytics.io/.default` |
| US Gov | `https://api.loganalytics.us` (from SDK samples; not on a current reference page - verify) | `https://api.loganalytics.us/.default` |
| China | `https://api.loganalytics.azure.cn` | `https://api.loganalytics.azure.cn/.default` |

The workspace id is the **workspace GUID** (customerId on the Overview page), not the ARM resource id.

## Request / response

- POST body: `query` (KQL, required), `timespan` (ISO 8601 interval `start/end`, or a duration such as `PT12H`),
  `workspaces` (extra workspaces for cross-workspace queries, max 100 resources). A `timespan` in both the query
  string and the body is intersected. Without a timespan the query runs over all data.
- `Prefer: wait=<seconds>` sets the server-side timeout (default 180 s, max 10 min); the manifest sends
  `wait=120` (`AZURE_LOG_ANALYTICS_WAIT_S`).
- Response: `{"tables": [{"name": "PrimaryResult", "columns": [{"name", "type"}], "rows": [[...], ...]}]}`
  (row-oriented arrays, parsed with `expand: columnar orientation: row`). Dynamic columns arrive as JSON text;
  in some examples numbers arrive as strings.
- Fatal errors: HTTP 4xx/5xx with a OneAPI `error` object (`SemanticError` for unknown tables/columns,
  `PathNotFoundError` for a wrong workspace). **Non-fatal errors: HTTP 200 with `tables` plus
  `error.code = "PartialError"`** (truncated results, failed cross-workspace leg) - raised by a source-level
  error rule.

## Rate limits

- 200 queries per 30 s per Entra user / client IP across all workspaces.
- 5 concurrent queries against Analytics tables, 2 against Basic/Auxiliary; extra queries wait in a FIFO queue
  (max 200, 3 minutes) and then fail with 429.
- Max 500,000 rows and ~100 MB (64 MB compressed) per response; max running time 10 minutes.
- Manifest: `concurrency: 5`, `requests_per_minute: 360`, retries on 429/502/503 only (504 = the query exceeded
  `Prefer: wait`; repeating it burns the concurrency slots).

## Retention

Per workspace / table: 30 days (default; 90 days for Application Insights tables) up to 730 days interactive,
long-term retention up to 12 years (search jobs). Basic tables are queryable 30 days back through `/search`;
Auxiliary tables over their whole retention.

## Time formats

- `timespan`: ISO 8601 interval `2026-10-01T11:00:00Z/2026-10-01T12:00:00Z` (the engine always sends explicit
  start/end); Basic/Auxiliary `/search` takes it as a query-string parameter.
- `TimeGenerated` comes back as ISO 8601 UTC strings; KQL literals `datetime(2026-10-01T11:00:00Z)`.
- Application Insights durations are milliseconds (`DurationMs`, real). `bin()` widths in the generated KQL are
  seconds (`bin(TimeGenerated, 60s)`).

## Pitfalls

- Application Insights telemetry is sampled: count with `sum(ItemCount)` (the stats/groups tables do), never
  `count()` over raw rows. Raw-row tables return at most the newest 5000 rows.
- Workspace-based App Insights tables use PascalCase (`AppRequests`, `AppRoleName`, `OperationId`, `DurationMs`,
  `ResultCode` string, `Success` bool); the classic names (`requests`, `cloud_RoleName`, `operation_Id`) exist only
  in a resource-centric query against the App Insights component.
- Unknown table/column = loud 400; a wrong VALUE (role name, namespace, `_ResourceId` casing) silently returns 0
  rows - list values first (`azure_log_analytics_app_services`, `azure_log_analytics_table_usage`).
- Ingestion latency (30 s - 3 min, longer for some resource logs) makes the end of a window look like a drop.
- Basic/Auxiliary plans need `/search` (`plan = 'basic'`): no resource-centric scope, 2 concurrent queries,
  billed per GB scanned.
- `ContainerLogV2` is absent on clusters still using the legacy `ContainerLog` schema; Container insights collects
  only Warning `KubeEvents` by default.
- `KubePodInventory.ContainerRestartCount` is cumulative: use `restarts_in_window`.
- The `ts` predicate is only the API timespan for summarize tables (`reapply: false`): bins that start just before
  the window are kept.
