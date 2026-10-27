# Kibana source (`kibana`)

Manifest: `src/omniops/sources/manifests/kibana.yaml` (15 tables)
Fixtures: `src/omniops/sources/fixtures/kibana/`
API reference root: https://www.elastic.co/docs/api/doc/kibana/ (OpenAPI document downloadable from
the same site; every example response used for a fixture was taken from it, operation by
operation).

Checked against the official reference on 2026-10-26 (Kibana 9.x reference; all endpoints below are
also public in 8.x unless noted). Alert *documents* (alerts-as-data) are read from Elasticsearch by
`elasticsearch_alerts`; Kibana's own alert search (`/internal/rac/alerts/find`) is internal and not
used. Security detection alerts have a public search API and get their own table here.

## Tables -> endpoints -> reference pages

| SQL table | HTTP | Official reference |
|---|---|---|
| `kibana_alerting_rules` | GET `/api/alerting/rules/_find` (`search`, `search_fields`, KQL `filter`, `filter_consumers`, `page`/`per_page`) | https://www.elastic.co/docs/api/doc/kibana/operation/operation-get-alerting-rules-find |
| `kibana_alerting_rule` | GET `/api/alerting/rule/{id}` | https://www.elastic.co/docs/api/doc/kibana/operation/operation-get-alerting-rule-id |
| `kibana_maintenance_windows` | GET `/api/maintenance_window/_find` (`status[]`, `title`, `created_by`, `page`/`per_page` max 100) | https://www.elastic.co/docs/api/doc/kibana/operation/operation-get-maintenance-window-find |
| `kibana_cases` | GET `/api/cases/_find` (`status`, `severity`, `tags`, `owner`, `search`/`searchFields`, `from`, `sortField=updatedAt`, `perPage` max 100) | https://www.elastic.co/docs/api/doc/kibana/operation/operation-findcasesdefaultspace |
| `kibana_case_comments` | GET `/api/cases/{caseId}/comments/_find` | https://www.elastic.co/docs/api/doc/kibana/operation/operation-findcasecommentsdefaultspace |
| `kibana_case_alerts` | GET `/api/cases/{caseId}/alerts` | https://www.elastic.co/docs/api/doc/kibana/operation/operation-getcasealertsdefaultspace |
| `kibana_case_activity` | GET `/api/cases/{caseId}/user_actions/_find` (`types[]`, `sortOrder`) | https://www.elastic.co/docs/api/doc/kibana/operation/operation-findcaseactivitydefaultspace |
| `kibana_slos` | GET `[/s/{space}]/api/observability/slos` (`kqlQuery`, `sortBy`, `sortDirection`, `hideStale`, `page`/`perPage` max 5000) | https://www.elastic.co/docs/api/doc/kibana/operation/operation-findslosop |
| `kibana_synthetics_monitors` | GET `/api/synthetics/monitors` (`query`, `tags`, `monitorTypes`, `locations`, `status`, `page`/`per_page`) | https://www.elastic.co/docs/api/doc/kibana/operation/operation-get-synthetic-monitors |
| `kibana_apm_annotations` | GET `/api/apm/services/{serviceName}/annotation/search` (`environment`, `start`, `end` ISO 8601) | https://www.elastic.co/docs/api/doc/kibana/operation/operation-getannotation |
| `kibana_data_views` | GET `/api/data_views` | https://www.elastic.co/docs/api/doc/kibana/operation/operation-getalldataviewsdefault |
| `kibana_security_alerts` | POST `/api/detection_engine/signals/search` (Query DSL body) | https://www.elastic.co/docs/api/doc/kibana/operation/operation-searchalerts |
| `kibana_connectors` | GET `/api/actions/connectors` | https://www.elastic.co/docs/api/doc/kibana/operation/operation-get-actions-connectors |
| `kibana_status` | GET `/api/status` | https://www.elastic.co/docs/api/doc/kibana/operation/operation-get-status |
| `kibana_alerting_health` | GET `/api/alerting/_health` | https://www.elastic.co/docs/api/doc/kibana/operation/operation-getalertinghealth |

