# Zipkin (`zipkin`)

Manifest: `src/omniops/sources/manifests/zipkin.yaml`. Fixtures: `src/omniops/sources/fixtures/zipkin/`.

Reference: [Zipkin API v2](https://zipkin.io/zipkin-api/) (Swagger UI) generated from
[`zipkin2-api.yaml`](https://github.com/openzipkin/zipkin-api/blob/master/zipkin2-api.yaml)
(`basePath: /api/v2`), checked on `master` in October 2026. The spec's only response example is
the `Span` example (`get /api` on `backend`); list/array fixtures are built from the schemas
around it.

## Tables

| SQL table | HTTP | Official reference |
|---|---|---|
| `zipkin_services` | `GET /api/v2/services` | [GET /services](https://zipkin.io/zipkin-api/#/default/get_services) |
| `zipkin_span_names` | `GET /api/v2/spans?serviceName=` | [GET /spans](https://zipkin.io/zipkin-api/#/default/get_spans) |
| `zipkin_traces` | `GET /api/v2/traces?serviceName=&spanName=&annotationQuery=&minDuration=&maxDuration=&endTs=&lookback=&limit=` (one row per span) | [GET /traces](https://zipkin.io/zipkin-api/#/default/get_traces) |
| `zipkin_trace` | `GET /api/v2/trace/{traceId}` (one row per span) | [GET /trace/{traceId}](https://zipkin.io/zipkin-api/#/default/get_trace__traceId_) |
| `zipkin_trace_many` | `GET /api/v2/traceMany?traceIds=<id>,<id>,...` (>= 2 ids; one row per span) | [GET /traceMany](https://zipkin.io/zipkin-api/#/default/get_traceMany) |
| `zipkin_dependencies` | `GET /api/v2/dependencies?endTs=&lookback=` | [GET /dependencies](https://zipkin.io/zipkin-api/#/default/get_dependencies) |
| `zipkin_autocomplete_keys` | `GET /api/v2/autocompleteKeys` | [GET /autocompleteKeys](https://zipkin.io/zipkin-api/#/default/get_autocompleteKeys) |
| `zipkin_autocomplete_values` | `GET /api/v2/autocompleteValues?key=` | [GET /autocompleteValues](https://zipkin.io/zipkin-api/#/default/get_autocompleteValues) |

Pushdown in `zipkin_traces`: `service` -> `serviceName`, `name` -> `spanName`, `duration_ms`
-> `minDuration`/`maxDuration` (microseconds; `maxDuration` alone also sends `minDuration=1`
because the spec only accepts it together with `minDuration`), `status = 'error'` ->
`annotationQuery=error`, `http_status_code` -> `annotationQuery=http.status_code=<code>`,
`annotation_query` raw (terms joined with ` and `).

Roles: `traces` -> `zipkin_traces`, `trace_detail` -> `zipkin_trace`, `services` ->
`zipkin_services`, `service_map` -> `zipkin_dependencies`.

Not modelled: `POST /api/v2/spans` (ingest), the UI config endpoint, and the server-only
`/api/v2/remoteServices` / `remoteServiceName` parameter, which the zipkin-server implements but
`zipkin2-api.yaml` does not document.

## Auth

Zipkin has no authentication; deployments use a reverse proxy. The manifest sends
`Authorization: Bearer $ZIPKIN_TOKEN`, or basic auth (`ZIPKIN_USERNAME`/`ZIPKIN_PASSWORD`), or nothing.

## Base URL

`ZIPKIN_URL` = server root without `/api/v2`, e.g. `http://zipkin:9411`. The UI lives under
`/zipkin/` (used for `ui_base_url`).

## Limits

- No API rate limit.
- `limit` default 10 traces per search (100 sent here unless the SQL LIMIT / `trace_limit` says otherwise).
- `lookback` defaults to `endTs` and is capped by the server's `QUERY_LOOKBACK`
  (zipkin-server default 86400000 ms = 1 day): longer windows silently shrink.
- `traceMany` needs at least two distinct ids (HTTP 400 otherwise).

## Retention

Storage-specific: in-memory keeps at most `MEM_MAX_SPANS` (500000) and loses everything on
restart; Cassandra uses a table TTL (7 days by default in the zipkin2 schema); Elasticsearch
uses daily indices deleted by your own curator/ILM; MySQL keeps until purged.

## Time formats

- Requests: `endTs` and `lookback` in **milliseconds**; `minDuration`/`maxDuration` in
  **microseconds**.
- Responses: span `timestamp` (epoch **microseconds**) and `duration` (**microseconds**);
  annotation timestamps in microseconds. ids are lowercase hex (trace id 16 or 32 chars,
  span id 16).

## Pitfalls

- Service and span names are lowercase in Zipkin: the server lowercases query parameters, but
  SQL re-applies your literal to the rows, so write them lowercase.
- Unknown service / span name / annotationQuery = empty list, not an error.
- Errors are an `error` tag (value = message), not a status field; HTTP failures also carry
  `http.status_code`.
- `zipkin_traces` returns every span of each matching trace; predicates are re-applied per span.
- `annotationQuery` terms all have to match one span; `error` matches the tag key or an
  annotation value.
- RPCs may be reported as one shared span id (client + server halves, `shared = true` on the
  server side): join children with care when computing self time.
- The spec says `GET /spans` returns `ListOfSpans` while its description (and the server) return
  span-name strings; `zipkin_span_names` accepts both.
- Dependency links come from storage: in-memory and MySQL compute them live; Cassandra and
  Elasticsearch need the `zipkin-dependencies` Spark job, otherwise the list is empty.
