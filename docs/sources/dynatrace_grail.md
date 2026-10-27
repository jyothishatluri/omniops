# Dynatrace Grail source (`dynatrace_grail`): platform Query API (DQL)

Manifest: `src/omniops/sources/manifests/dynatrace_grail.yaml` (10 tables)
Fixtures: `src/omniops/sources/fixtures/dynatrace_grail/`
API reference root: https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-storage-query-v1
The classic Environment API v2 (Api-Token, `*.live.dynatrace.com`) is the separate `dynatrace`
source (see `docs/sources/dynatrace.md`).

Checked against the official reference on 2026-10-26. The HTTP flow, body fields and response
envelope come from the query:execute / query:poll reference pages. Record field names come from the
Semantic Dictionary and the DQL command references. The docs show no full record payloads for
these data objects, so the typed-table fixtures use the documented QueryPollResponse envelope with
records built strictly from documented field names; each fixture `note` says so.

## Request flow (every table)

1. `POST /platform/storage/query/v1/query:execute`
   (https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-storage-query-v1/query-execute-post).
   The JSON body has `query`, `defaultTimeframeStart` / `defaultTimeframeEnd` (RFC 3339 ms, the
   SQL ts window), `timezone: UTC`, `locale: en_US`, `maxResultRecords` (SQL LIMIT, default 1000,
   capped at 10000 here), `fetchTimeoutSeconds: 60`, `requestTimeoutMilliseconds: 1` and
   `enablePreview: false`. It answers 202 with `requestToken` (or 200 when the query finished
   within `requestTimeoutMilliseconds`).
2. `GET /platform/storage/query/v1/query:poll?request-token=...&request-timeout-milliseconds=5000`
   (https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-storage-query-v1/query-poll-get).
   The engine repeats it until `state == SUCCEEDED`; `FAILED`, `CANCELLED` and `RESULT_GONE` fail
   loudly. Rows come from `result.records[*]`; `result.types[].mappings` gives field types.

States: `NOT_STARTED`, `RUNNING`, `SUCCEEDED`, `RESULT_GONE`, `CANCELLED`, `FAILED`. Poll can
return HTTP 410 once the result is gone.

## Tables -> DQL -> reference pages

| SQL table | DQL issued (via execute + poll) | Field / command reference |
|---|---|---|
| `dynatrace_grail_query` | any DQL (`dql` filter) | https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-storage-query-v1/query-execute-post |
| `dynatrace_grail_logs` | `fetch logs \| filter ... \| sort timestamp desc \| limit n \| fields ...` | https://docs.dynatrace.com/docs/platform/grail/dynatrace-query-language/commands/data-source-commands |
| `dynatrace_grail_log_counts` | `fetch logs \| summarize value = count(), by:{ts = bin(timestamp, i), group_value = <field>}` | https://docs.dynatrace.com/docs/platform/grail/dynatrace-query-language/commands/aggregation-commands |
| `dynatrace_grail_spans` | `fetch spans \| filter ... \| fieldsAdd duration_ms = toDouble(duration)/1e6` | https://docs.dynatrace.com/docs/semantic-dictionary/model/trace |
| `dynatrace_grail_problems` | `fetch dt.davis.problems \| dedup event.id, sort:{timestamp desc} \| filterOut toBoolean(dt.davis.is_duplicate) == true \| filter <overlaps window>` | https://docs.dynatrace.com/docs/semantic-dictionary/model/davis |
| `dynatrace_grail_events` | `fetch dt.davis.events \| dedup event.id, sort:{timestamp desc} \| filter <overlaps window>` | https://docs.dynatrace.com/docs/semantic-dictionary/model/davis |
| `dynatrace_grail_changes` | `fetch events \| filter event.kind == "SDLC_EVENT" and event.type == "deployment" \| append [fetch dt.davis.events \| filter CUSTOM_DEPLOYMENT/CUSTOM_CONFIGURATION]` | https://docs.dynatrace.com/docs/semantic-dictionary/model/sdlc-events |
| `dynatrace_grail_entities` | `smartscapeNodes <TYPE> \| filter name ... \| fields id, type, name, tags` | https://docs.dynatrace.com/docs/platform/grail/dynatrace-query-language/commands/smartscape-commands |
| `dynatrace_grail_topology` | `smartscapeNodes <TYPE> \| traverse {"calls"}, {"*"}, direction: forward, fieldsKeep: name` | https://docs.dynatrace.com/docs/platform/grail/dynatrace-query-language/commands/smartscape-commands |
| `dynatrace_grail_metrics` | `timeseries value = <agg>(<metric>), ts = start(), by:{...}, filter:{...}, interval: i` | https://docs.dynatrace.com/docs/platform/grail/dynatrace-query-language/commands/metric-commands |

Davis DQL patterns (dedup by `event.id`, duplicate flag):
https://docs.dynatrace.com/docs/discover-dynatrace/platform/davis-ai/use-cases/davis-dql-examples.
Service Smartscape edges (`calls`, `runs_on`, `belongs_to`):
https://docs.dynatrace.com/docs/observe/application-observability/services/services-smartscape.

## Roles bound

`logs` -> `dynatrace_grail_logs`, `log_counts` -> `dynatrace_grail_log_counts`, `traces` and
`trace_detail` -> `dynatrace_grail_spans`, `metrics` -> `dynatrace_grail_metrics` (query language
`dql`), `incidents` and `active_alerts` (`status = ACTIVE`) -> `dynatrace_grail_problems`,
`alert_history` -> `dynatrace_grail_events`, `change_events` -> `dynatrace_grail_changes`,
`services` / `hosts` -> `dynatrace_grail_entities`, `service_map` -> `dynatrace_grail_topology`.

## Auth

