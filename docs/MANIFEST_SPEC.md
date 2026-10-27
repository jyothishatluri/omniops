# OmniOps source manifest specification (v1)

A **source manifest** turns one observability / engineering platform's REST API into SQL tables.
The model writes SQL; the engine parses it, pushes WHERE predicates down into HTTP parameters,
fetches every source in parallel over direct REST (no MCP), loads the rows into an in-memory
DuckDB, runs the SQL, and returns a compact table with digests, links and truncation markers.

Schema: `src/omniops/engine/manifest.py` (pydantic, `extra=forbid`).
Reference implementation: `src/omniops/sources/templates/prometheus.yaml` + fixtures in
`src/omniops/sources/fixtures/prometheus/`. **Copy its style.**

Check your work offline (no credentials needed):

```bash
uv run --no-sync omniops manifest validate src/omniops/sources/manifests/<source>.yaml
uv run --no-sync omniops manifest test <source> -v --show-rows
uv run --no-sync omniops manifest render <table> -f query='...'
```

---

## 1. File layout

| Path | What |
|---|---|
| `src/omniops/sources/manifests/<source>.yaml` | One manifest per platform (or per distinct API host, e.g. `kibana` vs `elasticsearch`). |
| `src/omniops/sources/templates/<id>.yaml` | Reusable API shapes (`extends:`). The template's `source` must equal its file name. |
| `src/omniops/sources/fixtures/<source>/<file>.json` | Offline fixtures: documented / recorded response bodies (`.json`, `.ndjson`, `.txt`). |
| `docs/sources/<source>.md` | Research notes: table → endpoint → doc URL, auth, limits, retention, pitfalls. |

## 2. Source-level keys

```yaml
source: datadog                 # id; [a-z][a-z0-9_]*; every table name must start with "<source>_"
display_name: Datadog
vendor: Datadog, Inc.
description: one line
docs_url: https://docs.datadoghq.com/api/latest/
categories: [metrics, logs, traces, rum, alerts, events, incidents]
base_url: "https://api.{{ env.DD_SITE }}"      # Jinja template
env:                                            # every variable the manifest reads
  - {name: DD_API_KEY, description: "API key", secret: true}
  - {name: DD_SITE, description: "datadoghq.com, us3.datadoghq.com, datadoghq.eu ...", default: datadoghq.com}
required_env: [DD_API_KEY, DD_APP_KEY]          # source is enabled only when all are set
auth:
  type: headers                                 # none|bearer|headers|basic|query|aws_sigv4|
                                                # oauth2_client_credentials|gcp_service_account|azure_ad|kubeconfig
  headers: {DD-API-KEY: "{{ env.DD_API_KEY }}", DD-APPLICATION-KEY: "{{ env.DD_APP_KEY }}"}
headers: {}                                     # common extra headers for every request
rate_limit: {concurrency: 4, requests_per_minute: 300, notes: "logs search ~300/min/org"}
retention: {logs: 15d, spans: 15d, metrics: 15mo}
errors:                                         # 2xx-with-error-body rules applied to every table
  - {path: "$.errors", non_empty: true, message: "$.errors", hint: "..."}
ui_base_url: "https://app.{{ env.DD_SITE }}"
pitfalls: ["source-wide gotchas the model must know"]
query_languages: {datadog_metrics: "how to write the native query string, concisely"}
tables: [...]
roles: {...}
```

Auth details:
- `bearer`: `token: "{{ env.X }}"`. `headers`: any headers (a header that renders to `None`/empty is skipped).
- `basic`: `username`, `password`. `query`: `query: {api_key: "{{ env.KEY }}"}`.
- `oauth2_client_credentials` / `azure_ad`: `token_url`, `client_id`, `client_secret`, `scope`/`audience`; the token is cached.
- `aws_sigv4`: `service`, `region` (credentials from the standard AWS chain; needs `omniops[aws]`).
- `gcp_service_account`: `scope`, optional `credentials_file` (ADC otherwise; needs `omniops[gcp]`).
- `kubeconfig`: `kubeconfig` (path, default `$KUBECONFIG` or `~/.kube/config`), `context`. base_url defaults to the cluster server.

## 3. Tables

