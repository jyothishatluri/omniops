# Grafana (`source: grafana`)

Manifest: `src/omniops/sources/manifests/grafana.yaml` · fixtures: `src/omniops/sources/fixtures/grafana/`
Companion sources through Grafana's data-source proxy: [`grafana_prometheus`](grafana_prometheus.md)
(this research), `grafana_loki`, `grafana_tempo`.

Grafana is both a UI over other back ends and a store of its own objects (dashboards,
annotations, Grafana-managed alert rules, silences, contact points). This manifest covers the
stored objects plus the unified query endpoint `/api/ds/query`, which runs a query against any
backend data source by uid and returns data frames.

Research date: 2026-10-26. Reference stack: a local Grafana 13.2 on `:3000` with anonymous access
and file-provisioned data sources `prometheus`, `loki`, `tempo` (uids equal to the plugin names).

## Tables

| SQL table | Method + endpoint | Official reference |
|---|---|---|
| `grafana_search` | `GET /api/search` (`query`, `tag`, `type`, `folderUIDs`, `dashboardUIDs`, `starred`, `limit`, `page`) | https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/folder_dashboard_search/ |
| `grafana_folders` | `GET /api/folders` (`parentUid`, `limit`, `page`) and `GET /api/folders/:uid` | https://grafana.com/docs/grafana/v12.4/developers/http_api/folder/ |
| `grafana_dashboard` | `GET /api/dashboards/uid/:uid` | https://grafana.com/docs/grafana/v12.4/developers/http_api/dashboard/#get-dashboard-by-uid |
| `grafana_dashboard_panels` | `GET /api/dashboards/uid/:uid` (panels + targets flattened, collapsed rows included) | same; panel/row schema: https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/view-dashboard-json-model/ |
| `grafana_dashboard_variables` | `GET /api/dashboards/uid/:uid` (`templating.list`) | https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/view-dashboard-json-model/#templating |
| `grafana_dashboard_annotation_queries` | `GET /api/dashboards/uid/:uid` (`annotations.list`) | https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/annotate-visualizations/ |
| `grafana_dashboard_versions` | `GET /api/dashboards/uid/:uid/versions` (`limit`, `start`) | https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/dashboard_versions/ |
| `grafana_dashboard_version` | `GET /api/dashboards/uid/:uid/versions/:version` (full JSON of one version) | https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/dashboard_versions/ |
| `grafana_library_panels` | `GET /api/library-elements?kind=1` (`searchString`, `folderFilterUIDs`, `sortDirection`, `perPage`, `page`) | https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/library_element/ |
| `grafana_datasources` | `GET /api/datasources` | https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/data_source/#get-all-data-sources |
| `grafana_datasource` | `GET /api/datasources/uid/:uid` | https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/data_source/#get-a-single-data-source-by-uid |
| `grafana_datasource_health` | `GET /api/datasources/uid/:uid/health` | https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/data_source/#check-data-source-health |
| `grafana_ds_query` | `POST /api/ds/query` (any backend data source) | https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/data_source/#query-a-data-source |
| `grafana_annotations` | `GET /api/annotations` (`from`, `to` ms, `limit`, `tags`, `type`, `dashboardUID`, `panelId`, `alertUID`) | https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/annotations/#find-annotations |
| `grafana_annotation_tags` | `GET /api/annotations/tags` (`tag` substring, `limit`) | https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/annotations/#find-annotations-tags |
| `grafana_alert_rules` | `GET /api/v1/provisioning/alert-rules` and `/alert-rules/:uid` | https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/alerting_provisioning/#route-get-alert-rules |
| `grafana_alert_rule_queries` | same, one row per `data[]` query / expression | https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/alerting_provisioning/#alertquery |
| `grafana_alert_rule_status` | `GET /api/prometheus/grafana/api/v1/rules` (`RouteGetGrafanaRuleStatuses`; `folder_uid`, `rule_group`, `rule_name`, `rule_uid`, `search.rule_name`, `state`, `health`, `rule_type`, `dashboard_uid`, `datasource_uid`, `receiver_name`, `limit_alerts`, `group_limit` + `group_next_token`) | https://github.com/grafana/grafana/blob/main/pkg/services/ngalert/api/tooling/post.json (swagger comments in `definitions/prom.go`, parsing in `api/prometheus/api_prometheus.go`) |
| `grafana_alert_instances` | `GET /api/prometheus/grafana/api/v1/alerts` (`RouteGetGrafanaAlertStatuses`) | same |
| `grafana_active_alerts` | `GET /api/alertmanager/grafana/api/v2/alerts` (`RouteGetGrafanaAMAlerts`) | same |
| `grafana_silences` | `GET /api/alertmanager/grafana/api/v2/silences` (`RouteGetGrafanaSilences`) | same |
| `grafana_alert_state_history` | `GET /api/v1/rules/history` (`RouteGetStateHistory`; `from`/`to` s, `limit`, `ruleUID`, `dashboardUID`, `panelID`, `current`, `previous`, `labels_<name>`, `matchers`) | same; parameter parsing: https://github.com/grafana/grafana/blob/main/pkg/services/ngalert/api/api_ruler_history.go |
| `grafana_contact_points` | `GET /api/v1/provisioning/contact-points` (`name`) | https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/alerting_provisioning/#route-get-contactpoints |
| `grafana_notification_policies` | `GET /api/v1/provisioning/policies` | https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/alerting_provisioning/#route-get-policy-tree |
| `grafana_mute_timings` | `GET /api/v1/provisioning/mute-timings` | https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/alerting_provisioning/#route-get-mute-timings |
| `grafana_health` | `GET /api/health` | https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/other/#health-api |
| `grafana_frontend_settings` | `GET /api/frontend/settings` | https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/other/#get-settings |
| `grafana_dashboard_write` (write) | `POST /api/dashboards/db` | https://grafana.com/docs/grafana/v12.4/developers/http_api/dashboard/#create--update-dashboard |
| `grafana_annotation_write` (write) | `POST /api/annotations` | https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/annotations/#create-annotation |

