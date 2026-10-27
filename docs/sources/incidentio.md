# incident.io source (`incidentio`)

Manifest: `src/omniops/sources/manifests/incidentio.yaml` (27 tables)
Fixtures: `src/omniops/sources/fixtures/incidentio/`
API reference root: https://docs.incident.io/api-reference/introduction (pages generated from the
OpenAPI spec; full spec https://api.incident.io/v1/openapiV3.json, per-tag specs
`https://docs.incident.io/openapi/tags/<tag>.json`, changelog
https://docs.incident.io/api-reference/changelog).

Checked against the official reference on 2026-10-27. Every fixture is the 200-response example
of the endpoint's per-tag OpenAPI spec (the "Response" example rendered on the reference page),
verbatim; the tables added in the second pass take theirs from the full spec
(https://api.incident.io/v1/openapiV3.json, same examples). `error_body.json` is the error body
documented in the introduction's Errors section.

## Tables -> endpoints -> reference pages

| SQL table | HTTP | Official reference |
|---|---|---|
| `incidentio_incidents` | GET `/v2/incidents?created_at[gte]&created_at[lte]&status_category[one_of]&severity[one_of]&status[one_of]&incident_type[one_of]&mode[one_of]&sort_by=created_at_newest_first&page_size=250&after` | https://docs.incident.io/api-reference/incidents-v2/list |
| `incidentio_incident` | GET `/v2/incidents/{id}` (ULID or reference number) | https://docs.incident.io/api-reference/incidents-v2/show |
| `incidentio_incident_updates` | GET `/v2/incident_updates?incident_id&page_size=250&after` | https://docs.incident.io/api-reference/incident-updates-v2/list |
| `incidentio_timeline_items` | GET `/v2/incident_timeline_items?incident_id&page_size=250&after` | https://docs.incident.io/api-reference/incident-timeline-items-v2/list |
| `incidentio_activity_log` | GET `/v2/incident_activity_log_entries?incident_id&page_size=250&after` | https://docs.incident.io/api-reference/incident-activity-log-entries-v2/list |
| `incidentio_incident_participants` | GET `/v2/incident_participants?incident_id` | https://docs.incident.io/api-reference/incident-participants-v2/list |
| `incidentio_incident_attachments` | GET `/v1/incident_attachments?incident_id` or `?resource_type&external_id` | https://docs.incident.io/api-reference/incident-attachments-v1/list |
| `incidentio_incident_relationships` | GET `/v1/incident_relationships?incident_id&page_size=250&after` | https://docs.incident.io/api-reference/incident-relationships-v1/list |
| `incidentio_postmortem_documents` | GET `/v1/postmortem_documents?incident_id&sort_by=created_at_newest_first&page_size=250&after` | https://docs.incident.io/api-reference/postmortemdocuments-v1/list |
| `incidentio_postmortem_content` | GET `/v1/postmortem_documents/{id}/content` (markdown) | https://docs.incident.io/api-reference/postmortemdocuments-v1/show-content |
| `incidentio_follow_ups` | GET `/v3/follow_ups?incident_id&assignee_team_id&incident_mode&created_at[gte]&created_at[lte]&page_size=250&after` | https://docs.incident.io/api-reference/follow-ups-v3/list |
| `incidentio_alerts` | GET `/v2/alerts?created_at[gte]&created_at[lte]&status[one_of]&deduplication_key[is]&alert_source[one_of]&tags[one_of]&page_size=50&after` | https://docs.incident.io/api-reference/alerts-v2/list |
| `incidentio_firing_alerts` | GET `/v2/alerts?status[one_of]=firing&page_size=50` | https://docs.incident.io/api-reference/alerts-v2/list |
| `incidentio_incident_alerts` | GET `/v2/incident_alerts?incident_id\|alert_id&page_size=50` | https://docs.incident.io/api-reference/incident-alerts-v2/list |
| `incidentio_escalations` | GET `/v2/escalations?created_at[gte]&created_at[lte]&status[one_of]&incident[one_of]&alert[one_of]&escalation_path[one_of]&page_size=50` | https://docs.incident.io/api-reference/escalations-v2/list |
| `incidentio_maintenance_windows` | GET `/v1/maintenance_windows?status=active\|upcoming\|past&page_size=50&after` | https://docs.incident.io/api-reference/maintenancewindows-v1/list |
| `incidentio_oncall_now` | GET `/v2/schedules?page_size=25` (one row per `current_shifts[]`) | https://docs.incident.io/api-reference/schedules-v2/list |
| `incidentio_schedules` | GET `/v2/schedules?page_size=25` | https://docs.incident.io/api-reference/schedules-v2/list |
| `incidentio_schedule_entries` | GET `/v2/schedule_entries?schedule_id&entry_window_start&entry_window_end` (`schedule_entries.final[]`, follows `pagination_meta.after_url`) | https://docs.incident.io/api-reference/schedule-entries-v2/list |
| `incidentio_schedule_overrides` | GET `/v2/schedule_overrides?schedule_id&rotation_id&layer_id&page_size=250&after` | https://docs.incident.io/api-reference/schedule-overrides-v2/list |
| `incidentio_severities` | GET `/v1/severities` | https://docs.incident.io/api-reference/severities-v1/list |
| `incidentio_incident_statuses` | GET `/v1/incident_statuses` | https://docs.incident.io/api-reference/incident-statuses-v1/list |
| `incidentio_incident_types` | GET `/v1/incident_types` | https://docs.incident.io/api-reference/incident-types-v1/list |
| `incidentio_custom_fields` | GET `/v2/custom_fields` | https://docs.incident.io/api-reference/custom-fields-v2/list |
| `incidentio_users` | GET `/v2/users?email&slack_user_id&include_inactive&page_size=250` | https://docs.incident.io/api-reference/users-v2/list |
| `incidentio_catalog_types` | GET `/v3/catalog_types` | https://docs.incident.io/api-reference/catalog-types-v3/list-types |
| `incidentio_catalog_entries` | GET `/v3/catalog_entries?catalog_type_id&identifier&page_size=250&after` | https://docs.incident.io/api-reference/catalog-entries-v3/list-entries |

Roles bound: `incidents` (incidentio_incidents), `active_alerts` (incidentio_firing_alerts),
`alert_history` (incidentio_alerts), `oncall` (incidentio_oncall_now - current shifts).

Not bound: `change_events` (incident.io has no change/deploy feed in its public API) and
`services` (services live in the catalog, whose type ID differs per account).

## Auth

- `Authorization: Bearer <INCIDENTIO_API_KEY>`. Keys are created in Settings > API keys with
  chosen permissions (account-level or team-scoped); they survive deactivation of the creating
  user. User API keys act with a user's own permissions. Any key can call GET `/v1/identity`.
- Escalations need "View data" or "Create and manage on-call resources".

## Base URL

`https://api.incident.io` for every account (no regional hosts documented). Web UI
`https://app.incident.io`.

## Rate limits

https://docs.incident.io/api-reference/introduction#rate-limits

- Default 1,200 requests/minute per API key (token bucket, refills 20/s).
- Lower limits: List incidents (v1 and v2) burst 60 / 60 per minute; Bulk update catalog entries
  10 / 60/min; Update catalog entry 25 / 300/min; Show post-mortem document content 60 / 60/min;
  Preview schedule entries 10 / 6/min; alert source events 60 / 120/min per source.
- Headers `X-RateLimit-Limit` (e.g. `60, 1200;window=60, 60;window=60`), `-Remaining`, `-Used`,
  `-Reset` (Unix seconds). 429 carries `Retry-After` (seconds) and a JSON body
  `{"type": "too_many_requests", "rate_limit": {...}}`. Some limits are shared by all keys of
  the account. The manifest caps itself at 1,000/min and honours Retry-After.

## Pagination

- Cursor: `page_size` (default 25; max per endpoint: incidents 250 per the description (schema
  says 500), incident updates / timeline / activity log / follow-ups / catalog entries 250,
  alerts / incident alerts / escalations 50, schedules and users up to 10,000) and `after`
  (= `pagination_meta.after`). Keep going until no `after` is returned - **do not stop at a short
  or empty page** (documented). The engine's cursor pagination does exactly that.
- Schedule entries: `pagination_meta.after_url` (full URL) or `after` passed back as
  `entry_window_start`; the manifest follows `after_url`.
- Configuration lists (severities, statuses, catalog types) return everything in one response.

## Time formats

- Responses: RFC3339 UTC with microseconds (`2021-08-17T13:28:57.801578Z`).
- Filters use deep-object operators `gte`, `lte`, `date_range` (`start~end`).
  - Incidents: the docs show only DATES (`created_at[lte]=2021-01-02`,
    `created_at[date_range]=2024-12-02~2024-12-08`), so the manifest pushes
    `created_at[gte]=<start date>` and `created_at[lte]=<end date + 1 day>` and re-applies the
    exact window in SQL.
  - Alerts, follow-ups: `updated_at[gte]=2025-01-01T00:00:00Z` (RFC3339) is documented, with the
    "same operators as created_at"; the manifest sends RFC3339 for created_at on alerts,
    follow-ups and escalations.
  - Schedule entries: `entry_window_start` / `entry_window_end` RFC3339 date-time.

## Retention

No retention limit is documented for incidents, alerts or follow-ups.

## Errors

https://docs.incident.io/api-reference/introduction#errors - standard HTTP codes with a JSON body
`{"type", "status", "request_id", "errors": [{"code", "message", "source": {"field"}}]}`; most
common is 422 `validation_error` (e.g. a bad filter value). No 2xx-with-error bodies are
documented and no documented 2xx GET response has a top-level `errors` key (checked against the
full OpenAPI spec), so the manifest declares a defensive source-wide rule on a non-empty
`$.errors` (fixture `error_body.json` with `expect_error`).

## Pitfalls

- Filters for status, severity, incident type, custom fields and roles take IDs, not names: look
  them up in incidentio_incident_statuses / incidentio_severities (the SQL `severity` / `status`
  columns are names and are filtered in SQL).
- Status categories differ from UI labels: `live` = Active, `learning` = Post-incident.
- Test and tutorial incidents are excluded unless `mode` is set explicitly.
- `GET /v2/follow_ups` is deprecated (changelog); `/v3/follow_ups` is used. Follow-ups are
  ordered by ID oldest first.
- Activity log omits chat messages, channel joins/leaves and call transcript fragments.
- `next_shifts` on schedules is only returned with page_size <= 25 (the manifest uses 25).
- incidentio_oncall_now is the CURRENT on-call; for who was on call at an alert time use
  incidentio_schedule_entries with a ts window.
- Team-scoped keys silently omit other teams' private incidents.
- Alert `service` is derived from the alert attribute named `service` (if the account defines
  one); otherwise inspect `attributes`.
- Maintenance windows (https://docs.incident.io/alerts/maintenance-windows.md) override alert
  routing while active: matching alerts are held (no escalation or incident), attached to a chosen
  incident, or escalated to chosen targets; at the end they can be resolved or re-routed. The
  alerts list has an `include_maintenance_window[is]` filter whose default is not documented -
  `incidentio_alerts.include_maintenance_window = true` makes it explicit.
- Incident attachments need either `incident_id` or BOTH `resource_type` and `external_id`
  (e.g. `pager_duty_incident` + the PagerDuty incident id) - that is the cross-tool join key.
- Post-mortem content is limited to 60 requests/minute and needs the
  `in_app_postmortems.copy_as_markdown` scope; only in-app documents have content.
- Not modelled (considered): workflow runs, call sessions / transcripts, status page incidents,
  escalation paths, alert sources/routes, teams (v3), incident roles/timestamps configuration.
