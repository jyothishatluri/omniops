# Grafana Loki (`loki`)

Template: `src/omniops/sources/templates/loki.yaml` (source `loki`) - reused by
`manifests/loki.yaml` (direct Loki) and `manifests/grafana_loki.yaml` (through Grafana's
datasource proxy, see [grafana_loki.md](grafana_loki.md)).
Fixtures: `src/omniops/sources/fixtures/loki/` (also used by `grafana_loki` through the
template fixture fallback - never copy them).

Reference: [Loki HTTP API](https://grafana.com/docs/loki/latest/reference/loki-http-api/)
(published "latest" = the v3.7.x docs). Checked against the docs source at Loki **v3.7.9**
(latest release, 2026-10-20; `docs/sources/reference/loki-http-api.md`) and against `main`
(which adds an Authentication section and a `stats` object on detected fields / values). Where the
docs show only a schema, the field names were checked in the v3.7.9 code
(`pkg/util/marshal/marshal.go`, `pkg/querier/queryrange/volume.go`, `pkg/ruler/base/api.go`).
o11y-bench runs Loki 3.7.8 (`auth_enabled: false`, pattern ingester off, compactor retention off).

## Tables

| SQL table | HTTP | Official reference |
|---|---|---|
| `loki_logs` | `GET /loki/api/v1/query_range` (LogQL log query, `resultType: streams`) | [Query logs within a range of time](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-logs-within-a-range-of-time) |
| `loki_query_range` | `GET /loki/api/v1/query_range` (LogQL metric query, `resultType: matrix`) | [Query logs within a range of time](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-logs-within-a-range-of-time) |
| `loki_query` | `GET /loki/api/v1/query` (instant, `resultType: vector`) | [Query logs at a single point in time](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-logs-at-a-single-point-in-time) |
| `loki_labels` | `GET /loki/api/v1/labels` | [Query labels](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-labels) |
| `loki_label_values` | `GET /loki/api/v1/label/<name>/values` | [Query label values](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-label-values) |
| `loki_series` | `GET /loki/api/v1/series` (`match[]` repeated) | [Query streams](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-streams) |
| `loki_index_stats` | `GET /loki/api/v1/index/stats` | [Query log statistics](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-log-statistics) |
| `loki_volume` | `GET /loki/api/v1/index/volume` | [Query log volume](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-log-volume) |
| `loki_volume_range` | `GET /loki/api/v1/index/volume_range` | [Query log volume](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-log-volume) |
| `loki_patterns` | `GET /loki/api/v1/patterns` | [Patterns detection](https://grafana.com/docs/loki/latest/reference/loki-http-api/#patterns-detection) |
| `loki_detected_fields` | `GET /loki/api/v1/detected_fields` | [Detected fields](https://grafana.com/docs/loki/latest/reference/loki-http-api/#detected-fields) |
| `loki_detected_field_values` | `GET /loki/api/v1/detected_field/<name>/values` | [Detected field values](https://grafana.com/docs/loki/latest/reference/loki-http-api/#detected-field-values) |
| `loki_rules` | `GET /prometheus/api/v1/rules` (ruler, Prometheus format; `type`, `rule_name`, `rule_group`, `file`) | [List rules](https://grafana.com/docs/loki/latest/reference/loki-http-api/#list-rules) -> [Prometheus rules](https://prometheus.io/docs/prometheus/latest/querying/api/#rules) |
| `loki_alerts` | `GET /prometheus/api/v1/alerts` (ruler, Prometheus format) | [List alerts](https://grafana.com/docs/loki/latest/reference/loki-http-api/#list-alerts) -> [Prometheus alerts](https://prometheus.io/docs/prometheus/latest/querying/api/#alerts) |
| `loki_buildinfo` | `GET /loki/api/v1/status/buildinfo` | [Show build information](https://grafana.com/docs/loki/latest/reference/loki-http-api/#show-build-information) |

Roles bound by the template: `logs` -> `loki_logs`, `log_counts` -> `loki_query_range`
(`sum(count_over_time({service_name="<svc>"} [$__auto]))`), `services` -> `loki_label_values`
(label `service_name`), `alert_rules` -> `loki_rules`, `active_alerts` -> `loki_alerts`.

Not modelled on purpose: `/loki/api/v1/tail` (WebSocket stream, best-effort lookback - the docs
say to use `query_range` for history), `/loki/api/v1/push`, `/otlp/v1/logs`, ruler writes and the
YAML rule-group endpoints (`/loki/api/v1/rules...`, same data as `loki_rules`), delete / flush /
ring / admin endpoints, `/loki/api/v1/format_query`. `/loki/api/v1/detected_labels` exists in the
code (used by Grafana Logs Drilldown) but is not in the HTTP API reference, so it is not exposed.

### Column notes

- `loki_logs.ts` comes from the nanosecond string (`epoch_ns`); `ts_ns` keeps the exact string.
- `service`, `level`, `env`, `host`, `namespace`, `pod`, `container` are read from the returned
  stream labels (indexed labels + structured metadata + labels extracted by `| json` / `| logfmt`).
- `trace_id`: label / structured metadata first; otherwise extracted from the raw line
  (`"trace_id": "..."` or `"traceId": "..."` in JSON lines, `trace_id=...` in logfmt lines). The
  o11y-bench logs carry `trace_id` inside JSON lines, which is also what its Grafana derived field
  matches.
- `status_code`, `duration_ms`: only when the query extracts them (`| json`) or they are labels.

## Auth

The Loki API has no auth of its own ([Authentication](https://grafana.com/docs/loki/latest/reference/loki-http-api/)):

| Deployment | What `manifests/loki.yaml` sends |
|---|---|
| Self-hosted, single tenant (`auth_enabled: false`, o11y-bench) | nothing |
| Self-hosted, multi-tenant | `X-Scope-OrgID: $LOKI_TENANT_ID` (several tenants: `t1\|t2\|t3`) |
| Grafana Enterprise Logs | Basic auth: tenant name / access policy token (`LOKI_USERNAME`, `LOKI_PASSWORD`) |
| Grafana Cloud Logs | Basic auth: Loki instance ID ("User" in the Cloud Portal) / Cloud Access Policy token with `logs:read` ([Authenticate to query Cloud Logs](https://grafana.com/docs/grafana-cloud/send-data/logs/authenticate-to-query-logs/)) |
| Reverse proxy with bearer token | `Authorization: Bearer $LOKI_TOKEN` |

## Base URLs / regions

`LOKI_URL` is the root without `/loki/api/v1`: `http://loki:3100` (o11y-bench: `http://localhost:3100`).
Grafana Cloud: copy the URL from the stack's Loki card (Details) in the Cloud Portal; the host is
region-specific (`https://logs-prod-<n>.grafana.net` style) and "Grafana Cloud URLs use one of two
formats depending on when your region was created".

## Pagination

Loki has no cursor. `loki_logs` uses `time_cursor` pagination: pages of 2000 lines
(`direction=backward`), and each next request sets `end` to the oldest nanosecond timestamp of the
previous page (`end` is exclusive, so the boundary line is not repeated), up to 5 pages / 10000
lines; a short page stops. A plain `SELECT ... LIMIT n` (no ORDER BY / GROUP BY) sends `limit=n`
(max 5000). `direction=forward` effectively returns one page from the window start.

## Limits (Loki v3.7.9 defaults, [configuration reference](https://grafana.com/docs/loki/latest/configure/#limits_config))

| Limit | Default | Effect |
|---|---|---|
| `max_entries_limit_per_query` | 5000 | per-request `limit` capped at 5000 |
| `max_query_series` | 500 | metric queries with more series fail: aggregate with `sum by` |
| `max_query_length` | 30d1h | `max_window: 30d` on time-bounded tables |
| `max_query_lookback` | 0s (unlimited) | |
| `query_timeout` | 1m | broad selectors over long windows time out (503/504) |
| `max_streams_matchers_per_query` | 1000 | |
| `split_queries_by_interval` | 1h | |
| `volume_enabled` / `volume_max_series` | true / 1000 | `loki_volume*` limit capped at 1000 |
| `pattern_ingester.enabled` | false | `loki_patterns` returns HTTP 404 when disabled (o11y-bench: disabled) |
| `pattern_ingester.retain_for` | 3h | patterns older than that are gone unless `pattern_persistence_enabled` |
| `discover_service_name` | `[service app application app_name name app_kubernetes_io_name container container_name k8s_container_name component workload job k8s_job_name]` | `service_name` label added at ingest |
| `discover_log_levels` | true | `detected_level` structured metadata |

No request-rate limit on reads by default. Grafana Cloud may apply different per-tenant limits.

## Retention

Self-hosted: `retention_period` default `0s` = keep forever (compactor retention disabled; also
off in o11y-bench). Grafana Cloud Logs: 14 days (Free) / 30 days (Pro) unless extended
([pricing](https://grafana.com/pricing/)). The template sets no retention clamp because it is
deployment-specific.

## Time formats

- Request `start` / `end` / `time`: **nanosecond** Unix epochs (`time.start | epoch_ns`); the API
  also accepts float seconds and RFC3339 ([Timestamps](https://grafana.com/docs/loki/latest/reference/loki-http-api/#timestamps)).
- `step` / `interval`: duration string or float seconds (`time.step_s`).
- Responses: streams values `[<ns epoch string>, <line>]`; matrix / vector values
  `[<seconds epoch number>, <value string>]`; pattern samples `[<seconds>, <count>]`.
- `start` is inclusive, `end` exclusive.

## Error handling

- `status: "error"` in a 2xx body -> loud error (source-level rule).
- `loki_logs` raises if the body is a matrix (a metric query was sent), `loki_query_range`
  raises if it is a stream (a log query was sent) - both proven by `expect_error` fixtures.
- `/loki/api/v1/query` rejects log queries with HTTP 400 (documented).

## Fixtures (`fixtures/loki/`)

| File | Source |
|---|---|
| `query_range_streams.json`, `query_range_matrix.json`, `query_vector.json`, `labels.json`, `label_values.json`, `series.json`, `index_stats.json`, `patterns.json`, `detected_fields.json`, `detected_field_values.json` | verbatim docs examples (`stats` elided as in the docs) |
| `query_range_streams_json_lines.json`, `query_range_streams_parsed.json` | built from the documented `<stream value>` schema with JSON / logfmt lines (o11y-bench style) and `| json` extracted labels |
| `volume.json`, `volume_range.json` | docs give only "Prometheus style vector / matrix"; built from that schema |
| `patterns_level.json` | v3.7.9 `WriteQueryPatternsResponseJSON` also writes `level` |
| `rules.json`, `alerts.json` | verbatim Prometheus API docs examples (Loki docs point there) |
| `buildinfo.json` | docs list the field names only |

## Pitfalls

- An unknown label/value returns 0 rows, not an error: check `loki_labels` / `loki_label_values` first.
- `{}` and all-empty-compatible selectors (`{a=~".*"}`) are rejected; at least one matcher must be non-empty.
- When the result notes say pagination stopped, the window has more than 10000 lines: narrow it,
  or count with `loki_query_range` / `loki_query`.
- `count_over_time(...[5m])` at a 15s step overlaps windows; use `[$__auto]` (replaced by the step)
  in `loki_query_range` and `[$__range]` in `loki_query`.
- Steps with no matching lines are absent from matrix results (not 0).
- `loki_query` returns the evaluation instant (= window end) as `eval_time`; its `ts` is a
  pushdown-only window column, so strict bounds (`ts < X`) cannot silently drop the result.
  Range tables stamp the last point/bucket at the window end: use `BETWEEN` or `<=`.
- Structured metadata and parsed (`| json`) labels are merged into the returned stream labels; a
  parsed key that collides with a stream label comes back as `<key>_extracted`.
- `loki_index_stats` and `loki_volume*` read the index only: data still in ingesters is missing.
- Volume is in bytes, not lines.
- Patterns need the pattern ingester (404 otherwise) and cover the last 3h only by default.
- Backdated lines are visible only after their chunks are flushed (o11y-bench flushes before tests).
