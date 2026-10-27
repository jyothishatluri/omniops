# Elasticsearch source (`elasticsearch`)

Manifest: `src/omniops/sources/manifests/elasticsearch.yaml` (17 tables)
Fixtures: `src/omniops/sources/fixtures/elasticsearch/`
API reference root: https://www.elastic.co/docs/api/doc/elasticsearch/ (every operation page has a
Markdown twin at `<page>.md`, e.g.
https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-search.md, and the request /
response examples come from the `elastic/elasticsearch-specification` repository,
`specification/<namespace>/<api>/examples/`).

Checked against the official reference on 2026-10-26 (Elasticsearch 9.x reference; every API used
here also exists in 8.x unless noted). Kibana alerting rules, cases, SLOs and monitors are in the
separate `kibana` source (`docs/sources/kibana.md`); this source reads the alert *documents* that
those rules write (alerts-as-data indices) directly from Elasticsearch.

## Tables -> endpoints -> reference pages

| SQL table | HTTP | Official reference |
|---|---|---|
| `elasticsearch_search` | POST `/{index}/_search` (bool filter: range on `@timestamp`, `query_string`, ECS/OTel terms; `fields` API; `search_after` paging) | https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-search |
| `elasticsearch_esql` | POST `/_query` (`columnar: true`, Query DSL `filter` for the time window) | https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-esql-query |
| `elasticsearch_log_counts` | POST `/{index}/_search` (`size: 0`, `date_histogram` `fixed_interval`, `min_doc_count: 0`, `extended_bounds`, optional `terms` sub-agg) | https://www.elastic.co/docs/reference/aggregations/search-aggregations-bucket-datehistogram-aggregation |
| `elasticsearch_metric_histogram` | POST `/{index}/_search` (`date_histogram` + avg/min/max/sum/cardinality/value_count/percentiles, optional `terms`) | https://www.elastic.co/docs/reference/aggregations/search-aggregations-bucket-datehistogram-aggregation |
| `elasticsearch_top_terms` | POST `/{index}/_search` (`terms` agg, optional metric sub-agg) | https://www.elastic.co/docs/reference/aggregations/search-aggregations-bucket-terms-aggregation |
| `elasticsearch_indices` | GET `/_cat/indices/{index}?format=json&bytes=b&h=...` | https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-cat-indices |
| `elasticsearch_data_streams` | GET `/_data_stream/{name}` | https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-indices-get-data-stream |
| `elasticsearch_data_stream_stats` | GET `/_data_stream/{name}/_stats` | https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-indices-data-streams-stats-1 |
| `elasticsearch_field_caps` | GET `/{index}/_field_caps?fields=...&filters=-metadata` | https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-field-caps |
| `elasticsearch_cluster_health` | GET `/_cluster/health[/{index}]` | https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-cluster-health |
| `elasticsearch_nodes` | GET `/_cat/nodes?format=json&h=...` | https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-cat-nodes |
| `elasticsearch_shards` | GET `/_cat/shards[/{index}]?format=json&bytes=b&h=...,unassigned.reason,unassigned.at` | https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-cat-shards |
| `elasticsearch_alerts` | POST `/.alerts-*/_search?expand_wildcards=open,hidden` (Kibana alerts-as-data) | https://www.elastic.co/docs/explore-analyze/alerting/alerts/query-alerts |
| `elasticsearch_apm_traces` | POST `/traces-apm*/_search` (`processor.event` transaction / span) | https://www.elastic.co/docs/solutions/observability/apm/transactions (+ `/spans`) |
| `elasticsearch_apm_errors` | POST `/logs-apm.error-*/_search` (`processor.event: error`) | https://www.elastic.co/docs/solutions/observability/apm/errors |
| `elasticsearch_apm_service_stats` | POST `/traces-apm*/_search` (`terms` service.name + `filter` failure + `avg` / `percentiles` on `transaction.duration.us`) | https://www.elastic.co/docs/reference/aggregations/search-aggregations-bucket-terms-aggregation |
| `elasticsearch_apm_dependencies` | POST `/traces-apm*/_search` (`terms` service.name -> `terms` span.destination.service.resource) | https://www.elastic.co/docs/solutions/observability/apm/spans |

Roles bound: `logs` (elasticsearch_search), `log_counts` (elasticsearch_log_counts), `metrics`
(elasticsearch_esql), `active_alerts` + `alert_history` (elasticsearch_alerts), `traces` +
`trace_detail` (elasticsearch_apm_traces), `errors` (elasticsearch_apm_errors), `services`
(elasticsearch_apm_service_stats), `service_map` (elasticsearch_apm_dependencies), `hosts`
(elasticsearch_top_terms on host.name in metrics-*).

### Fixture provenance

