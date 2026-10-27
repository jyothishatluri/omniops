# Grafana Mimir / Grafana Cloud Metrics / GEM (`mimir`)

Manifest: `src/omniops/sources/manifests/mimir.yaml` (extends `templates/prometheus.yaml`, drops `mimir_targets`
because Mimir has no scrape-target API). Fixtures: `src/omniops/sources/fixtures/mimir/`.

Reference: [Grafana Mimir HTTP API](https://grafana.com/docs/mimir/latest/references/http-api/) ·
[Grafana Cloud: query the Metrics HTTP APIs](https://grafana.com/docs/grafana-cloud/send-data/metrics/metrics-prometheus/query-http-api/) ·
[Prometheus HTTP API](https://prometheus.io/docs/prometheus/latest/querying/api/) (Mimir's compatible endpoints link to it) ·
[Alertmanager API v2 OpenAPI](https://github.com/prometheus/alertmanager/blob/main/api/v2/openapi.yaml)

## Tables

Paths are relative to `MIMIR_URL + MIMIR_PROMETHEUS_PREFIX` (default `/prometheus`) unless shown absolute.

| SQL table | HTTP | Official doc |
|---|---|---|
| `mimir_query_range` | GET `<prefix>/api/v1/query_range` | https://grafana.com/docs/mimir/latest/references/http-api/#range-query |
| `mimir_query` | GET `<prefix>/api/v1/query` | https://grafana.com/docs/mimir/latest/references/http-api/#instant-query |
| `mimir_series` | GET `<prefix>/api/v1/series` | https://grafana.com/docs/mimir/latest/references/http-api/#get-series-by-label-matchers |
| `mimir_label_values` | GET `<prefix>/api/v1/label/{name}/values` | https://grafana.com/docs/mimir/latest/references/http-api/#get-label-values |
| `mimir_labels` | GET `<prefix>/api/v1/labels` | https://grafana.com/docs/mimir/latest/references/http-api/#get-label-names |
| `mimir_metric_metadata` | GET `<prefix>/api/v1/metadata` | https://grafana.com/docs/mimir/latest/references/http-api/#get-metric-metadata |
| `mimir_exemplars` | GET `<prefix>/api/v1/query_exemplars` | https://grafana.com/docs/mimir/latest/references/http-api/#exemplar-query |
| `mimir_active_series` | GET `<prefix>/api/v1/cardinality/active_series` | https://grafana.com/docs/mimir/latest/references/http-api/#get-active-series-by-selector |
| `mimir_cardinality_label_names` | GET `<prefix>/api/v1/cardinality/label_names` | https://grafana.com/docs/mimir/latest/references/http-api/#label-names-cardinality |
| `mimir_cardinality_label_values` | GET `<prefix>/api/v1/cardinality/label_values` | https://grafana.com/docs/mimir/latest/references/http-api/#label-values-cardinality |
| `mimir_alerts` | GET `<prefix>/api/v1/alerts` (ruler) | https://grafana.com/docs/mimir/latest/references/http-api/#list-prometheus-alerts |
| `mimir_rules` | GET `<prefix>/api/v1/rules` (ruler; `group_limit` + `group_next_token` pagination) | https://grafana.com/docs/mimir/latest/references/http-api/#list-prometheus-rules |
| `mimir_alertmanager_alerts` | GET `MIMIR_URL/alertmanager/api/v2/alerts` | https://grafana.com/docs/mimir/latest/references/http-api/#alertmanager-api |
| `mimir_silences` | GET `MIMIR_URL/alertmanager/api/v2/silences` | https://grafana.com/docs/mimir/latest/references/http-api/#alertmanager-api |

Roles: `metrics` → `mimir_query_range` (PromQL); `active_alerts` → `mimir_alerts` and `mimir_alertmanager_alerts`;
`alert_rules` → `mimir_rules`.

Not modelled: `/config/v1/rules` (returns YAML; the engine has no YAML response format, and `/api/v1/rules`
carries the same definitions), the streaming `/api/v1/search/*` endpoints (experimental, NDJSON with a trailer),
remote read (protobuf), admin/ring/status pages.

## Auth

- Self-hosted Mimir/GEM: `X-Scope-OrgID: <tenant>` (`MIMIR_TENANT_ID`) when `-auth.multitenancy-enabled=true`
  (default). `tenant-a|tenant-b` queries several tenants when tenant federation is enabled. Optional
  `MIMIR_TOKEN` (bearer) for GEM access tokens or an auth proxy.
- Grafana Cloud: HTTP basic auth, username = metrics instance ID (`MIMIR_USERNAME`), password = Cloud access
  policy token with `metrics:read` (`MIMIR_PASSWORD`). Bearer tokens are rejected. No `X-Scope-OrgID`.

## Base URLs

- `MIMIR_URL` = gateway/query-frontend root (e.g. `http://mimir-nginx.mimir.svc:80`), `MIMIR_PROMETHEUS_PREFIX`
  defaults to `/prometheus` (`-http.prometheus-http-prefix`), Alertmanager under `/alertmanager`
  (`-http.alertmanager-http-prefix`) or `MIMIR_ALERTMANAGER_URL`.
- Grafana Cloud: `MIMIR_URL=https://prometheus-prod-<NN>-<region>.grafana.net`; the Mimir-style prefix
  `/prometheus` serves the Prometheus API and the cardinality APIs. Some Mimir endpoints are not exposed in
  Grafana Cloud or use other hosts; the Grafana-managed ruler is proxied through the stack URL instead
  (`/api/prometheus/<datasource-uid>/api/v1/rules`), which this manifest does not cover.

## Limits and rate limits

Per-tenant read limits (max fetched series / chunks / bytes per query, `-query-frontend.max-total-query-length`,
`-querier.max-query-lookback`, query parallelism) fail the query with 4xx/422 messages rather than 429. Grafana
Cloud enforces plan limits and answers 429 when exceeded. Cardinality endpoints: `limit` 0-500 (default 20) and
disabled unless `-querier.cardinality-analysis-enabled`.

## Retention

Per tenant `-compactor.blocks-retention-period` (default 0 = keep forever). Grafana Cloud: 13 months on paid
plans, 14 days on the Free tier ([pricing](https://grafana.com/pricing/)).

## Time formats

Prometheus API: `start`/`end`/`time` as Unix seconds (float) or RFC 3339 - the manifest sends epoch seconds;
`step` as seconds or a duration. Responses: sample timestamps in Unix seconds; `activeAt`/`lastEvaluation` RFC
3339. Alertmanager v2: RFC 3339 `startsAt`/`endsAt`/`updatedAt`.

## Pitfalls

- Missing `X-Scope-OrgID` → 401 `no org id`; a wrong tenant silently returns 0 series.
- Gaps can be ingestion limits (discarded samples, out-of-order), not outages.
- Exemplar storage is off by default (`-ingester.max-global-exemplars-per-user=0`) and only covers ingester data.
- `/api/v1/rules` returns 422 if both alerting and recording evaluation are disabled for the tenant.
- `health = err` on a rule means it is not evaluating: an alert that "never fired" may be broken.
- The query-frontend results cache can serve stale label / cardinality answers (configurable TTLs).
- Metadata only exists if the remote-write sender included it.
- Range/instant responses can carry `warnings` and info-level `infos` annotations (e.g. "metric might not be a
  counter"); both are exposed as JSON columns.
- Grafana Cloud does not expose every Mimir endpoint on the metrics host (ruler / Alertmanager tables may 404
  there); Grafana-managed rules sit behind the stack URL with a different credential and are not covered.
- The Mimir HTTP API reference lists the Alertmanager UI under `<alertmanager-http-prefix>`; the tenant's
  upstream Alertmanager v2 API (`/api/v2/alerts`, `/api/v2/silences`) is served under the same prefix (what
  Grafana's Mimir Alertmanager data source calls) but is not spelled out route by route in the Mimir reference.
- 2xx error rule (`status=error`) is proven by `query_error.json` (built from the documented Prometheus
  envelope). `series.json`, `metadata.json` and `query_vector.json` are not copied: the engine falls back to
  `fixtures/prometheus/`.