```yaml
- name: datadog_logs            # <source>_<object>, lowercase snake_case, unique across ALL manifests
  title: Log search
  description: >-               # what a row is, which filters are required, what it is good for
  kind: logs                    # timeseries|logs|spans|traces|events|entities|alerts|incidents|changes|profiles|text|generic|write
  request: {...}                # exactly one of: request | steps | python
  filters: [...]                # pushdown columns
  require_one_of: [[service, query]]   # at least one of each group must be filtered
  time: {column: ts, default_lookback: 1h, max_window: 7d, retention: 15d}
  pagination: {...}
  response: {...}
  columns: [...]
  max_rows: 5000
  sort_hint: "results are newest-first"
  pitfalls: ["table-specific gotchas"]
  examples: [{sql: "...", description: "...", verified: false}]   # >= 1 required; these are the few-shot shapes
  tests: [{sql: "...", min_rows: 0}]                               # live boot-time smoke tests
  fixtures: [{file: logs_search.json, filters: {...}, expect_rows: 2, expect_columns: {service: web}}]
  ui_link: "{{ source.base_url }}/..."    # optional deep-link template (ctx: filters, time, source, env)
  docs_url: https://...                   # the API reference page for this endpoint
  zero_fill: false                        # true for count series whose API omits empty buckets
```

### 3.1 Request templates

```yaml
request:
  method: POST                       # GET|POST|PUT|PATCH|DELETE
  path: /api/v2/logs/events/search   # appended to base_url; or `url:` absolute
  query: {"filter[from]": "{{ time.start | rfc3339 }}"}   # None / "" values are dropped
  headers: {}
  body:                              # dict/list/str template; exact "{{ expr }}" keeps native types
    filter:
      query: "{{ filters.query | default('*') }}"
      from: "{{ time.start | epoch_ms }}"
      to: "{{ time.end | epoch_ms }}"
      storage_tier: "{{ filters.storage_tier | default('indexes') }}"
    page: {limit: "{{ [limit or 1000, 1000] | min }}"}
    sort: "-timestamp"
  body_format: json                  # json|form|text|graphql
  response_format: json              # json|ndjson|text|csv
  accept_status: [404]               # statuses treated as "no rows" rather than errors
  timeout_s: 60
```

Template context:

| Name | Value |
|---|---|
| `env.X` | environment variable (with manifest defaults applied) |
| `filters.<param>` | pushed-down filter value (scalar per fan-out request; list when `fan_out: false`) |
| `time.start`, `time.end` | aware UTC datetimes of the resolved window |
| `time.step_s`, `time.step`, `time.range_s` | auto step (~300 points, nice values) and window length |
| `limit` | SQL LIMIT if any (else None) |
| `page.offset`, `page.number`, `page.size` | pagination state |
| `captures.<name>` | values captured by earlier `steps` |
| `source.base_url`, `source.name` | runtime info |

Filters: `epoch_s`, `epoch_ms`, `epoch_us`, `epoch_ns`, `rfc3339`, `rfc3339_ms`, `iso`, `strftime(fmt)`,
`prom_duration` (60 -> "1m"), `seconds`, `quote`, `squote`, `lucene_escape`, `label_escape`, `regex_escape`,
`as_list`, `format_each('svc:{}')`, `join_or('service:{}')` -> `(service:a OR service:b)`, `b64`, `json`,
`compact`, plus Jinja built-ins (`default`, `urlencode`, `tojson`, `join`, `min`, `max`, `int`, `lower`...).

**Get the time format right per API** (epoch s vs ms vs ns vs RFC3339). Silent wrong units were the
most common cause of empty results in production.

### 3.2 Multi-step / async jobs

```yaml
steps:
  - name: create
    method: POST
    path: /services/search/jobs
    body_format: form
    body: {search: "search {{ filters.query }}", earliest_time: "{{ time.start | epoch_s }}", output_mode: json}
    capture: {sid: "$.sid"}
  - name: wait
    method: GET
    path: "/services/search/jobs/{{ captures.sid }}"
    query: {output_mode: json}
    poll: {until: "response.entry[0].content.isDone", fail_if: "response.entry[0].content.isFailed", interval_s: 1, timeout_s: 90}
  - name: results                     # the LAST step produces rows and is paginated
    method: GET
    path: "/services/search/jobs/{{ captures.sid }}/results"
    query: {output_mode: json, count: 1000}
```

`when: "<jinja expr>"` skips a step when false.

### 3.3 Filters (pushdown)

```yaml
filters:
  - column: service          # must be a declared column (use filter_only: true if the API does not return it)
    param: service           # template key (default = column)
    required: false
    ops: ["=", "in"]         # subset of = != in like > >= < <= between
    type: VARCHAR
    description: "Service name (exact)"
    default: null
    fan_out: true            # IN (...) -> one request per value (max_fan_out); false -> template gets the list
    max_fan_out: 20
    variants: hostname       # engine retries short/FQDN host forms when empty
    examples: ["web-store"]  # used by offline render checks; give realistic values
```

