# Dynatrace source (`dynatrace`): Environment API v2

Manifest: `src/omniops/sources/manifests/dynatrace.yaml` (17 tables)
Fixtures: `src/omniops/sources/fixtures/dynatrace/`
API reference root: https://docs.dynatrace.com/docs/dynatrace-api/environment-api
Grail / DQL lives in a separate source with its own host and token: `dynatrace_grail`
(see `docs/sources/dynatrace_grail.md`).

Checked against the official reference on 2026-10-26. Paths, parameter names, time units,
pagination rules and response paths below come from those pages. Most fixtures are the docs'
verbatim example responses or verbatim "JSON model" blocks; where the docs only show placeholder
values (`"string"`, `1`), one extra fixture was built strictly from the documented schema and the
fixture `note` says so.

## Tables -> endpoints -> reference pages

| SQL table | HTTP | Official reference |
|---|---|---|
| `dynatrace_metrics` | GET `/api/v2/metrics/query` | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/metric-v2/get-data-points |
| `dynatrace_metric_descriptors` | GET `/api/v2/metrics` | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/metric-v2/get-all-metrics |
| `dynatrace_entities` | GET `/api/v2/entities` | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/entity-v2/get-entities-list |
| `dynatrace_relationships_out` | GET `/api/v2/entities?fields=+fromRelationships` (exploded) | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/entity-v2/get-entities-list |
| `dynatrace_relationships_in` | GET `/api/v2/entities?fields=+toRelationships` (exploded) | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/entity-v2/get-entities-list |
| `dynatrace_entity_types` | GET `/api/v2/entityTypes` | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/entity-v2/get-all-entity-types |
| `dynatrace_problems` | GET `/api/v2/problems` | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/problems-v2/problems/get-problems-list |
| `dynatrace_problem_evidence` | GET `/api/v2/problems/{problemId}?fields=evidenceDetails` | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/problems-v2/problems/get-problem-details (subtypes: https://docs.dynatrace.com/docs/dynatrace-api/environment-api/problems-v2/models) |
| `dynatrace_problem_impacts` | GET `/api/v2/problems/{problemId}?fields=impactAnalysis` | same as above |
| `dynatrace_problem_comments` | GET `/api/v2/problems/{problemId}/comments` | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/problems-v2/comments/get-all |
| `dynatrace_events` | GET `/api/v2/events` | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/events-v2/get-events |
| `dynatrace_event_types` | GET `/api/v2/eventTypes` | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/events-v2/get-event-types |
| `dynatrace_logs` | GET `/api/v2/logs/search` (follows `nextSliceKey`) | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/log-monitoring-v2/get-search-logs |
| `dynatrace_log_counts` | GET `/api/v2/logs/aggregate` | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/log-monitoring-v2/get-aggregate-logs |
| `dynatrace_slos` | GET `/api/v2/slo` | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/service-level-objectives-classic/get-all |
| `dynatrace_audit_logs` | GET `/api/v2/auditlogs` | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/audit-logs/get-log |
| `dynatrace_releases` | GET `/api/v2/releases` | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/releaseapi/get-releaseall |

Selector references: entity selector
https://docs.dynatrace.com/docs/dynatrace-api/environment-api/entity-v2/entity-selector, metric
selector https://docs.dynatrace.com/docs/dynatrace-api/environment-api/metric-v2/metric-selector,
throttling https://docs.dynatrace.com/docs/dynatrace-api/basics/access-limit.

## Roles bound

| Role | Table | Default filters |
|---|---|---|
| `metrics` | `dynatrace_metrics` | query language `dynatrace_metric_selector` |
| `active_alerts` | `dynatrace_problems` | `status = OPEN` |
| `alert_history`, `incidents` | `dynatrace_problems` | none |
| `logs` | `dynatrace_logs` | `level = ERROR` when error_only, `host`, `query` |
| `log_counts` | `dynatrace_log_counts` | `group_by = log.source`, `level = ERROR` when error_only |
| `services` / `hosts` | `dynatrace_entities` | `type = SERVICE` / `type = HOST` |
| `service_map` | `dynatrace_relationships_out` | `from_type = SERVICE`, `relationship = calls` |
| `change_events` | `dynatrace_events` (CUSTOM_DEPLOYMENT, CUSTOM_CONFIGURATION), `dynatrace_audit_logs` (category CONFIG), `dynatrace_releases` | as listed |
| `slo` | `dynatrace_slos` | none |

## Auth

- Header `Authorization: Api-Token <token>` (classic access token, `dt0c01.*`).
- Read scopes used: `metrics.read`, `entities.read`, `problems.read`, `events.read`,
  `logs.read`, `slo.read`, `auditLogs.read`, `releases.read`. Personal access tokens work with
  `environment:roles:viewer` (and `environment:roles:logviewer` for logs,
  `environment:roles:manage-settings` for audit logs).
- On Log Management and Analytics (Grail) environments the classic log endpoints need an OAuth /
  platform bearer token with `storage:logs:read` + `storage:buckets:read`; with an Api-Token they
  return nothing useful there. Use `dynatrace_grail_logs` instead.

## Base URLs

- SaaS: `https://{env-id}.live.dynatrace.com` (paths start with `/api/v2/...`). Not the
  `*.apps.dynatrace.com` platform URL, which is for `dynatrace_grail`.
- Managed: `https://{your-domain}/e/{env-id}`.
- Environment ActiveGate: `https://{activegate}:9999/e/{env-id}`.

`DT_ENV_URL` is the part before `/api/v2`.

## Time formats

- `from` / `to` accept UTC epoch milliseconds, ISO timestamps or `now-2h` style relative times.
  The manifest always sends epoch ms (`{{ time.start | epoch_ms }}`).
- Response timestamps (`startTime`, `endTime`, `timestamps[]`, `lastSeenTms`, `createdAtTimestamp`,
  audit `timestamp`) are epoch ms. Log search `timestamp` is epoch ms, sometimes as a string in
  the docs' example. Log aggregate bucket keys are parsed with `auto_time` because the docs do not
  state their unit.
- Defaults when `from` is omitted differ per endpoint: metrics, problems and events use `now-2h`,
  entities `now-3d`, and logs, SLOs, audit logs and releases `now-2w`. The manifest always sends
  an explicit window.
- Metric data point timestamps are the END of each storage slot. The last point may lie after `to`.
- Problem `endTime` is `-1` while OPEN; event `endTime` is `null` while active; evidence `endTime`
  is `-1` (event evidence) or `null` (metric evidence) while open.

## Pagination

All list endpoints use `nextPageKey`. The docs say that when it is set, all other query
parameters must be omitted (problems and comments allow `fields` next to it). The engine's
`cursor` pagination re-sends the first request's parameters, so these tables fetch ONE
maximum-size page and expose `has_more`:

| Table | Page size used | Documented max |
|---|---|---|
| entities / relationships | 500 | none stated (default 50) |
| entityTypes | 500 | 500 |
| metric descriptors | 500 | 500 |
| problems | 500 | 500 |
| problem comments | 500 | 500 |
| events | 1000 | 1000 |
| eventTypes | 500 | 500 |
| SLOs | 25 evaluated / 10000 definitions | 25 when `evaluate=true`, else 10000 |
| audit logs | 5000 | 5000 |
| releases | 1000 | 1000 |

`/metrics/query` is not paginated (`nextPageKey` is deprecated and always null). Log search uses
`nextSliceKey` ("all other query parameters are ignored"), so it uses normal cursor pagination
(up to 5 slices of 1,000 records).

## Rate limits

No fixed per-minute quota for reads. Each environment has a bounded request thread pool plus a
queue with a 10 s timeout, and the API returns HTTP 429 when both are full. Expensive requests
(relationship fields on wide entity selectors, many-metric selectors) hit it first. The manifest
uses concurrency 4 and retries 429/502/503/504.

## Retention (SaaS defaults)

- Metrics (Classic): up to 5 years, with resolution coarsening with age.
- Logs (Log Monitoring Classic): 35 days.
- Problems and events: 1 to 3 years, depending on type.
- Distributed traces: 10 days (not exposed here; use `dynatrace_grail_spans`).

## Limits

- Metrics query: 10 metric keys per selector, 20,000,000 data points per query, 100,000 series,
  10,080 points per series and 5,000 entities per entitySelector.
- Entity selector: 2,000 characters; one entity type per selector.
- Problem `text()` search: 30 characters.
- Log aggregate `maxGroupValues`: 100 (default 10; the manifest uses 20). `groupBy` cannot use
  high-cardinality attributes (trace_id, span_id).
- Classic log query: at most 10 distinct attributes and 20 relations.

## Pitfalls

- Unknown metric dimension keys, entity types or tags do not raise an error; they return empty
  data (metric queries add a `warnings` entry, which `dynatrace_metrics` keeps as a placeholder
  row with `series_warnings` / `warnings`).
- Selector values are double-quoted with `~` as the escape character (`~"`, `~~`). The tables
  escape pushed-down values with a Jinja macro; raw `*_selector` filters are passed through
  verbatim.
- Entity names are case-insensitive by default (`caseSensitive(...)` makes them strict). Tags,
  management zone names and health states are case-sensitive.
- Problems: the API returns problems whose start OR end falls inside `from`/`to`. ts predicates
  only set that window and are not re-applied in SQL (`time.reapply: false`); `start_ts` is a
  local copy for "started after X" filters. A problem that started before the window and is still
  open may be missing, so widen the window for `status = 'OPEN'`.
- Problem `status("open")` is lowercase in the selector but the response says `OPEN`/`CLOSED`.
- `eventSelector` accepts ONE `eventType`; `event_type IN (...)` fans out into one request per type.
- `builtin:service.response.time` is in MICROseconds.
- Metrics API v2 is not supported for "latest Dynatrace" (Grail-first) environments; use DQL
  (`dynatrace_grail_metrics`).
- `/logs/search` and `/logs/aggregate` are deprecated and will be removed by the end of 2027.
- Classic SLOs are deprecated on latest Dynatrace. Platform SLOs (`/platform/slo/v1/slos`) are not
  covered: the docs do not document the evaluation result schema.
- Audit logs need `auditLogs.read` AND audit logging enabled in settings; logins dominate volume.

## Fixture provenance

| Fixture | Source |
|---|---|
| `metrics_query_cpu.json`, `metrics_query_fold.json`, `metrics_query_reference.json` | verbatim examples / JSON model on the GET data points page |
| `error_envelope.json` | verbatim ErrorEnvelope JSON model (used by every v2 endpoint), as an `expect_error` fixture |
| `metric_descriptors_reference.json`, `metric_descriptors_example.json` | verbatim JSON model / example on the GET metrics page |
| `entities_services.json`, `entities_reference.json` | verbatim example / EntitiesList JSON model |
| `entities_single_service.json` | EntitiesList envelope wrapping the verbatim "GET an entity" example entity |
| `entity_types_example.json`, `entity_types_reference.json` | verbatim example / JSON model |
| `problems_reference.json` | verbatim Problems JSON model (placeholders) |
| `problems_schema_realistic.json` | built strictly from the documented Problem schema |
| `problem_details_reference.json` | verbatim Problem JSON model (GET problem details) |
| `problem_details_models.json` | Problem object with the verbatim Evidence and Impact JSON models from the Problems API v2 models page |
| `problem_comments_reference.json` | verbatim CommentsList JSON model |
| `events_example.json` | docs example response (one trailing comma removed) |
| `events_reference.json`, `event_types_example.json` | verbatim JSON model / example |
| `logs_search_reference.json`, `logs_aggregate_reference.json` | verbatim JSON models |
| `slos_reference.json`, `audit_logs_example.json`, `audit_logs_reference.json`, `releases_reference.json` | verbatim examples / JSON models |
