# Google Cloud Error Reporting (`gcp_error_reporting`)

Manifest: `src/omniops/sources/manifests/gcp_error_reporting.yaml`. Fixtures:
`src/omniops/sources/fixtures/gcp_error_reporting/`.

Reference: [Error Reporting API v1beta1 REST](https://docs.cloud.google.com/error-reporting/reference/rest) ·
[ListGroupStatsResponse / ErrorGroupStats / TimedCount](https://docs.cloud.google.com/error-reporting/reference/rest/v1beta1/ListGroupStatsResponse) ·
[QueryTimeRange](https://docs.cloud.google.com/error-reporting/reference/rest/v1beta1/QueryTimeRange) ·
[ErrorGroupOrder](https://docs.cloud.google.com/error-reporting/reference/rest/v1beta1/ErrorGroupOrder) ·
[ErrorEvent](https://docs.cloud.google.com/error-reporting/reference/rest/v1beta1/ErrorEvent) ·
[ErrorContext](https://docs.cloud.google.com/error-reporting/reference/rest/v1beta1/ErrorContext) ·
[ErrorGroup](https://docs.cloud.google.com/error-reporting/reference/rest/v1beta1/projects.groups) ·
[Quotas and limits](https://docs.cloud.google.com/error-reporting/quotas)

`v1beta1` is the only published version of the API (discovery:
`https://clouderrorreporting.googleapis.com/$discovery/rest?version=v1beta1`).

## Tables

Paths are relative to `https://clouderrorreporting.googleapis.com`; `{pn}` = `projects/{GCP_PROJECT_ID}` or,
when `GCP_ERROR_REPORTING_LOCATION` is set, `projects/{GCP_PROJECT_ID}/locations/{location}` (`-` = all
regions for list methods).

| SQL table | HTTP | Official doc |
|---|---|---|
| `gcp_error_reporting_groups` | GET `/v1beta1/{pn}/groupStats` (`groupId` repeated, `serviceFilter.service/.version/.resourceType`, `timeRange.period`, `order`, `pageSize`, `pageToken`) - one row per error group | https://docs.cloud.google.com/error-reporting/reference/rest/v1beta1/projects.groupStats/list |
| `gcp_error_reporting_group_counts` | same + `timedCountDuration` (`"60s"` ...) - one row per (group, timed count bucket) | https://docs.cloud.google.com/error-reporting/reference/rest/v1beta1/projects.groupStats/list |
| `gcp_error_reporting_events` | GET `/v1beta1/{pn}/events?groupId=...` (`groupId` required, `serviceFilter.*`, `timeRange.period`) - one row per occurrence | https://docs.cloud.google.com/error-reporting/reference/rest/v1beta1/projects.events/list |
| `gcp_error_reporting_group` | GET `/v1beta1/projects/{p}[/locations/{l}]/groups/{groupId}` - resolution state + tracking issues | https://docs.cloud.google.com/error-reporting/reference/rest/v1beta1/projects.groups/get |

Role: `errors` → `gcp_error_reporting_groups` (id = group_id, title = first stack-trace line, count, ts =
last_seen, first_seen, service, version, state, url).

## Auth

`auth.type: gcp_service_account` (key file `GCP_CREDENTIALS_FILE` or ADC; needs `omniops[gcp]`). Scope
`https://www.googleapis.com/auth/cloud-platform` (the API accepts only `cloud-platform` or
`stackdriver-integration`; there is no read-only scope). IAM: `roles/errorreporting.viewer`.

## Base URL / regions

Endpoint `https://clouderrorreporting.googleapis.com` for every region (override `GCP_ERROR_REPORTING_BASE_URL`).
Without a location the API uses `global`; regional error data needs `GCP_ERROR_REPORTING_LOCATION=<region>`
or `-` (all regions, list methods only - `groups.get` needs the concrete location).

## Time formats and time ranges

- There are **no start/end parameters**. `timeRange.period` is an enum relative to **now**: `PERIOD_1_HOUR`,
  `PERIOD_6_HOURS`, `PERIOD_1_DAY`, `PERIOD_1_WEEK`, `PERIOD_30_DAYS` (`PERIOD_UNSPECIFIED` "do not use"). The
  manifest picks the smallest period that reaches back to the SQL window start (`now - ts_start`).
- Recommended minimum `timedCountDuration` per period: 1 h → 1 min, 6 h → 10 min, 1 day → 1 h, 1 week → 6 h,
  30 days → 1 day (used by `gcp_error_reporting_group_counts` unless `bucket_s` is given). Duration format
  `"<seconds>s"`. Alignment defaults to `ALIGNMENT_EQUAL_AT_END` (buckets end at now).
- Response timestamps (`firstSeenTime`, `lastSeenTime`, `eventTime`, `timeRangeBegin`, TimedCount
  `startTime` inclusive / `endTime` exclusive) are RFC 3339.
- `count`, `affectedUsersCount` and TimedCount `count` are **int64 serialized as JSON strings**;
  `numAffectedServices` and `responseStatusCode` / `lineNumber` are JSON integers.

## Ordering and pagination

`order` (ErrorGroupOrder): `COUNT_DESC` (default), `LAST_SEEN_DESC`, `CREATED_DESC`, `AFFECTED_USERS_DESC`.
`pageSize` (groupStats default 20; manifest 100 / 50 with timed counts) + `pageToken` → `nextPageToken`
(non-empty = more; resend identical parameters). events.list does not document its order: ORDER BY ts in SQL.

## Rate limits

Default quotas: **60 error event data requests per minute per user**, **60 error group metadata requests per
minute per user** (adjustable), 6,000 error reports per minute. Exceeding returns 429 `RESOURCE_EXHAUSTED`.
Engine: 55 requests/min, concurrency 2, events fan-out <= 5 groups.

## Retention

Error data is kept **30 days**. Each project keeps at most 10,000 deployment (service + version) / error-group
pairs; beyond that the oldest deployments and their error data are discarded. `timeRangeBegin` may be later than
the requested period start when older data was deleted.

## Error handling

Non-2xx `{"error": {"code", "message", "status"}}` (403 PERMISSION_DENIED when the API is disabled or the
caller lacks errorreporting.viewer, 400 INVALID_ARGUMENT, 429). A source-level rule turns a 200 with that body
into a loud failure (fixture `google_error.json`). `groups.get` 404 (unknown id / wrong location) = 0 rows.

## Pitfalls

- Counts cover **period start → now**, not the incident window; use `gcp_error_reporting_group_counts`
  (timed buckets) or `gcp_error_reporting_events` (event times) for the window itself.
- `firstSeenTime` / `lastSeenTime` ignore the period (all time): first_seen inside the window = a NEW error
  (often introduced by a deploy - check `version`).
- All counts are approximate (events are sampled before counting).
- MUTED groups are excluded from groupStats by default (`gcp_error_reporting_group` still returns them);
  RESOLVED groups that recur go back to OPEN; `RESOLUTION_STATUS_UNSPECIFIED` is treated as OPEN.
- `serviceFilter` values are exact matches on the reported ServiceContext (Cloud Run service, GKE
  container, App Engine module, function name; version = revision / deployment).
- Only errors with a stack trace in the logs (or reported via `events:report`) appear here; also search
  `gcp_logging_entries` (Cloud Logging entries link to groups through `errorGroups[].id` → `error_group_ids`).
- `groupId[]` is sent as a repeated `groupId` query parameter.

## Fixture provenance

The v1beta1 reference documents only schemas (no example responses), so every fixture is built strictly from
the documented schema with illustrative values.

| Fixture | Source |
|---|---|
| `group_stats.json` | ListGroupStatsResponse / ErrorGroupStats / ErrorGroup / TimedCount / ErrorEvent / ErrorContext schema (int64 strings, RFC 3339 times). |
| `events.json` | ListEventsResponse / ErrorEvent / ServiceContext / ErrorContext (HttpRequestContext, SourceLocation, SourceReference) schema. |
| `group_get.json` | ErrorGroup schema (name, groupId, trackingIssues, resolutionStatus). |
| `google_error.json` | Google error envelope - proves the source-level error rule. |
