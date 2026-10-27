# Google Cloud Logging (`gcp_logging`)

Manifest: `src/omniops/sources/manifests/gcp_logging.yaml`. Fixtures: `src/omniops/sources/fixtures/gcp_logging/`.

Reference: [Cloud Logging API v2 REST](https://docs.cloud.google.com/logging/docs/reference/v2/rest) ·
[LogEntry](https://docs.cloud.google.com/logging/docs/reference/v2/rest/v2/LogEntry) ·
[Logging query language](https://docs.cloud.google.com/logging/docs/view/logging-query-language) ·
[Quotas and limits](https://docs.cloud.google.com/logging/quotas) ·
[Quickstart (gcloud) with example entries](https://docs.cloud.google.com/logging/docs/write-query-log-entries-gcloud)

## Tables

Paths are relative to `https://logging.googleapis.com`; `{p}` = `GCP_PROJECT_ID` (or the `project` filter).

| SQL table | HTTP | Official doc |
|---|---|---|
| `gcp_logging_entries` | POST `/v2/entries:list` body `{resourceNames, filter, orderBy, pageSize, pageToken}` | https://docs.cloud.google.com/logging/docs/reference/v2/rest/v2/entries/list |
| `gcp_logging_logs` | GET `/v2/projects/{p}/logs` (`pageSize`, `pageToken`) → `logNames[]` | https://docs.cloud.google.com/logging/docs/reference/v2/rest/v2/projects.logs/list |
| `gcp_logging_buckets` | GET `/v2/projects/{p}/locations/-/buckets` (`-` = all locations) | https://docs.cloud.google.com/logging/docs/reference/v2/rest/v2/projects.locations.buckets/list |
| `gcp_logging_log_metrics` | GET `/v2/projects/{p}/metrics` → `metrics[]` (LogMetric) | https://docs.cloud.google.com/logging/docs/reference/v2/rest/v2/projects.metrics/list |

Roles: `logs` → `gcp_logging_entries` (service + `min_severity = 'ERROR'` when error_only); `k8s_logs` →
`gcp_logging_entries` with `resource_type = 'k8s_container'`; `change_events` → `gcp_logging_entries` with
`log = 'cloudaudit.googleapis.com/activity'` (Admin Activity audit logs: deploys, IAM and config changes; columns
title = method + resource, author = principalEmail). Log volume (`log_counts`) is served by `gcp_monitoring`
(metric `logging.googleapis.com/log_entry_count`), since the Logging API has no aggregation endpoint.

## Auth

`gcp_service_account` (key file `GCP_CREDENTIALS_FILE` or ADC; needs `omniops[gcp]`), scope
`https://www.googleapis.com/auth/logging.read` (`logging.admin`, `cloud-platform.read-only`, `cloud-platform`
also accepted). IAM: `logging.logEntries.list` (roles/logging.viewer); Data Access audit logs need
`logging.privateLogEntries.list` (roles/logging.privateLogViewer); log views need `logging.views.access`.

## Base URL / regions

Global endpoint `https://logging.googleapis.com`; the reference also lists regional endpoints (override with
`GCP_LOGGING_BASE_URL`). `resourceNames` accepts `projects/`, `organizations/`, `billingAccounts/`, `folders/`
or a log view path; up to 100 per request; a filter naming a parent outside `resourceNames` returns nothing.

## Time formats

There are no time parameters: the window goes into the filter, `timestamp >= "2026-10-20T13:45:00.000Z" AND
timestamp <= "..."` (RFC 3339 / ISO 8601; a bare `YYYY-MM-DD` means midnight UTC). Entry `timestamp` and
`receiveTimestamp` are RFC 3339 with up to 9 fractional digits. `httpRequest.latency` is a Duration string
(`"3.5s"`) → `duration_ms`.

## Pagination and ordering

`pageSize` (default 50; no documented max - the manifest uses <= 1000) + `pageToken`; other parameters must
match the first call; tokens live 24 h. **An empty `entries` with a `nextPageToken` means the search did not
finish** - keep paging (cursor pagination continues on the token, max 3 pages) or narrow the filter.
`orderBy`: `"timestamp asc"` (default) or `"timestamp desc"` (the manifest sends desc - newest first); ties
by `insertId`.

## Rate limits

`entries.list`: **60 requests per minute per project** (also per folder/org/billing account), not raisable;
"not intended for high-volume retrieval". Engine: 55 req/min, concurrency 2, max 3 pages.
Query length <= 20,000 characters. Entry size ~256 KiB.

## Retention

`_Required` bucket (Admin Activity, System Event audit logs): 400 days, fixed. `_Default`: 30 days in a
project unless configured (1-3650 days); 30 days fixed at folder/org level. User buckets: `retentionDays`.
The manifest clamps windows at 400 days; check `gcp_logging_buckets` for the real per-bucket retention.

## Error handling

Non-2xx `{"error": {"code", "message", "status"}}` (400 INVALID_ARGUMENT for filter syntax, 403, 429
RESOURCE_EXHAUSTED). A source-level rule turns a 200 with that body into a loud failure.

## Query language essentials

`AND`/`OR`/`NOT` uppercase (lowercase are search terms); precedence NOT > OR > AND; adjacent comparisons are
AND-ed; `-` = NOT. Operators `= != > < >= <= : =~ !~`. Missing fields fail comparisons silently (`!=` on a
missing field is FALSE); `field:*` tests existence; JSON null = `NULL_VALUE`. String comparisons ignore case,
regexes do not (`(?i)`). Keys in `jsonPayload`/`labels` are case-sensitive; quote special keys
(`labels."k8s-pod/app"`). `log_id("cloudaudit.googleapis.com/activity")` takes the un-encoded id; `logName`
needs `%2F`. Full text: `SEARCH("token")` (case-insensitive token match) or a bare word (slow). Indexed
fields: `resource.type`, `resource.labels.*`, `logName`, `severity`, `timestamp`, `insertId`, `operation.id`,
`trace`, `httpRequest.status`, `labels.*`, `split.uid`.

## Pitfalls

- Missing `jsonPayload` key or wrong case = 0 rows, not an error; inspect one entry first.
- Severity is an enum; many apps log errors at DEFAULT/INFO in text: search the text too.
- `service` is a convenience OR over `resource.labels.service_name` (Cloud Run), `container_name` (GKE),
  `labels."k8s-pod/app"`, `module_id` (App Engine) and `function_name`.
- `trace` is usually `projects/P/traces/<id>`; `trace_id` strips the prefix; the trace_id filter matches both.
  `spanId` is 16 hex chars while Cloud Trace v1 span ids are decimal.
- Log ids: GKE `stdout`/`stderr` (resource `k8s_container`); Cloud Run `run.googleapis.com/stdout`,
  `run.googleapis.com/stderr`, `run.googleapis.com/requests`; audit `cloudaudit.googleapis.com/activity`,
  `cloudaudit.googleapis.com/data_access`, `cloudaudit.googleapis.com/system_event`.
- Log-based metrics (`gcp_logging_log_metrics.log_filter`) are the bridge from a log-based alert to entries.

## Fixture provenance

| Fixture | Source |
|---|---|
| `entries_list.json` | Docs gcloud quickstart: APIs Explorer `entries.list` response (my-test-log jsonPayload + textPayload entries). |
| `entries_request_log.json` | Schema-built LogEntry with HttpRequest, trace, spanId, severity (Cloud Run request log + GKE stderr entry). |
| `entries_audit_title.json` | Schema-built LogEntry with an AuditLog protoPayload. |
| `logs_list.json` | ListLogsResponse schema with the documented example name `projects/my-project/logs/syslog`. |
| `buckets_list.json`, `log_metrics.json` | Schema-built (LogBucket, LogMetric). |
| `google_error.json` | Google error envelope - proves the source-level error rule. |
