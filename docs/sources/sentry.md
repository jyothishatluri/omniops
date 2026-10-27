# Sentry source (`sentry`)

Manifest: `src/omniops/sources/manifests/sentry.yaml` (26 tables)
Fixtures: `src/omniops/sources/fixtures/sentry/`
API reference root: https://docs.sentry.io/api/ (web API "v0", prefix `/api/0/`).

Checked against the official reference on 2026-10-27. Paths, parameters, enums and example
responses come from the reference pages below and from Sentry's published OpenAPI document
(`getsentry/sentry-api-schema`, `openapi-derefed.json`), which is what docs.sentry.io renders.
Every JSON fixture is the documented example response of its endpoint, unmodified, except:
`release_deploys.json` (the deploys endpoint documents only a schema; the fixture is the Deploy
object from the Retrieve-Release example, as a list), `monitors.json` and `monitor_checkins.json`
(schema only; built strictly from the schema).

## Tables -> endpoints -> reference pages

| SQL table | HTTP | Official reference |
|---|---|---|
| `sentry_issues` | GET `/api/0/organizations/{org}/issues/` (`query`, `project`, `environment`, `start`, `end`, `sort`, `limit`, `groupStatsPeriod`, `shortIdLookup`) | https://docs.sentry.io/api/events/list-an-organizations-issues/ |
| `sentry_issue` | GET `/api/0/organizations/{org}/issues/{issue_id}/` | https://docs.sentry.io/api/events/retrieve-an-issue/ |
| `sentry_issue_events` | GET `/api/0/organizations/{org}/issues/{issue_id}/events/` (`start`, `end`, `environment`, `query`, `full`, `per_page`) | https://docs.sentry.io/api/events/list-an-issues-events/ |
| `sentry_issue_event` | GET `/api/0/organizations/{org}/issues/{issue_id}/events/{event_id}/` (`latest` / `oldest` / `recommended` / id; `llmFormat`) | https://docs.sentry.io/api/events/retrieve-an-issue-event/ |
| `sentry_issue_tags` | GET `/api/0/organizations/{org}/issues/{issue_id}/tags/{key}/` (rows = `topValues`) | https://docs.sentry.io/api/events/retrieve-tag-details/ |
| `sentry_issue_by_short_id` | GET `/api/0/organizations/{org}/shortids/{short_id}/` | https://docs.sentry.io/api/organizations/resolve-a-short-id/ |
| `sentry_event_lookup` | GET `/api/0/organizations/{org}/eventids/{event_id}/` | https://docs.sentry.io/api/organizations/resolve-an-event-id/ |
| `sentry_events` | GET `/api/0/organizations/{org}/events/` (`dataset`, repeated `field`, `query`, `sort`, `project`, `environment`, `start`, `end`, `per_page`) | https://docs.sentry.io/api/explore/query-explore-events-in-table-format/ |
| `sentry_events_timeseries` | GET `/api/0/organizations/{org}/events-timeseries/` (`dataset`, `yAxis`, `groupBy`, `topEvents`, `sort`, `interval`, `excludeOther`, ...) | https://docs.sentry.io/api/explore/query-explore-events-in-timeseries-format/ |
| `sentry_trace_item_attributes` | GET `/api/0/organizations/{org}/trace-items/attributes/` | https://docs.sentry.io/api/discover/list-trace-item-attributes/ |
| `sentry_trace_meta` | GET `/api/0/organizations/{org}/trace-meta/{trace_id}/` | https://docs.sentry.io/api/discover/retrieve-trace-metadata/ |
| `sentry_releases` | GET `/api/0/organizations/{org}/releases/` (`query`, `project`, `environment`, `per_page`) | https://docs.sentry.io/api/releases/list-an-organizations-releases/ |
| `sentry_release_health` | GET `/api/0/organizations/{org}/releases/{version}/?health=true&adoptionStages=true` | https://docs.sentry.io/api/releases/retrieve-an-organizations-release/ |
| `sentry_release_deploys` | GET `/api/0/organizations/{org}/releases/{version}/deploys/` | https://docs.sentry.io/api/releases/list-a-releases-deploys/ |
| `sentry_release_commits` | GET `/api/0/organizations/{org}/releases/{version}/commits/` | https://docs.sentry.io/api/releases/list-an-organization-releases-commits/ |
| `sentry_sessions` | GET `/api/0/organizations/{org}/sessions/` (repeated `field`, `groupBy`, `interval`, `orderBy`, ...) | https://docs.sentry.io/api/releases/retrieve-release-health-session-statistics/ |
| `sentry_flag_logs` | GET `/api/0/organizations/{org}/flags/logs/` (`start`, `end`, `flag`, `provider`, `sort`) | https://docs.sentry.io/api/flags/list-an-organizations-flag-logs/ |
| `sentry_detectors` | GET `/api/0/organizations/{org}/detectors/` ("Fetch an Organization's Monitors"; `query`, `type`, `id`, `project`, `enabled`, `sortBy`, `per_page`) | https://docs.sentry.io/api/monitors/fetch-an-organizations-monitors/ |
| `sentry_workflows` | GET `/api/0/organizations/{org}/workflows/` ("Fetch Alerts") | https://docs.sentry.io/api/monitors/fetch-alerts/ |
| `sentry_monitors` | GET `/api/0/organizations/{org}/monitors/` (crons) | https://docs.sentry.io/api/crons/retrieve-monitors-for-an-organization/ |
| `sentry_monitor_checkins` | GET `/api/0/organizations/{org}/monitors/{monitor}/checkins/` | https://docs.sentry.io/api/crons/retrieve-checkins-for-a-monitor/ |
| `sentry_projects` | GET `/api/0/organizations/{org}/projects/` | https://docs.sentry.io/api/organizations/list-an-organizations-projects/ |
| `sentry_environments` | GET `/api/0/organizations/{org}/environments/` | https://docs.sentry.io/api/environments/list-an-organizations-environments/ |
| `sentry_org_stats` | GET `/api/0/organizations/{org}/stats_v2/` (`field`, repeated `groupBy`, `interval`, `category`, `outcome`, `reason`, `start`, `end`) | https://docs.sentry.io/api/organizations/retrieve-event-counts-for-an-organization-v2/ |
| `sentry_dashboards` | GET `/api/0/organizations/{org}/dashboards/` | https://docs.sentry.io/api/dashboards/list-an-organizations-custom-dashboards/ |
| `sentry_replays` | GET `/api/0/organizations/{org}/replays/` | https://docs.sentry.io/api/replays/list-an-organizations-replays/ |

