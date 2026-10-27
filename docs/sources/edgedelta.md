# Edge Delta (`edgedelta`)

Manifest: `src/omniops/sources/manifests/edgedelta.yaml` · fixtures: `src/omniops/sources/fixtures/edgedelta/`

Edge Delta is the vendor behind the Edge Delta AI SRE Arena benchmark. Direct HTTPS against
`https://api.edgedelta.com/v1/orgs/{org_id}/...`; no MCP server and no `edx` binary at run time.
Researched 2026-10-27 from:

1. the public Swagger spec `https://api2.edgedelta.com/swagger/doc.json` (also published as
   OpenAPI 3 at `https://edgedelta.com/openapi.json`), linked from
   [Edge Delta API](https://docs.edgedelta.com/api/);
2. [Query Metrics with the API](https://docs.edgedelta.com/query-metrics-api/) (the only endpoint
   page with an example response);
3. Edge Delta's own open-source clients, which call endpoints that the public spec omits:
   [edgedelta-mcp-server](https://github.com/edgedelta/edgedelta-mcp-server) (`pkg/tools/search.go`,
   `graph.go`, `facet.go`, `services.go`, `client.go`) and the
   [edx CLI](https://github.com/edgedelta/edx) (`internal/cli/*.go`, `internal/api/client.go`,
   `skills/*/SKILL.md`), plus the [edx command reference](https://docs.edgedelta.com/edx-command-reference/),
   [MCP tools reference](https://docs.edgedelta.com/edge-delta-mcp-server-tools/) and
   [CQL reference](https://docs.edgedelta.com/cql/).

## Tables

| SQL table | HTTP | Endpoint (under `/v1/orgs/{org_id}`) | Source of truth |
|---|---|---|---|
| `edgedelta_logs` | GET | `/logs/log_search/search?query=&from=&to=&limit=&order=&scope=&cursor=` | Swagger (tags Log Search, ServedByMCP; response `core.ArchiveResponseV1`) - [API](https://docs.edgedelta.com/api/) |
| `edgedelta_log_counts` | POST (207) | `/graph?from=&to=&graph_type=` body `{"queries":{"A":{"scope":"log","query":"<cql> by {f}"}},"formulas":{"A":{"formula":"A"}}}` | edx [`logs.go`](https://github.com/edgedelta/edx/blob/main/internal/cli/logs.go) (`logs graph`), MCP `graph.go` (`get_log_graph`) |
| `edgedelta_metrics` | GET | `/metrics_v2/graph?query=&from=&to=` | [Query Metrics with the API](https://docs.edgedelta.com/query-metrics-api/) + Swagger (`core.CommonTimeseriesResponse`) |
| `edgedelta_events` | GET | `/events/search?query=&from=&to=&limit=&order=&cursor=` | Swagger (Event Search; `core.EventResponse`) |
| `edgedelta_patterns` | GET | `/clustering/stats?query=&from=&to=&limit=&summary=&negative=&volatility=&offset=` | Swagger (Pattern Stats; `core.ClusterStatResponse`) |
| `edgedelta_traces` | GET | `/traces?query=&from=&to=&limit=&order=&include_child_spans=&cursor=` | MCP [`search.go`](https://github.com/edgedelta/edgedelta-mcp-server/blob/main/pkg/tools/search.go) (`get_trace_timeline`), edx `traces.go` |
| `edgedelta_monitors` | GET | `/monitors?query=&limit=&cursor=` (items key `monitors`) | edx [`monitors.go`](https://github.com/edgedelta/edx/blob/main/internal/cli/monitors.go), [ed-monitors skill](https://github.com/edgedelta/edx/blob/main/skills/ed-monitors/SKILL.md) |
| `edgedelta_monitor` | GET | `/monitors/{id}` | edx `monitors.go` (get / evaluate) |
| `edgedelta_monitor_states` | GET | `/monitor_states?query=&limit=&cursor=` (items key `states`) | edx `monitors.go` (`monitors states`) |
| `edgedelta_facet_options` | GET | `/facet_options?scope=&facet_path=&limit=` | MCP [`facet.go`](https://github.com/edgedelta/edgedelta-mcp-server/blob/main/pkg/tools/facet.go), edx `metrics.go` (`metrics list`) |
| `edgedelta_facet_keys` | GET | `/facet_keys?scope=` | edx [`metrics.go`](https://github.com/edgedelta/edx/blob/main/internal/cli/metrics.go) |
| `edgedelta_pipelines` | GET | `/confs?empty_contents=true` | Swagger (`core.Conf`) |
| `edgedelta_pipeline_history` | GET | `/pipelines/{conf_id}/history?only_latest=` | Swagger (`core.ConfigHistory`) |

Not modelled: `/logs/log_search/stream` (SSE), `query_explain` / `query_log` / `table_schema`
(ClickHouse internals), `/service_map` and `/clustering/samples` (response shapes not published),
rehydrations, dashboards, lookup tables, AI teammate endpoints (other hosts:
`chat.ai.edgedelta.com`, `agent.ai.edgedelta.com`), and all write endpoints (monitor create,
pipeline save/deploy).

## Response shapes

- Log search (`core.ArchiveResponseV1`): `{items:[{id, timestamp (integer), body, severity_text,
  trace_id, span_id, resource{string map}, attributes{string map}}], next_cursor, query_id}`.
- Events (`core.EventResponse`): `{items:[{timestamp (integer), event_domain, event_type,
  severity_text, body, resource{}, attributes{}}], next_cursor, query_id}`. Monitor alerts carry
  `resource["ed.monitor.id"]`, `resource["event.name"]`, `attributes["ed.monitor.evaluated.value"]`,
  `attributes["ed.monitor.query"]` (ed-monitors skill).
- Metrics / graphs (`core.CommonTimeseriesResponse`): `{from, to, window, keys[], records:[{values[],
  timeseries:[{timestamp (ISO 8601), value}], aggregate:{value}}]}`. `keys` is null without `by`.
  The unified `/graph` endpoint wraps the same object in a map keyed by query/formula id
  (`{"A": {...}}`); the manifest names both `A` so the key is unambiguous.
- Patterns (`core.ClusterStatResponse`): `{stats:[{pattern, count, proportion, sentiment, delta,
  (deprecated: anomaly, past_count, past_proportion, offset_*)}]}`.
- Traces: `{items:[...], next_cursor}`; span fields are **not published** (the table keeps the raw
  record as `span` JSON plus dynamic columns).
- Monitors: list `{monitors:[...], next_cursor}`, single object for `/monitors/{id}`; documented
  metric_threshold fields: `name, type, evaluation_type, evaluation_function, evaluation_window (s),
  threshold_type (above|below), warning_threshold, alert_threshold, no_data_behavior,
  formula_query{formula, queries{A:{scope, query}}}`.
- Monitor states: `{states:[{id, ...}], next_cursor, previous_cursor}`; fields beyond `id` not published.
- Facet options `{options:[{name, count}]}`; facet keys `[{key, name}]`.
- Configs: array of `core.Conf`; config history: array of `core.ConfigHistory`
  `{author, config_id, content, description, org_id, status ("Deployed"|"Not Deployed"), timestamp}`.

## Auth

`X-ED-API-Token: <token>` (Swagger `securityDefinitions.ApiKeyAuth`). Create the token under
Admin > My Organization > API Tokens, adding a permission per resource type / instance / access
type (read is enough for every table here). The Organization ID shown on the same page is part
of every path (`ED_ORG_ID`). The MCP server and edx also accept OAuth bearer JWTs; not used here.

## Base URL / environments

`https://api.edgedelta.com` (Swagger `host`; MCP `ED_API_URL` default). `ED_API_URL` overrides it
(edx also knows `staging` and `local` environments; their hosts are not published). The API is
URL-versioned (`/v1/...`); breaking changes ship as new versions
([developers page](https://edgedelta.com/developers)).

## Rate limits

Not published: 'standard rate limits apply; treat HTTP 429 as a signal to back off'. edx retries
429/502/503/504 with 0.5 s / 1 s / 2 s backoff. The manifest caps the source at 120 requests/min,
concurrency 4. Default page sizes: search commands 20 (MCP/edx defaults), Swagger log/event search
default 1000; monitors list 50; the manifest asks for 1000 per page and follows `next_cursor`
(max 5 pages).

## Retention

Not published; plan / org specific. Archived data can be brought back with rehydrations (not
modelled). Event search has no maximum window (ed-events skill).

## Time formats

| Where | Format |
|---|---|
| `from` / `to` query params (all search/graph endpoints) | ISO 8601 UTC; edx sends `2006-01-02T15:04:05.000Z` (the manifest sends RFC 3339 with ms) |
| `lookback` (never sent by the manifest) | Go duration (`15m`, `1h`, `720h`; no `d` unit) |
| Graph / metric points `timeseries[].timestamp`, `from`, `to` | ISO 8601 strings |
| Graph `window` | Go duration string (`1m0s`) |
| Metric `.rollup(n)` | seconds |
| Log / event `timestamp`, config history `timestamp`, `deployed_timestamp` | integer epoch, **unit not documented** -> columns use magnitude inference (`auto_time`) |
| Pattern `offset`, `window` | Go durations (`24h`; combined values like `5m3s` not supported for `window`) |

## Pitfalls

- Field names are scope specific; unknown fields/values return 0 rows silently. Discover with
  `edgedelta_facet_keys` / `edgedelta_facet_options`.
- Log attributes need `@` in CQL (`@http.status_code:"504"`); bare words search `body` only.
- Traces and metrics reject full-text terms. `status.code` on spans is the HTTP code string.
- Monitor events use `event.domain` "Monitor" or "Monitor Alerts" depending on org/data age.
- Quoted CQL values are exact and case-sensitive; `AND`/`OR`/`NOT` must be upper case; no regex,
  no `=`/`!=`, wildcards only at the start/end of a quoted value.
- Cursor pages must repeat the same query and time window (otherwise HTTP 400 unknown cursor); a
  page count is a lower bound until `next_cursor` is empty.
- Time series omit empty buckets ('missing intervals mean no data'); the server chooses the
  bucket width (`window`), so the engine does not zero-fill these tables.
- `/graph` returns HTTP 207 Multi-Status on success.
- `/confs/validate` answers HTTP 200 with `{"valid": false, "reason": ...}` for an invalid config
  (the only documented 2xx-with-error body; not modelled). The source-level error rule on
  `$.error` is defensive: Edge Delta error bodies (`{"error": "..."}`) normally come with 4xx.
