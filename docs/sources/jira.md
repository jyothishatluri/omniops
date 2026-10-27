# Jira source (`jira`)

Manifest: `src/omniops/sources/manifests/jira.yaml` (22 tables)
Fixtures: `src/omniops/sources/fixtures/jira/`
API reference roots:
- Jira Cloud platform REST v3: https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/
- Jira Service Management Operations REST API (alerts, on-call): https://developer.atlassian.com/cloud/jira/service-desk-ops/rest/v2/intro/
- Jira Service Management Incident REST API (v1, **beta**; ITSM incident details): https://developer.atlassian.com/cloud/incidents/rest/intro/

Checked against the official reference on 2026-10-27. Paths, parameters and example responses come
from the OpenAPI documents that render the reference pages:
`https://developer.atlassian.com/cloud/jira/platform/swagger-v3.v3.json` (Jira platform, version
`1001.0.0-SNAPSHOT`), `https://developer.atlassian.com/cloud/jira/service-desk-ops/swagger.v3.json`
(JSM Operations) and `https://dac-static.atlassian.com/cloud/incidents/swagger.v3.json?_v=0.1.22`
(JSM Incident API, 27 operations under `/jsm/incidents/cloudId/{cloudId}/v1`). Jira fixtures are the
operations' `example` responses verbatim; JSM fixtures are the schema `example` values the
reference pages render as the example response.

## Tables -> endpoints -> reference pages

| SQL table | HTTP | Official reference |
|---|---|---|
| `jira_issues` | GET `/rest/api/3/search/jql?jql=&fields=&expand=renderedFields&maxResults=&nextPageToken=` | https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/#api-rest-api-3-search-jql-get |
| `jira_incidents` | same, JQL ANDed with `JIRA_INCIDENT_JQL` (default `issuetype = Incident`) | https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/#api-rest-api-3-search-jql-get |
| `jira_issue_count` | POST `/rest/api/3/search/approximate-count` `{jql}` | https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/#api-rest-api-3-search-approximate-count-post |
| `jira_issue` | GET `/rest/api/3/issue/{issueIdOrKey}?fields=*all&expand=renderedFields,names,changelog` | https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/#api-rest-api-3-issue-issueidorkey-get |
| `jira_issue_changelog` | GET `/rest/api/3/issue/{issueIdOrKey}/changelog?startAt=&maxResults=` | https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/#api-rest-api-3-issue-issueidorkey-changelog-get |
| `jira_changelogs` | POST `/rest/api/3/changelog/bulkfetch` `{issueIdsOrKeys, fieldIds, maxResults, nextPageToken}` | https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/#api-rest-api-3-changelog-bulkfetch-post |
| `jira_issue_comments` | GET `/rest/api/3/issue/{issueIdOrKey}/comment?orderBy=-created&expand=renderedBody&startAt=&maxResults=` | https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-comments/#api-rest-api-3-issue-issueidorkey-comment-get |
| `jira_issue_remote_links` | GET `/rest/api/3/issue/{issueIdOrKey}/remotelink` | https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-remote-links/#api-rest-api-3-issue-issueidorkey-remotelink-get |
| `jira_projects` | GET `/rest/api/3/project/search?query=&typeKey=&expand=description,lead,insight&startAt=&maxResults=` | https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-projects/#api-rest-api-3-project-search-get |
| `jira_fields` | GET `/rest/api/3/field` | https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-fields/#api-rest-api-3-field-get |
| `jira_users` | GET `/rest/api/3/user/search?query=&startAt=&maxResults=` | https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-user-search/#api-rest-api-3-user-search-get |
| `jira_jsm_alerts` | GET `https://api.atlassian.com/jsm/ops/api/{cloudId}/v1/alerts?query=...createdAt>=ms...&sort=createdAt&order=desc&offset=&size=` | https://developer.atlassian.com/cloud/jira/service-desk-ops/rest/v2/api-group-alerts/#api-api-cloudid-v1-alerts-get |
| `jira_jsm_active_alerts` | same, `query=status: open` | https://developer.atlassian.com/cloud/jira/service-desk-ops/rest/v2/api-group-alerts/#api-api-cloudid-v1-alerts-get |
| `jira_jsm_alert_logs` | GET `.../v1/alerts/{id}/logs?size=100` | https://developer.atlassian.com/cloud/jira/service-desk-ops/rest/v2/api-group-alerts/#api-api-cloudid-v1-alerts-id-logs-get |
| `jira_jsm_alert_notes` | GET `.../v1/alerts/{id}/notes?size=100` | https://developer.atlassian.com/cloud/jira/service-desk-ops/rest/v2/api-group-alerts/#api-api-cloudid-v1-alerts-id-notes-get |
| `jira_jsm_schedules` | GET `.../v1/schedules?query=&expand=rotation&offset=&size=` | https://developer.atlassian.com/cloud/jira/service-desk-ops/rest/v2/api-group-schedules/#api-api-cloudid-v1-schedules-get |
| `jira_jsm_oncalls` | GET `.../v1/schedules/{scheduleId}/on-calls?date=` (schedule name resolved via `.../v1/schedules?query=`) | https://developer.atlassian.com/cloud/jira/service-desk-ops/rest/v2/api-group-schedule-on-calls/#api-api-cloudid-v1-schedules-scheduleid-on-calls-get |
| `jira_jsm_teams` | GET `.../v1/teams` | https://developer.atlassian.com/cloud/jira/service-desk-ops/rest/v2/api-group-team/#api-api-cloudid-v1-teams-get |
| `jira_jsm_incident` | GET `https://api.atlassian.com/jsm/incidents/cloudId/{cloudId}/v1/incident/{issueID}` (key resolved with GET `/rest/api/3/issue/{key}?fields=issuetype`) | https://developer.atlassian.com/cloud/incidents/rest/api-group-incident/#api-jsm-incidents-cloudid-cloudid-v1-incident-issueid-get |
| `jira_jsm_incident_alerts` | GET `.../v1/incident/{issueID}/alert?startKey=` | https://developer.atlassian.com/cloud/incidents/rest/api-group-linked-alerts/#api-jsm-incidents-cloudid-cloudid-v1-incident-issueid-alert-get |
| `jira_jsm_incident_responder_alerts` | GET `.../v1/incident/{issueID}/responder-alerts` | https://developer.atlassian.com/cloud/incidents/rest/api-group-responder-alerts/#api-jsm-incidents-cloudid-cloudid-v1-incident-issueid-responder-alerts-get |
| `jira_jsm_incident_chat_channels` | GET `.../v1/incident/{issueID}/chat/channel` | https://developer.atlassian.com/cloud/incidents/rest/api-group-chat-tools/#api-jsm-incidents-cloudid-cloudid-v1-incident-issueid-chat-channel-get |

