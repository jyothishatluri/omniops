# AppDynamics source (`appdynamics`): Controller REST API

Manifest: `src/omniops/sources/manifests/appdynamics.yaml` (14 tables)
Fixtures: `src/omniops/sources/fixtures/appdynamics/`
API reference root (Splunk AppDynamics SaaS 26.10.0; the docs moved from docs.appdynamics.com to
help.splunk.com):
https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis

Checked against the official reference on 2026-10-27 (OAuth token request, metric-data /
metric-data-v2 parameters and granularity table, request-snapshot parameter spellings re-verified
in this pass). Every `/controller/rest/...` endpoint answers XML unless `output=JSON` is sent; the
JSON form is a top-level array whose keys are the XML element names. Most docs examples are XML,
so most fixtures are JSON renderings of the documented XML examples (each fixture `note` says so);
the alerting (health rules), anomaly and audit-history APIs document JSON and their fixtures are
verbatim.

## Tables -> endpoints -> reference pages

| SQL table | HTTP | Official reference |
|---|---|---|
| `appdynamics_applications` | GET `/controller/rest/applications?output=JSON` | https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis/application-model-api |
| `appdynamics_business_transactions` | GET `/controller/rest/applications/{app}/business-transactions` | same (Application Model API) |
| `appdynamics_tiers` | GET `/controller/rest/applications/{app}/tiers` | same |
| `appdynamics_nodes` | GET `/controller/rest/applications/{app}/nodes`, `.../tiers/{tier}/nodes`, `.../nodes/{node}` | same |
| `appdynamics_backends` | GET `/controller/rest/applications/{app}/backends` | same |
| `appdynamics_metric_tree` | GET `/controller/rest/applications/{app}/metrics?metric-path=` | https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis/metric-and-snapshot-api |
| `appdynamics_metric_data` | GET `/controller/rest/applications/{app}/metric-data` (or `metric-data-v2` via `APPD_METRIC_DATA_API`) | same (Metric and Snapshot API) |
| `appdynamics_snapshots` | GET `/controller/rest/applications/{app}/request-snapshots` | same |
| `appdynamics_health_rule_violations` | GET `/controller/rest/applications/{app}/problems/healthrule-violations` | https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis/alert-and-respond-api/events-api |
| `appdynamics_events` | GET `/controller/rest/applications/{app}/events` | same (Events API) |
| `appdynamics_health_rules` | GET `/controller/alerting/rest/v1/applications/{application_id}/health-rules[/{id}]` | https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis/alert-and-respond-api/health-rule-api |
| `appdynamics_anomalies` | GET `/controller/anomaly/rest/api/v1/applications/{application_id}/anomalies?fetchSuspectedCause=false` | https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis/anomaly-violation-api |
| `appdynamics_anomaly_causes` | same endpoint with `fetchSuspectedCause=true` (events x suspected causes) | same |
| `appdynamics_audit_history` | GET `/controller/ControllerAuditHistory` | https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis/controller-audit-history-api |

OAuth / API clients: https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis/api-clients

## Roles bound

| Role | Table | Default filters / notes |
|---|---|---|
| `metrics` | `appdynamics_metric_data` | query language `appdynamics_metric_path`; needs an application (filter or `APPD_APPLICATION`) |
| `services` | `appdynamics_tiers` | tier = service |
| `hosts` | `appdynamics_nodes` | machine name = host |
| `active_alerts` | `appdynamics_health_rule_violations` | `state = OPEN` |
| `alert_history`, `incidents` | `appdynamics_health_rule_violations` | none |
| `alert_rules` | `appdynamics_health_rules` | `health_rule_id` from the alert; needs the numeric application id |
| `change_events` | `appdynamics_events` (APPLICATION_DEPLOYMENT, APPLICATION_CONFIG_CHANGE, APP_SERVER_RESTART), `appdynamics_audit_history` (logins excluded) | |
| `traces`, `trace_detail` | `appdynamics_snapshots` | `error_only` when asked; `trace_id` = request GUID |
| `errors` | `appdynamics_events` | APPLICATION_ERROR, APPLICATION_CRASH, CLR_CRASH, DEADLOCK, STALL |

## Auth

- OAuth 2.0 client credentials with an API Client (Administration > API Clients):
  `POST {controller}/controller/api/oauth/access_token`, `Content-Type:
  application/x-www-form-urlencoded`, body `grant_type=client_credentials&client_id=<apiClientName>@<accountName>&client_secret=<secret>`.
  Response `{"access_token": "...", "expires_in": 300}`; the token goes in `Authorization: Bearer`.
  Default expiry is 5 minutes (configurable per client); the engine caches and refreshes it.
