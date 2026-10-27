# Jaeger (`jaeger`)

Manifest: `src/omniops/sources/manifests/jaeger.yaml`. Fixtures: `src/omniops/sources/fixtures/jaeger/`.

References:
- [Jaeger APIs](https://www.jaegertracing.io/docs/latest/architecture/apis/) (docs for Jaeger
  **2.22**, the latest release, 2026-10-06): "Query JSON over HTTP" on port 16686 (`/api/v3/*`,
  status **stable**) is the JSON/HTTP form of `api_v3/query_service.proto`, specified by the
  [OpenAPI file](https://github.com/jaegertracing/jaeger-idl/blob/main/swagger/api_v3/query_service.openapi.yaml)
  the docs link to. `/api/*` (the UI's API) is **internal** ("intentionally undocumented and
  subject to change"); only `/api/dependencies` is used here because api_v3 in 2.22 has no
  dependencies route.
- Parameter names checked against the v2.22.0 source
  (`cmd/jaeger/internal/extension/jaegerquery/internal/apiv3/query_parser.go`,
  `http_gateway.go`): camelCase names (`query.serviceName` ...) are canonical; the snake_case
  names (`query.service_name`, `query.start_time_min`, `query.num_traces`, `span_kind`,
  `raw_traces` ...) are kept as deprecated aliases. Times are parsed with RFC 3339 (nano),
  durations with Go `time.ParseDuration`, `query.attributes` as a JSON string map.

## Tables

| SQL table | HTTP | Official reference |
|---|---|---|
| `jaeger_services` | `GET /api/v3/services` | [OpenAPI `QueryService_GetServices`](https://github.com/jaegertracing/jaeger-idl/blob/main/swagger/api_v3/query_service.openapi.yaml) |
| `jaeger_operations` | `GET /api/v3/operations?service=&spanKind=` | [OpenAPI `QueryService_GetOperations`](https://github.com/jaegertracing/jaeger-idl/blob/main/swagger/api_v3/query_service.openapi.yaml) |
| `jaeger_traces` | `GET /api/v3/traces?query.serviceName=&query.operationName=&query.startTimeMin=&query.startTimeMax=&query.durationMin=&query.durationMax=&query.searchDepth=&query.attributes=` (OTLP, one row per span) | [OpenAPI `QueryService_FindTraces`](https://github.com/jaegertracing/jaeger-idl/blob/main/swagger/api_v3/query_service.openapi.yaml) |
| `jaeger_trace_summaries` | `GET /api/v3/trace-summaries` (same parameters; one row per trace; Jaeger 2.19+) | [OpenAPI `QueryService_FindTraceSummaries`](https://github.com/jaegertracing/jaeger-idl/blob/main/swagger/api_v3/query_service.openapi.yaml) |
| `jaeger_trace` | `GET /api/v3/traces/{traceId}[?rawTraces=true]` (OTLP, one row per span) | [OpenAPI `QueryService_GetTrace`](https://github.com/jaegertracing/jaeger-idl/blob/main/swagger/api_v3/query_service.openapi.yaml) |
| `jaeger_dependencies` | `GET /api/dependencies?endTs=<ms>&lookback=<ms>[&service=]` (internal) | [Service dependencies graph](https://www.jaegertracing.io/docs/latest/architecture/apis/#service-dependencies-graph) |

Roles: `traces` -> `jaeger_traces`, `trace_detail` -> `jaeger_trace`, `services` ->
`jaeger_services`, `service_map` -> `jaeger_dependencies`.

Not modelled on purpose: `GET/POST /api/v3/spans` (FindSpans) and `query.filter` /
`query.orderBy` / `query.pagination.*` (RFC 0005/0014 structured filters, marked experimental -
"a server that does not admit this field refuses the request"), `/api/v3/dependencies` (in the
IDL on `main` but not served by 2.22), `/api/v3/capabilities`, POST variants of the searches,
the internal `/api/traces`, `/api/services`, `/api/metrics/*` (SPM, needs a metrics store), and
the gRPC APIs on 16685.

## Auth

Jaeger has no authentication of its own (deployments put an OAuth/OIDC or basic-auth reverse
proxy in front). The manifest sends `Authorization: Bearer $JAEGER_TOKEN`, or basic auth from
`JAEGER_USERNAME`/`JAEGER_PASSWORD`, or nothing.

## Base URL

`JAEGER_URL` = query HTTP root incl. any `--query.base-path`, without `/api`:
`http://jaeger-query:16686` (all-in-one / `jaeger` binary with the `jaeger_query` extension).

Version switch: `JAEGER_LEGACY_PARAMS=true` sends the snake_case parameter names for Jaeger
2.0-2.18 and 1.x (which do not know the camelCase names). Jaeger 1.x docs are archived (1.76).

## Limits

No API rate limit; the cost is in the storage backend (Cassandra, Elasticsearch/OpenSearch,
Badger, ClickHouse, memory, gRPC remote storage). `query.searchDepth` bounds the traces
(50 here unless the SQL LIMIT or `trace_limit` says otherwise).

## Retention

Storage-specific: Elasticsearch/OpenSearch index rollover/ILM, Cassandra TTL (default 2 days in
the schema scripts), Badger `span_store_ttl` (72h), memory store (bounded by `max_traces`, lost
on restart). No clamp is set.

## Time formats

- Search windows: `query.startTimeMin` / `query.startTimeMax` are **RFC 3339** date-times
  (`time.start | rfc3339`); both required by the query service.
- Durations: Go duration strings with a unit (`0.500000s`); the spec pattern is
  `^-?(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,9})?s$`, so the manifest always sends seconds.
- `/api/dependencies`: `endTs` and `lookback` in **milliseconds**.
- Responses: OTLP/JSON (`startTimeUnixNano`/`endTimeUnixNano` ns strings, hex ids, enum
  integers) wrapped in `{"result": ...}`; summaries have `minStartTimeUnixNano` /
  `maxEndTimeUnixNano` (ns strings); proto3 JSON omits zero counts.

## Pitfalls

- A service name is required for trace search on most backends; list `jaeger_services` first.
  Names are exact and case-sensitive.
- `GET /api/v3/traces` answers **404 "No traces found"** when nothing matches; the manifest
  accepts 404 as 0 rows for `jaeger_traces` and `jaeger_trace`. `trace-summaries` answers 200
  with an empty list (and 404 on Jaeger < 2.19, which stays a loud error).
- Search matches spans: a trace qualifies when one span satisfies service, operation, duration
  and attributes; `jaeger_traces` returns all spans of those traces, so SQL predicates are
  re-applied to each span.
- `status = 'error'` is sent as the attribute filter `error=true` (matched against OTLP status
  ERROR or the v1 `error` tag); spans with only an HTTP 5xx code need `http_status_code`.
- `query.attributes` values are compared as strings (`"http.status_code": "500"`).
- Results are bounded by `searchDepth`: aggregates over `jaeger_traces` are samples.
- Dependencies come from the storage layer: memory/Badger compute them on the fly;
  Cassandra/Elasticsearch/OpenSearch need the `spark-dependencies` job - empty is then not proof
  of no calls.
- Jaeger adjusts clock skew by default; `raw = true` (`rawTraces`) shows stored timestamps.
