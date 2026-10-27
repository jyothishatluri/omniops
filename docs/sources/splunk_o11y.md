# Splunk Observability Cloud (`splunk_o11y`)

Manifest: `src/omniops/sources/manifests/splunk_o11y.yaml` · fixtures: `src/omniops/sources/fixtures/splunk_o11y/`
· python escape hatch: `src/omniops/sources/python/splunk_o11y.py`

Splunk Observability Cloud (Infrastructure Monitoring, APM, Synthetic Monitoring; formerly
SignalFx). Researched against the dev.splunk.com API reference (each reference page embeds its
OpenAPI spec, including the `servers` base path and example responses, which the fixtures copy
verbatim). Re-verified 2026-10-26 by decoding the embedded specs of: incidents 3.7.1, detectors
3.10.0, retrieve_events_v2 1.0.0, metrics_metadata 3.4.0, retrieve_timeserieswindow 4.0.0,
signalflow 3.2.0, apm_service_topology 3.0.1, trace_id 1.0.0, slo 1.0.0, synthetics_tests 1.0.4,
synthetics_runs 1.0.1, dashboards 3.3.0, charts 1.0.0, audit_events 2.0.0.

## Tables

| SQL table | HTTP | Endpoint | Official reference |
|---|---|---|---|
| `splunk_o11y_incidents` | GET | `/v2/incident` (or `/v2/incident/{id}`) `?includeResolved&query&limit&offset` | [Incidents and alerts](https://dev.splunk.com/observability/reference/api/incidents/latest) |
| `splunk_o11y_muting_rules` | GET | `/v2/alertmuting?include&query&limit&offset` | [Incidents and alerts](https://dev.splunk.com/observability/reference/api/incidents/latest) |
| `splunk_o11y_detectors` | GET | `/v2/detector?name&tags&limit&offset` | [Detectors](https://dev.splunk.com/observability/reference/api/detectors/latest) |
| `splunk_o11y_detector` | GET | `/v2/detector/{id}` | [Detectors](https://dev.splunk.com/observability/reference/api/detectors/latest) |
| `splunk_o11y_detector_incidents` | GET | `/v2/detector/{id}/incidents?limit&offset` | [Detectors](https://dev.splunk.com/observability/reference/api/detectors/latest) |
| `splunk_o11y_detector_events` | GET | `/v2/detector/{id}/events?from&to&limit&offset` (ms) | [Detectors](https://dev.splunk.com/observability/reference/api/detectors/latest) |
| `splunk_o11y_events` | GET | `/v2/event/find?query&start_time&end_time&order_by&limit&offset` (ms) | [Retrieve events V2](https://dev.splunk.com/observability/reference/api/retrieve_events_v2/latest) |
| `splunk_o11y_audit_events` | GET | `/v2/audit/events?sf_eventCategory&sf_eventType&startTime&endTime&sortBy=-timestamp&limit&offset` (ms) | [Audit Events](https://dev.splunk.com/observability/reference/api/audit_events/latest) |
| `splunk_o11y_metric_timeseries` | GET | `/v2/metrictimeseries?query&limit&searchInactive` | [Metrics metadata](https://dev.splunk.com/observability/reference/api/metrics_metadata/latest) |
| `splunk_o11y_mts` | GET | `/v2/metrictimeseries/{id}` | [Metrics metadata](https://dev.splunk.com/observability/reference/api/metrics_metadata/latest) |
| `splunk_o11y_metrics` | GET | `/v2/metric?query=name:<pattern>&limit&offset` | [Metrics metadata](https://dev.splunk.com/observability/reference/api/metrics_metadata/latest) |
| `splunk_o11y_dimensions` | GET | `/v2/dimension?query&limit&offset` | [Metrics metadata](https://dev.splunk.com/observability/reference/api/metrics_metadata/latest) |
| `splunk_o11y_timeseries_window` | GET | `/v1/timeserieswindow?query&startMS&endMS&resolution` (ms) | [Retrieve metric time series (MTS)](https://dev.splunk.com/observability/reference/api/retrieve_timeserieswindow/latest) |
| `splunk_o11y_signalflow` | POST (SSE) | `https://stream.<realm>.observability.splunkcloud.com/v2/signalflow/execute?start&stop&resolution&immediate=true` (python) | [SignalFlow](https://dev.splunk.com/observability/reference/api/signalflow/latest), [stream messages](https://dev.splunk.com/observability/docs/signalflow/messages/stream_messages_specification) |
| `splunk_o11y_services` | POST | `/v2/apm/topology` body `{timeRange, tagFilters}` -> `nodes` | [APM service topology](https://dev.splunk.com/observability/reference/api/apm_service_topology/latest) |
| `splunk_o11y_service_map` | POST | `/v2/apm/topology` -> `edges` | [APM service topology](https://dev.splunk.com/observability/reference/api/apm_service_topology/latest) |
| `splunk_o11y_service_dependencies` | POST | `/v2/apm/topology/{serviceName}` | [APM service topology](https://dev.splunk.com/observability/reference/api/apm_service_topology/latest) |
| `splunk_o11y_trace` | GET | `/v2/apm/trace/{traceId}/latest` | [Download APM traces](https://dev.splunk.com/observability/reference/api/trace_id/latest) |
| `splunk_o11y_slos` | POST | `/v2/slo/search?limit&offset` body `{name, sloIds, metadata, *AlertsTriggered}` | [SLOs](https://dev.splunk.com/observability/reference/api/slo/latest) |
| `splunk_o11y_synthetic_tests` | GET | `/v2/synthetics/tests?page&perPage&search&testType&lastRunStatus&active` | [Synthetics tests](https://dev.splunk.com/observability/reference/api/synthetics_tests/latest) |
| `splunk_o11y_synthetic_runs` | GET | `/v2/synthetics/tests/{id}/runs?startTime&endTime&success&page&perPage` (ISO 8601) | [Synthetics tests](https://dev.splunk.com/observability/reference/api/synthetics_tests/latest) |
| `splunk_o11y_dashboards` | GET | `/v2/dashboard?name&chartId&limit&offset` | [Dashboards](https://dev.splunk.com/observability/reference/api/dashboards/latest) |
| `splunk_o11y_chart` | GET | `/v2/chart/{id}` | [Charts](https://dev.splunk.com/observability/reference/api/charts/latest) |
| `splunk_o11y_charts` | GET | `/v2/chart?name&tags&limit&offset` | [Charts](https://dev.splunk.com/observability/reference/api/charts/latest) |

Not covered (no public synchronous REST): APM trace *search* (only download by trace id),
Log Observer log search (Log Observer Connect reads the Splunk platform: use the `splunk` source),
RUM session search. `/v2/apm/trace/{traceId}/segments` and `/{segmentTimestamp}` exist for older
segments; only `/latest` is mapped. Teams (`/v2/team`) list notification policies, not on-call
schedules, so no `oncall` role is bound. Detector Optimization (`/detectors`, `/issues`) is a
separate newer API not needed for investigations.

SignalFlow has no JSON request/response form: `POST /v2/signalflow/execute` on the stream host
"synchronously returns the data" as a Server-Sent Events stream whose JSON payloads are
pretty-printed over several `data:` lines (stream messages specification). The declarative
engine has no SSE parser, so `splunk_o11y_signalflow` uses the python escape hatch
(`omniops.sources.python.splunk_o11y:signalflow_execute`, which also honours the source's
requests_per_minute). The documented SSE sample (`fixtures/splunk_o11y/signalflow_execute.sse.txt`)
parses with `parse_stream` into 1 row; the offline checker has no fixture harness for python tables.

## Auth

- Header `X-SF-TOKEN: <token>` (org access token with the API scope, or a user session token).
  Env: `SPLUNK_O11Y_REALM`, `SPLUNK_O11Y_TOKEN` (required), optional `SPLUNK_O11Y_API_URL`,
  `SPLUNK_O11Y_STREAM_URL`.
  [Authentication basics](https://dev.splunk.com/observability/docs/apibasics/authentication_basics)
- Role requirements: SignalFlow `/execute` and `POST /slo/search` need admin or power;
  incidents, detectors, events, metadata, APM topology/traces, synthetics accept read_only.

## Base URLs and realms

- API: `https://api.<realm>.observability.splunkcloud.com` (legacy `https://api.<realm>.signalfx.com`).
  Paths: `/v2/...` for most APIs, `/v1/timeserieswindow`, `/v2/synthetics/...`.
- SignalFlow stream: `https://stream.<realm>.observability.splunkcloud.com/v2/signalflow`.
- UI: `https://app.<realm>.observability.splunkcloud.com`.
- Realms: us0, us1, us2, eu0, eu1, eu2, jp0, au0, sg0 ... A wrong realm is HTTP 401.

## Limits

- Retrieve operations return at most 10,000 objects per query (incidents, events, metadata,
  muting rules), regardless of offset ([Considerations for retrieve operations](https://dev.splunk.com/observability/docs/apibasics/retrieve_data_basics)).
- `/v2/event/find`: `limit` max 9,999 (default 1000); asking past 10,000 is HTTP 500.
- APM topology: at most 1,000 objects; `timeRange` must be >= 5 minutes and <= trace retention.
- Org-level rate limiting answers HTTP 429. The only published number is the APM API limit of
  100 requests/min per org ([APM system limits](https://help.splunk.com/en/splunk-observability-cloud/administer/org-reference-info/per-product-system-limits-in-splunk-observability-cloud/apm-system-limits));
  the manifest applies `requests_per_minute: 100` to the whole source (the engine enforces rpm per source).
- `/v2/audit/events`: `offset + limit <= 10,000`; needs the READ_AUDIT_EVENTS capability.

## Retention

([Data retention](https://help.splunk.com/en/splunk-observability-cloud/administer/org-reference-info/data-retention-in-splunk-observability-cloud))
- Metrics: 1-minute-or-coarser resolution 13 months; finer resolution 8 days (Standard) /
  96 days (Enterprise). `/v1/timeserieswindow` serves 1 s, 1 m, 5 m, 1 h rollups.
- APM raw traces: 8 days default (traces of interest extended up to 13 months).

## Time formats

| Endpoint | Unit |
|---|---|
| detector events `from`/`to`, events `start_time`/`end_time`, timeserieswindow `startMS`/`endMS`, SignalFlow `start`/`stop` | epoch **milliseconds** |
| APM topology `timeRange` | `"<start>/<end>"` ISO 8601 or epoch ms; documented constraint `5 minutes < (end - start) < span retention` (manifest sends ms, start widened to end - 6 min, window clamped to 7 d) |
| audit events `startTime`/`endTime` | epoch **milliseconds** (default last 7 days) |
| synthetics runs `startTime`/`endTime` | ISO 8601 |
| response timestamps (`timestamp`, `created`, `lastUpdated`, `startTime`/`stopTime`) | epoch ms; spans `startTime` ISO 8601 (microseconds) |
| `resolution` | ms: 1000, 60000, 300000, 3600000 |

## Discrepancies found between the reference and the vendor SDK

- `GET /v2/metric`: reference example and schema name the array `result`; the vendor Go SDK
  (`signalfx-go`, `RetrieveMetricMetadataResponseModel`) reads `results`. The manifest uses
  `rows_path: $.*[*]` which accepts both (the other top-level key, `count`, is scalar).
- `GET /v2/incident`: the reference schema lacks top-level `detectorName`, `isMuted`,
  `triggeredWhileMuted`, `triggeredNotificationSent`, `inputs` that the SDK's `Incident`
  model reads. Columns for them resolve to null when absent; `detector_name` falls back to the
  first event's `detectorName`.
- `GET /v2/metrictimeseries`: neither reference nor SDK lists an `id` field on MTS objects; the
  `id` column is nullable, and `splunk_o11y_mts` resolves tsIds explicitly.
- `GET /v1/timeserieswindow`: the parameter list says `startMs`, the description says `startMS`
  (and `endMS`); the manifest sends both start spellings.
- `POST /v2/apm/topology`: the documented example has top-level `nodes`/`edges`. If a deployment
  returns them wrapped in `data`, an error rule fails loudly with the fix instead of returning 0 rows.
- `events[].inputs`: documented as a list of `{dimensions, fragment, value}` in the incidents
  reference, but as an object keyed by stream label (`{"A": 5, "B": 6}`) in the detector events
  example, and `signalfx-go` models it as `*map[string]interface{}`. The `value` / `dimensions`
  columns accept both forms (fixture `incidents_inputs_object.json` checks the object form).
- `POST /v2/slo/search`: the body schema has no required property, so an unfiltered search
  sends `{}`. Whether several `*AlertsTriggered` flags combine with AND or OR is not documented,
  so the manifest sends at most one (`alert_type` filter, IN fans out one search per kind).
- `GET /v2/chart` list example omits `programText`/`tags` that the single-chart example has.
- `/v1/timeserieswindow` 404 is documented as "Requested data not found. The API returns this
  status code if your query returned no results" (mapped to 0 rows). Its 200 schema says
  `errors` "is always empty"; the documented error body `{code, message, type: "error"}` is also
  guarded by an error rule (fixture `timeserieswindow_error.json`).

## Pitfalls (also encoded in the manifest)

1. Incidents are active-only by default (`include_resolved = true` for cleared ones) and list
   only the latest incident per detector rule + series; history lives in detector events.
2. Search syntax: `<name>:<value>`, AND/OR/NOT, `*` wildcards, quote non-alphanumeric values;
   built-in event fields use the `sf_` prefix (`sf_eventType`, `sf_eventCategory`).
3. Detector/dashboard/SLO `name` filters are server-side substring matches and are not
   re-checked in SQL; use `title = '<exact>'` to pin one.
4. `/v1/timeserieswindow` returns HTTP 404 when nothing matches (mapped to 0 rows), only the
   default rollup and only tsIds (resolve with `splunk_o11y_mts`).
5. MTS search only returns active MTS unless `search_inactive = true` (a crashed host vanishes).
6. SignalFlow output needs `publish()`; the SSE `data:` payload spans several lines.
7. Detector `status` other than ACTIVE or `overMTSLimit = true` means it may not have evaluated.
8. Audit events are oldest-first by default; the manifest sends `sortBy=-timestamp` so truncation
   drops the oldest changes, and one category per request (CUSTOMER_AUDIT default, AUDIT for raw
   API/UI requests).
9. Chart programs omit the dashboard's filters/variables, which the UI applies on top; add them
   when reproducing a chart with SignalFlow.
