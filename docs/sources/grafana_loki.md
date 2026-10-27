# Grafana Loki via Grafana datasource proxy (`grafana_loki`)

Manifest: `src/omniops/sources/manifests/grafana_loki.yaml` (`extends: loki`; every table of
[loki.md](loki.md) is available as `grafana_loki_*`). Fixtures: none of its own - the engine falls
back to `src/omniops/sources/fixtures/loki/` (the proxy returns Loki's body unchanged).

Use this source when the investigator can reach Grafana but not Loki directly (Grafana Cloud,
locked-down networks, the o11y-bench stack). Grafana forwards the request to the URL configured on
the datasource and injects the datasource's own Loki credentials and tenant header.

## Tables

| SQL table | HTTP | Official reference |
|---|---|---|
| `grafana_loki_<x>` for every `loki_<x>` | `GET {GRAFANA_URL}/api/datasources/proxy/uid/{GRAFANA_LOKI_UID}` + the Loki path of `loki_<x>` | [Data source proxy calls](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/data_source/#data-source-proxy-calls) (`GET /api/datasources/proxy/uid/:uid/*`) + the Loki endpoint in [loki.md](loki.md) |

Example: `grafana_loki_logs` -> `GET http://localhost:3000/api/datasources/proxy/uid/loki/loki/api/v1/query_range?query=...&start=<ns>&end=<ns>&limit=2000&direction=backward`.

`grafana_loki_logs`, `grafana_loki_query_range` and `grafana_loki_query` also produce a Grafana
Explore deep link (`/explore?schemaVersion=1&orgId=..&panes={"a":{"datasource":"loki","queries":[...],"range":{...}}}`)
for the Slack answer.

Roles: same as `loki` (`logs`, `log_counts`, `services`, `alert_rules`, `active_alerts`) on the
`grafana_loki_*` tables.

## Auth

| Setup | Env |
|---|---|
| Grafana service account token (recommended; a role with `datasources:query` on the Loki datasource, e.g. Viewer) | `GRAFANA_TOKEN` -> `Authorization: Bearer ...` |
| Basic auth (only if enabled in Grafana) | `GRAFANA_USERNAME`, `GRAFANA_PASSWORD` |
| Anonymous access (o11y-bench: `GF_AUTH_ANONYMOUS_ENABLED=true`) | none |
| Non-default org | `GRAFANA_ORG_ID` -> `X-Grafana-Org-Id` |

RBAC: `datasources:query` with scope `datasources:uid:<uid>` or `datasources:*`
([RBAC actions](https://grafana.com/docs/grafana/latest/administration/roles-and-permissions/access-control/custom-role-actions-scopes/)).
Never send `X-Scope-OrgID` yourself: the tenant comes from the datasource settings.

## Base URLs

`GRAFANA_URL` = Grafana root (`http://localhost:3000` for o11y-bench, `https://<stack>.grafana.net`
for Grafana Cloud). `GRAFANA_LOKI_UID` defaults to `loki` (the uid provisioned by o11y-bench's
`docker/datasources.yaml`, `access: proxy`, url `http://localhost:3100`); find others with
`GET /api/datasources`. The uid-based proxy route exists since Grafana 9.0. Starting in Grafana 13
the `/api` routes are "legacy" in favour of `/apis`, but "remain fully accessible and operative"
(o11y-bench runs Grafana 13.2.4).

## Limits

- Grafana data proxy timeout: `[dataproxy] timeout` default 30s (shorter than Loki's 1m
  `query_timeout`) -> HTTP 502/504 on heavy queries.
- All Loki per-query limits apply behind the proxy (see loki.md).

## Retention / time formats / pagination

Same as Loki (nanosecond epochs on the wire, backward time-cursor paging of `grafana_loki_logs`,
retention is the Loki deployment's).

## Pitfalls

- Template pitfalls apply (renamed to `grafana_loki_*`); the manifest `append`s the proxy-specific ones:
- 404 `data source not found` = wrong `GRAFANA_LOKI_UID`; 401/403 = token lacks `datasources:query`.
- 502/504 = Grafana proxy timeout, not a schema problem.
- Only GET requests are modelled; no writes go through the proxy.