Roles bound: `alert_rules` (kibana_alerting_rules), `incidents` (kibana_cases), `slo`
(kibana_slos), `change_events` (kibana_apm_annotations: deployment / version markers).

### Fixture provenance

All fixtures are verbatim example responses from the Kibana API reference (OpenAPI `examples`):
`alerting_rules_find.json` + `alerting_rules_find_2.json` (get-alerting-rules-find, examples 1 and
2), `alerting_rule_get.json`, `maintenance_windows_find.json`, `cases_find.json`,
`case_comments_find.json`, `case_alerts.json`, `case_activity_find.json`
(findCaseActivityResponse), `slos_find.json`, `synthetics_monitors.json`
(getSyntheticMonitorsResponseExample1; a trailing comma in the documented JSON removed),
`apm_annotations.json`, `data_views.json`, `security_alerts_search_aggs.json`,
`connectors.json` (getConnectorsResponse), `status.json` + `status_redacted.json`,
`alerting_health.json`. Exception: `security_alerts_search.json` keeps the documented
signals/search envelope and adds one hit built from the documented Security alert schema fields
(the reference example is aggregation-only).

## Auth

- `Authorization: ApiKey <encoded>` - an Elasticsearch API key (`KIBANA_API_KEY`, falling back to
  `ELASTICSEARCH_API_KEY`). Basic auth fallback (`KIBANA_USERNAME` / `KIBANA_PASSWORD`, falling
  back to the Elasticsearch credentials).
- Headers sent on every request: `kbn-xsrf: true` (required on non-GET calls such as
  `signals/search`), `Elastic-Api-Version: 2023-10-31` (the only public API version; pins the
  versioned public APIs so a Kibana upgrade cannot silently change response shapes).
- The key needs Kibana feature privileges in the target space: Stack Rules / Alerting, Cases
  (per owner: Stack, Observability, Security), SLO, Synthetics, APM, Security alerts. Missing
  privileges give 403 for some APIs and silently empty lists for others.

## Base URLs and spaces

- Elastic Cloud Hosted: `https://<deployment>.kb.<region>.<provider>.cloud.es.io`.
- Serverless: `https://<project>.kb.<region>.<provider>.elastic.cloud`.
- Self-managed: `http(s)://<host>:5601` (plus any `server.basePath`).
- Spaces: set `KIBANA_SPACE`; every request is then prefixed with `/s/<space>` (the default space
  needs no prefix). Rules, cases, SLOs, monitors, connectors and data views are space-scoped.

## Rate limits

No published request quota. Kibana proxies to Elasticsearch and shares resources with the UI and
the task manager; keep `per_page` <= 100 (cases / maintenance windows enforce 100). Concurrency is
limited to 3 in the manifest.

## Retention

Saved objects (rules, cases, comments, SLO definitions, monitors, connectors) live until deleted.
SLO summaries are recomputed continuously by transforms. Rule execution history is in the
`.kibana-event-log-*` data stream (default 90 days) and is not exposed by a public API.

## Time formats

- Request time parameters are ISO 8601 strings (`start`/`end` of the APM annotation search, `from`
  of cases `_find`).
- Response timestamps are ISO 8601 strings (`created_at`, `updated_at`, `execution_status.last_execution_date`),
  except APM annotations whose `@timestamp` is epoch milliseconds.
- `execution_status.last_duration` is milliseconds; case `duration` is seconds.

## Pitfalls

- Everything is space-scoped: an empty result in the wrong space is not proof of absence.
- Rule thresholds live in `params` and differ per rule type (`threshold` + `thresholdComparator`,
  `criteria[]`, APM `threshold`).
- `execution_status: error` / `last_run.outcome: failed` means the rule could not evaluate - a
  monitoring blind spot. `kibana_alerting_health` shows framework-wide execution problems.
- Maintenance windows and snoozes suppress notifications, not alert documents.
- Synthetics `GET /api/synthetics/monitors` returns configuration only; check results are in the
  `synthetics-*` data streams (query with `elasticsearch_search`).
- SLO summaries lag by the transform frequency plus `syncDelay`; `NO_DATA` usually means the SLO's
  source query matches nothing.
- `kibana_security_alerts` excludes building-block alerts, like the Alerts UI.
