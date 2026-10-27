# Opsgenie source (`opsgenie`)

Manifest: `src/omniops/sources/manifests/opsgenie.yaml` (24 tables)
Fixtures: `src/omniops/sources/fixtures/opsgenie/`
API reference root: https://docs.opsgenie.com/docs/api-overview (REST API; alert/heartbeat/schedule/team
endpoints are `/v2`, incident/service endpoints `/v1`).

Checked against the official reference on 2026-10-27 (pages fetched as Markdown from
docs.opsgenie.com, `updatedAt` 2025-06/2025-07). Fixtures are the documented "Sample Response"
bodies. Several documented samples are not valid JSON; those fixtures fix only the syntax and say
so in their `note`: Get Incident (missing comma after `extraProperties`), List Incident Timeline
Entries (`"data": "entries"` without a brace, missing/extra commas), Get Schedule Timeline (missing
comma between two `baseTimeline` periods), List On Calls flat (stray `"`), List Escalations (trailing comma after the last element). The note texts of the
List Alert Notes / List Incident Notes samples (film quotes) were replaced by neutral sentences.

**Lifecycle**: Atlassian ended Opsgenie sales on 2025-06-04; end of support is 2027-04-05, when
access (UI and API) is shut off and unmigrated data is deleted. Customers migrate to Jira Service
Management or Compass (https://www.atlassian.com/software/opsgenie/pricing,
https://support.atlassian.com/jira-service-management-cloud/docs/merge-opsgenie-with-jira-service-management/).
A migrated account stops receiving data here.

## Tables -> endpoints -> reference pages

| SQL table | HTTP | Official reference |
|---|---|---|
| `opsgenie_alerts` | GET `/v2/alerts?query=<filters> AND createdAt >= <ms> AND createdAt < <ms>&sort=createdAt&order=desc&offset&limit` | https://docs.opsgenie.com/docs/alert-api#list-alerts |
| `opsgenie_open_alerts` | GET `/v2/alerts?query=status: open ...` | https://docs.opsgenie.com/docs/alert-api#list-alerts |
| `opsgenie_alert_detail` | GET `/v2/alerts/{identifier}?identifierType=id\|tiny\|alias` | https://docs.opsgenie.com/docs/alert-api#get-alert |
| `opsgenie_alert_count` | GET `/v2/alerts/count?query=` | https://docs.opsgenie.com/docs/alert-api#count-alerts |
| `opsgenie_alert_logs` | GET `/v2/alerts/{identifier}/logs?order=asc&limit=100` (follows `paging.next`) | https://docs.opsgenie.com/docs/alert-api-continued#list-alert-logs |
| `opsgenie_alert_notes` | GET `/v2/alerts/{identifier}/notes` | https://docs.opsgenie.com/docs/alert-api-continued#list-alert-notes |
| `opsgenie_alert_recipients` | GET `/v2/alerts/{identifier}/recipients` | https://docs.opsgenie.com/docs/alert-api-continued#list-alert-recipients |
| `opsgenie_incidents` | GET `/v1/incidents?query=... createdAt ...&sort=createdAt&order=desc` | https://docs.opsgenie.com/docs/incident-api#list-incidents |
| `opsgenie_incident_detail` | GET `/v1/incidents/{identifier}?identifierType=id\|tiny` | https://docs.opsgenie.com/docs/incident-api#get-incident |
| `opsgenie_incident_logs` | GET `/v1/incidents/{identifier}/logs` | https://docs.opsgenie.com/docs/incident-api#list-incident-logs |
| `opsgenie_incident_notes` | GET `/v1/incidents/{identifier}/notes` | https://docs.opsgenie.com/docs/incident-api#list-incident-notes |
| `opsgenie_incident_alerts` | GET `/v1/incidents/{identifier}/associated-alert-ids?limit=25` | https://docs.opsgenie.com/docs/incident-api#get-associated-alerts |
| `opsgenie_incident_timeline` | GET `/v2/incident-timelines/{incidentId}/entries?order=asc&limit=20` (cursor `nextOffset` -> `offset`) | https://docs.opsgenie.com/docs/incident-timeline-api#list-incident-timeline-entries |
| `opsgenie_oncall` | GET `/v2/schedules/{schedule}/on-calls?scheduleIdentifierType&flat=true&date`, or (no schedule) GET `/v2/schedules/on-calls?flat=true&date` (deprecated) | https://docs.opsgenie.com/docs/who-is-on-call-api#get-on-calls |
| `opsgenie_schedules` | GET `/v2/schedules?expand=rotation` | https://docs.opsgenie.com/docs/schedule-api#list-schedules |
| `opsgenie_schedule_timeline` | GET `/v2/schedules/{schedule}/timeline?identifierType&date&interval&intervalUnit=days` | https://docs.opsgenie.com/docs/schedule-api#get-schedule-timeline |
| `opsgenie_heartbeats` | GET `/v2/heartbeats` | https://docs.opsgenie.com/docs/heartbeat-api#list-heartbeats |
| `opsgenie_heartbeat` | GET `/v2/heartbeats/{heartbeatName}` | https://docs.opsgenie.com/docs/heartbeat-api#get-heartbeat-request |
| `opsgenie_services` | GET `/v1/services?query&sort=name` | https://docs.opsgenie.com/docs/service-api#list-services |
| `opsgenie_teams` | GET `/v2/teams` (no pagination) | https://docs.opsgenie.com/docs/team-api#list-teams |
| `opsgenie_deployments` | GET `/v2/deployments/search?serviceIds&environmentIds&startTime&endTime&paginationKey&limit` | https://docs.opsgenie.com/docs/deployment-api#search-deployments |
| `opsgenie_escalations` | GET `/v2/escalations` (one row per rule; no pagination) | https://docs.opsgenie.com/docs/escalation-api#list-escalations |
| `opsgenie_maintenance` | GET `/v1/maintenance?type=all\|non-expired\|past` | https://docs.opsgenie.com/docs/maintenance-api#list-maintenance |
| `opsgenie_maintenance_detail` | GET `/v1/maintenance/{maintenanceId}` (one row per rule) | https://docs.opsgenie.com/docs/maintenance-api#get-maintenance |

Roles bound: `active_alerts` (opsgenie_open_alerts), `alert_history` (opsgenie_alerts),
`incidents` (opsgenie_incidents), `oncall` (opsgenie_oncall), `services` (opsgenie_services).

## Auth

- Header `Authorization: GenieKey <apiKey>` (https://docs.opsgenie.com/docs/authentication).
- Key: an API integration key (Settings > Integrations > API) or an account API key (Settings >
  API key management, https://docs.opsgenie.com/docs/api-access-management). Integration keys
  "restricted to access configurations" cannot read configuration endpoints (schedules, teams,
  services) and answer 403. Read access suffices for every table here.
- 401 = key invalid or integration disabled; 402 = plan does not include the feature
  (e.g. Incident API); 403/40301 = key lacks permission.

## Base URLs / regions

- US (default): `https://api.opsgenie.com`.
- EU instance: `https://api.eu.opsgenie.com` (stated at the top of the Alert, Deployment and other API pages).
- Web UI: `https://<domain>.app.opsgenie.com` / `https://<domain>.app.eu.opsgenie.com` (`OPSGENIE_UI_URL`, deep links only).

## Rate limits

https://docs.opsgenie.com/docs/api-rate-limiting

- Token bucket per request domain (Alert, Heartbeat, Integration, Configuration, Incident, Email,
  Search = list alerts / list users, Schedule-oncalls, Integration Configuration), checked per
  minute and per second, account-wide (optional per-integration "fairness").
- Base limits depend on plan and seat count (example in the docs: Standard, 60 users -> 740
  requests/minute and 101/second); the Configuration domain is lower. Actual limits: Reports >
  API usage in the UI.
- States: OK, BURST (accepted, processing delayed 5 s), THROTTLED (HTTP 429). Headers
  `X-RateLimit-State`, `X-RateLimit-Reason` (ACCOUNT | INTEGRATION), `X-RateLimit-Period-In-Sec`
  (60 or 1). Retry with exponential backoff; 503 also retryable
  (https://docs.opsgenie.com/docs/response). The manifest caps itself at 300 requests/minute.

## Pagination

https://docs.opsgenie.com/docs/pagination

- `offset`, `limit` (default 20, max 100), `sort`, `order` (default desc); `paging.next/first/last`
  URLs in the body and `X-Paging-Next` headers.
- List Alerts: offset + limit must stay below 20,000. List Incidents / Services: offset/limit.
- Alert / incident logs and notes: `offset` is an opaque token from the previous page, with
  `direction` next|prev; the manifest follows `paging.next`.
- Incident timeline: `limit` 1..20, next page via `offset = data.nextOffset`.
- Associated alert ids: limit max 25 (no documented paging section).
- List Teams returns everything without pagination.

## Time formats

- Search queries (alerts, incidents): `createdAt`, `lastOccurredAt`, `snoozedUntil`,
  `postmortemPublishDueDate` are Unix epoch **milliseconds** (alerts also accept `DD-MM-YYYY`);
  operators `:`, `<`, `<=`, `>`, `>=`
  (https://support.atlassian.com/opsgenie/docs/search-queries-for-alerts/,
  https://support.atlassian.com/opsgenie/docs/search-syntax-for-incidents/).
  The manifest pushes the window as `createdAt >= start_ms AND createdAt < end_ms`.
- Schedule `date` parameters: `yyyy-MM-dd'T'HH:mm:ssZ` such as `2017-01-15T08:00:00+02:00`; the `+`
  must be URL-encoded (`%2B`) - the manifest sends `+00:00`, encoded by the HTTP client.
- Deployment search `startTime` / `endTime`: ISO DATE_TIME (sent as UTC `...Z`).
- Responses: ISO 8601 UTC (`2017-03-21T20:32:52.353Z`).

## Retention

No API retention limit is documented for alerts or incidents. The whole account (and its data)
is deleted at end of support (2027-04-05) unless migrated.

## Errors

- Standard HTTP codes (https://docs.opsgenie.com/docs/response): 400 invalid JSON, 401, 402, 403
  (40301 permission, 40302 unsupported), 404, 409, 422 semantic errors, 429 throttled, 503 at
  capacity (retry). Write endpoints answer 202 (async; not used here).
- No 2xx-with-error body is documented, so the manifest declares no body error rule.

## Pitfalls

- The createdAt window misses alerts created before the window that are still open (or that
  de-duplicated new occurrences into an old alert: count / lastOccurredAt grow, createdAt does
  not). Use `opsgenie_open_alerts` for the current state and `last_occurred_at` for recency.
- `priority` is not a documented alert search field (it is for incidents): severity filters on
  opsgenie_alerts run in SQL.
- Team and user names in queries must be exact; wildcards (`*`) work only as a suffix of a word
  and not for teams/users. Unknown fields match nothing.
- Identifier types: alerts accept id | tiny | alias (alias only for open alerts), incidents id |
  tiny, schedules id | name. `tinyId` rolls over and is not unique over time.
- Responders and impactedServices are IDs: join opsgenie_teams / opsgenie_services.
- `report.ackTime` / `report.closeTime` units are not stated in the docs; the sample values
  (15702, 60503) are consistent with milliseconds, which is how the columns are labelled.
- The all-schedules on-call endpoint is deprecated (since 2018-08-31) but still documented; pass
  a schedule name for the supported per-schedule endpoint.
- The Deployment API only knows Bitbucket Pipelines deployments and deployments pushed to it via
  the API; its search requires serviceIds.
- The Incident API requires a plan with incident management.
- Maintenance: while a maintenance is `active`, the integrations in its rules are disabled (rule
  state is always `disabled` for integrations) and policies can be enabled/disabled, so alerts may
  never be created or notified. Only `time.type = schedule` maintenances carry start/end dates;
  `for-N-minutes` / `indefinitely` ones must be judged by `status`. Rules are only in the Get
  Maintenance response (opsgenie_maintenance_detail), not in the list.
- Escalation rule delays are documented in minutes (`delay.timeUnit`); `notifyType` default | next
  | previous | users | admins | all decides who in a schedule/team is notified.
- Not bound to the `change_events` role: opsgenie_deployments requires Opsgenie service IDs
  (serviceIds is mandatory), which role filters cannot derive from a service name.
