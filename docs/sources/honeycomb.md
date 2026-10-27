# Honeycomb source (`honeycomb`)

Manifest: `src/omniops/sources/manifests/honeycomb.yaml` (21 tables)
Fixtures: `src/omniops/sources/fixtures/honeycomb/`
API reference root: https://docs.honeycomb.io/api/introduction (v1 REST API).

Checked against the official reference on 2026-10-27. Every path, parameter, unit and response
shape comes from the reference pages below, which are generated from Honeycomb's published
OpenAPI document (https://docs.honeycomb.io/api/openapi-public.yaml, OpenAPI 3.1, "Honeycomb API
1.0.0"). Fixtures are the documented example responses of that document where one exists; where
the docs only give a schema (triggers, signals, historical anomalies, service-map dependencies and
its `status: error` variant, query annotations, a time series with non-empty `series`), the fixture
is built strictly from the schema and its property-level `example` values, and the fixture `note`
says so. The endpoint list was cross-checked against the reference index
(https://docs.honeycomb.io/_llms/api-reference.txt, 109 pages).

## Tables -> endpoints -> reference pages

| SQL table | HTTP | Official reference |
|---|---|---|
| `honeycomb_query` | POST `/1/queries/{dataset}` -> POST `/1/query_results/{dataset}` (`disable_series: true`) -> poll GET `/1/query_results/{dataset}/{id}`; rows = `data.results[*]` | https://docs.honeycomb.io/api/queries/create-a-query , https://docs.honeycomb.io/api/query-data/create-a-query-result , https://docs.honeycomb.io/api/query-data/get-query-result |
| `honeycomb_query_series` | same 3 steps with `disable_series: false`; rows = `data.series[*]` | https://docs.honeycomb.io/api/query-data/get-query-result |
| `honeycomb_query_specs` | GET `/1/queries/{dataset}/{queryId}` | https://docs.honeycomb.io/api/queries/get-a-query |
| `honeycomb_query_annotations` | GET `/1/query_annotations/{dataset}?include_board_annotations=true` | https://docs.honeycomb.io/api/query-annotations/list-query-annotations |
| `honeycomb_datasets` | GET `/1/datasets` | https://docs.honeycomb.io/api/datasets/list-all-datasets |
| `honeycomb_columns` | GET `/1/columns/{dataset}` (`?key_name=`) | https://docs.honeycomb.io/api/columns/list-all-columns |
| `honeycomb_dataset_definitions` | GET `/1/dataset_definitions/{dataset}` | https://docs.honeycomb.io/api/dataset-definitions/get-all-dataset-definitions |
| `honeycomb_calculated_fields` | GET `/1/derived_columns/{dataset}` (`?alias=`) | https://docs.honeycomb.io/api/calculated-fields/list-all-calculated-fields |
| `honeycomb_auth` | GET `/1/auth` | https://docs.honeycomb.io/api/auth/list-authorizations |
| `honeycomb_markers` | GET `/1/markers/{dataset}` | https://docs.honeycomb.io/api/markers/list-all-markers |
| `honeycomb_triggers` | GET `/1/triggers/{dataset}` | https://docs.honeycomb.io/api/triggers/list-all-triggers |
| `honeycomb_slos` | GET `/1/slos/{dataset}` | https://docs.honeycomb.io/api/slos/get-all-slos |
| `honeycomb_slo_status` | GET `/1/slos/{dataset}/{sloId}?detailed=true` | https://docs.honeycomb.io/api/slos/get-an-slo |
| `honeycomb_slo_history` | POST `/1/reporting/slos/historical` `{ids, start_time, end_time}` | https://docs.honeycomb.io/api/reporting/get-slo-history |
| `honeycomb_slo_hourly_counts` | GET `/1/slos/{dataset}/{sloId}/counts/history?start_time=&end_time=` (epoch s; rows = `buckets[*]`) | https://docs.honeycomb.io/api/slos/get-slo-hourly-counts-history |
| `honeycomb_slo_counts` | GET `/1/slos/{dataset}/{sloId}/counts?start_time=&end_time=` (epoch s, one clock hour within the last 24 h; rows = `windows[*]`) | https://docs.honeycomb.io/api/slos/get-slo-realtime-counts |
| `honeycomb_burn_alerts` | GET `/1/burn_alerts/{dataset}?slo_id=` | https://docs.honeycomb.io/api/burn-alerts/list-all-burn-alerts-for-an-slo |
| `honeycomb_signals` | GET `/1/signals` (`service_name`, `dataset_slug`, `measured_signal`, `status`, `currently_anomalous`, `page[size]`) | https://docs.honeycomb.io/api/anomaly-detection/list-all-signals |
| `honeycomb_signal_anomalies` | GET `/1/signals/{signalId}/historical_anomalies?start_time=&end_time=` | https://docs.honeycomb.io/api/anomaly-detection/list-historical-anomalies |
| `honeycomb_service_map` | POST `/1/maps/dependencies/requests?limit=` (`{start_time, end_time, filters: [{name, type: service}]}`) -> poll GET `/1/maps/dependencies/requests/{requestId}?page[size]=100` | https://docs.honeycomb.io/api/service-maps/create-a-map-dependency-request , https://docs.honeycomb.io/api/service-maps/get-map-dependencies |
| `honeycomb_boards` | GET `/1/boards` | https://docs.honeycomb.io/api/boards/list-all-boards |

Not modelled (not useful for investigations, or write-only): events / batch / kinesis ingest,
marker settings (marker colours), board views, get-one variants already covered by list tables
(get a board / trigger / signal / burn alert / calculated field / column), triggers by recipient,
recipients (contain webhook secrets and PagerDuty integration keys), environments and API-key
management (Management-key v2 endpoints, `/1/auth` v2), create/update/delete of any resource.

Roles bound: `traces`, `trace_detail` (`honeycomb_query`), `metrics` (`honeycomb_query_series`),
`change_events` (`honeycomb_markers`), `alert_rules` (`honeycomb_triggers`), `active_alerts`
(`honeycomb_signals` with `currently_anomalous=true`), `slo` (`honeycomb_slos`; notes point at
`honeycomb_slo_status`, `honeycomb_slo_history`, `honeycomb_slo_hourly_counts`,
`honeycomb_slo_counts`, `honeycomb_burn_alerts`), `service_map`
(`honeycomb_service_map`), `services` (`honeycomb_datasets`), `dashboards` (`honeycomb_boards`).

## Query Data API (the core of the source)

Three steps, per the "Query Data" section of the reference:

1. `POST /1/queries/{dataset}` with the query spec: `calculations` [{op, column}], `breakdowns`,
   `filters` [{column, op, value}], `filter_combination` (AND|OR), `orders`, `havings`, `limit`
   (default 100, max 1000 when creating a query), `granularity` (seconds), and the time as
   `start_time` / `end_time` in **epoch seconds** (or `time_range` seconds). Creating a query does
   not run it. Response: the spec with an `id`.
2. `POST /1/query_results/{dataset}` with `{query_id, disable_series, limit}`; `limit` overrides
   the 1,000-group default up to 10,000 only when `disable_series` is true. Response 201 with an
   `id` (and a Location header).
3. `GET /1/query_results/{dataset}/{id}` until `complete: true`. **A failed query is still HTTP
   200** with `complete: true` and an `error` string instead of `data` (declared as an error rule;
   fixture `query_result_failed.json` proves it fires).

Result shape: `data.results[*].data` (summary rows) and `data.series[*].{time, data}` (graph
buckets). Keys inside `data` are the breakdown column names and the calculation names in
Honeycomb's spelling: `COUNT`, `P99(duration_ms)`, `HEATMAP(duration_ms)` (a histogram object),
or the `name` of a named calculation / formula. The engine exposes them as dynamic columns
(`count`, `p99_duration_ms`, `service_name`) and in the `data` JSON column; `value` / `calc` are
the first calculation.

Spec filters accepted by the SQL tables (`calculations`, `breakdowns`, `filter_expr`, `orders`,
`havings`) use a compact syntax that the manifest turns into the JSON spec (e.g.
`filter_expr = 'http.response.status_code >= 500 AND error = true'`); each also accepts a raw JSON
array, and `query_spec` accepts a whole JSON spec. `query_id` re-runs a saved query (e.g. a
trigger's `query_id`), skipping step 1.

Restrictions documented by Honeycomb:

- Enterprise feature. Key needs **Manage Queries and Columns** and **Run Queries**.
- Only events within the **past 7 days**; a window longer than 7 days is a 400.
- Start/end are truncated: <= 6 h windows to the minute, <= 2 d to 5 minutes, <= 7 d to 30 minutes.
- **Create Query Result: 10 requests per minute** (1/minute when the query uses relational fields
  such as `root.`, `parent.`, `child.`, `any.`); 429s from the Query Data API carry no
  `Retry-After`. A query result may not run longer than 10 seconds.
- Comparison results (`compare_time_offset_seconds`) are never returned by the API.

## Auth

- Header `X-Honeycomb-Team: <Configuration key token>` (`HONEYCOMB_API_KEY`). Configuration keys
  are environment-scoped; each table needs a permission: Run Queries + Manage Queries and Columns
  (query tables, columns, calculated fields), Create Datasets (datasets, dataset definitions),
  Manage Markers, Manage Triggers, Manage SLOs (SLOs, burn alerts, reporting), Manage Public
  Boards, Manage Signals, Read Service Maps. `honeycomb_auth` shows what the key may do.
- Ingest keys cannot read; Management keys (`Authorization: Bearer id:secret`) are only for the
  v2 team endpoints and are not used.

## Base URLs / regions

- US: `https://api.honeycomb.io` (default `HONEYCOMB_API_URL`); UI `https://ui.honeycomb.io`.
- EU: `https://api.eu1.honeycomb.io`; UI `https://ui.eu1.honeycomb.io` (`HONEYCOMB_UI_URL`).
- `{dataset}` path segment: a dataset slug, or `__all__` for environment-wide operations (not in
  Honeycomb Classic). `HONEYCOMB_DEFAULT_DATASET` (default `__all__`) is used when SQL does not
  filter `dataset`.

## Rate limits

- General v1 limit announced by `RateLimit: limit=X, remaining=Y, reset=Z` and
  `RateLimit-Policy: X;w=Y` headers (the reference's example: 100 requests / 60 s). 429 bodies are
  RFC 7807 problem details; most carry `Retry-After` as an HTTP date.
- Create Query Result: 10/min (see above). Every `honeycomb_query` / `honeycomb_query_series`
  statement costs one; the positive control re-query on an empty result costs another
  (`control_lookback: 6h`).

## Retention

- Event / span data: 60 days (Honeycomb's standard retention); the Query Data API itself only
  reads the last 7 days.
- SLO history: up to one year back (`start_time` "cannot be more than a year in the past").
- SLO realtime counts: only the last 24 hours, and one clock hour per request (counts reset at hour
  boundaries); the manifest narrows the window to the clock hour containing the window end.
  SLO hourly counts: no documented maximum window (the manifest caps it at 30 days).
- Signal historical anomalies: windows of at most 30 days.

## Time formats

- Query specs, markers (`start_time` / `end_time`), signals, historical anomalies, service-map
  requests, SLO history and SLO counts (`start_time`, `end_time`, `window_start`, `period_start`)
  all use **Unix epoch seconds** (integers).
- `created_at`, `updated_at`, `last_written_at`, `reset_at`: ISO-8601 strings.
- `data.series[*].time`: ISO-8601 bucket start.

## Pagination

- v1 list endpoints (datasets, columns, markers, triggers, SLOs, boards) return the full list in
  one response, without pagination.
- Signals, historical anomalies and map dependencies use cursor pagination: `page[size]`
  (default 20, max 100; the manifest sends 100), `page[after]`, and `links.next` (a relative URL
  with the cursor already set, `null` on the last page), followed with `next_url`. The Map
  Dependencies GET returns one page per request even if the POST asked for a larger `limit`.

## Pitfalls

- Datasets are per service in Honeycomb Environments; `__all__` queries the whole environment.
  An unknown dataset is a 404, not empty data.
- Column names are dotted and case-sensitive (`service.name`, `http.route`,
  `http.response.status_code` vs older `http.status_code`, `duration_ms`, `error`,
  `trace.trace_id`, `trace.parent_id`); filtering on a missing column matches nothing. Check
  `honeycomb_columns` / `honeycomb_dataset_definitions` first.
- Aggregates are sample-rate corrected estimates.
- No raw-event or raw-span listing API: find traces by breaking down on `trace.trace_id` and
  ordering by `MAX(duration_ms)`; follow the `url` column into the UI.
- Markers, triggers and SLO lists are per dataset; environment-wide (`__all__`) objects are listed
  separately, so query `dataset IN ('<service>', '__all__')`. Markers have no server-side time
  filter (all markers are fetched, `ts` is applied after).
- `triggers.triggered` is the current state only; there is no trigger history API. Burn alerts can
  only be listed per SLO.
- `honeycomb_slo_status` reporting fields (compliance, budget_remaining, status, burn_rate) need the
  Enterprise plan; `burn_rate` is always the last 4 hours relative to now.
- Honeycomb omits empty time buckets; `honeycomb_query_series` is zero-filled by the engine.
- SLO counts endpoints (Enterprise, feature-flagged per team): missing minutes / hours are
  unavailable data, not zero events; `is_partial` buckets are provisional; counts ignore budget
  resets.
- Service map requests can come back `status: error` with HTTP 200 (declared as an error rule,
  fixture `map_dependencies_error.json`).

## Engine gaps noted

- Per-endpoint rate limits are not declarable: Create Query Result is 10/min (1/min with relational
  fields) while other endpoints are far higher; only a source-wide `requests_per_minute` exists, so
  it is left unset and the 10/min budget is documented in pitfalls instead.
- Retry-After on Honeycomb 429s is an HTTP date; the engine only parses numeric Retry-After and
  falls back to exponential backoff.
