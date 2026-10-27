# Thanos Query (`thanos`)

Manifest: `src/omniops/sources/manifests/thanos.yaml` (extends `templates/prometheus.yaml`).
Fixtures: `src/omniops/sources/fixtures/thanos/`.

Reference: [Thanos Querier/Query docs](https://thanos.io/tip/components/query.md/) - "Query API exposed by Thanos
is guaranteed to be compatible with the Prometheus 2.x API", plus Thanos-specific parameters
([Query API overview](https://thanos.io/tip/components/query.md/#query-api-overview)). Route list and parameter
names verified in `pkg/api/query/v1.go`. Response shapes: [Prometheus HTTP API](https://prometheus.io/docs/prometheus/latest/querying/api/).

## Tables

| SQL table | HTTP | Official doc |
|---|---|---|
| `thanos_query_range` | GET `/api/v1/query_range` (+ `dedup`, `partial_response`, `max_source_resolution`, `engine`, `replicaLabels[]`, `storeMatch[]`) | https://thanos.io/tip/components/query.md/#query-api-overview |
| `thanos_query` | GET `/api/v1/query` (same extras) | https://thanos.io/tip/components/query.md/#query-api-overview |
| `thanos_series` | GET `/api/v1/series` | https://prometheus.io/docs/prometheus/latest/querying/api/#finding-series-by-label-matchers |
| `thanos_label_values` | GET `/api/v1/label/{name}/values` | https://prometheus.io/docs/prometheus/latest/querying/api/#querying-label-values |
| `thanos_labels` | GET `/api/v1/labels` | https://prometheus.io/docs/prometheus/latest/querying/api/#getting-label-names |
| `thanos_metric_metadata` | GET `/api/v1/metadata` (Metadata API fan-out) | https://prometheus.io/docs/prometheus/latest/querying/api/#querying-metric-metadata |
| `thanos_exemplars` | GET `/api/v1/query_exemplars` (Exemplars API fan-out) | https://prometheus.io/docs/prometheus/latest/querying/api/#querying-exemplars |
| `thanos_alerts` | GET `/api/v1/alerts` (Rules API fan-out) | https://prometheus.io/docs/prometheus/latest/querying/api/#alerts |
| `thanos_rules` | GET `/api/v1/rules` (`type`, `rule_name[]`, `rule_group[]`, `file[]`) | https://prometheus.io/docs/prometheus/latest/querying/api/#rules |
| `thanos_targets` | GET `/api/v1/targets` (Targets API fan-out) | https://prometheus.io/docs/prometheus/latest/querying/api/#targets |
| `thanos_stores` | GET `/api/v1/stores` (UI endpoint: StoreAPI health, label sets, time ranges) | https://thanos.io/tip/components/query.md/#store-filtering |
| `thanos_tsdb_status` | GET `/api/v1/status/tsdb` (`limit`, Thanos-only `match[]`; Status API fan-out, Prometheus TSDB-stats shape) | https://prometheus.io/docs/prometheus/latest/querying/api/#tsdb-stats |

Roles: `metrics` → `thanos_query_range`; `active_alerts` → `thanos_alerts`; `alert_rules` → `thanos_rules`;
`hosts` → `thanos_targets`.

## Auth

Thanos has no authentication of its own; deployments put a proxy in front. Optional `THANOS_TOKEN` (bearer) or
`THANOS_USERNAME`/`THANOS_PASSWORD` (basic). Tenancy: `THANOS_TENANT` is sent in the header named by
`THANOS_TENANT_HEADER` (`--query.tenant-header`, default `THANOS-TENANT`); with `--query.enforce-tenancy` the
querier injects the tenant label matcher.

## Base URL

`THANOS_URL` = Querier or Query Frontend HTTP root, e.g. `http://thanos-query.monitoring.svc:9090` (no `/api/v1`).
If served under a sub-path (`--web.route-prefix`), include it.

## Query parameters (Thanos-specific)

| Parameter | Values | Effect |
|---|---|---|
| `dedup` | bool (default true when replica labels configured) | merge HA replicas, drop replica labels |
| `partial_response` | bool (default `--query.partial-response`, usually true) | tolerate failing StoreAPIs with a warning |
| `max_source_resolution` | `auto`, `0`, `5m`, `1h` | max downsampling resolution read |
| `engine` | `prometheus`, `thanos` | PromQL engine |
| `replicaLabels[]` | label names | override `--query.replica-label` for this query (`replica_labels` filter) |
| `storeMatch[]` | `{__address__=~"..."}` | only ask matching StoreAPIs (`store_match` filter); only `__address__` is supported |

Parameter names were checked against the constants in `pkg/api/query/v1.go` (`replicaLabels[]`, `storeMatch[]`,
`engine`, `analyze`, `lookback_delta`, `shard_info`); the docs page writes `replicaLabels` without brackets but
the handler reads `replicaLabels[]`.

## Rate limits

None at the API; `--query.max-concurrent` (default 20) queues queries and `--query.timeout` (default 2m)
aborts them (503). Store gateway limits (`--store.limits.request-series/samples`) fail queries that touch too
much data.

## Retention

Sidecars serve the local Prometheus retention; store gateways serve object storage according to the compactor's
`--retention.resolution-raw/5m/1h`.

## Time formats

`start`/`end`/`time` Unix seconds or RFC 3339 (manifest: epoch seconds); `step` seconds or duration;
`max_source_resolution` duration. `/api/v1/stores` returns `minTime`/`maxTime` in epoch milliseconds
(math.MinInt64/MaxInt64 for unbounded; mapped to NULL).

## Pitfalls

- Partial responses: an unreachable store becomes a warning with HTTP 200. The `warnings` column surfaces it;
  set `partial_response = false` for correctness-critical queries, or check `thanos_stores`.
- dedup=false duplicates every series per HA replica; never `sum` across the replica label.
- Auto-downsampling can hide short spikes on long ranges; use `max_source_resolution = '0'`.
- The rules/alerts handlers ignore `partial_response`/`dedup` query parameters (they use the server defaults),
  so HA rulers can list the same alert twice.
- `/api/v1/stores` is a UI endpoint, undocumented in the API reference; its shape was taken from the
  `EndpointStatus` JSON tags in `pkg/query/endpointset.go`.
- `/api/v1/status/tsdb` only counts head blocks of receivers / sidecar Prometheus (not store-gateway blocks) and
  sums tenants/replicas; it is not described on thanos.io (route registered in `pkg/api/query/v1.go`; response is
  Prometheus' `v1.TSDBStatus`), so the fixture is the Prometheus docs TSDB-stats example.
- Errors: Thanos answers 4xx/5xx with the Prometheus error envelope; the manifest also has a 2xx error rule
  (`status = error`) proven by the `query_error.json` fixture (built from the documented envelope).
- Template fixtures (`series`, `metadata`, `query_vector`) are not copied: the engine falls back to
  `fixtures/prometheus/` for them.