JSM tables first call `GET <site>/_edge/tenant_info` (returns `{"cloudId": ...}`, see
https://support.atlassian.com/jira/kb/retrieve-my-atlassian-sites-cloud-id/) when `JIRA_CLOUD_ID`
is not set. JSM Incident tables additionally resolve a `key` filter to the numeric issue id the
Incident API requires (`GET /rest/api/3/issue/{key}?fields=issuetype`); pass `issue_id`
(`jira_issues.id`) to skip that call.

Not bound as tables (write-only or low investigation value): Incident API create/delete, add/remove
alerts/responders/affected services/stakeholders, major-incident update, chat channel create/unlink,
post-incident review create/primary-incident (Premium), `GET .../affected-service` (service ARIs only;
the same list is in `jira_jsm_incident.affected_services`), `GET .../responder` / `.../stakeholder`
(ARIs only) and `GET .../major` (boolean; the Major incident field is in `jira_jsm_incident`).

Roles bound: `tickets` (jira_issues), `incidents` (jira_incidents, `time_field = created`),
`active_alerts` (jira_jsm_active_alerts), `oncall` (jira_jsm_oncalls).

## Auth

- Basic auth: `Authorization: Basic base64(email:api_token)` (`JIRA_AUTH=basic`, default;
  `JIRA_EMAIL` + `JIRA_API_TOKEN`). Tokens: https://id.atlassian.com/manage-profile/security/api-tokens.
- Bearer: `JIRA_AUTH=bearer`, `JIRA_API_TOKEN` = OAuth 2.0 (3LO) access token. OAuth and scoped
  API tokens must call the gateway `https://api.atlassian.com/ex/jira/<cloudId>` (set `JIRA_URL` to it
  and `JIRA_UI_URL` to the site for browser links); the `*.atlassian.net` host rejects scoped tokens.
- Read-only needs: Browse projects (issues, comments, changelogs); Browse users and groups (user
  search; otherwise silently empty). JSM Operations: an agent with access to JSM Operations
  (OAuth scopes `read:ops-alert:jira-service-management`, `read:ops-config:jira-service-management`);
  the reference's curl examples use basic auth with an API token.

## Base URLs

- Jira: `https://<site>.atlassian.net` (no `/rest`); gateway form `https://api.atlassian.com/ex/jira/<cloudId>`.
- JSM Operations: `https://api.atlassian.com/jsm/ops/api/<cloudId>/v1/...` (no regional variants).
- JSM Incident API: `https://api.atlassian.com/jsm/incidents/cloudId/<cloudId>/v1/...` for basic auth
  (scripts, bots); OAuth 2.0 (3LO) apps use `https://api.atlassian.com/ex/jira/<cloudId>/jsm/incidents/v1/...`;
  Forge apps `/jsm/incidents/cloudId/<cloudId>/v1/...` via `requestJira`. Scope
  `read:incident:jira-service-management`. The intro page writes `/v1/incidents/<issueID>` (plural)
  in its overview while the operation reference and OpenAPI use `/v1/incident/{issueID}`; the
  manifest follows the operation reference.
- Cloud id: `JIRA_CLOUD_ID`, or `https://<site>.atlassian.net/_edge/tenant_info`.

## Rate limits

- HTTP 429 with `Retry-After` (s), `X-RateLimit-Limit/Remaining/Reset`, `X-RateLimit-NearLimit` and
  `RateLimit-Reason` (`jira-burst-based`, `jira-quota-global-based`, `jira-quota-tenant-based`,
  `jira-per-issue-on-write`). https://developer.atlassian.com/cloud/jira/platform/rate-limiting/
- Burst: ~100 GET/POST requests/s per tenant (GET issue 150/s).
- Points-based hourly quotas (65,000 points/hour global pool; 1 point per request + 1 per object,
  2 per user/group object) apply to Forge/Connect/OAuth 2.0 apps from 2 March 2026; API-token
  traffic is only subject to burst limits.
- JSM Operations alerts: `offset + size < 20,000`; `size` max 100 (alerts, logs, notes), 50 (schedules).
- Manifest: concurrency 4, 300 requests/minute.

## Retention

Jira keeps issues, comments and changelogs indefinitely (until deleted or archived; archived-project
issues are excluded from search unless `includeArchivedProjects=true`). JSM Operations alert retention
depends on the plan; alerts older than the retention window are not returned.

## Time formats

- Issue fields: `"2021-01-17T12:34:00.000+0000"` (offset without colon), parsed to UTC.
- JQL date literals: `"yyyy-MM-dd HH:mm"` / `"yyyy/MM/dd HH:mm"` / `"yyyy-MM-dd"`, interpreted in the
  API user's **profile time zone** with no way to say UTC. The manifest widens the pushed-down
  window by 14 h each side and re-applies the exact UTC range in SQL.
- Bulk changelog `created`: epoch seconds (example `1492070429`); single-issue changelog: ISO.
- JSM alert search: `createdAt` in **epoch milliseconds** (`createdAt >= 1470394841148`) or
  `DD-MM-YYYY`. The `from`/`to` query parameters of List alerts are typed int32 with no unit
  documented, so they are not used. On-calls `date`: RFC 3339 date-time. JSM response times are ISO 8601 UTC.

## Pagination

- `/search/jql`: `nextPageToken` (absent on the last page; `isLast`), `maxResults` up to 100 with
  fields (5000 when only ids). Token expires after 7 days.
- Changelog, comments, projects, users: `startAt` / `maxResults` (`total` where returned).
- Bulk changelog: `nextPageToken` in the JSON body.
- JSM: `offset` / `size` (`count` = total). Alert logs/notes page with a relative `links.next`
  (`/v1/alerts/{id}/logs?after=...`) that the engine cannot resolve on this host: first 100 only.
- JSM Incident API: cursor `startKey` (int64) in the body, sent back as `?startKey=`; `null` on the
  last page. Only linked alerts are paged; page sizes are an implementation detail.

## Pitfalls

- `/rest/api/3/search` (startAt/total) is deprecated and being removed (changelog CHANGE-2046);
  only `/rest/api/3/search/jql` is used. It needs **bounded** JQL (a search restriction) and
  returns **only ids** unless `fields` is given.
- Unknown JQL values (project, status, issuetype, label) are HTTP 400 errors, not empty results.
- v3 rich text is ADF JSON: the manifest reads `renderedFields` / `renderedBody` HTML and strips
  tags; ADF paragraphs are the fallback (the official examples carry plain strings or ADF).
- JQL `text ~` is a stemmed word search over summary, description, environment, comments and text
  custom fields; it is not a substring match.
- Moved issues answer with their new key.
- Severity in JSM is a custom field: look it up with `jira_fields`.
- Search results are eventually consistent (`reconcileIssues` exists for read-after-write).
- JSM Operations must be enabled; otherwise JSM tables fail with 401/403/404.
- The JSM Incident API is **beta** (v1): fields may change; experimental methods need
  `X-ExperimentalApi: opt-in` (none of the read methods used here are experimental). It addresses
  incidents by **numeric issue id** and returns neither key nor id in the body. Non-incident issues
  return 404 (0 rows) or 400. Major incident / post-incident review features need Premium or Enterprise.
- Responder alert ids (`<uuid>-<epoch ms>`) are JSM Operations alerts raised to page responders;
  linked alert ids join `jira_jsm_alerts.id`.

## Engine gaps

- Relative next links on another host: JSM Operations alert logs/notes return
  `links.next = /v1/alerts/{id}/logs?after=...`, relative to
  `https://api.atlassian.com/jsm/ops/api/<cloudId>`, while `next_url` resolves against the Jira
  `base_url`; only the first page (100 entries) is read. A per-table next-link base template would fix it.
- No ADF to text transform: v3 rich text (description, comments) is read from `renderedFields` /
  `renderedBody` HTML (tags stripped); the ADF fallback only collects paragraph-level text nodes.
- JQL / CQL date literals are in the API user's profile time zone with no UTC form: windows are
  widened by 14 h and re-applied in SQL (correct but fetches extra rows); knowing the user's time
  zone (GET /rest/api/3/myself `timeZone`) at runtime would allow exact pushdown.
