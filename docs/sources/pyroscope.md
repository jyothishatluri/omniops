# Grafana Pyroscope (`pyroscope`)

Manifest: `src/omniops/sources/manifests/pyroscope.yaml`. Python fetchers:
`src/omniops/sources/python/pyroscope.py` (`top_functions`, `diff_functions`).
Fixtures: `src/omniops/sources/fixtures/pyroscope/`.

Reference: [Pyroscope server API](https://grafana.com/docs/pyroscope/latest/reference-server-api/)
("Connect API" - `POST /querier.v1.QuerierService/<Method>` with a JSON body; "Pyroscope Legacy
HTTP API" - `GET /pyroscope/render`). The page documents request fields and curl examples but
no response bodies; response shapes come from the protobuf it is generated from,
[`api/querier/v1/querier.proto`](https://github.com/grafana/pyroscope/blob/main/api/querier/v1/querier.proto)
and [`api/types/v1/types.proto`](https://github.com/grafana/pyroscope/blob/main/api/types/v1/types.proto)
(main, October 2026), in protojson encoding (lowerCamelCase, int64 as strings, enum names,
zero values omitted). Flame-graph level encoding: `pkg/model/flamegraph.go` and
`pkg/model/flamegraph_diff.go`.

## Tables

| SQL table | HTTP | Official reference |
|---|---|---|
| `pyroscope_profile_types` | `POST /querier.v1.QuerierService/ProfileTypes` `{start, end}` | [ProfileTypes](https://grafana.com/docs/pyroscope/latest/reference-server-api/#querierv1querierserviceprofiletypes) |
| `pyroscope_label_names` | `POST /querier.v1.QuerierService/LabelNames` `{matchers, start, end}` | [LabelNames](https://grafana.com/docs/pyroscope/latest/reference-server-api/#querierv1querierservicelabelnames) |
| `pyroscope_label_values` | `POST /querier.v1.QuerierService/LabelValues` `{name, matchers, start, end}` | [LabelValues](https://grafana.com/docs/pyroscope/latest/reference-server-api/#querierv1querierservicelabelvalues) |
| `pyroscope_series` | `POST /querier.v1.QuerierService/Series` `{matchers, labelNames, start, end}` | [Series](https://grafana.com/docs/pyroscope/latest/reference-server-api/#querierv1querierserviceseries) |
| `pyroscope_select_series` | `POST /querier.v1.QuerierService/SelectSeries` `{profileTypeID, labelSelector, start, end, groupBy, step, aggregation, limit}` | [SelectSeries](https://grafana.com/docs/pyroscope/latest/reference-server-api/#querierv1querierserviceselectseries) |
| `pyroscope_exemplars` | `POST /querier.v1.QuerierService/SelectSeries` with `exemplarType` (one row per exemplar) | [SelectSeries](https://grafana.com/docs/pyroscope/latest/reference-server-api/#querierv1querierserviceselectseries) |
| `pyroscope_top_functions` (python) | `POST /querier.v1.QuerierService/SelectMergeStacktraces` `{profileTypeID, labelSelector, start, end, maxNodes, format: PROFILE_FORMAT_FLAMEGRAPH, spanSelector?, traceIdSelector?}` | [SelectMergeStacktraces](https://grafana.com/docs/pyroscope/latest/reference-server-api/#querierv1querierserviceselectmergestacktraces) |
| `pyroscope_diff_functions` (python) | `POST /querier.v1.QuerierService/Diff` `{left: {...}, right: {...}}` | [Diff](https://grafana.com/docs/pyroscope/latest/reference-server-api/#querierv1querierservicediff) |

Roles: `profiles` -> `pyroscope_top_functions` (filter `service`), `services` ->
`pyroscope_label_values` (`label = 'service_name'`).

### Why two tables use the python escape hatch

`SelectMergeStacktraces` and `Diff` return a flame graph: `names[]` plus `levels[].values`, a
flat int64 array of 4-tuples `[x_offset, total, self, name_index]` (7-tuples
`[xl, total_left, self_left, xr, total_right, self_right, name_index]` for Diff) whose x offsets
are delta-encoded per level (`x = delta + prev_end`, `prev_end = x + total`). One row per
function needs strided chunking, delta decoding, an index lookup into `names`, a parent walk
(interval containment on the previous level) to count recursive frames once, and - for Diff -
a parent lookup in whichever side the node exists. The declarative response language has no
strided-array or running-sum primitive. Both decoders are checked offline against
`select_merge_stacktraces.json` and `diff.json` (hand-computed expectations, including a
recursive frame and a node present on one side only); these two fixtures are not attached to
the tables because `omniops manifest test` does not execute python fetchers.

Not modelled: `SelectMergeProfile` and `SelectMergeSpanProfile` (deprecated in favour of
`SelectMergeStacktraces`), `SelectHeatmap` (v2 storage only), `GET /pyroscope/render`
(legacy; same flame graph data in flamebearer form, timeline without explicit timestamps),
`profileIdSelector` / `stackTraceSelector` (call-site paths and Go PGO), async queries, ingest.

## Auth

Pyroscope has no authentication layer of its own ("use an authenticating reverse proxy").

| Deployment | Env |
|---|---|
| Self-hosted single tenant | `PYROSCOPE_URL` only |
| Self-hosted multi-tenant | `PYROSCOPE_TENANT_ID` -> `X-Scope-OrgID` |
| Grafana Cloud Profiles | basic auth: numeric instance ID (`PYROSCOPE_USERNAME`) / access policy token with `profiles:read` (`PYROSCOPE_PASSWORD`) |
| Reverse proxy with bearer token | `PYROSCOPE_TOKEN` |

## Base URLs

`PYROSCOPE_URL` = server root, e.g. `http://pyroscope:4040` (default `http_listen_port` 4040);
Grafana Cloud: `https://profiles-prod-NNN.grafana.net` from the stack details. The o11y-bench
stack has no Pyroscope (its MCP sidecar is started with `--disable-pyroscope`).

## Limits (limits block, [configuration reference](https://grafana.com/docs/pyroscope/latest/configure-server/reference-configuration-parameters/))

| Limit | Default | Effect here |
|---|---|---|
| `max_query_length` | 1d | `max_window: 24h` on every table |
| `max_query_lookback` | 1w | older windows are refused |
| `max_flamegraph_nodes_default` / `max_flamegraph_nodes_max` | 8192 / 1048576 | `max_nodes` default 8192 |
| `http_server_read_timeout` / `write_timeout` | 30s | heavy merges over 24h may time out |
| `querier.query_store_after` | 4h | older data is read from object storage |

No request-rate limit on reads.

## Retention

`retention_period` default **31d** (0 = keep forever); `compactor_blocks_retention_period`
default 0 (v1 storage). Queries cannot look back further than `max_query_lookback` (7 days)
whatever the retention. Grafana Cloud Profiles retention depends on the plan.

## Time formats

- All Connect requests: `start` / `end` in **milliseconds since epoch** (`time.start | epoch_ms`).
  Without them ProfileTypes/Series only ask the ingesters (recent data).
- `SelectSeries.step`: **seconds** (double).
- Responses: `Point.timestamp` and `Exemplar.timestamp` are ms (int64 strings); values are in the
  profile type's sample unit (CPU: nanoseconds, memory: bytes, goroutines/contentions: count).
- Legacy `/pyroscope/render` (not used) accepts `from`/`until` as `now-1h` or epoch s/ms/us/ns.

## Pitfalls

- Profiles are keyed by `service_name`; unknown services/labels return nothing, not an error.
- Profile types depend on the language/SDK; check `pyroscope_profile_types` before asking for
  memory or lock profiles.
- `SelectSeries` values are per step: convert CPU nanoseconds to cores with
  `value / 1e9 / step_seconds` (set `step` explicitly).
- protojson omits zero values (`value: 0`, empty labels): the columns default them.
- Flame graphs are truncated to `max_nodes`; small frames are folded into `other`.
- Compare shares (`self_pct`, `*_pct_change`), not absolute values, across windows of different
  load or length (Diff left/right ticks differ).
- `span_id` / `trace_id` selectors and exemplar trace ids exist only for SDKs with
  OpenTelemetry span profiles.
