# Coralogix (`coralogix`)

Manifest: `src/omniops/sources/manifests/coralogix.yaml` · fixtures: `src/omniops/sources/fixtures/coralogix/`

Direct HTTPS, no MCP, no SDK. Every host is region specific: `https://api.<coralogix-domain>`.
Logs, spans and system datasets (alert history) are queried through the direct DataPrime /
Lucene HTTP query API (NDJSON response); metrics through the Prometheus-compatible Metrics API;
alert definitions, incidents, cases and SLOs through the management API (OpenAPI v5,
`/mgmt/openapi/5/...`). Re-verified 2026-10-27: query endpoint/body/limits on the 'Direct
Lucene & DataPrime query HTTP API' page, Metrics API paths, alert-definitions list and cases
list reference pages.

## Tables

| SQL table | HTTP | Endpoint (under `https://api.<domain>`) | Official reference |
|---|---|---|---|
| `coralogix_dataprime` | POST | `/api/v1/dataprime/query` (`metadata.syntax = QUERY_SYNTAX_DATAPRIME`) | [Direct Lucene & DataPrime query HTTP API](https://coralogix.com/docs/dataprime/API/direct-archive-query-http/) |
| `coralogix_lucene` | POST | `/api/v1/dataprime/query` (`QUERY_SYNTAX_LUCENE`) | same |
| `coralogix_alert_history` | POST | `/api/v1/dataprime/query` with `source system/alerts.history` | [alerts.history system dataset](https://coralogix.com/docs/user-guides/data-layer/system_dataspace/alerts-history/) |
| `coralogix_metrics_query_range` | GET | `/metrics/api/v1/query_range` | [Metrics API](https://coralogix.com/docs/user-guides/data-query/metrics-api/) |
| `coralogix_metrics_query` | GET | `/metrics/api/v1/query` | same |
| `coralogix_metrics_series` | GET | `/metrics/api/v1/series` | same |
| `coralogix_metrics_label_values` | GET | `/metrics/api/v1/label/{name}/values` | same |
| `coralogix_alert_defs` | GET | `/mgmt/openapi/5/alerts/alerts/v3` | [List alert definitions](https://coralogix.com/docs/api-reference/v5/alert-definitions-service/get-a-list-of-all-accessible-alert-definitions/) |
| `coralogix_alert_def` | GET | `/mgmt/openapi/5/alerts/alerts/v3/{id}` | [Get alert definition by id](https://coralogix.com/docs/api-reference/v5/alert-definitions-service/get-alert-definition-by-id/) |
| `coralogix_incidents` | POST | `/mgmt/openapi/5/incidents/incidents/v1` | [List incidents with filters](https://coralogix.com/docs/api-reference/v5/incidents-service/list-incidents-with-filters/) |
| `coralogix_cases` | POST | `/mgmt/openapi/5/cases/cases/v1` | [List cases with filters](https://coralogix.com/docs/api-reference/v5/cases-service/list-cases-with-filters/) |
| `coralogix_slos` | GET | `/mgmt/openapi/5/slo/slos/v1` | [List SLOs](https://coralogix.com/docs/api-reference/v5/slos-service/list-slos/) |

### Query API request / response

Request body: `{query, metadata: {tier, syntax, startDate, endDate, defaultSource, limit,
strictFieldsValidation, nowDate}}` (plus optional `executionProfile.preset`).
- `tier`: `TIER_FREQUENT_SEARCH` (default, high tier) or `TIER_ARCHIVE` (required to read the archive).
- `syntax`: `QUERY_SYNTAX_DATAPRIME` (default) or `QUERY_SYNTAX_LUCENE`.
- `startDate` inclusive, `endDate` exclusive, ISO 8601.
- `limit`: default 2,000; max 12,000 (`TIER_FREQUENT_SEARCH`) / 50,000 (`TIER_ARCHIVE`) per
  the API page. The separate 'API and background query limitations' page says 15,000 for the
  high tier; the manifest uses the stricter 12,000.

Response: `application/x-ndjson`, one object per line: `{"queryId":{...}}`, one or more
`{"result":{"results":[{"metadata":[{key,value}],"labels":[{key,value}],"userData":"<json string>"}]}}`
batches, optional `{"warning":{...}}` / `{"error":{...}}` lines and a final
`{"statistics":{"status": COMPLETED|FAILED|TIMED_OUT|CANCELLED|..., ...}}`. Error lines and
non-COMPLETED statuses arrive with HTTP 200, so the manifest declares error rules on
`$[*].error.message` and `$[*].statistics.status` (fixture `dataprime_query_error.ndjson`
proves the rule fires). `userData` is a JSON string; the manifest parses it (`expand parse: true`).

## Auth

`Authorization: Bearer <API key>` (personal or team key, Settings > API Keys). Queries and
metrics need the `DataQuerying` preset (or `metrics.data-api#high:ReadData` for metrics);
alert definitions need `alerts:ReadConfig`; incidents and cases need their read permissions
(cases: `CASE:*` read, no preset).

## Base URLs / regions

`CORALOGIX_DOMAIN` is the region domain, not the team URL: `us1.coralogix.com`,
`us2.coralogix.com`, `us3.coralogix.com`, `eu1.coralogix.com`, `eu2.coralogix.com`,
`ap1.coralogix.com`, `ap2.coralogix.com`, `ap3.coralogix.com`, `gov1.coralogixgov.us`.
API host = `api.<domain>` (e.g. `https://api.eu2.coralogix.com/api/v1/dataprime/query`,
`https://api.eu2.coralogix.com/metrics/api/v1/query`, `https://api.eu1.coralogix.com/mgmt/openapi/5/...`).
A key used against another region's host fails with 401/403. Reference:
[Coralogix endpoints](https://coralogix.com/docs/integrations/coralogix-endpoints/).

## Rate limits

- Direct query API: 30 requests per minute ([API and background query limitations](https://coralogix.com/docs/dataprime/API/limitations/)).
  The manifest applies 30/min to the whole source (engine-enforced), concurrency 3.
- Scan limits: high tier 100 MB fetched (OpenSearch-style); archive tied to the team quota.
  Background queries (not used) have their own limits and a 30-minute execution cap.

## Retention

Per team / TCO policy: frequent search (high tier) is short; archive retention is the S3/GCS
archive's. `system/alerts.history` exists only for alerts processed by Notification Center.

## Time formats

| Where | Format |
|---|---|
| `startDate` / `endDate` | ISO 8601 (manifest sends RFC 3339 with ms, UTC) |
| `$m.timestamp` in results | ISO-like string without zone (UTC) |
| alerts.history `alert.timestamp` | epoch nanoseconds |
| Metrics API | Prometheus conventions: `start`/`end`/`time` epoch seconds, `step` seconds |
| Management API | RFC 3339 strings (`createdTime`, `lastTriggeredTime`, `createTime`) |

## Pitfalls

- Labels `$l.applicationname` / `$l.subsystemname` are set by the shipper and case sensitive;
  list real values with a `groupby` first.
- Severity is metadata (`$m.severity`), not user data.
- Logs moved to monitoring/compliance tiers by TCO policies are invisible in
  `TIER_FREQUENT_SEARCH`; use `tier = 'TIER_ARCHIVE'`.
- Unknown keypaths silently return null / 0 rows unless `strictFieldsValidation` is true.
- `groupby roundTime(...)` omits empty buckets.
- Metrics API does not implement `/api/v1/alerts`, `/rules`, `/targets/metadata`,
  `/query_exemplars`.
- The Incidents UI is being replaced by Cases; the incidents API keeps working, but new
  accounts only have cases. Incidents filter on creation time; cases use ACTIVE_WINDOW semantics.
- Alert definitions list returns at most 1000 definitions per call.
