# Google Cloud Trace (`gcp_trace`)

Manifest: `src/omniops/sources/manifests/gcp_trace.yaml`. Fixtures: `src/omniops/sources/fixtures/gcp_trace/`.

Reference: [Cloud Trace API v1 REST](https://docs.cloud.google.com/trace/docs/reference/v1/rest) ·
[projects.traces (Trace / TraceSpan)](https://docs.cloud.google.com/trace/docs/reference/v1/rest/v1/projects.traces) ·
[Trace filter syntax](https://docs.cloud.google.com/trace/docs/trace-filters) ·
[Span labels](https://docs.cloud.google.com/trace/docs/trace-labels) ·
[Quotas and limits](https://docs.cloud.google.com/trace/docs/quotas)

The v1 API is the read API (list / get). v2 (`batchWrite`, `spans.createSpan`) and the Telemetry API (OTLP)
only write; spans written through them are readable through v1, with OpenTelemetry attribute keys kept as
labels.

## Tables

Paths are relative to `https://cloudtrace.googleapis.com`; `{p}` = `GCP_PROJECT_ID`.

| SQL table | HTTP | Official doc |
|---|---|---|
| `gcp_trace_traces` | GET `/v1/projects/{p}/traces?view=ROOTSPAN&startTime&endTime&filter&orderBy&pageSize&pageToken` - one row per trace (its root span) | https://docs.cloud.google.com/trace/docs/reference/v1/rest/v1/projects.traces/list |
| `gcp_trace_spans` | GET `/v1/projects/{p}/traces?view=COMPLETE&...` - every span of the matching traces (<= 100 traces per page) | https://docs.cloud.google.com/trace/docs/reference/v1/rest/v1/projects.traces/list |
| `gcp_trace_trace` | GET `/v1/projects/{p}/traces/{traceId}` - all spans of one trace | https://docs.cloud.google.com/trace/docs/reference/v1/rest/v1/projects.traces/get |

Roles: `traces` → `gcp_trace_traces` (query language `gcp_trace_filter`); `trace_detail` → `gcp_trace_trace`
(`parent_id` = `parent_span_id`, `message` = error label).

## Auth

`auth.type: gcp_service_account` (service-account key in `GCP_CREDENTIALS_FILE` or Application Default
Credentials; needs `omniops[gcp]`). Scope `https://www.googleapis.com/auth/trace.readonly`
(`cloud-platform` also accepted). IAM: `cloudtrace.traces.list` and `cloudtrace.traces.get` on the project
(`roles/cloudtrace.user`).

## Base URL / regions

Single global endpoint `https://cloudtrace.googleapis.com` (override `GCP_TRACE_BASE_URL`). Traces live in
the project that received them (`projectId` path parameter = project **id**). Spans are stored in the project's
`_Trace` observability bucket.

## Time formats

- `startTime` / `endTime` query parameters: RFC 3339 (`2014-10-02T15:01:23Z`, other offsets accepted), both
  **inclusive**, and they select traces by when the data was collected.
- Span `startTime` / `endTime`: RFC 3339 with nanosecond precision → `duration_ms` = end - start
  (microsecond arithmetic).
- `latency:` filter term: integer + `s` | `ms` | `ns`; no unit = **milliseconds** (`latency:500` = 500 ms). The
  manifest always sends `latency:<N>ms`.

## Identifiers

- `traceId`: 32 hex characters (example `382d4f4c6b7bb2f4a972559d9085001d`) - same as Cloud Logging `trace`
  (`projects/P/traces/<id>`) without the prefix, and as the W3C `traceparent` trace id.
- `spanId` / `parentSpanId`: **decimal uint64 strings** in v1 (example `2205310701640571284`). Cloud
  Logging `spanId` and OpenTelemetry ids are 16 hex characters: the manifest adds `span_id_hex`
  (`'%016x' % span_id`) for joins.
- Root span: no `parentSpanId` (or `0`) → `is_root`.

## Filter syntax (list `filter`)

Space-separated terms, all AND-ed, case-sensitive:
`root:PREFIX` / `+root:EXACT` (root span name), bare `PREFIX` = `root:PREFIX`, `span:PREFIX` / `+span:EXACT` (any
span), `latency:DURATION` (overall latency >=), `label:KEY` (exists), `KEY:VALUE_PREFIX`, `+KEY:EXACT`,
`method:` / `url:` shortcuts for `/http/method` / `/http/url`, `^` after the optional `+` restricts a term to
the root span (`+^url:/main`). Values outside `[a-zA-Z0-9./_][a-zA-Z0-9./_-]*` must be double-quoted.
`root`, `span`, `label`, `method`, `latency`, `url` cannot be used as label keys.
`orderBy`: one of `traceId`, `name`, `duration`, `start`, optionally followed by ` desc`.

Manifest pushdown (gcp_trace_traces): `resource` → `+root:"..."`, `root_prefix` → `root:"..."`, `span` →
`span:"..."`, `duration_ms >= N` → `latency:Nms`, `status_code` → `+/http/status_code:N`, `method` →
`+method:M`, `url_prefix` → `url:"..."`, `service` → `+service.name:"..."`, `filter` = raw terms, `order_by`.

## Span labels

Predefined v1 keys: `/http/method`, `/http/status_code`, `/http/url`, `/http/route`, `/http/path`, `/http/host`,
`/http/user_agent`, `/http/request/size`, `/http/response/size`, `/http/client_*`, `/component`,
`/error/message`, `/error/name`, `/stacktrace` (v1 only, JSON), `/agent`. Google-generated: `g.co/agent`,
`g.co/r/k8s_container/{project_id,location,cluster_name,namespace,pod_name,container_name}`, `g.co/gae/app/*`.
OpenTelemetry spans keep semantic-convention keys (`service.name`, `http.response.status_code`,
`http.request.method`, `url.full`, `exception.message`); the derived columns (`service`, `status_code`, `method`,
`url`, `error`) try both spellings.

## Pagination

`pageSize` + `pageToken` → `nextPageToken` (absent = done). Per call at most **1,000 traces** in `ROOTSPAN` /
`MINIMAL` and **100** in `COMPLETE`; GetTrace returns at most 10,000 spans. The manifest follows at most 2 pages
(traces: up to 2,000 root spans; spans: up to 200 traces).

## Rate limits

Reads share **300 quota units per 60 s per project**: `ListTraces` costs **25 units** (about 12 list calls a
minute), `GetTrace` / `ListSpans` 1 unit; Trace Explorer in the console does not count. Exceeding returns 429
`RESOURCE_EXHAUSTED` (retried with backoff). Engine: 30 requests/min, concurrency 3. Google recommends a
linked BigQuery dataset for high-volume analysis.

## Retention

Spans in the `_Trace` bucket are kept **30 days** (manifest `retention: traces/spans 30d` clamps windows).
Traces are sampled: absence of a trace is not proof a request did not happen.

## Error handling

Non-2xx `{"error": {"code", "message", "status"}}` (400 INVALID_ARGUMENT for a bad filter or orderBy, 403, 404
for an unknown trace id - accepted as 0 rows on `gcp_trace_trace`, 429). A source-level rule turns a 200 with
that body into a loud failure (fixture `google_error.json`).

## Pitfalls

- ListTraces is expensive: one well-filtered list (root name / latency / status), then `gcp_trace_trace` per
  interesting trace id.
- Label terms (`status_code`, `method`, `service`) match ANY span in the trace server-side; SQL re-applies the
  predicate on the root-span row.
- `status_code` pushes the v1 key `/http/status_code`; spans ingested with OTel keys only carry
  `http.response.status_code` - retry with `filter = '+http.response.status_code:503'` when empty.
- `gcp_trace_spans` returns every span of the selected traces; repeat span-level predicates in SQL.
- Traces become readable a few seconds to minutes after they end; a 404 on a fresh id may just be early.

## Fixture provenance

The Trace v1 reference documents only the schema (no example response).

| Fixture | Source |
|---|---|
| `traces_rootspan.json` | Schema-built ListTracesResponse (ROOTSPAN) using the reference's example traceId / spanId and predefined labels from the labels doc. |
| `traces_complete.json` | Schema-built ListTracesResponse (COMPLETE) with decimal span / parent ids. |
| `trace_get.json` | Schema-built Trace (projects.traces.get) with the reference's example ids. |
| `google_error.json` | Google error envelope - proves the source-level error rule. |
