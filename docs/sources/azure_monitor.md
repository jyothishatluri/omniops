# Azure Monitor control plane (`azure_monitor`)

Manifest: `src/omniops/sources/manifests/azure_monitor.yaml`. Fixtures: `src/omniops/sources/fixtures/azure_monitor/`.
Log Analytics / Application Insights KQL is a separate source (`azure_log_analytics`, different host and token
audience); managed Prometheus (PromQL) is `azure_prometheus`.

Reference: [Azure Monitor REST API](https://learn.microsoft.com/en-us/rest/api/monitor/) ·
[Alerts Management REST API](https://learn.microsoft.com/en-us/rest/api/alerts-management/) ·
[Resource Health REST API](https://learn.microsoft.com/en-us/rest/api/resourcehealth/) ·
[Resource Graph REST API](https://learn.microsoft.com/en-us/rest/api/azureresourcegraph/) ·
[ARM throttling](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/request-limits-and-throttling) ·
[Azure Monitor service limits](https://learn.microsoft.com/en-us/azure/azure-monitor/fundamentals/service-limits) ·
[Resource Graph change analysis](https://learn.microsoft.com/en-us/azure/governance/resource-graph/changes/get-resource-changes)

## Tables

Host `https://management.azure.com` (ARM). `{sub}` = `AZURE_SUBSCRIPTION_ID` or the `subscription_id` filter.

| SQL table | HTTP (api-version) | Official doc |
|---|---|---|
| `azure_monitor_metrics` | GET `/{resourceUri}/providers/Microsoft.Insights/metrics` (`metricnames`, `timespan`, `interval`, `aggregation`, `$filter`, `top`, `orderby`, `rollupby`, `metricnamespace`, `AutoAdjustTimegrain`) 2023-10-01 | https://learn.microsoft.com/en-us/rest/api/monitor/metrics/list |
| `azure_monitor_subscription_metrics` | GET `/subscriptions/{sub}/providers/Microsoft.Insights/metrics?region=` (same params; `region` required) 2023-10-01 | https://learn.microsoft.com/en-us/rest/api/monitor/metrics/list-at-subscription-scope |
| `azure_monitor_metric_definitions` | GET `/{resourceUri}/providers/Microsoft.Insights/metricDefinitions` 2023-10-01 | https://learn.microsoft.com/en-us/rest/api/monitor/metric-definitions/list |
| `azure_monitor_metric_namespaces` | GET `/{resourceUri}/providers/microsoft.insights/metricNamespaces` 2017-12-01-preview (only documented version) | https://learn.microsoft.com/en-us/rest/api/monitor/metric-namespaces/list |
| `azure_monitor_alerts` | GET `/subscriptions/{sub}/providers/Microsoft.AlertsManagement/alerts` (`monitorCondition`, `severity`, `alertState`, `monitorService`, `targetResource*`, `alertRule`, `smartGroupId`, `includeContext`, `pageCount`, `sortBy`, `sortOrder`, `customTimeRange`) 2019-03-01 | https://learn.microsoft.com/en-us/rest/api/alerts-management/alerts/alerts/get-all?view=rest-alerts-management-alerts-2019-03-01 |
| `azure_monitor_alert` | GET `{alertId}` (full id, or `/subscriptions/{sub}/providers/Microsoft.AlertsManagement/alerts/{guid}`) 2019-03-01 | https://learn.microsoft.com/en-us/rest/api/alerts-management/alerts/alerts/get-by-id?view=rest-alerts-management-alerts-2019-03-01 |
| `azure_monitor_alert_history` | GET `{alertId}/history` 2019-03-01 | https://learn.microsoft.com/en-us/rest/api/alerts-management/alerts/alerts/get-history?view=rest-alerts-management-alerts-2019-03-01 |
| `azure_monitor_alerts_summary` | GET `/subscriptions/{sub}/providers/Microsoft.AlertsManagement/alertsSummary?groupby=` 2019-03-01 | https://learn.microsoft.com/en-us/rest/api/alerts-management/alerts/alerts/get-summary?view=rest-alerts-management-alerts-2019-03-01 |
| `azure_monitor_metric_alert_rules` | GET `/subscriptions/{sub}/providers/Microsoft.Insights/metricAlerts` 2018-03-01 (latest GA; the REST page now shows 2024-03-01-preview, same shape plus PromQL criteria) | https://learn.microsoft.com/en-us/rest/api/monitor/metric-alerts/list-by-subscription · versions: https://learn.microsoft.com/en-us/azure/templates/microsoft.insights/metricalerts |
| `azure_monitor_log_alert_rules` | GET `/subscriptions/{sub}/providers/Microsoft.Insights/scheduledQueryRules` 2023-12-01 (GA; REST page documents the 2021-08-01 subset) | https://learn.microsoft.com/en-us/rest/api/monitor/scheduled-query-rules/list-by-subscription · versions: https://learn.microsoft.com/en-us/azure/templates/microsoft.insights/scheduledqueryrules |
| `azure_monitor_activity_log_alert_rules` | GET `/subscriptions/{sub}/providers/Microsoft.Insights/activityLogAlerts` 2020-10-01 | https://learn.microsoft.com/en-us/rest/api/monitor/activity-log-alerts/list-by-subscription-id |
| `azure_monitor_alert_processing_rules` | GET `/subscriptions/{sub}/providers/Microsoft.AlertsManagement/actionRules` 2021-08-08 | https://learn.microsoft.com/en-us/rest/api/alerts-management/processing-rules/alert-processing-rules/list-by-subscription?view=rest-alerts-management-processing-rules-2021-08-08 |
| `azure_monitor_autoscale_settings` | GET `/subscriptions/{sub}/providers/Microsoft.Insights/autoscalesettings` 2022-10-01 | https://learn.microsoft.com/en-us/rest/api/monitor/autoscale-settings/list-by-subscription |
| `azure_monitor_activity_log` | GET `/subscriptions/{sub}/providers/Microsoft.Insights/eventtypes/management/values` (`$filter` = time range + ONE of resourceGroupName / resourceUri / resourceProvider / correlationId) 2015-04-01 | https://learn.microsoft.com/en-us/rest/api/monitor/activity-logs/list |
| `azure_monitor_resource_graph` | POST `/providers/Microsoft.ResourceGraph/resources` body `{query, subscriptions \| managementGroups, options {resultFormat: objectArray, $top, $skipToken}}` 2024-04-01 | https://learn.microsoft.com/en-us/rest/api/azureresourcegraph/resourcegraph/resources/resources |
| `azure_monitor_resources` | same POST, KQL over `resources` built from the filters | https://learn.microsoft.com/en-us/azure/governance/resource-graph/samples/starter |
| `azure_monitor_resource_changes` | same POST, KQL over `resourcechanges` / `resourcecontainerchanges` / `healthresourcechanges` | https://learn.microsoft.com/en-us/azure/governance/resource-graph/changes/get-resource-changes |
| `azure_monitor_service_health_events` | GET `/subscriptions/{sub}/providers/Microsoft.ResourceHealth/events` (`queryStartTime` M/D/YYYY, `$filter`) 2025-05-01 | https://learn.microsoft.com/en-us/rest/api/resourcehealth/events/list-by-subscription-id |
| `azure_monitor_service_health_impacted_resources` | GET `/subscriptions/{sub}/providers/Microsoft.ResourceHealth/events/{trackingId}/impactedResources` (`$filter=targetRegion eq '...'`) 2025-05-01 | https://learn.microsoft.com/en-us/rest/api/resourcehealth/impacted-resources/list-by-subscription-id-and-event-id |
| `azure_monitor_resource_health` | GET `/subscriptions/{sub}[/resourceGroups/{rg}]/providers/Microsoft.ResourceHealth/availabilityStatuses` (`$expand=recommendedactions`) 2025-05-01 | https://learn.microsoft.com/en-us/rest/api/resourcehealth/availability-statuses/list-by-subscription-id |
| `azure_monitor_resource_health_history` | GET `/{resourceUri}/providers/Microsoft.ResourceHealth/availabilityStatuses` 2025-05-01 | https://learn.microsoft.com/en-us/rest/api/resourcehealth/availability-statuses/list |

Roles: `metrics` → `azure_monitor_metrics` (labels = `dimensions`); `active_alerts` (`state = 'Fired'`) and
`alert_history` (instances of the rule: `alert_rule` = the rule ARM id) → `azure_monitor_alerts`; `alert_rules` →
`azure_monitor_metric_alert_rules`, `azure_monitor_log_alert_rules` (KQL, `query_language: kql`),
`azure_monitor_activity_log_alert_rules`; `change_events` → `azure_monitor_resource_changes` (property diffs) and
`azure_monitor_activity_log` (control-plane operations); `hosts` → `azure_monitor_resources` (VMs); `services` →
`azure_monitor_resources` (web apps, AKS, container apps). Tables without a role answer specific questions:
`azure_monitor_alert_processing_rules` ("why was nobody paged?"), `azure_monitor_autoscale_settings` ("was
autoscale capped?"), `azure_monitor_service_health_events` / `_impacted_resources` ("is Azure itself broken?"),
`azure_monitor_resource_health*` ("was the resource unavailable and whose fault was it?").

## Auth

`auth.type: azure_ad` = OAuth2 client credentials against Microsoft Entra ID:
`POST {AZURE_AUTHORITY_HOST}/{AZURE_TENANT_ID}/oauth2/v2.0/token` with `grant_type=client_credentials`,
`client_id`, `client_secret`, `scope=https://management.azure.com/.default`. The token is cached until shortly
before `expires_in`.

RBAC on the subscription(s): **Monitoring Reader** (metrics, alerts, alert rules, processing rules, autoscale,
activity log) and **Reader** (Resource Graph sees only what the principal can read; Resource Health / Service
Health). Alerts in another subscription (central monitoring subscription) need `subscription_id` set to it.

## Base URLs / sovereign clouds

| Cloud | ARM endpoint (`AZURE_RESOURCE_MANAGER_ENDPOINT`) | Token scope (`AZURE_RESOURCE_MANAGER_SCOPE`) | Authority (`AZURE_AUTHORITY_HOST`) |
|---|---|---|---|
| Public | `https://management.azure.com` | `https://management.azure.com/.default` | `https://login.microsoftonline.com` |
| US Gov | `https://management.usgovcloudapi.net` | `https://management.usgovcloudapi.net/.default` | `https://login.microsoftonline.us` |
| China (21Vianet) | `https://management.chinacloudapi.cn` | `https://management.chinacloudapi.cn/.default` | `https://login.chinacloudapi.cn` |

Metrics at subscription scope need a `region` (`AZURE_REGION` default) - one call per region.

## Rate limits

- ARM reads use a token bucket per subscription + principal + region (bucket 250, refill 25/s); HTTP 429 carries
  `Retry-After` (honoured by the engine). The metrics API is the most common cause of subscription throttling:
  prefer `azure_monitor_subscription_metrics` (one call per region, top N resources) over per-resource fan-outs.
- Alerts Management: Get All 100 calls/min/subscription, Get Summary 50/min, other alert calls 1000/min.
- Activity log API: 50 queries per 30 s, 75 s timeout.
- Resource Graph: per-user quota (`x-ms-user-quota-remaining` / `x-ms-user-quota-resets-after`, about 15 queries
  per 5 s window), `$top` max 1000 rows per page.
- Manifest: `concurrency: 6`, `requests_per_minute: 600` (enforced across all tables of the source).

## Retention

| Data | Kept |
|---|---|
| Platform metrics | 93 days (`metricAvailabilities[].retention` = P93D) |
| Alerts (Alerts Management) | 30 days (`customTimeRange` must be within 30 days) |
| Activity log | 90 days |
| Resource Graph `resourcechanges` | 14 days |
| Service Health events | `queryStartTime` default 3 days; events filtered on `lastUpdateTime` |
| Resource Health history | recent weeks |

## Time formats

- Metrics `timespan`: `startISO/endISO` (`2026-10-01T11:00:00Z/2026-10-01T12:00:00Z`); `interval` ISO 8601
  duration (`PT1M`, `PT5M`, `PT15M`, `PT30M`, `PT1H`, `PT6H`, `PT12H`, `P1D`) or `FULL`; the manifest maps the
  auto step to the nearest supported grain and sends `AutoAdjustTimegrain=true`. Data point `timeStamp` = start
  of the grain (ISO 8601 UTC).
- Alerts `customTimeRange`: `start/end` ISO 8601 (no `timeRange` at the same time).
- Activity log `$filter`: `eventTimestamp ge '2026-10-01T09:00:00Z' and eventTimestamp le '...'` (quoted ISO 8601).
- Resource Graph change time: `properties.changeAttributes.timestamp` (`2024-06-12T13:26:17.347+00:00`), filtered
  in KQL with `datetime(...)`.
- Service Health `queryStartTime`: `M/D/YYYY` (e.g. `7/24/2020`), day granularity.
- Alert processing rule schedules: `effectiveFrom` / `effectiveUntil` are local times **without offset** in the
  rule's `timeZone`.
- ISO 8601 durations (`windowSize`, `evaluationFrequency`, `cooldown`) are kept as strings.

## Pagination

- `nextLink` (absolute URL including api-version and `$skiptoken`): alerts, rules, autoscale, activity log,
  Service Health, Resource Health → `pagination: next_url`.
- Resource Graph: `$skipToken` in the response → `options.$skipToken` in the next POST body (`cursor`).
  A query without an `id` column cannot be paged: the service returns `resultTruncated: "true"` and no token -
  raised as an error.
- Metrics and metric definitions are not paginated (top N series via `top` + `orderby`).

## 2xx-with-error bodies

- Metrics - List returns HTTP 200 with a per-metric `errorCode` / `errorMessage` (documented example:
  `InvalidSamplingType` when the aggregation is not supported). Rule: `$.value[*].errorMessage` non-empty → error.
- Resource Graph `resultTruncated == "true"` → error (rows were dropped).

## Pitfalls

- Resource ids, resource groups and types are case-insensitive but returned in inconsistent casing (alerts and
  Resource Graph lower-case them, the activity log mixes cases). Pushdown columns echo the filter value when the
  API value matches case-insensitively, so `WHERE resource_group = 'RG-Shop-Prod'` keeps rows; use `lower()` in
  joins across tables.
- Metrics without `$filter` return one aggregated series; `Dim eq '*'` splits, capped at `top` (default 10).
  `OR` cannot combine two different dimensions. A grain with no data has a timestamp but no value (null).
- Unsupported aggregation for a metric is a 200 with `InvalidSamplingType` (raised); unknown metric names are 400.
- `state` (monitorCondition Fired/Resolved) and `alert_state` (New/Acknowledged/Closed) are different. Stateless
  log alerts never resolve on their own.
- Activity log `$filter` accepts only one scope besides time (resource group > resource > provider > correlation
  id here); everything else filters in SQL. Reads are not logged; data-plane operations are in resource logs.
- Resource Graph lags ARM by seconds to minutes; `changes` keys are full property paths with dots
  (`changes->'properties.siteConfig.numberOfWorkers'`).
- Service Health `service`/`region` use display names (`Virtual Machines`, `West Europe`); impacted resources use
  region codes (`westus`). The documented `$filter` example ORs service and region; the table pushes only one.
- Suppression (alert processing rules) hides notifications, not alerts (`is_suppressed`).
- PromQL criteria on metric alerts (`Microsoft.Azure.Monitor.PromQLCriteria`) are documented only in
  2024-03-01-preview; through 2018-03-01 such rules may be missing or come without criteria. Prometheus rule
  groups belong to `azure_prometheus`.
- `rate_limit` / Get All throttling: one `azure_monitor_alerts` call per severity when `severity IN (...)` fans
  out; group in SQL or use `azure_monitor_alerts_summary` instead.