Not modelled: the nested trace tree `GET .../trace/{trace_id}/` (arbitrary-depth `children`; the
flat span list of a trace comes from `sentry_events` with `dataset = 'spans'` and `trace_id`),
legacy Discover saved queries, project-scoped duplicates (`/projects/{org}/{project}/events|issues|
releases`), source maps / debug files, members / teams, Seer autofix, and every write endpoint.
The legacy alert-rule endpoints (`/alert-rules/`, `/projects/.../rules/`) are no longer in the
published reference; alert definitions are read from detectors and workflows.

Roles bound: `errors` (`sentry_issues`), `logs` (`sentry_events` dataset logs), `traces` and
`trace_detail` (`sentry_events` dataset spans), `metrics` (`sentry_events_timeseries`),
`log_counts` (`sentry_events_timeseries` dataset logs),
`change_events` (`sentry_releases`, `sentry_flag_logs`), `alert_rules` (`sentry_detectors`,
`sentry_workflows`), `services` (`sentry_projects`), `rum` (`sentry_replays`), `dashboards`
(`sentry_dashboards`).

## Auth

- `Authorization: Bearer <SENTRY_AUTH_TOKEN>`: an internal-integration (organization) token or a
  user auth token with `org:read`, `project:read`, `event:read` (plus `member:read` / `team:read`
  for owners). Available on SaaS and self-hosted. OAuth access tokens (30-day expiry) also work.
- `SENTRY_ORG` is the organization slug (or id) used in every path.

## Base URLs / regions

- SaaS: `https://sentry.io` (default `SENTRY_URL`). Region domains `https://us.sentry.io` and
  `https://de.sentry.io` give lower latency; not every resource is served on region domains.
- Self-hosted: `https://<your-host>`; the manifest appends `/api/0/...`.

## Rate limits

- Per (caller identity, endpoint): a fixed-window requests-per-second limit and a concurrent
  request limit. Headers: `X-Sentry-Rate-Limit-Limit`, `-Remaining`, `-Reset` (epoch seconds),
  `-ConcurrentLimit`, `-ConcurrentRemaining`. Generating more tokens does not raise the limit.
  The manifest uses concurrency 3; 429s are retried with backoff.

## Retention (SaaS, from the data-retention page)

