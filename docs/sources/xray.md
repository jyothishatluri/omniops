# AWS X-Ray — source `xray`

Manifest: `src/omniops/sources/manifests/xray.yaml` · Fixtures: `src/omniops/sources/fixtures/xray/`
API reference: https://docs.aws.amazon.com/xray/latest/api/Welcome.html

## Tables

| SQL table | HTTP request (REST-JSON) | Official reference |
|---|---|---|
| `xray_trace_summaries` | `POST /TraceSummaries` | https://docs.aws.amazon.com/xray/latest/api/API_GetTraceSummaries.html |
| `xray_trace_segments` | `POST /Traces` (BatchGetTraces); segment documents parsed (`expand parse: true`) | https://docs.aws.amazon.com/xray/latest/api/API_BatchGetTraces.html · https://docs.aws.amazon.com/xray/latest/devguide/xray-api-segmentdocuments.html |
| `xray_trace_subsegments` | `POST /Traces`; first-level `subsegments` of each parsed segment | same as above |
| `xray_service_graph` (nodes) | `POST /ServiceGraph` | https://docs.aws.amazon.com/xray/latest/api/API_GetServiceGraph.html |
| `xray_service_edges` (edges) | `POST /ServiceGraph` | https://docs.aws.amazon.com/xray/latest/api/API_GetServiceGraph.html |
| `xray_service_statistics` | `POST /TimeSeriesServiceStatistics` | https://docs.aws.amazon.com/xray/latest/api/API_GetTimeSeriesServiceStatistics.html |
| `xray_insights` | `POST /InsightSummaries` | https://docs.aws.amazon.com/xray/latest/api/API_GetInsightSummaries.html |
| `xray_groups` | `POST /Groups` | https://docs.aws.amazon.com/xray/latest/api/API_GetGroups.html |
| `xray_retrieved_traces` | `POST /StartTraceRetrieval` (capture `RetrievalToken`) → `POST /ListRetrievedTraces` polled until `RetrievalStatus = COMPLETE` | https://docs.aws.amazon.com/xray/latest/api/API_StartTraceRetrieval.html · https://docs.aws.amazon.com/xray/latest/api/API_ListRetrievedTraces.html |

Roles bound: `traces` → `xray_trace_summaries`; `trace_detail` → `xray_trace_segments` (now with
canonical `service`, `ts`, `duration_ms`, `status_code`, `parent_id`); `service_map` →
`xray_service_edges`; `services` → `xray_service_graph`.

## Protocol and auth

REST-JSON (service model protocol `rest-json`, API 2016-04-12): each operation is a `POST` to its own
path with `Content-Type: application/json` and a JSON body; no `X-Amz-Target`. SigV4 signing name
`xray`; credentials from the AWS chain (`omniops[aws]`). Errors: `InvalidRequestException` 400,
`ThrottledException` **429** (retried), `ResourceNotFoundException` 404 (trace retrieval).

## Base URLs / regions

`https://xray.<region>.amazonaws.com` (FIPS `xray-fips.<region>.amazonaws.com` in US and GovCloud
regions; China `.amazonaws.com.cn`); override with `AWS_ENDPOINT_URL_XRAY`.
Endpoint list: https://docs.aws.amazon.com/general/latest/gr/xray.html

## Limits / quotas

Trace and service-graph retention **30 days**; GetInsightSummaries start/end can't be more than 30
days old; segment document 64 KB; 50 indexed annotations per trace. BatchGetTraces: ≤ 5 trace ids
per call (the manifest sends one per request, fan-out ≤ 20). StartTraceRetrieval: ≤ 100 trace ids;
retrievals time out after 60 minutes. ListRetrievedTraces returns ≤ 5 traces per page and an empty
body until `RetrievalStatus` is `COMPLETE`. GetInsightSummaries `MaxResults` ≤ 100, `States` holds
one value. The API reference documents no maximum window for GetTraceSummaries / GetServiceGraph;
the manifest caps them at 24 h (`max_window`).

## Time formats

- Requests: `StartTime`/`EndTime` are **epoch seconds** ("in Unix time seconds").
- Responses: timestamps are epoch seconds with fractions (`"StartTime": 1528317567.0`); segment
  documents carry `start_time`/`end_time` as float epoch seconds (`1.499473411677E9`).
- `Duration`, `ResponseTime`, histogram `Value`s and filter-expression `duration`/`responsetime` are
  **seconds**; the manifest exposes `*_ms` columns.
- Trace id `1-5759e988-bd862e3fe1be46a994272793`: the 8 hex digits after `1-` are the trace start in
  epoch seconds — used as a fallback `ts` (the docs example summaries have no `StartTime`). W3C ids
  converted to X-Ray format need not carry a real timestamp.

## Pitfalls

- Traces are **sampled** (default: first request per second plus 5% per host): never derive totals or
  rates from row counts; use CloudWatch metrics for volume.
- With CloudWatch **Transaction Search** enabled, all spans are stored in the `aws/spans` log group and
  only a configurable share (default 1%) is indexed as X-Ray trace summaries; BatchGetTraces "cannot
  find traces ... if Transaction Search is enabled" → use `xray_retrieved_traces` for known ids and
  `cloudwatch_logs_insights` on `aws/spans` for search.
- `service("x")` matches traces passing through x anywhere; `service("x") { fault }` restricts to x's own faults.
- GetTraceSummaries returns newest results first; the manifest stops after 5 pages (truncation note) —
  keep windows short and add a filter expression.
- Segment documents arrive as JSON strings; the manifest parses them. Subsegments are nested; only the
  first level becomes rows in `xray_trace_subsegments` (deeper levels in `child_subsegments`).
- `UnprocessedTraceIds` (expired/unknown ids) is a loud error, not an empty trace (fixture-tested).
- Service-graph counts are sampled; `client`/remote nodes have no statistics; edge stats are measured
  by the caller; async (`link`) edges carry `ReceivedEventAgeHistogram` instead of response times.
- X-Ray Insights only exist for groups with Insights enabled (`xray_groups.insights_enabled`), and
  GetInsightSummaries returns insights that **started** inside the window.

## Fixtures

| File | Provenance |
|---|---|
| `get_trace_summaries.json` | "Example GetTraceSummaries output", X-Ray developer guide, https://docs.aws.amazon.com/xray/latest/devguide/xray-api-gettingdata.html |
| `get_trace_summaries_fault.json` | Built strictly from the GetTraceSummaries schema (StartTime, EntryPoint, FaultRootCauses with EntityPath exceptions) |
| `batch_get_traces.json` | "Example BatchGetTraces output", same page (used by segments and subsegments tables) |
| `batch_get_traces_unprocessed.json` | Built from the BatchGetTraces schema with `UnprocessedTraceIds` (expect_error) |
| `get_service_graph.json` | "Example GetServiceGraph output", same page (used by nodes and edges tables) |
| `get_time_series_service_statistics.json` | Built strictly from the GetTimeSeriesServiceStatistics response schema (no documented example) |
| `get_insight_summaries.json` | Built strictly from the GetInsightSummaries response schema (no documented example) |
| `get_groups.json` | Built strictly from the GetGroups response schema (no documented example) |
| `list_retrieved_traces.json` | Built strictly from the ListRetrievedTraces schema; span documents copied from the guide's BatchGetTraces example |