The alerting routes that are not in the HTTP API docs (`/api/prometheus/grafana/...`,
`/api/alertmanager/grafana/...`, `/api/v1/rules/history`) come from Grafana's own alerting
OpenAPI spec, `pkg/services/ngalert/api/tooling/post.json`, which is what the Grafana UI calls.
The OpenAPI v2 spec for the core routes is `public/api-merged.json` (linked from the HTTP API
index page); it adds parameters the prose docs omit (`/api/search` `sort`/`permission`/`deleted`,
`/api/annotations` `alertUID`/`matchAny`, the `login`/`alertName` annotation fields).

Write tables are never executed inline: the engine stages them and Slack asks a human to approve.

Dashboard and Folder APIs are no longer in the `latest` legacy index (Grafana 13 documents only
`/apis/dashboard.grafana.app` and `/apis/folder.grafana.app` there), so those tables cite the
v12.4 reference, which documents the legacy routes that Grafana 13 still serves.

### Fixture provenance

- Official docs examples (JSON syntax errors fixed, noted per fixture): search (2), dashboard by
  uid, dashboard versions (v12 + v11 shapes), dashboard version, folders (2), library elements,
  data sources (list, by uid, health), `/api/ds/query` (TestData), annotations, annotation tags,
  contact points, health, frontend settings, dashboard save, annotation create, provisioned rules.
- Real backend outputs: Prometheus / Loki data frames from the Grafana plugins' golden files.
- Built strictly from schemas (docs give none): Prometheus-compatible rule status / instances,
  Alertmanager alerts / silences, state history frames (Loki and annotation backends),
  notification policies, mute timings, a timeseries library panel, the panel / variable /
  annotation-query dashboards (`dashboard_checkout_review.json`, `dashboard_request_variables.json`,
  `dashboard_collapsed_row.json`, built from the dashboard JSON model and schema).
- Error fixtures (`expect_error`): a 207/400 `/api/ds/query` body with `results.A.error`
  (backend.DataResponse encoding) and a `status: error` Prometheus-compatible body; both prove
  the 2xx error rules fire.

## Roles bound

| Role | Table(s) |
|---|---|
| `dashboards` | `grafana_search`, `grafana_dashboard_panels` |
| `alert_rules` | `grafana_alert_rules`, `grafana_alert_rule_queries` |
| `active_alerts` | `grafana_active_alerts`, `grafana_alert_rule_status` (state = firing) |
| `alert_history` | `grafana_alert_state_history`, `grafana_annotations` (type = alert) |
| `change_events` | `grafana_annotations` (type = annotation; deploy markers) |

`metrics` is bound by `grafana_prometheus`; `logs` / `log_counts` / `traces` by the sibling
`grafana_loki` / `grafana_tempo` sources (Loki and Tempo through the same Grafana proxy).
`grafana_ds_query` reaches any backend data source ad hoc (Loki, Tempo, SQL, Elasticsearch...).

## Auth

- Service account token: `Authorization: Bearer <token>` (Grafana OSS/Enterprise and Cloud).
  Docs: https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/authentication/
