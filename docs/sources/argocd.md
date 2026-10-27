# Argo CD (source `argocd`)

Manifest: `src/omniops/sources/manifests/argocd.yaml` (8 tables) · fixtures:
`src/omniops/sources/fixtures/argocd/`.

Reference used: Argo CD **v3.5.6** (latest release, 20 Oct 2026). The REST API is the
gRPC-gateway of the API server; its contract is the Swagger file
`assets/swagger.json` (served live at `<ARGOCD_URL>/swagger-ui`). The developer guide page documents
auth and the 403/404 rule; field semantics come from `pkg/apis/application/v1alpha1/types.go`
and server behaviour from `server/application/application.go` (same tag).

## Tables

| SQL table | Method + endpoint | Official reference |
|---|---|---|
| `argocd_applications` | `GET /api/v1/applications?name=&projects=&repo=&selector=&appNamespace=` | https://github.com/argoproj/argo-cd/blob/v3.5.6/assets/swagger.json (`ApplicationService_List`) |
| `argocd_history` | same list, rows = `items[].status.history[]` | swagger `v1alpha1RevisionHistory` |
| `argocd_app_resources` | same list, rows = `items[].status.resources[]` | swagger `applicationv1alpha1ResourceStatus` |
| `argocd_sync_results` | same list, rows = `items[].status.operationState.syncResult.resources[]` | swagger `v1alpha1OperationState` / `v1alpha1ResourceResult` |
| `argocd_revision_metadata` | `GET /api/v1/applications/{name}/revisions/{revision}/metadata?appNamespace=&project=&sourceIndex=&versionId=` | swagger `ApplicationService_RevisionMetadata` |
| `argocd_resource_tree` | `GET /api/v1/applications/{applicationName}/resource-tree?appNamespace=&project=` | swagger `ApplicationService_ResourceTree` |
| `argocd_managed_resources` | `GET /api/v1/applications/{applicationName}/managed-resources?kind=&namespace=&name=&group=&appNamespace=&project=` | swagger `ApplicationService_ManagedResources` |
| `argocd_app_events` | `GET /api/v1/applications/{name}/events?resourceName=&resourceNamespace=&resourceUID=&appNamespace=&project=` | swagger `ApplicationService_ListResourceEvents` |

Docs pages: https://argo-cd.readthedocs.io/en/stable/developer-guide/api-docs/ (auth, Swagger UI,
"How to Avoid 403 Errors for Missing Applications"), and
https://argo-cd.readthedocs.io/en/stable/operator-manual/upgrading/2.14-3.0/ ("Health status in
the Application CR": `resourceHealthSource: appTree`).

## Roles bound

| Role | Table | Columns |
|---|---|---|
| `change_events` | `argocd_history` | ts = deployedAt, service = app, title, version = revision, author = initiated_by, url |

`argocd_revision_metadata` (author / commit message) needs `app` + `revision`, so it is not bound
to `code_changes` (role fetches cannot supply a revision); query it after `argocd_history`.

## Auth, base URL

- `Authorization: Bearer $ARGOCD_TOKEN` (`auth: bearer`). A token of an `apiKey` account with
  `role:readonly` is enough (the Arena creates account `omniops`). Session JWTs from
  `POST /api/v1/session` also work but expire.
- `ARGOCD_URL` = server root including any `--rootpath` (e.g. `http://localhost:8080` through the
  Arena port-forward, `server.insecure: true`). `ARGOCD_APP_NAMESPACE` optionally pins
  `appNamespace` for apps-in-any-namespace.
- UI links: `<ARGOCD_URL>/applications/<appNamespace>/<app>`; the `url` columns are paths relative
  to `ARGOCD_URL` (the engine's column expressions cannot read env).

## Rate limits, pagination

- No documented REST rate limit (only login failure throttling). The list endpoint returns full
  Application objects without pagination (`pagination: none`); filter `app`/`project` to keep it
  small. Concurrency 4.
- `refresh=hard|normal` on list/get triggers reconciliation - never sent.

## Retention / freshness

- `status.history`: only successful syncs, at most `spec.revisionHistoryLimit` (default 10).
- `status.operationState`: only the current/last operation.
- Application events: the Argo CD cluster's event TTL (default 1h).
- State is refreshed every `timeout.reconciliation` (default 120 s; 60 s in the Arena).

## Time formats

All timestamps are RFC3339 (`v1Time`): `deployedAt`, `deployStartedAt`, `reconciledAt`,
`operationState.startedAt/finishedAt`, revision `date`. No endpoint filters by time; `ts`
predicates are applied locally.

## Pitfalls

- Without `project`, a missing application and one you may not see both return 403 (existence is
  hidden); with `project` a missing app is 404.
- Synced + Degraded is the normal state of a runtime fault that arrived through Git: the change
  is the newest `argocd_history` entry (ts = deployedAt), not OutOfSync.
- Since Argo CD 3.0, per-resource health is not persisted in the Application
  (`resource_health_source = 'appTree'`): `argocd_app_resources.health_status` is null - use
  `argocd_resource_tree` (pods expose `status_reason` such as CrashLoopBackOff and `restart_count`).
- Several applications may share a repository and a commit (Arena: `flagd-values`, `batch-jobs`,
  `arena-control` all track the fault repository); the relevant app is the one whose `path` the
  commit changed.
- Resource events need `resource_name`, `resource_namespace` and `resource_uid` (from
  `argocd_resource_tree.uid`) of a node of the app's tree; otherwise HTTP 400.
- Revision metadata exists for Git sources only (not Helm chart versions).
- `argocd_managed_resources` returns `target_state` / `live_state` only for configuration kinds
  (ConfigMap, Service, NetworkPolicy, quotas, storage, webhooks, RBAC, Applications); workloads
  and Secrets are omitted because env/data may embed credentials (Argo CD masks Secret data, but
  not Deployment env literals).
- A ConfigMap value that is itself JSON (flagd flags) is a string inside `data`; use
  `json_extract(live_state->'data'->>'demo.flagd.json', '$.flags.<flag>.defaultVariant')`.