- Give the API client a role with read access to the applications to investigate; objects the
  role cannot see are simply absent from responses.
- Basic auth (`user@account:password`) also exists on the Controller but is not supported by this
  manifest (an engine `auth` block is one type per source; SAML/Cisco-identity users cannot use
  basic auth anyway).

Env: `APPD_CONTROLLER_URL`, `APPD_ACCOUNT_NAME`, `APPD_API_CLIENT_NAME`, `APPD_API_CLIENT_SECRET`
(required); `APPD_APPLICATION`, `APPD_APPLICATION_ID` (defaults), `APPD_METRIC_DATA_API`.

## Base URLs

- SaaS: `https://<account>.saas.appdynamics.com`; on-premises: the Controller UI host and port
  (e.g. `https://appd.example.com:8181`). `APPD_CONTROLLER_URL` must NOT include `/controller`
  (paths add it). An HTML response means a wrong base URL.

## Time formats

- `/controller/rest/...` (metric-data, healthrule-violations, events, request-snapshots):
  `time-range-type=BETWEEN_TIMES` with `start-time` / `end-time` in epoch MILLISECONDS
  (`BEFORE_NOW` + `duration-in-mins` also exists; not used to avoid partial windows).
- Anomaly API: `startTime` / `endTime` epoch ms.
- Controller audit history: `startTime` / `endTime` as `yyyy-MM-dd'T'HH:mm:ss.SSS+0000`, at most
  24 h per call.
- Response timestamps: `startTimeInMillis`, `eventTime`, `serverStartTime`, `timeStamp` are epoch ms.

## Metric granularity

Classic `metric-data`: 1-minute points for windows up to ~24.2 h (1,459 min), 10-minute points
up to 49 h, 60-minute beyond. `metric-data-v2`: 1-minute data for any 24 h window within the last
8 days, 60-minute otherwise. `rollup=false` returns every point (default `true` = one aggregated
point).

## Pagination and limits

- No cursor pagination on the classic REST endpoints: events return at most 600 per call;
  request-snapshots return at most 600 unless `maximum-results` is sent (the table sends the SQL
  LIMIT, capped at 5,000).
- Anomaly API: `pageNumber` (0-based) / `pageSize` paging (engine `page` pagination, 100 per page).
- No published per-minute REST quota; the Controller shares capacity with the UI: keep calls to one
  application and short windows (engine concurrency 4).

## Retention

- Metrics: 1-minute data ~8 days, hourly data much longer (SaaS defaults).
- Events, health rule violations, snapshots: about 2 weeks by default (Controller settings
  `events.retention.period`, `snapshots.retention.period`).

## Pitfalls (also in the manifest)

- `output=JSON` is mandatory; without it you get XML.
- `/controller/rest/...` accepts the application NAME or numeric id; the alerting and anomaly APIs
  accept only the numeric id (`appdynamics_applications.id`).
- Metric paths are case-sensitive and must match the metric browser; an unknown path returns no
  values (or a `METRIC DATA NOT FOUND` series), not an error: browse `appdynamics_metric_tree`.
  `*` matches exactly one segment.
- Count metrics (Calls/Errors per Minute) are per-minute values; min/max carry sentinel values
  (2147483647 / -2147483648) for sum/count metrics.
- Snapshots are samples (periodic, slow/error triggered), not every request; BT/tier/node filters
  take numeric ids.
- Deployment markers exist only if a pipeline posts APPLICATION_DEPLOYMENT/CUSTOM events.
- Event types must be exact Events Reference names (upper case); an unknown type is an HTTP 400.

## Fixtures provenance

| Fixture | Source |
|---|---|
| `applications.json` | verbatim JSON example of "Using the Controller APIs" |
| `business_transactions.json`, `tiers.json`, `nodes.json`, `nodes_tier.json`, `backends.json`, `metric_tree.json` | JSON renderings of the Application Model / Metric API XML examples |
| `metric_data_rollup_false.json`, `metric_data_wildcard.json` | JSON renderings of the Metric and Snapshot API XML examples |
| `snapshots.json`, `snapshots_error_props.json` | JSON renderings of the request-snapshots XML examples |
| `health_rule_violations.json`, `events.json` | JSON renderings of the Events API XML examples |
| `health_rules.json`, `health_rule_detail.json` | verbatim Health Rule API JSON examples |
| `anomalies.json`, `anomalies_suspected_causes.json` | verbatim Anomaly Violation API JSON examples 1 and 2 |
| `audit_history.json`, `audit_history_changes.json` | verbatim Controller Audit History API JSON examples |
