# Grafana Tempo (`tempo`)

Template: `src/omniops/sources/templates/tempo.yaml` (source `tempo`), reused by
`manifests/tempo.yaml` (direct Tempo) and `manifests/grafana_tempo.yaml` (through Grafana's
datasource proxy, see [grafana_tempo.md](grafana_tempo.md)).
Fixtures: `src/omniops/sources/fixtures/tempo/` (`grafana_tempo` falls back to them).

Reference: [Tempo HTTP API](https://grafana.com/docs/tempo/latest/api_docs/), checked against
the docs source on `main` (October 2026, Tempo 3.x; `docs/sources/tempo/api_docs/_index.md`).
Where the docs give no response example (`/api/v2/traces`, `/api/metrics/*`) the shapes come
from [`pkg/tempopb/tempo.proto`](https://github.com/grafana/tempo/blob/main/pkg/tempopb/tempo.proto)
as the query frontend writes them with gogo `jsonpb` (lowerCamelCase fields, 64-bit integers
as strings, enum names, `bytes` as base64, zero values omitted), and from the payloads the
o11y-bench grading code parses (`vendor/o11y-bench/grading/env_context.py`, Tempo 3.1).

## Tables

| SQL table | HTTP | Official reference |
|---|---|---|
| `tempo_search` | `GET /api/search?q=<TraceQL>&start=<s>&end=<s>&limit=&spss=` (one row per trace) | [Search](https://grafana.com/docs/tempo/latest/api_docs/#search) |
| `tempo_search_spans` | `GET /api/search` with `\| select(...)` appended (one row per matched span) | [Search](https://grafana.com/docs/tempo/latest/api_docs/#search), [TraceQL select](https://grafana.com/docs/tempo/latest/traceql/construct-traceql-queries/#selection) |
| `tempo_trace` | `GET /api/v2/traces/<traceid>[?q=<spanset filter>&keep_hierarchy=&match_depth=]` (one row per span) | [Query V2](https://grafana.com/docs/tempo/latest/api_docs/#query-v2) |
| `tempo_trace_attributes` | `GET /api/v2/traces/<traceid>` (one row per span attribute) | [Query V2](https://grafana.com/docs/tempo/latest/api_docs/#query-v2) |
| `tempo_metrics_range` | `GET /api/metrics/query_range?q=&start=&end=&step=<n>s` | [TraceQL Metrics](https://grafana.com/docs/tempo/latest/api_docs/#traceql-metrics) |
| `tempo_metrics_instant` | `GET /api/metrics/query?q=&start=&end=` | [Instant](https://grafana.com/docs/tempo/latest/api_docs/#instant) |
| `tempo_metrics_exemplars` | `GET /api/metrics/query_range?...&exemplars=<n>` (exemplars of each series) | [TraceQL Metrics](https://grafana.com/docs/tempo/latest/api_docs/#traceql-metrics) |
| `tempo_tag_names` | `GET /api/v2/search/tags?scope=&q=&start=&end=&limit=` | [Search tags V2](https://grafana.com/docs/tempo/latest/api_docs/#search-tags-v2) |
| `tempo_tag_values` | `GET /api/v2/search/tag/<tag>/values?q=&start=&end=&limit=` | [Search tag values V2](https://grafana.com/docs/tempo/latest/api_docs/#search-tag-values-v2) |

Pushdown into TraceQL (`tempo_search`, `tempo_search_spans`): `service` -> `resource.service.name`,
`name`, `status`, `kind`, `http_route` -> `span.http.route`, `root_service` -> `trace:rootService`,
`root_name` -> `trace:rootName`, `trace_id` -> `trace:id`, `duration_ms` -> `traceDuration`
(trace table) or `duration` (span table) in microseconds. With an explicit `query` they are
appended as a pipeline stage `| { ... }`; a trailing `with (...)` hint is kept last;
`most_recent = true` adds `with (most_recent=true)`.

Roles bound by the template: `traces` -> `tempo_search_spans` (TraceQL), `trace_detail` ->
`tempo_trace`, `services` -> `tempo_tag_values` (tag `resource.service.name`).

Not modelled on purpose: `/api/traces/<id>` (v1; v2 is the current one), `/api/search/tags` and
`/api/search/tag/<tag>/values` (v1; v2 adds scopes and types), tags-based search
(`tags=`/`minDuration`/`maxDuration`, superseded by TraceQL), `POST /api/v2/traces/diff`
(experimental, response schema not documented), the experimental `span_pruning*` parameters,
`Accept: application/vnd.grafana.llm` (documented as unstable), the built-in MCP server
(OmniOps uses REST only), ingest, ring, overrides, usage and admin endpoints. Tempo has no
service-dependency API: the service graph is produced by the metrics-generator
(`traces_service_graph_request_total`) and lives in Prometheus/Mimir when remote write is
configured (it is not in o11y-bench: `remote_write: []`).

## Auth

The Tempo API has no auth of its own:

| Deployment | What `manifests/tempo.yaml` sends |
|---|---|
| Self-hosted single tenant (`multitenancy_enabled: false`, o11y-bench) | nothing |
| Self-hosted multi-tenant | `X-Scope-OrgID: $TEMPO_TENANT_ID` (several tenants: `t1\|t2`) |
| Grafana Enterprise Traces | Basic auth: tenant / access policy token (`TEMPO_USERNAME`, `TEMPO_PASSWORD`) |
| Grafana Cloud Traces | Basic auth: numeric Tempo instance ID / Cloud Access Policy token with `traces:read` |
| Reverse proxy with a bearer token | `Authorization: Bearer $TEMPO_TOKEN` |

## Base URLs

`TEMPO_URL` = query-frontend root without `/api`: `http://tempo:3200` (o11y-bench:
`http://localhost:3200`, Tempo 3.1). Grafana Cloud: the Tempo URL from the stack details,
`https://tempo-prod-NN-<region>.grafana.net/tempo`.

## Limits (Tempo defaults, [configuration](https://grafana.com/docs/tempo/latest/configuration/))

| Limit | Default | Effect here |
|---|---|---|
| `query_frontend.search.max_duration` | 168h | `max_window: 168h` on search/tag tables (o11y-bench: 72h) |
| `query_frontend.metrics.max_duration` | 24h | `max_window: 24h` on metrics tables (o11y-bench: 72h) |
| `query_frontend.search.default_result_limit` | 20 | tables send `limit` (default 100, or the SQL LIMIT) |
| `query_frontend.search.max_result_limit` | 262144 | |
| `query_frontend.search.default_spans_per_span_set` / `max_spans_per_span_set` | 3 / 100 | `spss` (10 for `tempo_search_spans`) |
| `query_frontend.metrics.max_exemplars` | 100 | `exemplars` capped |
| `query_frontend.metrics.query_backend_after` | 15m | recent data comes from the live store |
| `query_frontend.query_end_cutoff` | 30s | the last 30 s before "now" are not searched |
| `overrides ... max_bytes_per_trace` | 5 MB | bigger traces come back `PARTIAL` (o11y-bench: 50 MB) |
| `overrides ... max_bytes_per_tag_values_query` | 1 MB | tag discovery can be incomplete |

No request-rate limit on reads. Grafana Cloud applies per-tenant query limits on top.

## Retention

Self-hosted: compactor `block_retention` default **336h (14 days)**. Grafana Cloud Traces:
14 days (free) / 30 days (paid) by default. The template sets no retention clamp because it is
deployment-specific; an empty window older than retention is "gone", not "absent".

## Time formats

- `/api/search`, `/api/v2/search/tags`, `/api/v2/search/tag/*/values`, `/api/v2/traces` (`start`/`end`):
  **Unix epoch seconds** (`time.start | epoch_s`). Without `start`/`end` Tempo searches only the
  live store (recent data), so the engine always sends them.
- `/api/metrics/*`: `start`/`end` accept seconds, nanoseconds or RFC3339 (seconds sent);
  `step` is a duration string (`"60s"`).
- TraceQL durations are literals (`duration > 300ms`); pushdown sends microseconds (`>= 300000us`).
- Responses: `startTimeUnixNano` / `durationNanos` (ns, strings) and `durationMs` (ms) in search;
  OTLP `startTimeUnixNano`/`endTimeUnixNano` (ns strings) in traces; `timestampMs` (ms strings) in
  metrics. TraceQL metrics duration values (`quantile_over_time(duration, ...)`) are **seconds**.

## Pitfalls

- Search returns the *first N traces found* (not newest, not all): aggregates over the search
  tables are samples. Use `tempo_metrics_instant` / `tempo_metrics_range` for exact counts, rates
  and percentiles; `most_recent = true` for newest-first examples.
- Unknown attributes/values match nothing (0 rows, no error). Check `tempo_tag_names` /
  `tempo_tag_values`. Scope matters: `resource.service.name`, not `span.service.name`.
- OpenTelemetry semantic-convention drift: `http.method`/`http.status_code`/`http.url` (old) vs
  `http.request.method`/`http.response.status_code`/`url.full` (new). Columns accept both;
  TraceQL needs the one actually emitted.
- Tempo 3.x defaults to parquet dedicated columns for common attributes; o11y-bench disables them
  because tag discovery then misses `http.route`/`http.status_code` (they still work in TraceQL).
- Trace ids in search results have leading zeros trimmed; `tempo_trace` accepts either form.
- `/api/v2/traces` returns base64 span ids (`bytes` in jsonpb); `tempo_trace` decodes them to hex
  so they join with `tempo_search_spans.span_id` and Pyroscope span selectors.
- Search-span `kind`/`status` come back as intrinsic attributes (`kind`, `status`) with
  `stringValue`/enum values; the `select(...)` that `tempo_search_spans` appends is what makes
  `service`, `kind`, `status` and the HTTP attributes present on every row.
- Edge spans (storefront-web, edge-proxy) repeat the downstream error/latency: blame the deepest error
  span or the span with the most self time (examples in the template).
- HTTP 400 `invalid TraceQL query` = syntax error; 400 about the range = window longer than
  `max_duration`. `tempo_trace` `span_filter` rejects pipelines, structural operators and
  trace-level intrinsics (400).
- `tempo_trace` with a missing trace -> HTTP 404 -> 0 rows (accepted status).
- jsonpb omits zero values: `errorCount`, `value: 0` and `status: COMPLETE` are absent; the
  columns default them.
