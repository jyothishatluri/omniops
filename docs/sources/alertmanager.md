# Prometheus Alertmanager (`alertmanager`)

Template: `src/omniops/sources/templates/alertmanager.yaml` (source `alertmanager`), used by
`manifests/alertmanager.yaml`. Fixtures: `src/omniops/sources/fixtures/alertmanager/`.

Reference: the Alertmanager API v2 is defined only by its OpenAPI 2.0 spec,
[api/v2/openapi.yaml](https://github.com/prometheus/alertmanager/blob/v0.34.1/api/v2/openapi.yaml)
(v0.34.1 = latest release, 2026-09-17; byte-identical to `main` on 2026-10-26). The README states
that v2 is served under `/api/v2` (prefixed by `--web.route-prefix`) and that v1 was removed in
0.27.0. The spec has schemas but **no example bodies**, so every fixture is built strictly from the
schema definitions and says so. Filter semantics were checked in `api/v2/api.go` (v0.34.1).

## Tables

| SQL table | HTTP | Official reference |
|---|---|---|
| `alertmanager_alerts` | `GET /api/v2/alerts` (`filter` repeated, `receiver`, `active`/`silenced`/`inhibited`) | [openapi.yaml#L155](https://github.com/prometheus/alertmanager/blob/v0.34.1/api/v2/openapi.yaml#L155) (`getAlerts`, schema `gettableAlerts`) |
| `alertmanager_alert_groups` | `GET /api/v2/alerts/groups` (`filter`, `receiver`, `active`/`silenced`/`inhibited`) | [openapi.yaml#L224](https://github.com/prometheus/alertmanager/blob/v0.34.1/api/v2/openapi.yaml#L224) (`getAlertGroups`, schema `alertGroups`) |
| `alertmanager_silences` | `GET /api/v2/silences` (`filter`, `active`/`expired`/`pending` since 0.34) | [openapi.yaml#L49](https://github.com/prometheus/alertmanager/blob/v0.34.1/api/v2/openapi.yaml#L49) (`getSilences`, schema `gettableSilences`) |
| `alertmanager_silence` | `GET /api/v2/silence/{silenceID}` | [openapi.yaml#L114](https://github.com/prometheus/alertmanager/blob/v0.34.1/api/v2/openapi.yaml#L114) (`getSilence`) |
| `alertmanager_status` | `GET /api/v2/status` | [openapi.yaml#L21](https://github.com/prometheus/alertmanager/blob/v0.34.1/api/v2/openapi.yaml#L21) (`getStatus`, schema `alertmanagerStatus`) |
| `alertmanager_receivers` | `GET /api/v2/receivers` | [openapi.yaml#L32](https://github.com/prometheus/alertmanager/blob/v0.34.1/api/v2/openapi.yaml#L32) (`getReceivers`) |

Roles bound:
- `active_alerts` -> `alertmanager_alerts`
  (`id=fingerprint, name=alertname, ts=starts_at, state, severity, service, title, url, labels`).
- `change_events` -> `alertmanager_silences` (`ts=starts_at, title, service, author=created_by`):
  a silence created just before an incident usually marks planned work.

Pushdown:
- `alertname`, `severity`, `matcher` -> repeated `filter=<matcher>` (AND).
- `state = 'active'` -> `silenced=false&inhibited=false`; `state = 'suppressed'` -> `active=false`
  (supersets; SQL re-applies the exact state). `unprocessed` is filtered in SQL only - the
  `unprocessed` parameter is declared in the spec but ignored by the v0.34.1 handler.
- Silences: `state` -> `active`/`expired`/`pending` flags; `matcher` -> `filter`.
- `service`, `env`, `host` are derived from several label names and are filtered in SQL only.

Not modelled: `POST /alerts`, `POST /silences`, `DELETE /silence/{id}` (writes; would need a
`kind: write` table with Slack approval), and the management endpoints `/-/healthy`, `/-/ready`,
`/-/reload`.

## Version notes (diffing the spec across releases)

| Field / parameter | Since |
|---|---|
| `status.mutedBy` on alerts | before 0.28 (present in 0.28.1) |
| silence `annotations` | 0.32 |
| receiver `labels`, `receiver_matchers` parameter | 0.33 |
| alert group `routeLabels`, silences `active`/`expired`/`pending` parameters | 0.34 |

Older servers simply omit the newer fields (columns are NULL) and ignore unknown parameters, so
SQL re-applies every pushed-down predicate.

## Auth and base URLs

`ALERTMANAGER_URL` is everything before `/api/v2`:

| Deployment | `ALERTMANAGER_URL` | Auth env |
|---|---|---|
| Upstream Alertmanager | `http://alertmanager:9093` (+ `--web.route-prefix`) | none, or `ALERTMANAGER_TOKEN` behind a proxy |
| Grafana Mimir / Cortex | `http://mimir:8080/alertmanager` (`-http.alertmanager-http-prefix`, default `/alertmanager`) | `ALERTMANAGER_TENANT_ID` -> `X-Scope-OrgID` |
| Grafana Cloud Alertmanager | the stack's Alertmanager URL (ending in `/alertmanager`) | `ALERTMANAGER_USERNAME` (instance id) + `ALERTMANAGER_PASSWORD` (access policy token) |
| Grafana built-in Alertmanager (Grafana-managed alerts) | `http://grafana:3000/api/alertmanager/grafana` | `ALERTMANAGER_TOKEN` = Grafana service account token (none with anonymous access) |
| External Alertmanager datasource through Grafana | `http://grafana:3000/api/alertmanager/<datasource uid>` | Grafana service account token |

Grafana's Alertmanager API implements `api/v2/status`, `alerts`, `alerts/groups`, `silences`,
`silence/{id}` for both `grafana` and `{DatasourceUID}` (Grafana
`pkg/services/ngalert/api/tooling/definitions/alertmanager.go`), but not `/api/v2/receivers`
(receivers live at `/api/alertmanager/grafana/config/api/v1/receivers`), and its `status.config` is
a structured object rather than `{original: <yaml>}` (the table exposes both `config_original` and
`config`). o11y-bench provisions no alert rules, so its built-in Alertmanager is empty.

## Rate limits

None documented. Every call returns the complete in-memory state; concurrency is capped at 4.

## Retention

Alertmanager is not a history store: resolved alerts are dropped shortly after resolution, and
silences/notification log entries are kept for `--data.retention` (default 120h).

## Time formats

All timestamps are RFC3339 strings (`date-time`), parsed into TIMESTAMP columns. No endpoint takes
a time range, so no table has a `ts` pushdown.

## Fixtures

All six fixtures (`alerts.json`, `alert_groups.json`, `silences.json`, `silence.json`,
`status.json`, `receivers.json`) are built strictly from the v0.34.1 schema definitions (all
required fields present); values are illustrative.

## Pitfalls

- `endsAt` of a firing alert is a rolling expiry in the future, not the resolution time.
- `state = 'suppressed'` = silenced, inhibited or muted by a time interval = nobody was notified;
  check `silenced_by` / `inhibited_by` / `muted_by`.
- Matchers are exact and case-sensitive; unknown labels/values return 0 rows.
- The silences `filter` only returns silences containing that **exact** matcher (same name,
  operator and value - `CheckSilenceMatchesFilterLabels`); a regex silence is not returned for an
  equality filter. Use `alertmanager_alerts.silenced_by` to find the silences muting an alert.
- An alert routed to several receivers appears once per receiver in `alertmanager_alert_groups`.
- Errors come back as 4xx/5xx with a JSON string body (`BadRequest`), never as 2xx, so no
  2xx-error rules are needed.
