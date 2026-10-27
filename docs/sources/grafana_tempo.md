# Grafana Tempo via Grafana datasource proxy (`grafana_tempo`)

Manifest: `src/omniops/sources/manifests/grafana_tempo.yaml` (`extends: tempo`; every table of
[tempo.md](tempo.md) is available as `grafana_tempo_*`). Fixtures: none of its own - the
fixture check falls back to `src/omniops/sources/fixtures/tempo/` because the proxy returns
Tempo's bodies unchanged.

Use this source when the investigator can reach Grafana but not Tempo directly (Grafana Cloud,
locked-down networks, a local Grafana stack). Grafana forwards the request to the Tempo URL of
the datasource and injects the datasource's own credentials and tenant header.

## Tables

| SQL table | HTTP | Official reference |
|---|---|---|
| `grafana_tempo_<x>` for every `tempo_<x>` | `GET {GRAFANA_URL}/api/datasources/proxy/uid/{GRAFANA_TEMPO_UID}` + the Tempo path of `tempo_<x>` | [Data source proxy calls](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/data_source/#data-source-proxy-calls) + the Tempo endpoint in [tempo.md](tempo.md) |

| Table | Proxied Tempo endpoint |
|---|---|
| `grafana_tempo_search` | `/api/search` ([Search](https://grafana.com/docs/tempo/latest/api_docs/#search)) |
| `grafana_tempo_search_spans` | `/api/search` with `\| select(...)` |
| `grafana_tempo_trace` | `/api/v2/traces/<id>` ([Query V2](https://grafana.com/docs/tempo/latest/api_docs/#query-v2)) |
| `grafana_tempo_trace_attributes` | `/api/v2/traces/<id>` |
| `grafana_tempo_metrics_range` | `/api/metrics/query_range` ([TraceQL Metrics](https://grafana.com/docs/tempo/latest/api_docs/#traceql-metrics)) |
| `grafana_tempo_metrics_instant` | `/api/metrics/query` ([Instant](https://grafana.com/docs/tempo/latest/api_docs/#instant)) |
| `grafana_tempo_metrics_exemplars` | `/api/metrics/query_range?exemplars=` |
| `grafana_tempo_tag_names` | `/api/v2/search/tags` ([Search tags V2](https://grafana.com/docs/tempo/latest/api_docs/#search-tags-v2)) |
| `grafana_tempo_tag_values` | `/api/v2/search/tag/<tag>/values` ([Search tag values V2](https://grafana.com/docs/tempo/latest/api_docs/#search-tag-values-v2)) |

Example: `grafana_tempo_trace` ->
`GET http://localhost:3000/api/datasources/proxy/uid/tempo/api/v2/traces/4bf92f3577b34da6a3ce929d0e0e4736`.

`grafana_tempo_search`, `grafana_tempo_search_spans`, `grafana_tempo_trace` and
`grafana_tempo_metrics_range` also produce a Grafana Explore deep link
(`/explore?schemaVersion=1&panes=...`, TraceQL query type) for the Slack answer.

Roles: same as `tempo` (`traces`, `trace_detail`, `services`) on the `grafana_tempo_*` tables.

## Auth

| Setup | Env |
|---|---|
| Grafana service account token (recommended; Viewer with `datasources:query` on the Tempo datasource) | `GRAFANA_TOKEN` -> `Authorization: Bearer ...` |
| Basic auth (only if enabled in Grafana) | `GRAFANA_USERNAME`, `GRAFANA_PASSWORD` |
| Anonymous access (`GF_AUTH_ANONYMOUS_ENABLED=true`) | none |
| Non-default org | `GRAFANA_ORG_ID` -> `X-Grafana-Org-Id` |

Never send `X-Scope-OrgID` yourself: the tenant comes from the datasource settings.

## Base URLs

`GRAFANA_URL` = Grafana root (`http://localhost:3000` for a local stack, `https://<stack>.grafana.net`
for Grafana Cloud). `GRAFANA_TEMPO_UID` defaults to `tempo` (the usual uid of a file-provisioned
Tempo data source); find others with `GET /api/datasources`. The uid-based proxy route
exists since Grafana 9.0; the `/api` routes are documented as "legacy" but fully supported.

## Limits

- Grafana data proxy timeout: `[dataproxy] timeout` default 30s -> HTTP 502/504 on heavy searches
  or metrics queries; narrow the window or the TraceQL.
- All Tempo per-query limits apply behind the proxy (see tempo.md): search window 168h, metrics
  window 24h by default (both configurable per deployment), `spss` max 100, exemplars max 100.

## Retention / time formats

Same as Tempo (epoch seconds on the wire; retention is the Tempo deployment's: 14 days by
default self-hosted, 30 days Grafana Cloud paid).

## Pitfalls

- 404 `data source not found` = wrong `GRAFANA_TEMPO_UID`; 401/403 = token lacks
  `datasources:query`. A 404 from `grafana_tempo_trace` with Tempo's own "trace not found" body is
  a missing trace and yields 0 rows.
- The proxy is used read-only (GET) here.
- `extends:` replaces the template's top-level `pitfalls` list, so this manifest repeats the Tempo
  pitfalls with the renamed tables and adds the proxy-specific ones; keep both in sync when
  editing `templates/tempo.yaml`.