- Basic auth (self-hosted only) when `GRAFANA_TOKEN` is unset and `GRAFANA_USERNAME` is set
  (same variable names as `grafana_loki` / `grafana_tempo`).
- No header when neither is set: works with anonymous access (`GF_AUTH_ANONYMOUS_ENABLED=true`,
  role from `GF_AUTH_ANONYMOUS_ORG_ROLE`).
- `X-Grafana-Org-Id` from `GRAFANA_ORG_ID` selects the organization (optional).
- Permissions that matter (RBAC action names from
  https://grafana.com/docs/grafana/latest/administration/roles-and-permissions/access-control/custom-role-actions-scopes/):
  `alert.provisioning:read` for the provisioning tables (rules, contact points, policies);
  `alert.rules:read` (+ `folders:read`, `datasources:query`) for `grafana_alert_rule_status`;
  `alert.instances:read` for Alertmanager alerts and silences; `datasources:query` for
  `/api/ds/query` and the proxy; `annotations:read`; `dashboards:read`. Viewer-role tokens see
  only what their folder permissions allow: missing rows can be a permission gap.

## Base URLs

| Deployment | `GRAFANA_URL` |
|---|---|
| Local / docker-compose | `http://localhost:3000` |
| Grafana Cloud | `https://<stack>.grafana.net` |
| Self-hosted behind a sub-path | include it, e.g. `https://ops.example.com/grafana` |

No regional hosts: the stack URL is the API host.

## Rate limits and timeouts

- Grafana OSS documents no global HTTP API rate limit. `/api/ds/query` and the data-source proxy
  put load on the underlying data source and are bounded by `[dataproxy] timeout` (default 30 s,
  https://grafana.com/docs/grafana/latest/setup-grafana/configure-grafana/#dataproxy).
- Manifest concurrency: 4.

## Retention

| Data | Default |
|---|---|
| Annotations (dashboard / API / alert) | kept forever: `[annotations.dashboard\|api] max_age = 0`, `[unified_alerting.state_history.annotations] max_age = 0` |
| Dashboard versions | 20 per dashboard (`[dashboards] versions_to_keep`) |
| Alert state history | depends on `[unified_alerting.state_history] backend` (`loki`, `prometheus`, `multiple`; annotations otherwise) |

Source: https://grafana.com/docs/grafana/latest/setup-grafana/configure-grafana/

## Time formats (checked per endpoint)

| Endpoint | Format |
|---|---|
| `/api/ds/query` `from` / `to` | **strings** holding epoch milliseconds (or relative `now-5m`); numbers fail to unmarshal (`MetricRequest.from` is `type: string` in api-merged.json). `intervalMs` in ms. |
| `/api/annotations` `from` / `to`, `time` / `timeEnd` | epoch **milliseconds** |
| `/api/v1/rules/history` `from` / `to` | epoch **seconds** (`time.Unix(from, 0)` in `api_ruler_history.go`) |
| Data frame time fields | epoch milliseconds in `data.values` |
| Alert rule `for`, `keepFiringFor` | duration strings (`5m`); `relativeTimeRange.from/to` are **seconds** |
| Prometheus-compatible rule `duration`, `keepFiringFor`, `evaluationTime` | seconds (float) |
| Alertmanager / provisioning timestamps | RFC 3339 |

## Data frames (`/api/ds/query`, state history)

`/api/ds/query` returns `{"results": {"<refId>": {"status": 200, "frames": [...]}}}`; each frame is
`{"schema": {"name", "refId", "meta", "fields": [{"name", "type", "labels", "config"}]},
"data": {"values": [[column 0], [column 1], ...]}}` (column-oriented). The manifest expands
`results` (refId) -> frames -> columnar rows and picks:

- `ts` = first field of type `time` (Prometheus `Time`, Loki dataplane `timestamp`, older Loki `Time`),
- `value` / `series` = first field of type `number` (Prometheus names it after the metric),
- `labels` = the number field's `labels` (time series) or the per-row `labels` field (Loki streams),
- `message` = first `string` field (Loki `body` / `Line`), `fields` = the whole row.

Fixtures are real backend outputs: `grafana-prometheus-datasource` `range_simple.result.golden.jsonc`
and `grafana-loki-datasource` `streams_simple.golden.jsonc`, wrapped in the documented envelope.

## Pitfalls

1. Legacy `/api` routes are deprecated from Grafana 13 in favour of `/apis/...` but "not being
   disabled" (https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/apis-migration/).
   They are the only routes on Grafana 9-11, so the manifest uses them.
2. `/api/search` `query` matches titles only; it is not full-text. Several `tag`s in one request
   mean AND; the manifest fans out IN lists (OR) - de-duplicate on uid.
3. `GET /api/annotations` returns 100 rows unless `limit` is set (manifest: LIMIT or 1000).
4. Data-source annotation queries (e.g. Loki deploy markers configured on a dashboard) are not
   stored annotations: read them from `grafana_dashboard_annotation_queries` and run the expr.
5. `POST /api/dashboards/db` without `folderUid` moves an existing dashboard to the root folder
   (documented). 412 responses: `version-mismatch`, `name-exists`, `plugin-dashboard`.
6. Panels inside a collapsed row are nested in the row's `panels`; library panels keep their
   targets outside the dashboard. Old dashboards reference data sources by name.
7. Panel `expr` contains variables (`$service`, `${service:regex}`, `$__range`, `$__rate_interval`).
   `/api/ds/query` runs them through the data-source backend, which expands Grafana's built-in
   interval variables (Prometheus: `$__interval`, `$__rate_interval`, `$__range` and their `_ms`/`_s`
   forms, `pkg/promlib/models/query.go`; Loki: `$__interval`, `$__range`, `$__auto`,
   `pkg/tsdb/loki/parse_query.go`) from the request window and `intervalMs`. Dashboard-defined
   variables are expanded only by the dashboard frontend, so ad hoc runs need the matcher the
   variable renders.
8. Grafana-managed rules are not in Prometheus' `/api/v1/rules`; use the Grafana tables.
9. Prometheus-compatible instance `state` strings carry reasons (`Alerting (NoData)`), and the
   `/alerts` list includes Normal instances.
10. State history: the annotation backend requires `ruleUID` (HTTP 400 otherwise) and ignores
    label filters; its `data` field is a JSON string (eval_values is a JSON-encoded string then).
11. `/api/datasources/uid/:uid/health` answers HTTP 400 `{status: ERROR, message}` for a failed
    check (`pkg/api/plugins.go` CheckHealth). The engine drops bodies of `accept_status` codes, so
    the manifest does not accept 400: a failed check fails the query loudly with the message,
    which is the finding. Query one uid per statement.
12. A wrong `GRAFANA_URL` sub-path behind some reverse proxies yields an HTML 200 page: treat an
    empty `grafana_health` / `grafana_datasources` as a configuration error.
13. Duplicate field names inside one wide frame collapse in `fields`; NaN/Inf values arrive as
    null (data-frame `entities` encoding).
14. Library panels keep their queries outside the dashboard: `grafana_dashboard_panels.library_panel_uid`
    -> `grafana_library_panels` (list endpoint; uid filtered in SQL).
15. `GET /api/annotations/tags` counts are all-time and ordered by name; check them before
    filtering `grafana_annotations` by a guessed deploy tag.
16. Notification policies reference mute timings by name (`mute_time_intervals`); an active
    mute timing (`grafana_mute_timings`) silently suppresses notifications.
17. Prometheus-compatible rule `state` accepts normal, inactive, alerting, firing, pending, nodata,
    error, recovering (else HTTP 400); `health` ok, error, nodata. Set `limit_alerts` for rules with
    many instances.

## Dashboard authoring notes

These rules are repeated as `grafana_dashboard_write` / `grafana_dashboard_variables` pitfalls.

- Single-number panels (stat, gauge, bar gauge) that summarise the dashboard time range compute
  the summary in the query and run it as an instant target (`"instant": true, "range": false`):
  `max_over_time(<gauge>[$__range])`, `max_over_time(sum(rate(x[5m]))[$__range:])` for an
  expression, `sum(increase(x[$__range]))` for totals, Loki `count_over_time(...[$__range])` with
  `queryType: instant`. A range target reduced by the panel (`reduceOptions.calcs`) only sees the
  samples at the panel step, so peaks between steps are lost and the value changes with the
  panel width.
- Saved queries keep `$__range` / `$__rate_interval` / `$__interval` and existing dashboard
  variables instead of literal windows or values, so panels follow the time picker and dropdowns.
- Multi-value / include-All variables render `a|b|...` when more than one value (or All) is
  selected: they only work in regex matchers (`label=~"$var"`, `label=~"${var:regex}"`); inside an
  `=` matcher they match nothing.
- `POST /api/dashboards/db` replaces the whole model: start from `grafana_dashboard.dashboard_json`,
  keep `uid`, `version`, panel ids, `gridPos` and untouched targets, and pass `folder_uid` back.