- `Authorization: Bearer <platform token>` (`dt0s16.*`) or an OAuth access token. Scopes are
  documented per data object: `storage:buckets:read` plus `storage:logs:read`,
  `storage:spans:read`, `storage:events:read`, `storage:metrics:read`, `storage:entities:read`,
  `storage:bizevents:read`, `storage:system:read`, and `storage:fieldsets:read` for sensitive
  fields.
- The source sends a static bearer token. An OAuth client-credentials flow
  (`sso.dynatrace.com`, `urn:dtaccount:` resource) is not wired up; see the open questions.

## Base URL

`https://{env-id}.apps.dynatrace.com` (the platform URL, not `*.live.dynatrace.com`).

## Time formats

- The SQL window goes into `defaultTimeframeStart` / `defaultTimeframeEnd` as RFC 3339 with ms.
  A `from:` / `to:` / `timeframe:` inside the DQL takes precedence, so the typed tables never put
  one in the query text.
- Record timestamps are RFC 3339 with nanoseconds (`2026-10-01T11:42:07.512000000Z`), parsed with
  `auto_time`. Durations (`duration`, `resolved_problem_duration`) are nanoseconds; the tables
  expose `duration_ms`.
- Davis problems and events: the fetch window starts 6 h before the SQL window (Davis exports a
  refresh at least every 6 h), records are deduplicated to the latest update per `event.id`, and
  a DQL overlap filter (`event.start <= end and (active or event.end >= start)`) replaces SQL
  re-filtering (`time.reapply: false`).

## Limits and rate limits

- `fetch` scans at most 500 GB uncompressed per query by default (`scanLimitGBytes`, -1 =
  unlimited). Hitting it returns a partial result with a notification. `dynatrace_grail_query`
  exposes `notifications`.
- `maxResultRecords` caps returned records and adds a warning notification when exceeded.
- `timeseries`: at most 1,500 points per series. The interval snaps to 1/2/3/5/10/15/30 min or
  1/2/3/4/6/8/12/24 h.
- Query concurrency and consumption limits are enforced per environment (HTTP 429). The manifest
  uses concurrency 4 with retries, and `control_lookback` keeps positive-control re-queries short
  (spans 2 h, logs 6 h).

## Retention (defaults)

Logs 35 days (`default_logs`), spans 10 days, events 35 days (Davis problems/events longer),
metrics about 15 months. Custom buckets can differ.

## Pitfalls

- Field names and `==` string comparisons are case-sensitive. Unknown fields evaluate to null
  without error, so a filter on a misspelled field returns 0 records. Check fields with
  `fetch logs | limit 5` in `dynatrace_grail_query`.
- `dt.davis.problems` / `dt.davis.events` hold one record PER UPDATE. Always dedup by `event.id`
  (latest `timestamp`) before filtering on status. The typed tables do this; hand-written DQL
  must too.
- `filter not(x)` drops records where `x` is null. Use `filterOut x == true` to exclude flagged
  records (the duplicate flag is compared after `toBoolean`, because the docs show both boolean
  and string forms).
- Problem status is `ACTIVE` / `CLOSED` (classic API: `OPEN` / `CLOSED`). Davis event status
  casing varies (`Active`/`ACTIVE`), so the tables compare with `lower()`.
- `affected_entity_*`, `root_cause_entity_*`, `dt.source_entity` are deprecated in favour of
  `smartscape.affected_entities`, `root_cause.smartscape_entity`, `dt.smartscape_source.*`. The
  tables read both.
- SDLC deployment events (`event.kind == "SDLC_EVENT"`, `event.type == "deployment"`,
  `event.status` started/finished) only exist when a CI/CD integration posts them to
  `/platform/ingest/v1/events.sdlc`. Deployment events carry `cicd.deployment.*`,
  `vcs.ref.base.*`, `vcs.repository.url.full`, `task.*` but no version field.
- Smartscape commands (`smartscapeNodes`, `smartscapeEdges`, `traverse`) exist only on latest
  Dynatrace. Node types are UPPER_SNAKE (`SERVICE`, `K8S_POD`) and edge types lower_snake
  (`calls`, `runs_on`, `belongs_to`).
- Grail metric keys differ from classic keys (`dt.service.request.response_time` in
  microseconds, `dt.service.request.count`, `dt.service.request.failure_count`,
  `dt.host.cpu.usage`). `percentile` needs `rollup:` except for the service response-time
  histograms.
- Request tokens can contain `+`; they are sent as an encoded query parameter (httpx `params`),
  which is what the community thread on rejected tokens recommends.

## Engine gap

If `query:execute` returns 200 with the result inline (query finished within
`requestTimeoutMilliseconds`) and without a `requestToken`, the poll step cannot reuse the inline
result: the declarative steps have no "skip the last step and take rows from an earlier step"
primitive. `requestTimeoutMilliseconds: 1` makes the documented 202 + `requestToken` path the
normal case. Whether a 200 response still carries a `requestToken` is not documented.

## Fixture provenance

| Fixture | Source |
|---|---|
| `query_reference.json` | verbatim QueryPollResponse example (SUCCEEDED, 4 records) from the query:execute / query:poll docs |
| `logs.json`, `log_counts.json`, `spans.json`, `metrics.json` | documented envelope; records from documented field names (logs/trace semantic dictionary, timeseries output shape) |
| `problems.json`, `problems_smartscape_only.json`, `events.json` | documented envelope; records from the Davis semantic dictionary (deprecated and smartscape fields) |
| `changes.json` | documented envelope; SDLC deployment finished event fields plus Davis CUSTOM_DEPLOYMENT properties |
| `entities.json`, `topology.json` | documented envelope; records shaped as the documented `smartscapeNodes` / `traverse` examples emit them |
