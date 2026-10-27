# PagerDuty source (`pagerduty`)

Manifest: `src/omniops/sources/manifests/pagerduty.yaml` (26 tables)
Fixtures: `src/omniops/sources/fixtures/pagerduty/`
API reference root: https://docs.pagerduty.com/developer/api/reference/rest (REST API v2).
The old `developer.pagerduty.com/api-reference/<hash>-...` links now 301-redirect to
`docs.pagerduty.com/developer/api/reference/rest/<tag>/<operation>`.

Checked against the official reference on 2026-10-27. Paths, parameters, enums and example
responses come from the reference pages below and from PagerDuty's own OpenAPI description
(`github.com/PagerDuty/api-schema`, `reference/REST/openapiv3.json`), whose `examples` are the
"Example response" blocks rendered on docs.pagerduty.com. Every JSON fixture is one of those
examples, unmodified (except `error_body.json`, the documented error object shape).

## Tables -> endpoints -> reference pages

| SQL table | HTTP | Official reference |
|---|---|---|
| `pagerduty_incidents` | GET `/incidents?since&until&statuses[]&service_ids[]&team_ids[]&user_ids[]&urgencies[]&incident_key&date_range&sort_by` | https://docs.pagerduty.com/developer/api/reference/rest/incidents/list-incidents |
| `pagerduty_incident_detail` | GET `/incidents/{id}` (id or incident number) | https://docs.pagerduty.com/developer/api/reference/rest/incidents/get-incident |
| `pagerduty_incident_log_entries` | GET `/incidents/{id}/log_entries?include[]=channels` | https://docs.pagerduty.com/developer/api/reference/rest/incidents/list-incident-log-entries |
| `pagerduty_log_entries` | GET `/log_entries?since&until&include[]=channels&is_overview&team_ids[]` | https://docs.pagerduty.com/developer/api/reference/rest/log-entries/list-log-entries |
| `pagerduty_incident_alerts` | GET `/incidents/{id}/alerts?statuses[]&alert_key` | https://docs.pagerduty.com/developer/api/reference/rest/incidents/list-incident-alerts |
| `pagerduty_incident_notes` | GET `/incidents/{id}/notes` | https://docs.pagerduty.com/developer/api/reference/rest/incidents/list-incident-notes |
| `pagerduty_related_change_events` | GET `/incidents/{id}/related_change_events` | https://docs.pagerduty.com/developer/api/reference/rest/change-events/list-incident-related-change-events |
| `pagerduty_past_incidents` | GET `/incidents/{id}/past_incidents?limit` | https://docs.pagerduty.com/developer/api/reference/rest/incidents/get-past-incidents |
| `pagerduty_related_incidents` | GET `/incidents/{id}/related_incidents` | https://docs.pagerduty.com/developer/api/reference/rest/incidents/get-related-incidents |
| `pagerduty_outlier_incident` | GET `/incidents/{id}/outlier_incident` | https://docs.pagerduty.com/developer/api/reference/rest/incidents/get-outlier-incident |
| `pagerduty_change_events` | GET `/change_events?since&until&team_ids[]&integration_ids[]`, or GET `/services/{id}/change_events` when service_id is given | https://docs.pagerduty.com/developer/api/reference/rest/change-events/list-change-events (per service: https://docs.pagerduty.com/developer/api/reference/rest/change-events/list-service-change-events) |
| `pagerduty_audit_records` | GET `/audit/records?since&until&root_resource_types[]&actions[]&actor_type&actor_id&limit=100` (cursor `next_cursor` -> `cursor`) | https://docs.pagerduty.com/developer/api/reference/rest/audit/list-audit-records |
| `pagerduty_services` | GET `/services?query&team_ids[]&include[]=teams,escalation_policies&sort_by=name` | https://docs.pagerduty.com/developer/api/reference/rest/services/list-services |
| `pagerduty_service_dependencies` | GET `/service_dependencies/technical_services/{id}` | https://docs.pagerduty.com/developer/api/reference/rest/service-dependencies/get-technical-service-service-dependencies |
| `pagerduty_business_services` | GET `/business_services` | https://docs.pagerduty.com/developer/api/reference/rest/business-services/list-business-services |
| `pagerduty_maintenance_windows` | GET `/maintenance_windows?filter=ongoing\|open\|future\|past\|all&service_ids[]&team_ids[]&query` | https://docs.pagerduty.com/developer/api/reference/rest/maintenance-windows/list-maintenance-windows |
| `pagerduty_oncalls` | GET `/oncalls?since&until&escalation_policy_ids[]&schedule_ids[]&user_ids[]&earliest` | https://docs.pagerduty.com/developer/api/reference/rest/on-calls/list-on-calls |
| `pagerduty_schedules` | GET `/schedules?query&team_ids[]` | https://docs.pagerduty.com/developer/api/reference/rest/schedules/list-schedules |
| `pagerduty_schedule_entries` | GET `/schedules/{id}?since&until` (`schedule.final_schedule.rendered_schedule_entries`) | https://docs.pagerduty.com/developer/api/reference/rest/schedules/get-schedule |
| `pagerduty_schedule_overrides` | GET `/schedules/{id}/overrides?since&until` | https://docs.pagerduty.com/developer/api/reference/rest/schedules/list-schedule-overrides |
| `pagerduty_escalation_policies` | GET `/escalation_policies?query&user_ids[]&team_ids[]` | https://docs.pagerduty.com/developer/api/reference/rest/escalation-policies/list-escalation-policies |
| `pagerduty_users` | GET `/users?query&team_ids[]` | https://docs.pagerduty.com/developer/api/reference/rest/users/list-users |
| `pagerduty_teams` | GET `/teams?query` | https://docs.pagerduty.com/developer/api/reference/rest/teams/list-teams |
| `pagerduty_priorities` | GET `/priorities` | https://docs.pagerduty.com/developer/api/reference/rest/priorities/list-priorities |
| `pagerduty_analytics_incidents` | POST `/analytics/raw/incidents` (body `filters.created_at_start/end`, cursor `starting_after`) | https://docs.pagerduty.com/developer/api/reference/rest/analytics/get-analytics-incidents |
| `pagerduty_analytics_services` | POST `/analytics/metrics/incidents/services` (body `filters`, `aggregate_unit`) | https://docs.pagerduty.com/developer/api/reference/rest/analytics/get-analytics-metrics-incidents-service |