- Errors: 90 days on Team / Business / Enterprise (30 on Developer).
- Spans / transactions: 30 days full fidelity (13 months sampled on Business+).
- Logs, profiles, crons: 30 days. Session replays: 90 days (30 on Developer).
- Self-hosted: whatever `SENTRY_EVENT_RETENTION_DAYS` is set to (default 90).

## Time formats

- Requests: `start` / `end` as ISO-8601 (the manifest sends RFC 3339 UTC, e.g.
  `2026-10-01T11:00:00Z`). `statsPeriod` (e.g. `24h`) overrides start/end and is never sent.
  stats_v2 and replays also accept epoch seconds; sessions / stats_v2 `interval` is a duration
  string (`1h` minimum, must divide a day).
- Responses: ISO-8601 strings (`dateCreated`, `firstSeen`, `lastSeen`...), except
  `events-timeseries` bucket `timestamp`s in **epoch milliseconds**, issue `stats` buckets
  `[epoch_seconds, count]`, and replay `started_at` without a timezone (UTC).
- Durations: `span.duration` in milliseconds; replay `duration` in seconds; cron check-in
  `duration` in milliseconds.

## Pagination

Link header (RFC 5988) with a `cursor`, e.g.
`<...?&cursor=0:100:0>; rel="next"; results="true"; cursor="0:100:0"`. A `next` link is ALWAYS
present; `results="false"` marks the last page. The engine's `link_header` pagination does not
read the `results` attribute (it would keep fetching empty pages and report false truncation), so
every Sentry table fetches one page of up to 100 rows (`limit` / `per_page` max 100), relying on
server-side sorting. Listed as an engine gap.

## Pitfalls

- `sentry_issues` without `query` applies `is:unresolved`: resolved / ignored / regressed issues
  are hidden. Use `query = '*'` (sent as a blank query = every status) or explicit `is:` terms.
- The issues `ts` window selects issues **with events in the window** and scopes `count` /
  `userCount` to it; `lifetime_count` is all-time. Last-seen is not re-filtered in SQL.
- Project filters take slugs or ids (older self-hosted: ids). Unknown projects are 400/403.
- Explore field names differ per dataset (spans: `span.op`, `span.description`, `span.duration`,
  `span.status_code`, `transaction`, `is_transaction`; logs: `message`, `severity`, `trace`,
  `span_id`; errors: `title`, `message`, `level`, `error.type`, `culprit`, `issue`). At most 20
  fields; `sort` must be one of them. Span/log aggregates are extrapolated from samples.
- `events-timeseries`: trailing buckets may be `incomplete` (INGESTION_PENDING / NOT_ELAPSED);
  the documented example shows `meta.interval: 3600` but the server code emits milliseconds
  (`rollup * 1000`), so the manifest does not expose it. `interval` (request) is seconds or a
  duration string; omitted by default so Sentry picks a valid one.
- `issue tags` top values and counts are lifetime (or per environment), not the incident window.
- `sentry_issue_event` with `latest` is the newest event overall; pick an event id from
  `sentry_issue_events` for a specific time. `llmFormat=markdown` (default here) adds a readable
  `formatted` rendering; secrets are masked by the `redact` transform.
- Releases / deploys / flag logs exist only if CI and flag providers report them to Sentry; empty
  does not prove "no change". `sentry_releases` has no time filter (newest 100, then `ts`).
- Monitors (crons) schema types `environments` as an object, but the serializer returns a list;
  the manifest handles both.
- `stats_v2` `project` takes numeric project ids only (not slugs); grouping by `project` returns
  period totals without a time series. `category` values: error, transaction, attachment, replay,
  profile, profile_duration, profile_duration_ui, profile_chunk, profile_chunk_ui, monitor.
- `events-timeseries` `interval`: the reference types it as an integer without a unit (Sentry
  parses it as seconds or a duration string); an invalid interval silently falls back to a default.

## Engine gaps noted

- `link_header` pagination does not read Sentry's `results="true|false"` Link attribute, so it
  cannot tell the last page; Sentry tables fetch a single page of at most 100 rows and say so in
  pitfalls. Supporting `results="false"` as a stop condition would enable deep pagination.
- Sentry's detailed trace tree (`/organizations/{org}/trace/{trace_id}/`) nests spans to arbitrary
  depth; the declarative `expand` steps cannot flatten a recursive tree (the flat span list comes
  from `sentry_events` with `dataset = 'spans'` instead).
- Dropped data: `sentry_org_stats` with outcome `rate_limited` / `filtered` / `client_discard`
  shows whether "no errors in Sentry" is a data gap (quota, spike protection, inbound filters).