The engine fills filter columns the API does not return with the request's filter value, so the SQL
predicate (which DuckDB re-applies) still matches. Search-expression parameters (Datadog `query`,
NRQL, SPL, LogQL, KQL, DQL, PromQL) are `filter_only: true` columns.

### 3.4 Time

`time: {column: ts, default_lookback: 1h, max_window: 30d, retention: 15d, target_points: 300}`.
Predicates `ts >= / > / < / <= / BETWEEN` on the column set `time.start` / `time.end`. Without a
predicate the engine uses the run's trigger window (alert investigations) or `default_lookback`
before the anchor. Starts are clamped to `retention`; ends in the future are clamped to the anchor.
Omit `time` for tables with no time dimension (inventories, definitions).

### 3.5 Pagination

```yaml
pagination: {type: cursor, cursor_path: "$.meta.page.after", cursor_param: "body.page.cursor", max_pages: 5}
pagination: {type: offset, offset_param: query.offset, limit_param: query.limit, page_size: 100, total_path: "$.total"}
pagination: {type: page, page_param: query.page, limit_param: query.per_page, page_size: 100, start: 1}
pagination: {type: link_header, max_pages: 5}
pagination: {type: next_url, next_url_path: "$.links.next"}
```
`has_more_path` optionally gates continuation. Targets are `query.<name>` or `body.<dotted.path>`.
Prefer sorting server-side (newest first, or count-descending for aggregates) over deep pagination.

### 3.6 Response -> rows

```yaml
response:
  rows_path: "$.data[*]"          # elements that become rows ("$" = the body itself / a top-level array)
  expand:                          # optional, applied in order; each binds an alias usable as @alias
    - {explode: "values", as: p}                                # one row per array element
    - {zip: {t: "$.times", v: "$.values[#i]"}, as: pt}          # parallel arrays -> one row per index
    - {columnar: {columns: "schema.fields[*].name", data: "data.values", orientation: column}, as: row}
    - {entries: "attributes.buckets", as: e}                     # object -> rows of {key, value}
  dynamic: "attributes"            # optional: flatten all keys of this node into extra columns
  dynamic_prefix: ""
  text: {regex: "^(?P<ts>\\S+) (?P<msg>.*)$"}   # for text responses: one row per line ({line, ...groups})
  errors: [{path: "$.status", equals: "error", message: "$.error"}]
```

Path syntax: `$` = body root; no prefix = relative to the current `rows_path` element; `.` = the
element itself; `@alias` = an expanded element; `[*]` iterate; `[n]` / `[-1]` index; `['a.b']`
quoted key; `[#i]` = index of the current `rows_path` element (for column-oriented payloads).

### 3.7 Columns

```yaml
columns:
  - {name: ts, type: TIMESTAMP, path: "attributes.timestamp", transform: auto_time, description: "..."}
  - {name: value, type: DOUBLE, path: "@p[1]"}
  - {name: tags, type: JSON, path: "attributes.tags"}   # JSON columns: query with tags->>'key' / json_extract
  - {name: query, filter_only: true, description: "Datadog log search query"}
  - {name: duration_ms, type: DOUBLE, path: "duration", transform: ns_to_ms}
  - {name: summary, expr: "_.title ~ ' (' ~ _.status ~ ')'"}   # Jinja expression; scope: _ (element), aliases, filters
```
Types: `VARCHAR BIGINT DOUBLE BOOLEAN TIMESTAMP JSON DATE INTERVAL`.
Transforms: `epoch_s epoch_ms epoch_us epoch_ns auto_time iso json str int float bool lower upper
ns_to_ms us_to_ms s_to_ms duration_to_s join first length` (`join` uses `transform_arg` as separator).

**Canonical column names** (use them whenever the concept exists so roles and joins work across
platforms): `ts` (event/sample time, TIMESTAMP), `service`, `env`, `host`, `pod`, `namespace`,
`message`, `level` (log level/status), `value` (metric value), `metric`, `labels`/`tags` (JSON),
`trace_id`, `span_id`, `duration_ms`, `status_code`, `resource` (endpoint/operation), `id`,
`title`, `state`/`status`, `severity`, `url` (UI link to the object), `author`, `version`.

### 3.8 Python escape hatch

When the declarative language lacks a primitive (binary payloads, websocket streams, SigV4-signed
JSON-RPC that templates can express is fine), set `python: "omniops.sources.python.<module>:<func>"`
and describe exactly what the function must do in `description` + `pitfalls`. Signature:

```python
async def fetch(rt: SourceRuntime, table: TableSpec, ctx: dict, logs: list[RequestLog]) -> list[dict]
```