Roles bound: `incidents` and `active_alerts` (pagerduty_incidents; active = status IN
(triggered, acknowledged) AND date_range = 'all'), `oncall` (pagerduty_oncalls), `change_events`
(pagerduty_change_events), `services` (pagerduty_services), `alert_history`
(pagerduty_log_entries).

## Auth

- Header `Authorization: Token token=<PAGERDUTY_API_TOKEN>` on every request
  (https://docs.pagerduty.com/developer/authentication). HTTPS only.
- Account API keys (admins create them; can be read-only) see all account data; user API keys
  (accounts with Advanced Permissions) see what that user can see. OAuth app tokens would use
  `Authorization: Bearer`, not supported by this manifest.
- Versioning: `Accept: application/vnd.pagerduty+json;version=2` pins v2 regardless of token
  (https://docs.pagerduty.com/developer/versioning). Sent on every request.

## Base URLs / regions

https://docs.pagerduty.com/get-started/service-regions

| Region | REST API | Web UI |
|---|---|---|
| US | `https://api.pagerduty.com` (default `PAGERDUTY_API_URL`) | `https://<subdomain>.pagerduty.com` |
| EU | `https://api.eu.pagerduty.com` | `https://<subdomain>.eu.pagerduty.com` |

US-bound requests carrying an EU key are forwarded, but PagerDuty recommends calling the EU host
directly. `PAGERDUTY_UI_URL` only affects deep links.

## Rate limits

https://docs.pagerduty.com/developer/rest-api-rate-limits

- 960 requests/minute per account API key; per user across all of the user's keys; per PagerDuty
  App per account (app token) or per user (user token).
- Some operations add operation-specific limits (the reference notes it, e.g. List on-calls).
- Exceeded: HTTP 429, body `{"error": {"message": "Rate Limit Exceeded", "code": 2020}}`, headers
  `ratelimit-limit`, `ratelimit-remaining`, `ratelimit-reset` (seconds). Recommendation: make
  requests serially per token. The manifest caps itself at 900/min, concurrency 3, and retries
  429 (body pattern "Rate Limit Exceeded" too).

## Pagination

https://docs.pagerduty.com/developer/pagination

- Classic: `limit` (default 25, max 100) + `offset`; responses carry `limit`, `offset`, `more`,
  `total` (null unless `total=true`). The manifest pages with limit=100 while `more` is true.
- Hard cap: `offset + limit <= 10000`, otherwise HTTP 400. Narrow with filters instead.
- Cursor pagination exists only for `/audit/records` (`pagerduty_audit_records`): pass the previous
  response's `next_cursor` as `cursor`; `next_cursor: null` ends the list. No documented default
  or maximum `limit` (the response echoes min(requested, API maximum)); the manifest asks for 100.
- Analytics raw endpoints use their own cursor: response `last` + `more`; next request sends
  `starting_after=<last>` in the JSON body; `limit` 1..1000.

## Time formats and windows

- All times are ISO 8601 (https://docs.pagerduty.com/developer/types). The manifest always sends
  UTC `YYYY-MM-DDThh:mm:ssZ` and `time_zone=UTC` (rendering).
- `since`/`until` WITHOUT an offset are interpreted in the account's time zone (List incidents
  docs); `/change_events` and `/services/{id}/change_events` reject non-UTC values with HTTP 400.
- List incidents: max range 6 months, default 1 month; `date_range=all` ignores since/until.
  since/until select by incident CREATION time (a community answer confirms updated incidents
  are not returned by since). The manifest clamps windows to 180 days.
- On-calls: entries overlapping [since, until] are returned; defaults to now; future limited to
  90 days ahead; `until` must not be before `since`.
- Schedules (GET /schedules/{id}): rendered entries for [since, until] (defaults 2 weeks around
  the given bound); entries are truncated at the bounds unless `overflow=true`.
- Audit records: `since` defaults to now - 24h, `until` to now, and `until` may be at most 31 days
  after `since` (manifest max_window 31d). Records are sorted newest first by `execution_time`.
- Analytics: `created_at_start` (inclusive) / `created_at_end` (exclusive), ISO 8601 with offset,
  max one year; responses render naive timestamps in the request's `time_zone` (Etc/UTC here).

## Retention

- Incidents and log entries: kept for the life of the account (no documented expiry).
- Change events: the Recent Changes view does not show change events older than 90 days
  (https://docs.pagerduty.com/ai-automation/aiops/recent-changes); the manifest sets retention 90d.
- Past incidents: the last 6 months only. Analytics: data processed in pipeline cycles, generally
  available within 24 hours.

## Errors

https://docs.pagerduty.com/developer/errors

- Failures are 4xx/5xx with `{"error": {"message", "code", "errors": [...]}}`. Notable codes:
  2001 invalid input, 2004 invalid since/until, 2005 invalid query date range, 2006 auth failed,
  2010 access denied, 2014 missing account ability, 2020 rate limited. 402 = the account lacks
  an ability (e.g. Event Intelligence for past/related incidents, teams for team_ids[]).
- PagerDuty does not document 2xx-with-error bodies; the manifest still treats any 2xx body with
  `error.message` as a failure (fixture `error_body.json`, `expect_error`).

## Pitfalls

- Filters take IDs, not names (service_ids[], team_ids[], user_ids[], escalation_policy_ids[],
  schedule_ids[]). Resolve names first with pagerduty_services / teams / users / schedules /
  escalation_policies (`query` does a server-side name match).
- Array parameters must be repeated with brackets: `statuses[]=triggered&statuses[]=acknowledged`.
- Incidents are listed by creation time; still-open older incidents need `date_range = 'all'`.
- `user_ids[]` on incidents returns only triggered/acknowledged incidents.
- IDs are unique only per endpoint (a schedule and a service can share an id).
- The incident title is typically the first alert's summary; read pagerduty_incident_alerts for all
  grouped alerts and their custom details, and the log entries (`include[]=channels`) for the
  original trigger payloads (https://docs.pagerduty.com/developer/retrieve-incident-details).
  The channel structure is not formally specified and varies by integration.
- Past/related incidents need the Event Intelligence (AIOps) package or Digital Operations plan.
- Analytics endpoints require a service_ids or team_ids filter for user-level keys and OAuth
  tokens, and lag up to ~24h.
- Business service/technical service dependency rows carry IDs only; join with
  pagerduty_services / pagerduty_business_services for names.
- Services in an ongoing maintenance window create no incidents: an empty pagerduty_incidents
  window can mean "in maintenance", check pagerduty_maintenance_windows.
- Audit records need an admin / account owner key or an account-level key on a plan with the Audit
  Trail feature; 402/403 there is a permission/plan problem, not "no changes".
- The outlier incident endpoint is an AIOps / Event Intelligence capability; its documented
  example shows category `rare` - other category values are not enumerated in the reference.
- Not modelled (considered): `/notifications` (per-user phone/SMS/push notifications; contains
  phone numbers, and notify_log_entry rows in the incident log already show who was notified),
  `/incidents/{id}/custom_fields/values`, incident reviews / postmortems (`/incidents/reviews`,
  newer endpoints), `/paused_incident_reports/*`, status pages and `/v3/schedules`.
