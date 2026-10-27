# OpenSearch source (`opensearch`)

Manifest: `src/omniops/sources/manifests/opensearch.yaml` (15 tables)
Fixtures: `src/omniops/sources/fixtures/opensearch/`
API reference root: https://docs.opensearch.org/latest/api-reference/ (page sources in
`opensearch-project/documentation-website`, which is what was read; Trace Analytics index
schemas from `opensearch-project/data-prepper/docs/schemas/trace-analytics/`).

Checked against the official reference on 2026-10-26 (OpenSearch 3.x docs; the APIs used here
exist since 1.x/2.x with the `_plugins` prefix). Covers self-managed OpenSearch and Amazon
OpenSearch Service domains with fine-grained access control (internal users).

## Tables -> endpoints -> reference pages

| SQL table | HTTP | Official reference |
|---|---|---|
| `opensearch_search` | POST `/{index}/_search` (bool filter: range on `@timestamp`/time_field, `query_string`, multi-schema terms; `fields`; `search_after`) | https://docs.opensearch.org/latest/api-reference/search-apis/search/ |
| `opensearch_ppl` | POST `/_plugins/_ppl?format=jdbc` (`query` + Query DSL `filter`) | https://docs.opensearch.org/latest/sql-and-ppl/sql-and-ppl-api/index/ |
| `opensearch_sql` | POST `/_plugins/_sql?format=jdbc` (`query` + `filter`) | https://docs.opensearch.org/latest/sql-and-ppl/sql-and-ppl-api/index/ |
| `opensearch_log_counts` | POST `/{index}/_search` (`date_histogram` `fixed_interval`, `min_doc_count: 0`, `extended_bounds`, optional `terms`) | https://docs.opensearch.org/latest/aggregations/bucket/date-histogram/ |
| `opensearch_metric_histogram` | POST `/{index}/_search` (`date_histogram` + metric sub-aggregation) | https://docs.opensearch.org/latest/aggregations/bucket/date-histogram/ |
| `opensearch_top_terms` | POST `/{index}/_search` (`terms`) | https://docs.opensearch.org/latest/aggregations/bucket/terms/ |
| `opensearch_alerting_monitors` | GET `/_plugins/_alerting/monitors/_search` (Query DSL body: `ids`, `match_phrase monitor.name`, `monitor.enabled`, `monitor.monitor_type`) | https://docs.opensearch.org/latest/observing-your-data/alerting/api/#search-monitors |
| `opensearch_alerts` | GET `/_plugins/_alerting/monitors/alerts` (`alertState`, `severityLevel`, `monitorId`, `searchString`, `sortString`, `sortOrder`, `size`, `startIndex`) | https://docs.opensearch.org/latest/observing-your-data/alerting/api/#get-alerts |
| `opensearch_trace_spans` | POST `/otel-v1-apm-span-*/_search` (range on `startTime`) | https://github.com/opensearch-project/data-prepper/blob/main/docs/schemas/trace-analytics/otel-v1-apm-span-index-template.md |
| `opensearch_service_map` | POST `/otel-v1-apm-service-map*/_search` | https://github.com/opensearch-project/data-prepper/blob/main/docs/schemas/trace-analytics/otel-v1-apm-service-map-index-template.md |
| `opensearch_indices` | GET `/_cat/indices/{index}?format=json&bytes=b&h=...` | https://docs.opensearch.org/latest/api-reference/cat/cat-indices/ |
| `opensearch_cluster_health` | GET `/_cluster/health[/{index}]` | https://docs.opensearch.org/latest/api-reference/cluster-api/cluster-health/ |
| `opensearch_nodes` | GET `/_cat/nodes?format=json&h=...` | https://docs.opensearch.org/latest/api-reference/cat/cat-nodes/ |
| `opensearch_data_streams` | GET `/_data_stream[/{name}]` | https://docs.opensearch.org/latest/api-reference/data-stream/data-stream-info/ |
| `opensearch_data_stream_stats` | GET `/_data_stream[/{name}]/_stats` | https://docs.opensearch.org/latest/api-reference/data-stream/data-stream-stats/ |

Roles bound: `logs` (opensearch_search), `log_counts` (opensearch_log_counts), `metrics`
(opensearch_ppl), `alert_rules` (opensearch_alerting_monitors), `active_alerts` +
`alert_history` (opensearch_alerts), `traces` + `trace_detail` (opensearch_trace_spans),
`service_map` (opensearch_service_map).

### Fixture provenance

