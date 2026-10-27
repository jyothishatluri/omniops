# Sumo Logic (`sumologic`)

Manifest: `src/omniops/sources/manifests/sumologic.yaml` · fixtures: `src/omniops/sources/fixtures/sumologic/`

Direct HTTPS against the deployment-specific API host. No MCP, no SDK. Log search uses the
OpenAPI-based **Search Job Management API** (`/api/v2/search/jobs`), which Sumo recommends for
new integrations: every call is addressed by `jobId` alone, so no session cookies are needed
(the legacy `/api/v1/search/jobs` flow loses the job with a 404 when cookies are not replayed).
Verified 2026-10-27 against the OpenAPI spec (`https://api.sumologic.com/docs/sumologic-api.yaml`,
tag `searchJobManagement`: `createSearchJob`, `getSearchJobStatus`,
`getSearchJobPaginatedMessages`, `getSearchJobPaginatedRecords`, schemas `CreateJobRequest`,
`SearchJobStatusResponse`, `SearchQueryPaginatedMessages`, `SearchQueryPaginatedRecords`).

## Tables

| SQL table | HTTP | Endpoint (under `https://api.<pod>.sumologic.com/api`) | Official reference |
|---|---|---|---|
| `sumologic_search_messages` | POST, GET (poll), GET (paged) | `/v2/search/jobs` -> `/v2/search/jobs/{jobId}` -> `/v2/search/jobs/{jobId}/messages?offset=&limit=` | [Search Job APIs](https://www.sumologic.com/help/docs/api/search-job/), [API reference: searchJobManagement](https://api.sumologic.com/docs/#tag/searchJobManagement) |
| `sumologic_search_records` | POST, GET (poll), GET (paged) | `/v2/search/jobs` (`requiresRawMessages: "false"`) -> `/v2/search/jobs/{jobId}` -> `/v2/search/jobs/{jobId}/records?offset=&limit=` | same |
| `sumologic_alert_events` | POST, GET, GET | search job over `_index=sumologic_system_events _sourceCategory=alerts` (messages; `_raw` is the JSON event) | [System Event Index](https://www.sumologic.com/help/docs/manage/security/audit-indexes/system-event-index/) + Search Job APIs |
| `sumologic_metrics_query` | POST | `/v1/metricsQueries` | [Metrics Query API](https://www.sumologic.com/help/docs/api/metrics-query/), [API reference: metricsQuery](https://api.sumologic.com/docs/#tag/metricsQuery) |
| `sumologic_monitors` | GET | `/v1/monitors/search?query=&offset=&limit=` | [Monitor Management APIs](https://www.sumologic.com/help/docs/api/monitors-management/), [API reference: monitorsLibraryManagement](https://api.sumologic.com/docs/#tag/monitorsLibraryManagement) |
| `sumologic_monitor` | GET | `/v1/monitors/{id}` | same |
| `sumologic_health_events` | GET | `/v1/healthEvents?limit=&token=` | [Health Events APIs](https://www.sumologic.com/help/docs/api/health-events/), [API reference: healthEvents](https://api.sumologic.com/docs/#tag/healthEvents) |

### Search Job Management API details (OpenAPI)

- `POST /v2/search/jobs` body `CreateJobRequest`: required `query` (<= 15000 chars), `from`, `to`
  (strings, `YYYY-MM-DDTHH:MM:SS`, <= 24 chars, interpreted in `timezone`), `timezone`
  (default `UTC`); optional `autoParsingMode` (`Manual` default | `AutoParse`),
  `requiresRawMessages` (**string** `"true"|"false"`, default `"false"`), `intervalTimeType`
  (`messageTime` default | `receiptTime` | `searchableTime`), `maxRawRecords`, `childOrgIds`,
  `includeAllChildOrgs`. Response **202** `CreateJobResponse` with `id`, `isAggregation`, `runnableQuery`, ...
- `GET /v2/search/jobs/{jobId}` -> `SearchJobStatusResponse`: `state` (`NOT STARTED`,
  `GATHERING RESULTS`, `GATHERING RESULTS FROM SUBQUERIES`, `FORCE PAUSED`,
  `DONE GATHERING RESULTS`, `DONE GATHERING HISTOGRAM`, `CANCELLED`), `messageCount`,
  `recordCount`, `pendingErrors`, `pendingWarnings`, `histogramBuckets`, `usageDetails`.
  The manifest polls every 2 s until a DONE/FORCE PAUSED state and fails on `CANCELLED` or any
  `pendingErrors`.
- `GET .../messages` and `.../records`: `offset`, `limit` (max 10,000; messages also capped at
  100 MB per page). Body: `{warning, fields[{name, fieldType, keyField}], messages[{map}]}` /
  `{..., records[{map}]}`; every map value is a string, `_messagetime`, `_receipttime`,
  `_timeslice` are epoch-millisecond strings.
- `DELETE /v2/search/jobs/{jobId}` exists; the engine cannot issue it after reading, so jobs
  expire on their own (see pitfalls).

## Auth

HTTP basic auth with an access ID / access key pair (`SUMOLOGIC_ACCESS_ID`,
`SUMOLOGIC_ACCESS_KEY`), created under Administration > Security > Access Keys. Keys inherit
the creating user's role capabilities (or a custom scope): the search job API needs log search,
monitors need alerting read, metrics need metrics read. Search results are restricted by the
role's search filter: data the role cannot see returns 0 rows, not 403.

## Base URLs / deployments

| Deployment | API endpoint |
|---|---|
| US1 | `https://api.sumologic.com/api/` |
| US2 | `https://api.us2.sumologic.com/api/` |
| AU / CA / CH / DE / ESC / EU / FED / JP / KR | `https://api.<pod>.sumologic.com/api/` |

Source: [API getting started - endpoints by deployment](https://www.sumologic.com/help/docs/api/about-apis/getting-started/).
`SUMOLOGIC_DEPLOYMENT` selects the host (default `us1`); `SUMOLOGIC_API_URL` overrides it. A
call to the wrong deployment answers HTTP 301 (browsers/collectors are redirected, API clients
are not). The UI deep links use `https://service.<pod>.sumologic.com`.

## Rate limits

- 4 API requests per second (240/min) per user across all APIs; 10 concurrent requests per
  access key per endpoint; HTTP 429 `rate.limit.exceeded` otherwise.
- Search Job API: at most 200 active concurrent search jobs per organization (UI, scheduled and
  dashboard searches do not count), 429 beyond that.
- One log table query costs 1 create + N polls + result pages, so the manifest caps the source at
  200 requests/min (engine-enforced sliding window) and concurrency 4.

## Retention / lifetimes

- Log retention is per partition (account specific). The System Event Index follows the
  Default Partition retention unless its partition is edited.
- A search job is cancelled if it is not polled or read for ~20-30 s (later calls 404) and
  times out after 8 h regardless.
- Raw-message jobs stop at 100,000 messages (`FORCE PAUSED`); aggregate in the query.
- Metrics retention is account specific.

## Time formats

| Where | Format |
|---|---|
| Search job `from`/`to` | `YYYY-MM-DDTHH:MM:SS` local to `timezone` (manifest sends UTC + `timezone: UTC`) |
| `_messagetime`, `_receipttime`, `_timeslice` in results | epoch milliseconds as strings |
| Metrics `timeRange` | `EpochTimeRangeBoundary.epochMillis` (ms); `quantization` and `timeshift` in ms |
| Metrics points | `points.timestamps` epoch ms |
| Monitors / health events | ISO 8601 (`createdAt`, `modifiedAt`, `eventTime`) |
| System event `eventTime` | ISO 8601 inside the JSON `_raw` |

## Pitfalls

- The Search Job API is only available on Enterprise-level plans (Enterprise Operations /
  Security / Suite, Trial, Cloud Flex Legacy Enterprise); other plans answer 403.
- `requiresRawMessages` is a string in the OpenAPI schema (`"false"`), not a JSON boolean.
- A query without an aggregation operator has no records (`/records` answers 400
  `not.an.aggregation.query`); use the messages table.
- `timeslice` + `count` omits empty buckets: append `| fillmissing timeslice(1m)`.
- JSON fields are only extracted when the query references them with `AutoParse` (manifest
  default) or `| json`; otherwise `level` / `service` / `trace_id` are null.
- `_sourceCategory` naming is a per-deployment convention; look at raw messages before
  filtering on a guessed value. Wildcards (`*checkout*`) work.
- Metrics API query strings reject `fillmissing`, `outlier`, `quantize` and `timeshift`
  operators: use `quantization_ms`, `rollup`, `timeshift_ms`. Partial results are reported in
  the body's `errors` object with HTTP 200 (surfaced in the `warnings` column).
- Monitor search returns folders too (`content_type = 'Folder'`); `monitorStatus:AllTriggered`
  lists monitors with any triggered condition.
- Health events list only unresolved events.
- Alert history comes from the System Event Index (enabled by default only on Enterprise);
  creation, update and resolution are separate events.
