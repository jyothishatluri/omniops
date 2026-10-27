# New Relic (NerdGraph) source notes

Manifest: `src/omniops/sources/manifests/newrelic.yaml` (source id `newrelic`, 22 tables).
Fixtures: `src/omniops/sources/fixtures/newrelic/`.

Everything goes through one GraphQL endpoint (direct REST, no MCP): `POST https://api.newrelic.com/graphql`.
Each SQL table is one GraphQL document (or two steps when a service name has to be resolved to an entity
GUID first). NRQL tables send the NRQL as the `$nrql: Nrql!` variable with `SINCE <epoch ms> UNTIL <epoch ms>`
appended from the SQL `ts` predicate, and `timeout: 60` (the NerdGraph default is only 5 s).

## Tables -> endpoint -> official reference

All rows are `POST /graphql`; the "endpoint" column is the GraphQL field that is queried.

| SQL table | GraphQL field (POST /graphql) | Official doc |
|---|---|---|
| `newrelic_nrql` | `actor.account(id).nrql(query, timeout) { results metadata { timeWindow ... } }` | https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-nrql-tutorial/ |
| `newrelic_logs` | `actor.account.nrql` with `SELECT * FROM Log[, Log_<partition>] WHERE ...` | https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-nrql-tutorial/ , https://docs.newrelic.com/docs/logs/ui-data/data-partitions/ , https://docs.newrelic.com/docs/logs/logs-context/get-started-logs-context/ |
| `newrelic_log_counts` | `actor.account.nrql` with `SELECT count(*) FROM Log ... FACET <attr> TIMESERIES <n> seconds` | https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/ |
| `newrelic_spans` | `actor.account.nrql` with `SELECT * FROM Span WHERE ...` | https://docs.newrelic.com/attribute-dictionary/?event=Span |
| `newrelic_trace` | `actor.distributedTracing.trace(traceId) { spans {...} }` | https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-distributed-trace-data-tutorial/ |
| `newrelic_errors` | `actor.account.nrql` with `SELECT count(*), latest(error.message), ... FROM TransactionError FACET appName, error.class, transactionName` | https://docs.newrelic.com/attribute-dictionary/?event=TransactionError |
| `newrelic_apm_golden_signals` | `actor.account.nrql` with `SELECT rate(count(*), 1 minute), average(duration), percentile(duration, 95), percentage(...), filter(...) FROM Transaction FACET appName TIMESERIES` | https://docs.newrelic.com/attribute-dictionary/?event=Transaction |
| `newrelic_alert_events` | `actor.account.nrql` with `SELECT * FROM NrAiIncident WHERE ...` | https://docs.newrelic.com/attribute-dictionary/?event=NrAiIncident |
| `newrelic_entities` | `actor.entitySearch(query).results(cursor) { nextCursor entities {...} }` | https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-entities-api-tutorial/ |
| `newrelic_entity_golden_metrics` | `actor.entitySearch` (name -> guid) then `actor.entity(guid) { goldenMetrics goldenTags }` | https://docs.newrelic.com/docs/apis/nerdgraph/examples/golden-metrics-entities-nerdgraph-api-tutorial/ |
| `newrelic_entity_relationships` | `actor.entitySearch` (name -> guid) then `actor.entity(guid).relatedEntities { results { source target type } }` | https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-entities-api-tutorial/#list-relationships |
| `newrelic_issues` | `actor.account.aiIssues.issues(cursor, timeWindow, filter)` | https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-issues-api-via-github/ |
| `newrelic_incidents` | `actor.account.aiIssues.incidents(cursor, timeWindow, filter)` | https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-issues-api-via-github/ (also "List violations" in https://docs.newrelic.com/docs/apis/rest-api-v2/migrate-to-nerdgraph/) |
| `newrelic_issue_events` | `actor.account.aiIssues.issuesEvents(cursor, timeWindow, filter)` | https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-issues-api-via-github/ |
| `newrelic_incident_events` | `actor.account.aiIssues.incidentsEvents(cursor, timeWindow, filter)` | https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-issues-api-via-github/ |
| `newrelic_alert_policies` | `actor.account.alerts.policiesSearch(cursor, searchCriteria)` | https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-api-alerts-policies/ |
| `newrelic_nrql_conditions` | `actor.account.alerts.nrqlCondition(id)` (when filtering by id) then `nrqlConditionsSearch(searchCriteria, cursor)` | https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-api-nrql-condition-alerts/ |
| `newrelic_muting_rules` | `actor.account.alerts.mutingRules { id name enabled condition { operator conditions } schedule {...} }` | https://docs.newrelic.com/docs/alerts/get-notified/muting-rules-suppress-notifications/#manage-with-nerdgraph |
| `newrelic_change_events` | `actor.changeTracking.changeTrackingSearch(filter: { timeWindow, accountIds, query, limit }) { changeEvents }` | https://docs.newrelic.com/docs/change-tracking/query-data/ |
| `newrelic_deployments` | `actor.entitySearch` (name -> guid) then `actor.entity(guid).deploymentSearch(filter: { timeWindow, query, limit })` | https://docs.newrelic.com/docs/change-tracking/query-data/ |
| `newrelic_change_markers` | `actor.account.nrql` with `SELECT * FROM ChangeTrackingEvent, Deployment WHERE ...` (the docs' recommended pattern for both stores) | https://docs.newrelic.com/docs/change-tracking/query-data/#use-nrql , attributes: https://docs.newrelic.com/attribute-dictionary/?dataSource=Change+tracking |
| `newrelic_service_levels` | `actor.entitySearch` (name -> guid) then `actor.entity(guid).serviceLevel.indicators { id events objectives resultQueries }` | https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-slm/ , https://docs.newrelic.com/docs/service-level-management/consume-slm/ |

Field names and GraphQL input type names that the docs only show implicitly were cross-checked against New
Relic's official Go client, which is generated from the NerdGraph schema
(https://github.com/newrelic/newrelic-client-go: `pkg/nrdb` for `Nrql!`/`Seconds`/`metadata.timeWindow`,
`pkg/alerts` for `AlertsPoliciesSearchCriteriaInput`, `AlertsNrqlConditionsSearchCriteriaInput` and the
NRQL condition field list, `pkg/entities` for `EntityOutline`/`apmSummary`/`hostSummary`/`relatedEntities`,
`pkg/changetracking` for `ChangeTrackingDeploymentEvent`/`ChangeTrackingSearchFilter`).
The complete `AiIssuesIssue` field set (incl. `conditionName`, `conditionFamilyId`, `policyIds`, `policyName`,
`mutingState`, `incidentIds`, `deepLinkUrl`, which the issues tutorial does not list) is documented on the
official workflow-automation action page
https://docs.newrelic.com/docs/workflow-automation/setup-and-configure/actions-catalog/newrelic/issue/newrelic-issue-list/.
The documented `Incident` type has no `issueId`/`account` fields, so `newrelic_incidents` does not request them
(an unknown field fails the whole GraphQL document); issues -> incidents go through `Issue.incidentIds`.
The FACET + TIMESERIES result shape (`beginTimeSeconds`, `endTimeSeconds`, `facet`, the faceted attribute and
one key per aggregate) is shown in New Relic's official workshop
https://github.com/newrelic/nr1-workshop/blob/master/lab4/4-QueryWithNRQL.md.

## Roles bound

| Role | Table | Default filters |
|---|---|---|
| metrics | `newrelic_nrql` (query language `nrql`) | - |
| logs | `newrelic_logs` | service, host, `query` = level IN (error...) when error_only |
| log_counts | `newrelic_log_counts` | service |
| traces | `newrelic_spans` | service, is_error when error_only |
| trace_detail | `newrelic_trace` | (needs trace_id) |
| service_map | `newrelic_entity_relationships` | service |
| services | `newrelic_entities` | domain IN (APM, EXT), name = service |
| hosts | `newrelic_entities` | domain INFRA, type HOST, name = host |
| change_events | `newrelic_change_markers` (account-wide, both stores), `newrelic_change_events`, `newrelic_deployments` | service |
| active_alerts | `newrelic_issues` | state = ACTIVATED |
| alert_history | `newrelic_alert_events` | service |
| alert_rules | `newrelic_nrql_conditions` | id = alert_id |
| incidents | `newrelic_issues` | - |
| errors | `newrelic_errors` | service |
| slo | `newrelic_service_levels` | service |

## Auth

- Header `API-Key: <user key>` (`NEW_RELIC_API_KEY`). It must be a **User key** (`NRAK-...`); license/ingest
  keys and browser keys do not work with NerdGraph.
  (https://docs.newrelic.com/docs/apis/intro-apis/new-relic-api-keys/)
- `NEW_RELIC_ACCOUNT_ID` (numeric) scopes NRQL, aiIssues, alerts and change tracking. The key's user must have
  access to that account; otherwise NerdGraph returns `ACCESS_DENIED` inside a 200 response.
- The aiIssues tutorial says those fields require the opt-in header
  `nerd-graph-unsafe-experimental-opt-in: AiIssues`; without it NerdGraph answers HTTP 200 with
  `errors[0].extensions.errorClass = ACCESS_DENIED` (verbatim body in `ai_issues_optin_error.json`, used as an
  `expect_error` fixture). The issues/incidents/events tables send it.

## Base URLs / regions

`NEW_RELIC_REGION` picks the host (https://docs.newrelic.com/docs/apis/nerdgraph/get-started/introduction-new-relic-nerdgraph/#endpoints):

| Region | API | UI |
|---|---|---|
| us (default) | https://api.newrelic.com/graphql | https://one.newrelic.com |
| eu | https://api.eu.newrelic.com/graphql | https://one.eu.newrelic.com |
| jp | https://api.jp.newrelic.com/graphql | https://one.newrelic.com |

## Rate limits

- NerdGraph: **25 concurrent requests per user** (not per key); excess requests get HTTP 429. The request rate
  itself is not limited. (https://docs.newrelic.com/docs/apis/nerdgraph/nerdgraph-usage-limits/)
- NRQL via API: **3,000 queries per account per minute**; the **same NRQL from the same user is limited to
  120 per minute** (HTTP 429); New Relic recommends no more than 5 concurrent complex (FACET/TIMESERIES or
  >1M-event) queries. Up to 30 accounts per cross-account query.
  (https://docs.newrelic.com/docs/nrql/using-nrql/rate-limits-nrql-queries/)
- NRQL duration limit: 1 minute (Original Data option) / 10 minutes (Data Plus). The **default NerdGraph NRQL
  timeout is 5 seconds**, so every NRQL table passes `timeout: 60`. Longer queries need the async API
  (`async: true` + `nrqlQueryProgress`), which this manifest does not use
  (https://docs.newrelic.com/docs/apis/nerdgraph/examples/async-queries-nrql-tutorial/).
- Manifest setting: `concurrency: 5`, `requests_per_minute: 1000` (enforced by the engine across all tables,
  well under the 3,000 NRQL/min account limit so other users of the account keep headroom).
- Offline positive controls are bounded (`control_lookback: 6h`) on the raw Log/Span tables and the generic NRQL
  table so an empty result does not trigger a multi-day raw scan.

## Retention (defaults; Data Plus accounts keep more)

From https://docs.newrelic.com/docs/data-apis/manage-data/manage-data-retention/ (Original Data / Data Plus):

| Data | Retention |
|---|---|
| APM events (Transaction, TransactionError) | 8 / 98 days |
| Distributed traces (Span) | 8 / 98 days |
| Logs | 30 / 120 days |
| Custom events | 30 / 120 days |
| Dimensional metrics (Metric) | raw 30 days, aggregates 13 months |
| Change tracking markers | 13 / 16 months |
| Infrastructure integrations | 13 / 16 months |

Table `retention` settings: logs 30d, spans 8d, errors / golden signals 8d (Original Data defaults, so the
engine reports "beyond retention" instead of a false "absent").

## Time formats

- NRQL `SINCE`/`UNTIL` accept Unix epoch **milliseconds** (`SINCE 1702899296789 UNTIL 1702902896789`), default
  timezone UTC (https://docs.newrelic.com/docs/nrql/using-nrql/query-time-range/). The engine always sends
  epoch ms.
- NRQL result timestamps: raw events have `timestamp` in epoch **ms**; TIMESERIES buckets have
  `beginTimeSeconds` / `endTimeSeconds` in epoch **seconds**; `metadata.timeWindow.begin/end` are epoch ms.
- aiIssues `timeWindow { startTime endTime }` and all `*At` fields are `EpochMilliseconds`; default window when
  omitted is the last 24 h.
- Change tracking `timeWindow { startTime endTime }` is epoch ms (default: the last hour); `timestamp` fields
  are epoch ms.
- Muting rule `schedule.startTime` / `endTime` / `endRepeat` are **local wall-clock ISO times without offset**
  in `schedule.timeZone` (e.g. `2020-07-08T14:30:00` + `America/Los_Angeles`); `createdAt`/`updatedAt` are
  DateTime strings.
- Service levels: `createdAt`/`updatedAt` epoch ms; SLO `target` is a percentage (up to 100.00);
  `timeWindow.rolling.count` in `DAY`s (1, 7, 14, 28, 30).
- `apmSummary.responseTimeAverage` is in **seconds** (converted to `apm_response_time_ms`); Span `duration`
  is seconds, `duration.ms` milliseconds; Transaction `duration` is seconds.

## Pitfalls (what silently returns empty or wrong data)

1. **HTTP 200 with `errors[]`**: NerdGraph never uses HTTP status for query errors. A source-level error rule
   turns any non-empty `errors` array into a loud failure (NRQL syntax, unknown field, ACCESS_DENIED, TIMEOUT).
2. **NRQL default LIMITs**: FACET returns 10 groups and `SELECT *` 100 rows unless LIMIT is given (max 5,000).
   The generic NRQL table appends `LIMIT MAX` to non-TIMESERIES queries without LIMIT; FACET+TIMESERIES keeps
   10 facets unless the query sets LIMIT. https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/#sel-limit
3. **TIMESERIES max 366 buckets**, and buckets snap to the end of the window (the first bucket may start
   after the requested start). Metric queries snap to roll-up boundaries (see `nrdb_messages`).
4. **Default 5-second NerdGraph NRQL timeout**: without `timeout` a heavy query fails fast; we send 60 s.
5. **Log partitions**: `FROM Log` reads only the default partition; data routed to `Log_<name>` is invisible
   unless the partition is named.
6. **Case**: NRQL `=`/`IN` are case-sensitive, `LIKE` is case-insensitive. SQL LIKE in DuckDB is
   case-sensitive, so use `ILIKE` for pushed-down text matches. Level matching in `newrelic_logs` is
   case-insensitive on `level`, `log.level` and `severity.text`.
7. **APM vs OpenTelemetry naming**: APM uses `appName`/`entity.name` and emits Transaction events; OTel uses
   `service.name` and has no Transaction events. Tables match both where possible.
8. **Booleans**: NRQL needs `IS TRUE` / `IS FALSE`, not `= true`.
9. **Wrong account**: everything is scoped to `NEW_RELIC_ACCOUNT_ID`; sub-account data is invisible.
10. **aiIssues windows** select issues by time; an issue opened before the window may be missing. Widen `ts`
    to find long-running open issues. Pagination is capped by NRQL `LIMIT MAX` (5,000).
11. **Two deployment stores**: `changeTrackingSearch` returns events created with `changeTrackingCreateEvent`;
    legacy REST v2 / `changeTrackingCreateDeployment` deployments are `Deployment` events, reachable through
    `deploymentSearch` (per entity) or NRQL `FROM Deployment`.
12. **Entity search** pages are 200 entities; names are not unique across accounts; summary metrics are a
    current snapshot, not values for the SQL window.
13. **Repeated identical NRQL** (>120/min per user) returns 429; do not poll.
14. **Single aggregates**: NRQL aggregates without FACET/TIMESERIES have no timestamp; `newrelic_nrql` gives
    them `ts` = midpoint of the query window. The aggregate NRQL tables (`newrelic_nrql`, `newrelic_log_counts`,
    `newrelic_apm_golden_signals`, `newrelic_errors`) use `time.reapply: false`: NRDB's SINCE/UNTIL window is
    authoritative, so roll-up-snapped Metric buckets are not dropped by a re-applied `ts` predicate.
15. **Incident titles** come back as a JSON-array string (`"[\"...\"]"`); `newrelic_incidents.title` unwraps it.
16. **Errors Inbox**: no public, documented NerdGraph reference for Errors Inbox error groups exists (checked
    the docs-website sources under errors-inbox/ and triage-inbox/, and newrelic-client-go has no errors-inbox
    package); error grouping is done in NRQL over `TransactionError` instead (`newrelic_errors`).
17. **Muted incidents** still open (NrAiIncident `muted = true`, `mutingRuleId`; issue `mutingState`), they just
    notify nobody. `newrelic_muting_rules` shows the rule; its schedule is in local time of `timeZone`.
18. **Two change stores, three tables**: `newrelic_change_markers` (NRQL, account-wide, both stores) is the
    default `change_events` binding; `newrelic_change_events` (changeTrackingSearch) misses legacy deployments and
    `newrelic_deployments` (deploymentSearch) needs one entity.

## Fixture provenance

| Fixture | Source |
|---|---|
| `nrql_facet_timeseries.json` | Verbatim (first 3 buckets) from github.com/newrelic/nr1-workshop lab4 |
| `nrql_count.json` | Verbatim from the NerdGraph NRQL tutorial |
| `nrql_facet_timeseries_metadata.json`, `nrql_aggregate_metadata.json` | Verbatim NerdGraph responses from newrelic-client-go `pkg/nrdb/nrdb_customized_unit_test.go` |
| `ai_issues.json`, `ai_issues_cursor.json`, `ai_incidents.json`, `ai_issues_events.json`, `ai_incidents_events.json` | Verbatim from the NerdGraph issues tutorial (truncated closing braces completed) |
| `alerts_policies_search.json` | Verbatim from the alert policies tutorial (comment line removed) |
| `nrql_conditions_search.json`, `nrql_conditions_cursor.json` | Verbatim from the NRQL conditions tutorial |
| `nrql_conditions_full.json` | Doc's static-condition example values in the response shape of the Go client's field list |
| `entities_search.json`, `entity_golden_metrics.json`, `entity_related.json`, `distributed_trace.json`, `change_tracking_search.json`, `deployment_search.json` | Built strictly from the documented GraphQL selection sets (a GraphQL response mirrors its selection set); the docs publish no response bodies |
| `nrql_logs.json`, `nrql_spans.json`, `nrql_errors_facet.json`, `nrql_golden_signals.json`, `nrql_log_counts.json`, `nrql_alert_events.json`, `nrql_change_markers.json` | Built from the documented NRQL result shapes (raw-event / FACET / TIMESERIES) and attribute names from the New Relic data dictionary |
| `ai_issues_optin_error.json` | Verbatim HTTP-200 `errors[]` body from the issues tutorial (missing opt-in header); `expect_error` fixture |
| `muting_rules.json` | Built strictly from the documented muting-rule field table and the doc's example rules/schedule values |
| `service_levels.json` | Built strictly from the documented SLI selection set and the doc's example SLI (Latency below 0.25 seconds, 99.5 % over 7 days) and attainment NRQL |

## Open points to verify against a live account

- NRQL string escaping of a single quote inside a literal (the templates use `\'`).
- That `AS 'alias'` aliases appear as result keys exactly as written, and whether
  `percentile(x, 95) AS 'p95'` returns a scalar or `{"95": x}` (both handled).
- Whether `changeTrackingSearch` accepts `entity.name` in its `query` (the docs show `entity.guid`).
- Whether the aiIssues opt-in header is still needed (it is harmless when not).
- Whether `AiIssuesIssue.policyName` / `conditionName` are scalars or lists (both are joined safely).
- Whether `SELECT * FROM ChangeTrackingEvent, Deployment` returns custom ChangeTrackingEvent attributes as top-level
  keys (they land in `attributes` either way).
- Whether an Errors Inbox NerdGraph field (undocumented) is available to the key; if New Relic documents one, add a
  `newrelic_error_groups` table.