| Fixture | Source |
|---|---|
| `search_response.json` | Verbatim example response of the Search API reference |
| `search_fields_response.json` | Verbatim example of "Retrieve selected fields" (fields API) |
| `search_shard_failure.json`, `search_timed_out.json` | Built strictly from the documented search response schema (`_shards.failures[]` = ShardFailure {shard, index, node, reason: ErrorCause}; `timed_out`) - `expect_error` fixtures |
| `esql_columnar.json` | Verbatim columnar example of the ES|QL REST API page |
| `esql_partial.json` | Built from the documented ES|QL response schema (`is_partial`) - `expect_error` fixture |
| `date_histogram.json`, `terms.json`, `terms_sub_agg.json` | Verbatim aggregation docs examples (aggregation names renamed to the manifest's `h` / `t` / `m`) |
| `date_histogram_grouped.json`, `metric_histogram*.json`, `apm_service_stats.json`, `apm_dependencies.json` | Built from the documented bucket / sub-aggregation shapes; values illustrative |
| `cat_indices.json`, `data_streams.json`, `data_stream_stats.json`, `field_caps.json`, `cluster_health.json` | Verbatim API reference examples |
| `cat_nodes.json`, `cat_shards.json`, `cat_shards_unassigned.json` | Verbatim `CatNodesResponseExample1`, `CatShardsResponseExample1`, `CatShardsResponseExample5` |
| `alerts_as_data.json` | Search envelope + documented alerts-as-data field names (fields-API and dotted `_source` forms) |
| `apm_traces.json`, `apm_errors.json` | APM data-model docs example transaction / span / error documents in the documented `_search` envelope |

## Auth

- `Authorization: ApiKey <encoded>` where `<encoded>` is the base64 `encoded` value returned by the
  create API key API (`ELASTICSEARCH_API_KEY`). Preferred; works on Elastic Cloud Hosted,
  Serverless and self-managed.
- Basic auth fallback (`ELASTICSEARCH_USERNAME` / `ELASTICSEARCH_PASSWORD`).
- Required privileges: `read` + `view_index_metadata` on the log / APM / metrics patterns,
  `read` on `.alerts-*` (hidden; also `.internal.alerts-*`), cluster `monitor` for `_cat/*`,
  `_cluster/health`, `_data_stream/_stats`. A key without index privileges gets 0 hits from
  wildcard patterns rather than a 403.

## Base URLs

- Elastic Cloud Hosted: `https://<deployment>.es.<region>.<provider>.cloud.es.io:443`
  (e.g. `https://my-deployment.es.us-central1.gcp.cloud.es.io`).
- Elastic Cloud Serverless: `https://<project>.es.<region>.<provider>.elastic.cloud`.
- Self-managed: `http(s)://<host>:9200`.
- Cross-cluster search: prefix index patterns with the remote alias (`remote:logs-*`).
- `KIBANA_URL` (optional) is used only for Discover / APM deep links.

## Rate limits

No fixed request quota. Overload shows up as HTTP 429 (`es_rejected_execution_exception`, search
thread-pool queue full) or `circuit_breaking_exception`; the engine retries 429/502/503/504 with
backoff. Keep `size` <= 1000 (the default `index.max_result_window` is 10,000 from+size; this
manifest pages with `search_after`, max 3 pages). `search.max_buckets` (65,536) bounds
date_histogram x terms combinations. ES|QL returns at most 1,000 rows without `LIMIT` and 10,000
with it (`esql.query.result_truncation_max_size`).

## Retention (defaults, all configurable)

| Data | Default |
|---|---|
| `logs-*-*` (logs@lifecycle) | rollover only, no delete |
| `traces-apm` | 10 days |
| `traces-apm.rum` | 90 days |
| `logs-apm.error`, `logs-apm.app` | 10 days |
| `metrics-apm.app`, `metrics-apm.internal` | 90 days |
| `metrics-apm.{transaction,service_transaction,service_summary,service_destination}_1m / _10m / _60m` | 90 / 180 / 390 days |
| `.alerts-*` | rollover only, no delete |

APM numbers from https://www.elastic.co/docs/solutions/observability/apm/index-lifecycle-management
(the page warns defaults can change between minor versions). Check the real policy with
`elasticsearch_data_streams.ilm_policy` / `lifecycle`.

## Time formats

- Range filters: `gte` / `lte` as RFC 3339 with milliseconds (`2026-10-01T11:00:00.000Z`) and
  `format: strict_date_optional_time` (UTC).
- `date_histogram`: `fixed_interval` `<n>s`, `extended_bounds.min/max` in epoch milliseconds;
  bucket `key` is epoch milliseconds (UTC).
- `fields` API returns dates as ISO strings (or epoch-millis strings when mapped that way); `_source`
  keeps whatever the shipper sent.
- `_data_stream/_stats.maximum_timestamp`: epoch milliseconds.
- Durations: APM `transaction.duration.us` / `span.duration.us` and `kibana.alert.duration.us` are
  MICROseconds; OTel-native `duration` is NANOseconds. Columns named `duration_ms` are converted.

## Pitfalls

- An unknown field or index pattern returns 0 hits, not an error. Confirm patterns with
  `elasticsearch_data_streams` / `elasticsearch_indices` and fields with `elasticsearch_field_caps`.
  ES|QL is the exception: unknown fields are a loud 400 `verification_exception`.
- ECS vs OTel field names: Elastic Agent / APM / Beats use `service.name`, `log.level`,
  `host.name`, `trace.id`; OTel-native data streams (`*.otel-*`) use
  `resource.attributes.service.name`, `severity_text`, `body.text`, `trace_id`. The canonical
  columns and filters try both.
- keyword fields are case-sensitive (`error` != `ERROR`); text fields need `query_string` or the
  `.keyword` sub-field for exact matches; `terms` aggregations on text fields fail with
  "Fielddata is disabled".
- A 200 response can be partial: `_shards.failures` (mapping conflicts, bad query on some indices),
  `timed_out: true`, ES|QL `is_partial: true`. These are declared as error rules and fail loudly.
- APM traces are sampled: counts from `traces-apm*` are sampled counts; use ratios (failure rate)
  or the `metrics-apm.service_transaction.*` data streams for throughput.
- Hidden / dot-prefixed indices (`.alerts-*`) need `expand_wildcards=open,hidden`.
- `query` filters are Lucene `query_string` (AND / OR / NOT upper case), not KQL.
- `_cat` APIs return strings; the tables cast numbers. `bytes=b` gives sizes in bytes.
- `include_empty_fields` (field caps `hide_empty`) needs 8.13+; older clusters reject unknown
  parameters with HTTP 400.
- Cross-cluster search silently skips unavailable remotes when `skip_unavailable` is true.