### 3.9 Write tables

`kind: write` tables are never executed inline: the engine stages them and Slack asks a human to
approve (30-minute expiry, claimed atomically). Model them as `INSERT`-like via filters
(e.g. `grafana_dashboard_write WHERE uid=... AND dashboard_json=...`).

### 3.10 Engine features added after the first research pass

| Feature | Where | Use it for |
|---|---|---|
| `pagination: {type: time_cursor, cursor_path: "$.data.result[*].values[*][0]", cursor_param: query.end, time_direction: backward}` | pagination | APIs without cursor tokens (Loki): next page ends at the oldest timestamp returned |
| `expand: [{explode: "@s.Document", parse: true, as: doc}]` | expand | JSON documents encoded as strings (X-Ray segments, CloudTrail events) - parsed before expanding |
| `transform: from_json` | column | parse a JSON-encoded string field into a JSON column |
| `{{ filters.dimensions \| kv_list }}` | template filter | `"a=1,b=2"` -> `[{"Name":"a","Value":"1"},...]` (AWS dimensions; args: sep, kv, key_name, value_name) |
| cursor targets on JSON-text bodies | pagination | `cursor_param: body.NextToken` now works when `body_format: text` renders JSON |
| `rate_limit: {retry_body_patterns: ["ThrottlingException"]}` | source | retry 4xx responses whose body says throttled (AWS JSON protocol) |
| `rate_limit.requests_per_minute` | source | now enforced (sliding 60 s window across the source's tables) |
| `errors: [{path: "$.Results[*].StatusCode", equals: Forbidden}]` | error rules | wildcard paths fire when ANY element equals the value |
| `time: {reapply: false}` | time | instant-query tables whose rows are stamped with the evaluation time: the engine strips time predicates after pushdown |
| `time: {control_lookback: 6h}` | time | bound the positive-control re-query for expensive/async sources (or set `positive_control: false`) |
| `append: {pitfalls: [...], env: [...], errors: [...]}` | manifest with `extends` | add to the template's lists instead of replacing them |
| fixture fallback | fixtures | a derived source (`extends`) finds fixtures under the template source's directory automatically - do not copy them |
| `fixtures: [{file: err.json, expect_error: "parse error"}]` | fixtures | prove a 2xx error rule fires |
| `pagination: {type: cursor, cursor_exclusive: true, cursor_keep_params: [fields]}` | pagination | follow-up pages must send only the cursor (Dynatrace nextPageKey) |
| `request: {accept_status_rows: [400]}` | request | a 4xx whose body is data (datasource health check failures) becomes rows |
| `{{ filters.name \| tilde_quote }}` | template filter | Dynatrace selector quoting (~ escapes ~ and ") |
| sibling-table renaming | extends | descriptions/pitfalls/examples that name template tables are renamed to the derived prefix |

## 4. Roles (tool-agnostic layer)

Seeds and specialist agents never name a vendor; they ask for a *role*. Bind each table that fulfils
a role, mapping canonical names to the table's columns and giving default filter templates
(rendered with `service`, `env`, `host`, `query`, `error_only`, `alert_id`):

```yaml
roles:
  logs:
    table: datadog_logs
    filters: {query: "service:{{ service }}{{ ' status:error' if error_only else '' }}"}
    columns: {ts: ts, service: service, message: message, level: status, host: host}
    query_language: datadog_logs
  change_events:
    table: datadog_events
    filters: {query: "source:deploy OR category:change"}
    columns: {ts: ts, service: service, title: title, version: version, url: url}
```

Role names: `alert_rules active_alerts alert_history metrics logs log_counts traces trace_detail
service_map services hosts change_events code_changes incidents oncall rum errors k8s_state
k8s_events k8s_logs dashboards slo tickets docs chat_history profiles`.

## 5. Quality bar (checked by `omniops manifest test`)

1. Every table renders with its filters' `examples`.
2. Every table (except `python`/`write`) has at least one fixture taken from the **official docs'
   example response** (or a real recorded response). Say in the fixture `note` where it came from.
   Never invent a payload shape the docs do not show; if the docs give only a schema, build the
   fixture strictly from that schema and say so.
3. Every table has at least one example SQL that parses and uses only declared columns.
4. Endpoint paths, parameter names, time units and response paths match the official API reference
   (cite `docs_url` per table). Prefer current, non-deprecated API versions.
5. Pitfalls capture what silently returns empty or wrong data (unknown facet = 0 rows, alphabetical
   group-by ordering, storage tiers, host-name forms, sampling, retention, rate limits).
