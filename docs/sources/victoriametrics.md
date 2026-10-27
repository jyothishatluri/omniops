# VictoriaMetrics (`victoriametrics`)

Manifest: `src/omniops/sources/manifests/victoriametrics.yaml` (extends `templates/prometheus.yaml`).
Fixtures: `src/omniops/sources/fixtures/victoriametrics/`.

Reference: [URL examples (every API, single + cluster)](https://docs.victoriametrics.com/victoriametrics/url-examples/) ·
[Prometheus querying API usage + enhancements](https://docs.victoriametrics.com/victoriametrics/single-server-victoriametrics/#prometheus-querying-api-usage) ·
[Timestamp formats](https://docs.victoriametrics.com/victoriametrics/single-server-victoriametrics/#timestamp-formats) ·
[JSON line export](https://docs.victoriametrics.com/victoriametrics/single-server-victoriametrics/#how-to-export-data-in-json-line-format) ·
[TSDB stats](https://docs.victoriametrics.com/victoriametrics/single-server-victoriametrics/#tsdb-stats) ·
[vmalert web API](https://docs.victoriametrics.com/victoriametrics/vmalert/#web) ·
[Cluster URL format](https://docs.victoriametrics.com/victoriametrics/cluster-victoriametrics/#url-format) ·
[MetricsQL](https://docs.victoriametrics.com/victoriametrics/metricsql/)

## Tables

Paths are relative to the base URL: single-node `VM_URL`, cluster `VM_URL/select/<VM_TENANT>/prometheus`.

| SQL table | HTTP | Official doc |
|---|---|---|
| `victoriametrics_query_range` | GET `/api/v1/query_range` (+ `nocache=1`; `stats` object → `series_fetched`, `execution_ms`) | https://docs.victoriametrics.com/victoriametrics/url-examples/#apiv1query_range |
| `victoriametrics_query` | GET `/api/v1/query` | https://docs.victoriametrics.com/victoriametrics/url-examples/#apiv1query |
| `victoriametrics_series` | GET `/api/v1/series` (`limit` honoured) | https://docs.victoriametrics.com/victoriametrics/url-examples/#apiv1series |
| `victoriametrics_label_values` | GET `/api/v1/label/{name}/values` | https://docs.victoriametrics.com/victoriametrics/url-examples/#apiv1labelvalues |
| `victoriametrics_labels` | GET `/api/v1/labels` | https://docs.victoriametrics.com/victoriametrics/url-examples/#apiv1labels |
| `victoriametrics_metric_metadata` | GET `/api/v1/metadata` | https://docs.victoriametrics.com/victoriametrics/url-examples/#apiv1metadata |
| `victoriametrics_export` | GET `/api/v1/export` (JSON lines; raw samples) | https://docs.victoriametrics.com/victoriametrics/url-examples/#apiv1export |
| `victoriametrics_tsdb_status` | GET `/api/v1/status/tsdb` (`topN`, `date`, `focusLabel`, `match[]`) | https://docs.victoriametrics.com/victoriametrics/single-server-victoriametrics/#tsdb-stats (URL forms: https://docs.victoriametrics.com/victoriametrics/url-examples/#apiv1statustsdb) |
| `victoriametrics_targets` | GET `VMAGENT_URL or VM_URL` + `/api/v1/targets` | https://docs.victoriametrics.com/victoriametrics/url-examples/#apiv1targets |
| `victoriametrics_alerts` | GET `VMALERT_URL or base` + `/api/v1/alerts` | https://docs.victoriametrics.com/victoriametrics/vmalert/#web |
| `victoriametrics_rules` | GET `VMALERT_URL or base` + `/api/v1/rules` (`type`, `rule_name[]`, `rule_group[]`, `file[]`, `state`, always `extended_states=true`) | https://docs.victoriametrics.com/victoriametrics/vmalert/#web |

Roles: `metrics` → `victoriametrics_query_range` (MetricsQL); `active_alerts` → `victoriametrics_alerts`;
`alert_rules` → `victoriametrics_rules`; `hosts` → `victoriametrics_targets`.

Not modelled: `/api/v1/export/csv|native`, `/federate`, `/api/v1/series/count`, `/api/v1/status/active_queries`
and `/top_queries` (no documented response example; top_queries mixes scalar settings and several lists in one
object), admin/snapshot endpoints, Graphite APIs.

## Auth

VictoriaMetrics has no built-in auth; vmauth/vmgateway front it. Optional `VM_TOKEN` (bearer) or
`VM_USERNAME`/`VM_PASSWORD` (basic).

## Base URLs

- Single node: `VM_URL=http://<vmsingle>:8428` (all Prometheus handlers also work under `/prometheus`).
- Cluster: `VM_URL=http://<vmselect>:8481`, `VM_TENANT=<accountID>[:<projectID>]` → `/select/<tenant>/prometheus`;
  `VM_TENANT=multitenant` reads all tenants (adds `vm_account_id`/`vm_project_id` labels).
- vmalert: `VMALERT_URL=http://<vmalert>:8880`, or `-vmalert.proxyURL` on vmsingle/vmselect.
- Targets: vmsingle or `VMAGENT_URL=http://<vmagent>:8429` (vmselect has no targets API).

## Rate limits

None at the API. `-search.maxConcurrentRequests`, `-search.maxQueueDuration` and `-search.maxQueryDuration`
(default 30s) queue/abort queries; `-search.maxSeries`, `-search.maxSamplesPerQuery`, `-search.maxExportDuration`
cap result sizes.

## Retention

`-retentionPeriod` (default 1 month). Metadata is an in-memory ring buffer (not persisted).

## Time formats

`start`/`end`/`time`: Unix seconds (float), ms, µs, ns, RFC 3339, partial RFC 3339 or relative (`-1h`); the
manifest sends epoch seconds. Query responses: Unix seconds. Export JSON lines: `timestamps` in **milliseconds**.
TSDB stats `date=YYYY-MM-DD` (UTC day). vmalert: RFC 3339 `activeAt` / `lastEvaluation`.

## Pitfalls

- `/api/v1/labels`, `/label/.../values`, `/series` round `start..end` to whole UTC days and default to the last
  day - values outside the incident window can appear.
- MetricsQL `rate`/`increase` do not extrapolate like Prometheus; small numeric differences are expected.
- The rollup result cache can hide late/backfilled samples: `nocache = true`.
- `series_fetched = 0` means the selector matched no series (typo / wrong label), not "value was zero".
- vmalert `state = nomatch` (or `last_series_fetched = 0`) marks rules that can never fire. vmalert only reports
  the `ok` / `nomatch` / `unhealthy` states when `extended_states=true` is passed (checked in
  `app/vmalert/web.go` + `rule.ExtendState`); the manifest always sends it.
- Cluster `/api/v1/status/tsdb` sums per-vmstorage counts (inflated by replication).
- `/api/v1/export` returns every raw sample: keep selectors and windows narrow (max 6h here, 20k rows).
- `/api/v1/status/tsdb` is *similar to* Prometheus but not identical: VictoriaMetrics returns `totalSeries`,
  `totalLabelValuePairs`, `seriesCountByMetricName` (optionally with `requestsCount` / `lastRequestTimestamp`),
  `seriesCountByLabelName`, `seriesCountByFocusLabelValue`, `seriesCountByLabelValuePair` and
  `labelValueCountByLabelName` - no `headStats`. The docs show no example body, so the fixture is built from the
  response template `app/vmselect/prometheus/tsdb_status_response.qtpl`.
- Errors come back as 4xx/5xx with the Prometheus error envelope; the 2xx error rule is proven by
  `query_error.json` (built from the documented envelope).
- `series.json` / `metadata.json` are not copied: the engine falls back to the template's
  `fixtures/prometheus/` files.