| Fixture | Source |
|---|---|
| `search_response.json`, `search_fields_response.json` | Verbatim Search API reference examples (movies index; "Retrieve specific fields") |
| `search_timed_out.json` | The Search API example with `timed_out: true` (documented as "results may be partial or empty") - `expect_error` fixture |
| `ppl_response.json`, `sql_response.json` | Verbatim JDBC-format examples of the SQL/PPL API and Response formats pages |
| `date_histogram.json`, `terms.json` | Verbatim aggregation docs examples (agg names renamed to `h` / `t`) |
| `date_histogram_grouped.json`, `metric_histogram.json` | Built from documented bucket / sub-aggregation shapes; values illustrative |
| `alerting_monitors_search.json`, `alerting_alerts.json` | Verbatim "Search monitors" and "Get alerts" examples of the Alerting API |
| `trace_spans.json`, `service_map.json` | Data Prepper example documents from the trace-analytics index-template docs, in the documented `_search` envelope |
| `cat_indices.json`, `cat_nodes.json` | Built from the documented `?v` text examples keyed by their column headers (format=json shape) |
| `cat_indices_system.json`, `cluster_health.json`, `data_streams.json`, `data_stream_stats.json` | Verbatim API reference examples |

Not covered: Alerting findings (`GET /_plugins/_alerting/findings/_search`) - the reference
documents the parameters but publishes no response example or schema, so no fixture could be
built without inventing the shape.

## Auth

- Basic auth with a security-plugin internal user (`OPENSEARCH_USERNAME` / `OPENSEARCH_PASSWORD`),
  or `Authorization: Bearer <token>` (`OPENSEARCH_TOKEN`: JWT / on-behalf-of token).
- Amazon OpenSearch Service domains that use IAM-only access require AWS SigV4 (service `es`;
  `aoss` for Serverless collections) - not configured in this manifest; use an FGAC internal user
  or a signing proxy. (The engine supports `aws_sigv4` auth; a variant manifest could add it.)
- Privileges: `indices:data/read/search*` on log / trace indices, `cluster:admin/opendistro/alerting/*`
  read actions (`alerting_read_access` role) for monitors and alerts, `cluster:monitor/*` for
  `_cat` / health, `indices:admin/data_stream/get` for data streams, `cluster:admin/opensearch/ppl`
  and `cluster:admin/opendistro/sql` for PPL / SQL.

## Base URLs

- Self-managed: `https://<host>:9200`.
- Amazon OpenSearch Service: `https://search-<domain>-<id>.<region>.es.amazonaws.com` (or the
  custom endpoint).
- `OPENSEARCH_DASHBOARDS_URL` (optional) is used only for Trace Analytics deep links.

## Rate limits

No request quota in OpenSearch itself. Overload returns 429 (search thread pool rejections, search
backpressure); retried with backoff. SQL/PPL results are capped by `plugins.query.size_limit`;
`index.max_result_window` (10,000) bounds from+size (this manifest uses `search_after`).

## Retention

Set by ISM policies (no default deletion). Data Prepper's `raw-span-policy` rolls span indices
over; deletion is opt-in. Alert history (`.opendistro-alerting-alert-history-*`) is kept for
`plugins.alerting.alert_history_retention_period` (default 60 days) while history is enabled.

## Time formats

- Range filters: RFC 3339 with milliseconds and `format: strict_date_optional_time`.
- `date_histogram` `extended_bounds` and bucket `key`: epoch milliseconds.
- Alerting plugin times (`start_time`, `end_time`, `acknowledged_time`, `last_notification_time`,
  monitor `last_update_time`, `enabled_time`): epoch milliseconds.
- Data Prepper spans: `startTime` / `endTime` ISO strings (date_nanos), `durationInNanos`
  NANOseconds (duration_ms columns are converted).
- PPL / SQL return timestamps as `yyyy-MM-dd HH:mm:ss[.SSS]` strings in UTC.
- `_data_stream/_stats.maximum_timestamp`: epoch milliseconds.

## Pitfalls

- Unknown fields / index patterns return 0 hits, not errors; dynamically mapped strings need the
  `.keyword` sub-field for exact matches and aggregations.
- Log schemas vary by shipper: Fluent Bit (`log`, `kubernetes.pod_name`), Data Prepper OTel
  (`body`, `severityText`, `traceId`, `resource.attributes.service@name` - dots become `@`), SS4O
  (`ss4o_logs-*`: `body`, `severity.text`), Logstash / ECS (`message`, `log.level`). The canonical
  columns coalesce them.
- PPL / SQL window filter targets `@timestamp`; spans use `startTime` and OTel logs `time` - set
  `time_field` or every query returns 0 rows.
- Alert state `ERROR` means the monitor failed to run (blind spot), not an application error.
- `status.code` on spans is the OTel status (2 = error); HTTP codes are in
  `span.attributes.http@status_code`.
- A 200 search response with `timed_out: true` or `_shards.failures` is partial; both are declared
  error rules.
