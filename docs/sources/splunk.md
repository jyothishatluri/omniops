# Splunk Enterprise / Splunk Cloud Platform (`splunk`)

Manifest: `src/omniops/sources/manifests/splunk.yaml` · fixtures: `src/omniops/sources/fixtures/splunk/`

Direct REST against the management port (8089). No MCP, no SDK. Researched against the
REST API Reference for Splunk Enterprise 10.6 (Splunk Cloud Platform uses the same endpoints for
the search tier). Re-verified 2026-10-26 against the 10.6 "Search endpoint descriptions" page
(full text): POST `search/jobs` parameters, `search/v2/jobs/export` (POST only), v2 results
parameters (`count` default 100, `offset`, `f`, `output_mode`), job status keys, fired alert and
saved search history fields, and the metrics catalog parameters (`earliest` default `-1d`,
`latest`, `filter`, `metric_name`).

## Tables

| SQL table | HTTP | Endpoint | Official reference |
|---|---|---|---|
| `splunk_search` | POST, GET, GET | `{ns}/search/jobs` (create, `exec_mode=normal`) -> `{ns}/search/jobs/{sid}` (poll `isDone`) -> `{ns}/search/v2/jobs/{sid}/results` (offset/count pages) | [Search endpoint descriptions](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-reference/10.6/search-endpoints/search-endpoint-descriptions) (search/jobs, search/jobs/{search_id}, search/v2/jobs/{search_id}/results) |
| `splunk_job_results` | GET | `{ns}/search/v2/jobs/{sid}/results` | same page, search/v2/jobs/{search_id}/results |
| `splunk_saved_searches` | GET | `/servicesNS/-/-/saved/searches[/{name}]` | same page, saved/searches |
| `splunk_saved_search_history` | GET | `/servicesNS/-/-/saved/searches/{name}/history` | same page, saved/searches/{name}/history |
| `splunk_fired_alerts` | GET | `/servicesNS/-/-/alerts/fired_alerts/{name or -}` | same page, alerts/fired_alerts/{name} |
| `splunk_fired_alert_summary` | GET | `/servicesNS/-/-/alerts/fired_alerts` | same page, alerts/fired_alerts |
| `splunk_indexes` | GET | `/services/data/indexes?datatype=all` | [Introspection endpoint descriptions](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-reference/10.6/introspection-endpoints) (data/indexes) |
| `splunk_metric_names` | GET | `/services/catalog/metricstore/metrics` | [Metrics Catalog endpoint descriptions](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-reference/10.6/metrics-catalog-endpoints) |
| `splunk_metric_dimensions` | GET | `/services/catalog/metricstore/dimensions?metric_name=` | same page |
| `splunk_metric_dimension_values` | GET | `/services/catalog/metricstore/dimensions/{dimension}/values?metric_name=` | same page |
| `splunk_messages` | GET | `/services/messages` | [System endpoint descriptions](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-reference/10.6/system-endpoints) (messages) |
| `splunk_health` | GET | `/services/server/health/splunkd/details` | [Introspection endpoint descriptions](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-reference/10.6/introspection-endpoints) (server/health/splunkd/details) |

