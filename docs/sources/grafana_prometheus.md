# Prometheus via Grafana (`source: grafana_prometheus`)

Manifest: `src/omniops/sources/manifests/grafana_prometheus.yaml` (`extends: prometheus`)
Fixtures: the template's Prometheus docs examples are found through the fixture fallback
(`src/omniops/sources/fixtures/prometheus/`; Grafana's proxy forwards the upstream body
unchanged). Only Grafana-specific additions live in `src/omniops/sources/fixtures/grafana_prometheus/`:
`labels.json` (docs example for `GET /api/v1/labels`) and `query_error.json` (the documented
`{status: error, errorType, error}` envelope, `expect_error` proves the template's error rule fires).
Template pitfalls are kept and Grafana pitfalls added with `append:`.

All tables of `templates/prometheus.yaml`, renamed to `grafana_prometheus_*`, sent through
Grafana's data-source proxy:

```
{GRAFANA_URL}/api/datasources/proxy/uid/{GRAFANA_PROMETHEUS_UID}/api/v1/<prometheus path>
```

Proxy reference: https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/data_source/#data-source-proxy-calls
(`GET /api/datasources/proxy/uid/:uid/*` "Proxies all calls to the actual data source").

Use it when Prometheus/Mimir is only reachable or credentialed through Grafana (Grafana Cloud,
o11y-bench, locked-down networks). For other data sources use `grafana_ds_query`.

## Tables

| SQL table | Upstream endpoint (behind the proxy) | Official reference |
|---|---|---|
| `grafana_prometheus_query_range` | `GET /api/v1/query_range` (`query`, `start`, `end` epoch s, `step` s) | https://prometheus.io/docs/prometheus/latest/querying/api/#range-queries |
| `grafana_prometheus_query` | `GET /api/v1/query` (`query`, `time` epoch s) | https://prometheus.io/docs/prometheus/latest/querying/api/#instant-queries |
| `grafana_prometheus_series` | `GET /api/v1/series` (`match[]`, `start`, `end`, `limit`) | https://prometheus.io/docs/prometheus/latest/querying/api/#finding-series-by-label-matchers |
| `grafana_prometheus_labels` | `GET /api/v1/labels` (`match[]`, `start`, `end`, `limit`) | https://prometheus.io/docs/prometheus/latest/querying/api/#getting-label-names |
| `grafana_prometheus_label_values` | `GET /api/v1/label/:name/values` | https://prometheus.io/docs/prometheus/latest/querying/api/#querying-label-values |
| `grafana_prometheus_metric_metadata` | `GET /api/v1/metadata` | https://prometheus.io/docs/prometheus/latest/querying/api/#querying-metric-metadata |
| `grafana_prometheus_alerts` | `GET /api/v1/alerts` | https://prometheus.io/docs/prometheus/latest/querying/api/#alerts |
| `grafana_prometheus_rules` | `GET /api/v1/rules` | https://prometheus.io/docs/prometheus/latest/querying/api/#rules |
| `grafana_prometheus_targets` | `GET /api/v1/targets?state=active` | https://prometheus.io/docs/prometheus/latest/querying/api/#targets |

Roles (inherited from the template, renamed): `metrics` -> `grafana_prometheus_query_range`
(PromQL), `active_alerts` -> `grafana_prometheus_alerts`, `alert_rules` -> `grafana_prometheus_rules`,
`hosts` -> `grafana_prometheus_targets`.

Deep links (`ui_link`) open Grafana Explore with the query and window instead of the Prometheus UI.

## Auth and configuration

| Env | Meaning |
|---|---|
| `GRAFANA_URL` (required) | Grafana root URL, e.g. `http://localhost:3000` (o11y-bench) |
| `GRAFANA_TOKEN` | Service account token, sent as `Authorization: Bearer`; needs `datasources:query` on the data source. Optional with anonymous access. |
| `GRAFANA_USERNAME` / `GRAFANA_PASSWORD` | Basic auth fallback (self-hosted) |
| `GRAFANA_ORG_ID` | Optional `X-Grafana-Org-Id` |
| `GRAFANA_PROMETHEUS_UID` | Data source uid, default `prometheus` (o11y-bench). Grafana Cloud stacks usually name it `grafanacloud-prom`; look it up with `SELECT uid, type FROM grafana_datasources WHERE type = 'prometheus'`. |

Grafana injects the data source's own credentials when proxying; the caller never needs the
Prometheus credentials.

## Limits

- Grafana `[dataproxy] timeout` (default 30 s) applies before Prometheus' own query timeout.
- Prometheus range queries: at most 11,000 points per series (the engine sizes `step` for ~300).
- Retention is Prometheus' (or Mimir's) own; Grafana adds none.

## Time formats

Prometheus accepts RFC 3339 or Unix seconds; the template sends Unix **seconds** (`epoch_s`) and
`step` in seconds. Sample timestamps come back as float seconds.

## Pitfalls

1. 404 "data source not found" = wrong `GRAFANA_PROMETHEUS_UID`; 403 = token lacks `datasources:query`.
2. Only Prometheus-compatible data sources work through this path; for Loki/Tempo use the
   `grafana_loki` / `grafana_tempo` sources, for anything else `grafana_ds_query`.
3. `/api/v1/rules` and `/api/v1/alerts` are Prometheus-server rules, not Grafana-managed alerts
   (those are `grafana_alert_rule_status`, `grafana_active_alerts`).
4. An unknown metric or label yields 0 series, not an error: discover with
   `grafana_prometheus_labels` / `grafana_prometheus_label_values` first.
5. o11y-bench metric names: `http_requests_total{job, instance, status}`,
   `http_request_duration_seconds_bucket`, `service_retry_queue_depth`,
   `service_cache_refresh_lag_seconds`, `process_cpu_seconds_total`,
   `process_resident_memory_bytes`, `go_goroutines`, `up`.
