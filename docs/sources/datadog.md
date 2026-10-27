# Datadog source (`datadog`)

Manifest: `src/omniops/sources/manifests/datadog.yaml` (49 tables)
Fixtures: `src/omniops/sources/fixtures/datadog/`
API reference root: https://docs.datadoghq.com/api/latest/ . Every endpoint page also has a raw Markdown
twin at `<page>.md` (for example https://docs.datadoghq.com/api/latest/logs/search-logs-post.md), which is
what was read for this manifest.

Last checked against the official reference on 2026-10-27. Paths, parameter names, time units, pagination
and response paths come from those pages. All 39 fixtures taken from reference examples were compared
programmatically with (or extracted from) the current pages and are JSON-equal copies. Where the reference
example is a placeholder, the fixture comes from an official Datadog guide, from a recorded response in
Datadog's own API client (`DataDog/datadog-api-client-python`, `tests/v*/cassettes/test_scenarios/*.yaml`),
or is built strictly from the documented schema (fixture `note` says which).

## Tables -> endpoints -> reference pages

| SQL table | HTTP | Official reference |
|---|---|---|
| `datadog_metrics_timeseries` | POST `/api/v2/query/timeseries` (data_source `metrics`, optional formula) | https://docs.datadoghq.com/api/latest/metrics/query-timeseries-data-across-multiple-products/ |
| `datadog_metrics_query` | GET `/api/v1/query` | https://docs.datadoghq.com/api/latest/metrics/query-timeseries-points/ |
| `datadog_metrics_scalar` | POST `/api/v2/query/scalar` (top-N, formula `limit` sorted desc) | https://docs.datadoghq.com/api/latest/metrics/query-scalar-data-across-multiple-products/ |
| `datadog_apm_metrics` | POST `/api/v2/query/timeseries` (data_source `apm_metrics`) | https://docs.datadoghq.com/api/latest/metrics/query-timeseries-data-across-multiple-products/ |
| `datadog_apm_metrics_top` | POST `/api/v2/query/scalar` (data_source `apm_metrics`) | https://docs.datadoghq.com/api/latest/metrics/query-scalar-data-across-multiple-products/ |
| `datadog_metrics_active` | GET `/api/v1/metrics` | https://docs.datadoghq.com/api/latest/metrics/get-active-metrics-list/ |
| `datadog_metrics` | GET `/api/v2/metrics` | https://docs.datadoghq.com/api/latest/metrics/get-a-list-of-metrics/ |
| `datadog_metric_metadata` | GET `/api/v1/metrics/{metric_name}` | https://docs.datadoghq.com/api/latest/metrics/get-metric-metadata/ |
| `datadog_metric_tags` | GET `/api/v2/metrics/{metric_name}/all-tags` | https://docs.datadoghq.com/api/latest/metrics/list-tags-by-metric-name/ |
| `datadog_logs` | POST `/api/v2/logs/events/search` (`storage_tier` indexes / online-archives / flex) | https://docs.datadoghq.com/api/latest/logs/search-logs-post/ |
| `datadog_log_counts` | POST `/api/v2/logs/analytics/aggregate` (compute `total`, group-by sorted `measure` desc) | https://docs.datadoghq.com/api/latest/logs/aggregate-events/ |
| `datadog_log_timeseries` | POST `/api/v2/logs/analytics/aggregate` (compute `timeseries`) | https://docs.datadoghq.com/api/latest/logs/aggregate-events/ |
| `datadog_log_indexes` | GET `/api/v1/logs/config/indexes` | https://docs.datadoghq.com/api/latest/logs-indexes/get-all-indexes/ |
| `datadog_log_exclusion_filters` | GET `/api/v1/logs/config/indexes` (`exclusion_filters` exploded) | https://docs.datadoghq.com/api/latest/logs-indexes/get-all-indexes/ |
| `datadog_spans` | POST `/api/v2/spans/events/search` | https://docs.datadoghq.com/api/latest/spans/search-spans/ |
| `datadog_span_counts` | POST `/api/v2/spans/analytics/aggregate` (`total`) | https://docs.datadoghq.com/api/latest/spans/aggregate-spans/ |
| `datadog_span_timeseries` | POST `/api/v2/spans/analytics/aggregate` (`timeseries`) | https://docs.datadoghq.com/api/latest/spans/aggregate-spans/ |
| `datadog_rum_events` | POST `/api/v2/rum/events/search` | https://docs.datadoghq.com/api/latest/rum/search-rum-events/ |
| `datadog_rum_counts` | POST `/api/v2/rum/analytics/aggregate` (`total`) | https://docs.datadoghq.com/api/latest/rum/aggregate-rum-events/ |
| `datadog_rum_timeseries` | POST `/api/v2/rum/analytics/aggregate` (`timeseries`) | https://docs.datadoghq.com/api/latest/rum/aggregate-rum-events/ |
| `datadog_monitors` | GET `/api/v1/monitor` | https://docs.datadoghq.com/api/latest/monitors/get-all-monitors/ |
| `datadog_monitor_by_id` | GET `/api/v1/monitor/{monitor_id}?group_states=all&with_downtimes=true&with_assets=true` | https://docs.datadoghq.com/api/latest/monitors/get-a-monitors-details/ |
| `datadog_monitor_states` | GET `/api/v1/monitor/{monitor_id}?group_states=all` (`state.groups` exploded) | https://docs.datadoghq.com/api/latest/monitors/get-a-monitors-details/ |
| `datadog_monitor_search` | GET `/api/v1/monitor/search` | https://docs.datadoghq.com/api/latest/monitors/monitors-search/ |
| `datadog_monitor_group_search` | GET `/api/v1/monitor/groups/search` | https://docs.datadoghq.com/api/latest/monitors/monitors-group-search/ |
| `datadog_downtimes` | GET `/api/v2/downtime` | https://docs.datadoghq.com/api/latest/downtimes/get-all-downtimes/ |
| `datadog_events` | POST `/api/v2/events/search` (alerts, Change Tracking change/deployment events, k8s, integrations) | https://docs.datadoghq.com/api/latest/events/search-events/ |
| `datadog_audit_events` | POST `/api/v2/audit/events/search` | https://docs.datadoghq.com/api/latest/audit/search-audit-logs-events/ |
| `datadog_incidents` | GET `/api/v2/incidents/search?include=users` | https://docs.datadoghq.com/api/latest/incidents/search-for-incidents/ |
| `datadog_teams` | GET `/api/v2/team` | https://docs.datadoghq.com/api/latest/teams/get-all-teams/ |
| `datadog_oncall` | GET `/api/v2/team?filter[keyword]=` then GET `/api/v2/on-call/teams/{team_id}/on-call` | https://docs.datadoghq.com/api/latest/on-call/get-team-on-call-users/ |
| `datadog_dora_deployments` | POST `/api/v2/dora/deployments` | https://docs.datadoghq.com/api/latest/delivery-performance/get-a-list-of-deployment-events/ |
| `datadog_dora_failures` | POST `/api/v2/dora/failures` | https://docs.datadoghq.com/api/latest/delivery-performance/get-a-list-of-incident-events/ |
| `datadog_service_definitions` | GET `/api/v2/services/definitions?schema_version=v2.2` | https://docs.datadoghq.com/api/latest/service-definition/get-all-service-definitions/ |
| `datadog_catalog_entities` | GET `/api/v2/catalog/entity` | https://docs.datadoghq.com/api/latest/software-catalog/get-a-list-of-entities/ |
| `datadog_catalog_relations` | GET `/api/v2/catalog/relation` | https://docs.datadoghq.com/api/latest/software-catalog/get-a-list-of-entity-relations/ |
| `datadog_service_dependencies` | GET `/api/v1/service_dependencies` | https://docs.datadoghq.com/api/latest/service-dependencies/get-all-apm-service-dependencies/ |
| `datadog_service_neighbors` | GET `/api/v1/service_dependencies/{service}` | https://docs.datadoghq.com/api/latest/service-dependencies/get-one-apm-services-dependencies/ |
| `datadog_apm_services` | GET `/api/v2/apm/services?filter[env]=` | https://docs.datadoghq.com/api/latest/apm/get-service-list/ |
| `datadog_hosts` | GET `/api/v1/hosts` | https://docs.datadoghq.com/api/latest/hosts/get-all-hosts-for-your-organization/ |
| `datadog_containers` | GET `/api/v2/containers` | https://docs.datadoghq.com/api/latest/containers/get-all-containers/ |
| `datadog_slos` | GET `/api/v1/slo/search` | https://docs.datadoghq.com/api/latest/service-level-objectives/search-for-slos/ |
| `datadog_slo_history` | GET `/api/v1/slo/{slo_id}/history` | https://docs.datadoghq.com/api/latest/service-level-objectives/get-an-slos-history/ |
| `datadog_slo_status` | GET `/api/v2/slo/{slo_id}/status` | https://docs.datadoghq.com/api/latest/service-level-objectives/get-slo-status/ |
| `datadog_dashboards` | GET `/api/v1/dashboard` | https://docs.datadoghq.com/api/latest/dashboards/get-all-dashboards/ |
| `datadog_dashboard_widgets` | GET `/api/v1/dashboard/{dashboard_id}` (widgets; group/powerpack children exploded) | https://docs.datadoghq.com/api/latest/dashboards/get-a-dashboard/ |
| `datadog_synthetics_tests` | GET `/api/v1/synthetics/tests` | https://docs.datadoghq.com/api/latest/synthetics/get-the-list-of-all-synthetic-tests/ |
| `datadog_synthetics_results` | GET `/api/v2/synthetics/tests/{public_id}/results` | https://docs.datadoghq.com/api/latest/synthetics/get-a-tests-latest-results/ |
| `datadog_error_tracking_issues` | POST `/api/v2/error-tracking/issues/search?include=issue` | https://docs.datadoghq.com/api/latest/error-tracking/search-error-tracking-issues/ |

### Roles bound

| Role | Table(s) |
|---|---|
| `metrics` | `datadog_metrics_timeseries`, `datadog_apm_metrics`, `datadog_metrics_scalar` |
| `logs` | `datadog_logs` |
| `log_counts` | `datadog_log_timeseries`, `datadog_log_counts` |
| `traces` / `trace_detail` | `datadog_spans` |
| `service_map` | `datadog_service_dependencies` (observed), `datadog_catalog_relations` (declared) |
| `services` | `datadog_service_definitions`, `datadog_apm_services` |
| `hosts` | `datadog_hosts` |
| `change_events` | `datadog_dora_deployments`, `datadog_events` (`@evt.category:(change OR deployment)`), `datadog_audit_events` |
| `active_alerts` | `datadog_monitor_group_search` (`group_status:alert`) |
| `alert_rules` | `datadog_monitor_by_id` (`monitor_id = alert_id`), `datadog_monitors` |
| `alert_history` | `datadog_monitor_states` |
| `incidents` | `datadog_incidents`, `datadog_dora_failures` |
| `errors` | `datadog_error_tracking_issues` |
| `rum` | `datadog_rum_events` |
| `oncall` | `datadog_oncall`, `datadog_service_definitions` (owner team, contacts, PagerDuty) |
| `k8s_state` | `datadog_containers` (live only) |
| `k8s_events` | `datadog_events` (`source:kubernetes`) |
| `dashboards` | `datadog_dashboards`, `datadog_dashboard_widgets` |
| `slo` | `datadog_slos`, `datadog_slo_status` |

Not bound: `code_changes` (Datadog has no commit/PR API; DORA deployments carry the commit SHA),
`k8s_logs` (pod logs are ordinary logs: `datadog_logs` with `query = 'kube_namespace:x pod_name:y'`),
`tickets`, `docs`, `chat_history`, `profiles` (no public profile-query API in scope).

Role filter templates that use the deployment environment are written
`{{ env if env is string else none }}`: the computed-role fetch path renders binding filters with `env`
bound to the source's environment-variable dict (see Engine gap below), and a bare `{{ env }}` would push
that dict (including secrets) into a search query.

## Authentication

- Headers `DD-API-KEY` + `DD-APPLICATION-KEY` (https://docs.datadoghq.com/api/latest/authentication/).
  Every read endpoint needs the application key. Scope it read-only with the permissions named on each
  reference page: `logs_read_data`, `logs_read_config` (log indexes), `timeseries_query`, `metrics_read`,
  `monitors_read`, `monitors_downtime`, `apm_read`, `rum_apps_read`, `events_read`, `incident_read`,
  `slos_read`, `dashboards_read`, `synthetics_read`, `hosts_read`, `apm_service_catalog_read`,
  `dora_metrics_read`, `error_tracking_read`, `audit_logs_read`, `teams_read`, `on_call_read`.
- Current reference code samples also show `Authorization: Bearer <Personal or Service Access Token>`.
  Setting `DD_BEARER_TOKEN` switches the manifest to that header (the key headers are then not sent).
- `required_env: [DD_API_KEY, DD_APP_KEY]`.

## Base URLs / regions

`base_url = https://api.<DD_SITE>` (the manifest strips a pasted `https://`, `app.` or `api.` prefix).

| Site | `DD_SITE` | API host | UI host |
|---|---|---|---|
| US1 | `datadoghq.com` | api.datadoghq.com | app.datadoghq.com |
| US3 | `us3.datadoghq.com` | api.us3.datadoghq.com | us3.datadoghq.com |
| US5 | `us5.datadoghq.com` | api.us5.datadoghq.com | us5.datadoghq.com |
| EU1 | `datadoghq.eu` | api.datadoghq.eu | app.datadoghq.eu |
| AP1 | `ap1.datadoghq.com` | api.ap1.datadoghq.com | ap1.datadoghq.com |
| AP2 | `ap2.datadoghq.com` | api.ap2.datadoghq.com | ap2.datadoghq.com |
| UK1 | `uk1.datadoghq.com` | api.uk1.datadoghq.com | uk1.datadoghq.com |
| US1-FED | `ddog-gov.com` | api.ddog-gov.com | app.ddog-gov.com |
| US2-FED | `us2.ddog-gov.com` | api.us2.ddog-gov.com | us2.ddog-gov.com |

The site table is printed at the top of every reference page. `DD_API_BASE_URL` / `DD_UI_BASE_URL`
override both (proxies). A wrong site returns 403 even with valid keys. Change Tracking is not supported
on the two FED sites (Tracked Change Types page).

## Rate limits (https://docs.datadoghq.com/api/latest/rate-limits/)

- Per endpoint (sometimes per bucket of endpoints), reported in `X-RateLimit-Limit`, `-Period`,
  `-Remaining`, `-Reset` (seconds until reset), `-Name`. A 429 means wait; the engine honours
  `X-RateLimit-Reset` (capped at 20 s, 3 retries).
- Spans search and spans aggregate: **300 requests per hour** (stated on both reference pages). The span
  tables use a 2 h positive-control lookback and the docs steer rates to `trace.*` / `apm_metrics`.
- Error Tracking search: at most 100 issues per request. v1 synthetics API results: last 150 results.
- No source-wide `requests_per_minute` is set: limits differ per endpoint and a global cap would throttle
  cheap endpoints for the sake of expensive ones.

## Retention (https://docs.datadoghq.com/data_security/data_retention_periods/)

| Data | Retention | Manifest |
|---|---|---|
| Metrics | 15 months | `retention.timeseries: 455d` |
| Logs | plan / index dependent (Standard tier `num_retention_days`, Flex `num_flex_logs_retention_days`) | not clamped; see `datadog_log_indexes` |
| Indexed spans | 15 or 30 days by plan; services/resources statistics 30 days | `retention.spans: 30d` |
| RUM | 30 d sessions/views/actions/errors; 15 d resources/long tasks/vitals | `time.retention: 30d` |
| Events (Event Management) | 15 months | `retention.events: 455d` |
| Audit Trail | 90 days (7 days when Audit Trail is disabled) | `time.retention: 90d` |
| Error Tracking | samples 30 days, issues 1 year after last activity | `time.retention: 30d` |
| Synthetics results | 15 months | - |
| DORA deployments | 2 years | `retention.changes: 730d` |
| Live containers / processes | 36 hours (container metadata 2 hours) | live inventory only |
| Monitors, dashboards, incidents, catalog | lifetime of the account | - |

## Time formats (each table already sends the right unit)

| Endpoint family | Request time params | Response times |
|---|---|---|
| v2 logs / spans / RUM / events / audit search + aggregate | `filter.from` / `filter.to`: strings; epoch **ms**, ISO 8601 or date math (`now-15m`) | ISO 8601 |
| v2 `/query/timeseries`, `/query/scalar` | `from` / `to` / `interval`: int64 epoch **ms** | `times[]` epoch ms |
| v1 `/query` | `from` / `to`: epoch **seconds** | `pointlist` epoch **ms** |
| v1 `/metrics` (active), v1 `/hosts` `from` | epoch seconds | - |
| v1 `/service_dependencies` | `start` / `end`: epoch seconds | - |
| v1 `/slo/{id}/history`, v2 `/slo/{id}/status` | `from_ts` / `to_ts`: epoch seconds | epoch seconds |
| v2 synthetics results | `from_ts` / `to_ts`: epoch **ms** | `started_at` / `finished_at` epoch ms |
| v2 error-tracking search | `from` / `to`: int64 epoch ms | `first_seen` / `last_seen` epoch ms |
| v2 DORA | `from` / `to`: RFC 3339 date-time | RFC 3339 |
| v1 monitors / hosts / groups | - | `last_*_ts`, `last_reported_time` epoch **seconds**; monitor `created`/`modified` ISO |

Durations: span `@duration` / `custom.duration`, RUM `@view.loading_time` and event `duration` are
**nanoseconds**; APM `trace.*.duration` metrics are seconds; Synthetics timings are ms. Aggregate
`compute.interval` is a duration string (`5m`); the tables send the engine's auto step.

## Pagination

| Table(s) | Style |
|---|---|
| logs / spans / RUM / events / audit search | cursor `meta.page.after` -> `page.cursor` (spans: `data.attributes.page.cursor`); page limit 1000 |
| `/api/v2/metrics`, `/api/v2/containers` | cursor `meta.pagination.next_cursor` -> `page[cursor]`; metrics `page[size]` max 10000 |
| monitors list / search / group search | page number from 0 (`page` + `page_size` / `per_page`) |
| incidents search (max 100), downtimes, catalog entities / relations | offset `page[offset]` + `page[size]` / `page[limit]` |
| SLO search, service definitions (max 100), teams (max 100) | `page[number]` (from 0) + `page[size]` |
| hosts | offset `start` + `count` (max 1000), `total_matching` |
| dashboards | offset `start` + `count` |
| synthetics tests | `page_number` (starts at zero) + `page_size` |
| aggregates, scalar, DORA, SLO, synthetics results, log indexes | single page (sorted server-side) |

## 2xx-with-error bodies (declared as error rules, each proven by an `expect_error` fixture)

- v1 `/query`: `status: "error"` with the message in `error`.
- v2 `/query/timeseries`, `/query/scalar`: top-level `errors` string.
- logs / spans / RUM / events / audit search + aggregate: `meta.status: "timeout"` = partial results;
  raised so a partial answer is never presented as complete. `meta.warnings` (e.g. `unknown_index`)
  are non-fatal and not raised.
- v1 SLO history: `errors[]` may accompany a 200; surfaced in `api_errors` / `errors` columns.

## Pitfalls captured in the manifest

- Search syntax: reserved attributes without `@` (service, env, host, status, source, trace_id, version,
  resource_name); everything else needs `@`. Unknown facet = 0 rows, no error.
- Aggregate group-by ordering defaults to **alphabetical**; the `*_counts` tables always send
  `sort: {type: measure, aggregation, order: desc}` (the form used by the official Log Analytics guide),
  and scalar tables send `formulas[].limit {count, order: desc}`.
- Flex / online-archive logs are searched only with `storage_tier`; missing logs may be dropped by an
  index daily quota (`is_rate_limited`) or an exclusion filter (`datadog_log_exclusion_filters`).
- Indexed spans are retention-filter samples; counts are not traffic. Use `apm_metrics` / `trace.*`.
- Recorded spans-aggregate responses carry values under `attributes.compute`, while the reference also
  lists `attributes.computes`; both are read.
- Monitor search facets differ from returned values (`type:metric` vs `query alert`, `group_status:alert`
  vs `Alert`), so those search facets are filter-only columns.
- Host names come in short / FQDN / cloud-id forms (`variants: hostname`).
- Log `status` may be upper-case (`INFO` in the reference example); `level` is lower-cased.
- Error Tracking issue details are in `included[]`, joined to results by id inside the table.
- Service definitions: v2.2 paths first, v1 (`info.dd-service`, `org.team`) as fallback.
- Backend deployments are tracked through the APM `version` tag: `by {version}` on `trace.*` metrics
  shows a rollout even when no deploy event or DORA deployment exists.

## Fixture provenance

| Fixture | Source |
|---|---|
| `logs_search.json`, `metrics_query_v1.json`, `metrics_active.json`, `metric_metadata.json`, `metrics_list_v2.json`, `metric_all_tags.json`, `spans_search.json`, `spans_aggregate_docs.json`, `rum_search.json`, `monitors_list.json` (the documented Monitor object wrapped in `[]`), `monitor_get.json`, `monitor_search.json`, `monitor_group_search.json`, `events_search.json`, `audit_search.json`, `incidents_search.json`, `teams_list.json`, `oncall_team.json`, `service_definitions.json`, `catalog_entities.json`, `catalog_relations.json`, `service_dependencies.json`, `service_dependencies_one.json`, `apm_services.json`, `hosts.json`, `slo_search.json`, `slo_history.json`, `slo_status.json`, `dashboards_list.json`, `dashboard_get.json`, `downtimes.json`, `synthetics_tests.json`, `synthetics_results_v2.json`, `error_tracking_search.json`, `dora_deployments.json`, `dora_failures.json`, `log_indexes.json`, `query_timeseries_docs.json`, `query_scalar_docs.json` | Official API reference 200 example (the last two carry the placeholder `errors: "string"` and are `expect_error` fixtures) |
| `logs_aggregate_counts_guide.json`, `logs_aggregate_timeseries_guide.json`, `logs_aggregate_two_facets_guide.json` | Official guide https://docs.datadoghq.com/logs/guide/build-custom-reports-using-log-analytics-api/ (the reference example is a `<any-key>` placeholder) |
| `query_timeseries_recorded.json`, `query_scalar_recorded.json`, `apm_metrics_timeseries_recorded.json`, `apm_metrics_scalar_recorded.json`, `spans_search_recorded.json` (trimmed), `spans_aggregate_timeseries_recorded.json`, `containers_recorded.json` (trimmed) | Recorded responses from Datadog's official `datadog-api-client-python` test cassettes |
| `*_schema.json` | Built strictly from the documented response schema where the example is a placeholder (grouped timeseries/scalar, APM metrics, spans/RUM aggregates, monitor thresholds and state groups, change event, APM service list, v1 query error, logs timeout) |

## Engine gap found

- `omniops/engine/computed/__init__.py` `role_fetch` renders binding filters with
  `{**ctx_vars, "env": rt.env}`: the deployment-environment variable `env` is shadowed by the source's
  environment-variable dict. A binding filter `env: "{{ env }}"` then renders to that dict (secrets
  included) and is pushed into the request. The Datadog bindings guard with
  `{{ env if env is string else none }}`; the engine should expose the env-var dict under another name.

## Open points

- Maximum `page.limit` for spans / RUM / events / audit search is not stated (1000 assumed, like logs);
  the logs reference shows an example with `limit: 5000` but the pagination guide documents 1000.
- Monitor list / search `page_size` / `per_page` maxima are not stated; teams / service definitions
  `page[number]` start is not stated (0 assumed, as for SLO search and Synthetics).
- DORA list `limit` maximum is not stated (1000 cap, default 200 sent).
- Software Catalog entity reference format for `filter[ref]`, `filter[from_ref]` / `filter[to_ref]` is
  not spelled out; prefer name + kind filters.
- `datadog_apm_metrics` group_by values other than `resource_name` (for example `version`, `peer.service`)
  follow the field description ("optional fields to group by") but have no documented example.
- The v2 query endpoints also accept `apm_resource_stats`, `apm_dependency_stats`, `slo`, `process` and
  `container` data sources; they are not exposed as tables yet (`apm_dependency_stats` requires
  operation_name and resource_name).
- Incident search default query `state:(active OR stable OR resolved OR completed)` relies on the
  documented facet syntax; verify against a live org.
