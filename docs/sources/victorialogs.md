# VictoriaLogs (`victorialogs`)

Manifest: `src/omniops/sources/manifests/victorialogs.yaml` (standalone). Fixtures:
`src/omniops/sources/fixtures/victorialogs/` (all from the querying docs' example responses; two doc examples
had JSON syntax slips that were fixed).

Reference: [Querying HTTP API](https://docs.victoriametrics.com/victorialogs/querying/) ·
[LogsQL](https://docs.victoriametrics.com/victorialogs/logsql/) ·
[Timestamp formats](https://docs.victoriametrics.com/victoriametrics/single-server-victoriametrics/#timestamp-formats) ·
[Multitenancy](https://docs.victoriametrics.com/victorialogs/#multitenancy)

## Tables

| SQL table | HTTP | Official doc |
|---|---|---|
| `victorialogs_logs` | GET `/select/logsql/query` (JSON lines; `limit` = newest N) | https://docs.victoriametrics.com/victorialogs/querying/#querying-logs |
| `victorialogs_hits` | GET `/select/logsql/hits` (`step`, repeated `field`, `fields_limit`) | https://docs.victoriametrics.com/victorialogs/querying/#querying-hits-stats |
| `victorialogs_stats_range` | GET `/select/logsql/stats_query_range` (Prometheus matrix) | https://docs.victoriametrics.com/victorialogs/querying/#querying-log-range-stats |
| `victorialogs_stats` | GET `/select/logsql/stats_query` (Prometheus vector) | https://docs.victoriametrics.com/victorialogs/querying/#querying-log-stats |
| `victorialogs_facets` | GET `/select/logsql/facets` | https://docs.victoriametrics.com/victorialogs/querying/#querying-facets |
| `victorialogs_streams` | GET `/select/logsql/streams` | https://docs.victoriametrics.com/victorialogs/querying/#querying-streams |
| `victorialogs_field_names` | GET `/select/logsql/field_names` | https://docs.victoriametrics.com/victorialogs/querying/#querying-field-names |
| `victorialogs_field_values` | GET `/select/logsql/field_values` | https://docs.victoriametrics.com/victorialogs/querying/#querying-field-values |

Roles: `logs` → `victorialogs_logs` (LogsQL); `log_counts` → `victorialogs_hits`. The role filter builds
`(service.name:="svc" OR service:="svc" OR app:="svc")` and, for error_only,
`(level:~"(?i)err|fatal|crit|panic" OR i(error))`.

Not modelled: `/select/logsql/tail` (live streaming), `stream_ids`, `stream_field_names|values`,
`/select/tenant_ids`, CSV output.

## Auth and tenancy

No built-in auth (vmauth in front): optional `VICTORIALOGS_TOKEN` (bearer) or basic auth. Tenant via
`AccountID` / `ProjectID` headers (`VICTORIALOGS_ACCOUNT_ID`, `VICTORIALOGS_PROJECT_ID`; default 0:0).

## Base URL

`VICTORIALOGS_URL` = `http://<victorialogs>:9428` (single node) or the vlselect / vmauth root.

## Rate limits

None at the API; `-search.maxConcurrentRequests` / `-search.maxQueueDuration` queue queries,
`-search.maxQueryDuration` aborts (override lower with `timeout`), `-search.maxQueryTimeRange` rejects broad
ranges. Cluster: 502 if a vlstorage is down unless `allow_partial_response=1`.

## Retention

`-retentionPeriod` (default 7 days).

## Time formats

`start`/`end`/`time`: RFC 3339 (manifest), Unix s/ms/µs/ns, partial RFC 3339 or relative; `end` is exclusive.
`step`/`offset`: LogsQL durations (`5m`, `1h`). Responses: `_time` RFC 3339 (nanoseconds); hits timestamps RFC
3339; stats endpoints Unix seconds.

## Pitfalls

- Field names depend on ingestion (service.name vs service vs app; level vs severity): check
  `victorialogs_field_names` first - a filter on a missing field silently returns 0 rows.
- Stream filters `{app="x"}` are fast but only for declared stream fields (`victorialogs_streams`).
- `/select/logsql/query` without `limit` streams all matches unsorted; the manifest always sends a limit (newest first).
- stats endpoints require a `| stats ...` pipe; `stats_query_range` forbids pipes that change `_time` before it.
- Hits buckets are aligned to the step (first bucket may start before the window); empty buckets are zero-filled by the engine.
- `streams` / `field_values` with `limit` return an arbitrary subset with `hits` zeroed when the limit is hit.
- `stats_query` rows are stamped with the evaluation time (= window end), so `victorialogs_stats` does not
  re-apply time predicates in SQL (`time.reapply: false`).
- `/select/logsql/facets` hides fields with a single constant value unless `keep_const_fields = true`, and fields
  with too many / too long values (`max_values_per_field`, `max_value_len`).
- `field_names` / `field_values` accept `filter=<substring>` (the `contains` column).
- `-search.maxQueryTimeRange` may reject wide windows; `allow_partial_response=1` (not sent by the manifest) would
  turn a missing vlstorage node into silently partial results, so the default 502 is kept.
- Errors come back as non-2xx plain-text bodies; the `status=error` rule only applies to the Prometheus-shaped
  stats endpoints.