`{ns}` = `SPLUNK_SEARCH_NAMESPACE` (default `services`; use `servicesNS/<user>/<app>` when searches
need an app's macros, lookups or eventtypes).

Supporting pages:
- Pagination/filtering parameters (`count` default 30, `offset`, `search`, `f`, `sort_*`):
  [REST API Reference introduction](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-reference/10.6/introduction)
- Atom feed, namespaces (`/servicesNS/-/-/`), output modes, token auth, status codes:
  [Basic concepts about the Splunk platform REST API](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-user-manual/10.6/rest-api-user-manual/basic-concepts-about-the-splunk-platform-rest-api)
- Search tips (`max_count`, `status_buckets`, all-time default range, prepending `search`):
  [Creating searches using the REST API](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-tutorials/10.6/rest-api-tutorials/creating-searches-using-the-rest-api)
- Splunk Cloud access: [Access requirements and limitations for the Splunk Cloud Platform REST API](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-tutorials/10.6/rest-api-tutorials/access-requirements-and-limitations-for-the-splunk-cloud-platform-rest-api)

## There is no `search/v2/jobs` create endpoint

The semantic-versioning section of the reference lists exactly five v2 endpoints:
`search/v2/jobs/export`, `search/v2/jobs/{search_id}/events`, `search/v2/jobs/{search_id}/results`,
`search/v2/jobs/{search_id}/results_preview` and `search/v2/parser`. Job creation
(`POST search/jobs`), status (`GET search/jobs/{sid}`) and control stay unversioned and are not
deprecated. The manifest therefore creates with `search/jobs` and reads with
`search/v2/jobs/{sid}/results`.

## Why the async job flow (and not oneshot / export)

- `POST search/jobs` with `exec_mode=oneshot` returns results in the same call but has no sid,
  no status (`isFailed`, `dispatchState`) and is limited by default result counts.
- `search/v2/jobs/export` is POST-only in v2 and streams previews for non-streaming searches;
  the reference recommends `search/jobs` + paged results for large or transforming searches.
- The reference itself recommends `search/jobs` + paging the results "for extremely large result
  sets that need to be chunked" instead of export.
- `create -> poll -> v2 results` gives a loud failure (`isFailed`, `dispatchState=FAILED`), offset
  paging (`offset`/`count`, 1000 per page) and `max_count=50000` stored results.
- Since Splunk Enterprise 9.0.1 / Cloud 9.0.2208 the v1 GET results/events/results_preview/export
  and parser endpoints are deprecated **and disabled by default**; only `search/v2/...` works for
  them. `search/jobs` (create) and `search/jobs/{sid}` (status) have no v2 and stay unversioned.

Request details used by `splunk_search` (form-encoded body): `search` (prefixed with `search `
unless it starts with `search ` or `|`), `earliest_time`/`latest_time` = epoch seconds,
`exec_mode=normal`, `adhoc_search_level=smart`, `max_count=50000`, `auto_cancel=300`,
`timeout=600`, `output_mode=json`. Poll every 1 s up to 150 s on `entry[0].content.isDone`;
fail on `isFailed` or `dispatchState` in FAILED / *_CANCEL. Documented defaults the manifest
overrides: `adhoc_search_level` defaults to `fast` (manifest: `smart`), `max_count` 10000
(manifest: 50000), `timeout` 86400 s (manifest: 600 s), `auto_cancel` 0 (manifest: 300 s).
The positive-control re-query of an empty `splunk_search` result is bounded to 6 h
(`time.control_lookback`) because every query is a search job.

## Auth

- `Authorization: Bearer <token>` with a Splunk authentication token (Settings > Tokens, 7.3+;
  token auth must be enabled). Basic auth and `Authorization: Splunk <sessionKey>` also exist but
  are not used. Env: `SPLUNK_URL`, `SPLUNK_TOKEN` (required), `SPLUNK_WEB_URL`, `SPLUNK_SEARCH_NAMESPACE`.
- The token user's roles decide which indexes are searchable and which knowledge objects are
  visible. Capabilities: `list_metrics_catalog` (metrics catalog), admin or `list_health`
  (health), `indexes_list_all` may restrict `data/indexes`.

## Base URLs

- Splunk Enterprise: `https://<host>:8089`
- Splunk Cloud Platform: `https://<deployment-name>.splunkcloud.com:8089`, reachable only after
  adding egress IPs to the `search-api` allow list (Admin Config Service) or a support request.
  Free trial stacks have no REST access. Splunk Cloud only exposes the search tier and the
  Access control, Federated Search, Knowledge, KV store, Metrics and Search resource groups, so
  `splunk_indexes`, `splunk_messages` and `splunk_health` may be refused there (use
  `| rest ...` through `splunk_search`).
- Management ports often use self-signed certificates (`OMNIOPS_TLS_VERIFY=false` for labs only).

## Rate limits and quotas

No HTTP rate limit is documented; the limiting factor is search concurrency: per-role
`srchJobsQuota` / `cumulativeSrchJobsQuota` and per-search-head limits (limits.conf). A job over
quota waits in `QUEUED` and the poll times out (reported as a rate-limit style error).
Manifest concurrency is 3.

## Retention

- Events: per index `frozenTimePeriodInSecs` (default 188697600 s = 6 years); see `splunk_indexes`.
- Search job artifacts: ad hoc jobs `timeout` (this manifest: 600 s after completion), scheduled
  jobs `dispatch.ttl` (default `2p` = two scheduled periods), alert-triggered jobs the alert
  action ttl (default 86400 s). Expired sid -> HTTP 404 in `splunk_job_results`.
- Fired alerts: `alert.expires` per saved search (default 24h); only when `alert.track=1`.

## Time formats

- Request: `earliest_time`/`latest_time` accept epoch seconds with fractional part, relative
  modifiers (`-15m`) or a formatted time; the manifest sends integer epoch seconds. Earliest is
  inclusive, latest exclusive. Without them REST searches run over **all time**.
- Response `_time` uses `time_format` default `%FT%T.%Q%:z` (ISO 8601 with the user's UTC
  offset, e.g. `2012-04-06T12:13:42.943-07:00`). The engine keeps the offset.
- Fired alerts `trigger_time` and messages `timeCreated_epochSecs`: epoch seconds.
- Indexes `minTime`/`maxTime`: `2011-07-10T22:20:53-0700` (offset without colon).

## Response shapes

- Search results (`output_mode=json`): `{"init_offset", "messages": [{type,text}], "preview",
  "results": [ {field: value or [values]} ]}` (documented example in the results reference and
  in Basic concepts "Example B").
- Every other endpoint is an Atom feed; with `output_mode=json`: `{"links", "origin", "updated",
  "generator", "entry": [{"name", "id", "updated", "links", "author", "acl", "content"}],
  "paging": {"total", "perPage", "offset"}, "messages"}` (documented by the server/info JSON
  example). Most reference examples are XML only; JSON fixtures were built strictly from the
  documented XML fields rendered in this envelope (each fixture note says so).

## Pitfalls (also encoded in the manifest)

1. Results limited by role: an unsearchable index returns 0 rows, not an error.
2. `earliest=`/`latest=` inside the SPL override the window sent by the engine.
3. `timechart` zero-fills, `stats ... by _time` does not; alias numbers `AS value`.
4. `adhoc_search_level=smart`: transforming searches only keep referenced fields.
5. Fired alerts only exist for tracked alerts and expire after 24h by default; empty != nothing fired.
6. `/services/saved/searches` only shows the token user's app context; the manifest uses
   `/servicesNS/-/-/` for all apps and owners.
7. `data/indexes` on a search head describes the search head (counts often 0 in distributed /
   Cloud deployments): use `tstats` for cluster-wide freshness.
8. Error bodies: results carry `messages[{type,text}]` with HTTP 200. Two source-level rules
   with wildcard paths (`$.messages[*].type` equals FATAL / ERROR) fail loudly when ANY message
   has that level; DEBUG / INFO / WARN are ignored. The documented examples only show DEBUG,
   INFO and WARN; the error fixture (`search_results_error.json`) is built from the documented
   envelope and says so. The INFO message "Your timerange was substituted based on your search
   string" means `earliest=`/`latest=` inside the SPL replaced the requested window.
9. Field names are deployment-specific (`log_level` vs `level`, `service` vs `app`); inspect
   `fields` JSON before filtering.
10. `alert.severity` is numeric: 1 DEBUG/Info, 2 INFO/Low, 3 WARN/Medium (default), 4 ERROR/High,
    5 SEVERE/Critical, 6 FATAL; `severity_name` gives the UI word.
11. Saved search history resolves the name across all apps (`/servicesNS/-/-/`); duplicate names
    in different apps are ambiguous (the reference offers a `savedsearch=user:app:name` triplet).
