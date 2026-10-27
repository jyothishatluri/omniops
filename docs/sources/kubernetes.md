# Kubernetes (source `kubernetes`)

Manifest: `src/omniops/sources/manifests/kubernetes.yaml` (31 tables) · fixtures:
`src/omniops/sources/fixtures/kubernetes/` · Python escape hatch:
`src/omniops/sources/python/kubernetes.py` (`kubernetes_container_env` only).

Reference used: the generated Kubernetes API reference for **v1.37** (pages updated 26 Aug 2026,
grouped by API group under `https://kubernetes.io/docs/reference/kubernetes-api/<group>/<kind>-v1/`),
the API concepts page (chunked lists), the field selector page, the metrics.k8s.io reference, and
`k8s.io/api/core/v1/types.go` for `PodLogOptions`.

## Tables

| SQL table | Method + endpoint | Official reference |
|---|---|---|
| `kubernetes_pods` | `GET /api/v1/pods`, `GET /api/v1/namespaces/{ns}/pods` (one row per `status.containerStatuses[]`) | https://kubernetes.io/docs/reference/kubernetes-api/core/pod-v1/#get-list |
| `kubernetes_pod_logs` | `GET /api/v1/namespaces/{ns}/pods/{pod}/log?container=&previous=&timestamps=true&sinceTime=&tailLines=&limitBytes=&stream=` | https://kubernetes.io/docs/reference/kubernetes-api/core/pod-v1/#get-read-log |
| `kubernetes_container_env` (python) | the workload list endpoints below; env / envFrom of the pod template, values redacted | https://kubernetes.io/docs/reference/kubernetes-api/core/pod-v1/#Container |
| `kubernetes_events` | `GET /api/v1[/namespaces/{ns}]/events` | https://kubernetes.io/docs/reference/kubernetes-api/core/event-v1/#get-list |
| `kubernetes_events_v1` | `GET /apis/events.k8s.io/v1[/namespaces/{ns}]/events` | https://kubernetes.io/docs/reference/kubernetes-api/events/event-v1/#get-list |
| `kubernetes_deployments` | `GET /apis/apps/v1[/namespaces/{ns}]/deployments` | https://kubernetes.io/docs/reference/kubernetes-api/apps/deployment-v1/#get-list |
| `kubernetes_replicasets` | `GET /apis/apps/v1[/namespaces/{ns}]/replicasets` | https://kubernetes.io/docs/reference/kubernetes-api/apps/replica-set-v1/#get-list |
| `kubernetes_statefulsets` | `GET /apis/apps/v1[/namespaces/{ns}]/statefulsets` | https://kubernetes.io/docs/reference/kubernetes-api/apps/stateful-set-v1/#get-list |
| `kubernetes_daemonsets` | `GET /apis/apps/v1[/namespaces/{ns}]/daemonsets` | https://kubernetes.io/docs/reference/kubernetes-api/apps/daemon-set-v1/#get-list |
| `kubernetes_jobs` | `GET /apis/batch/v1[/namespaces/{ns}]/jobs` | https://kubernetes.io/docs/reference/kubernetes-api/batch/job-v1/#get-list |
| `kubernetes_cronjobs` | `GET /apis/batch/v1[/namespaces/{ns}]/cronjobs` | https://kubernetes.io/docs/reference/kubernetes-api/batch/cron-job-v1/#get-list |
| `kubernetes_workload_containers` | `GET /apis/{apps,batch}/v1[/namespaces/{ns}]/{deployments,statefulsets,daemonsets,replicasets,jobs}` (one row per template container) | https://kubernetes.io/docs/reference/kubernetes-api/apps/deployment-v1/#DeploymentSpec |
| `kubernetes_services` | `GET /api/v1[/namespaces/{ns}]/services` | https://kubernetes.io/docs/reference/kubernetes-api/core/service-v1/#get-list |
| `kubernetes_endpointslices` | `GET /apis/discovery.k8s.io/v1[/namespaces/{ns}]/endpointslices` (one row per endpoint) | https://kubernetes.io/docs/reference/kubernetes-api/discovery/endpoint-slice-v1/#get-list |
| `kubernetes_networkpolicies` | `GET /apis/networking.k8s.io/v1[/namespaces/{ns}]/networkpolicies` | https://kubernetes.io/docs/reference/kubernetes-api/networking/network-policy-v1/#get-list |
| `kubernetes_ingresses` | `GET /apis/networking.k8s.io/v1[/namespaces/{ns}]/ingresses` | https://kubernetes.io/docs/reference/kubernetes-api/networking/ingress-v1/#get-list |
| `kubernetes_configmaps` | `GET /api/v1[/namespaces/{ns}]/configmaps` | https://kubernetes.io/docs/reference/kubernetes-api/core/config-map-v1/#get-list |
| `kubernetes_secrets` | `GET /api/v1[/namespaces/{ns}]/secrets` (key names + sizes only) | https://kubernetes.io/docs/reference/kubernetes-api/core/secret-v1/#get-list |
| `kubernetes_persistentvolumeclaims` | `GET /api/v1[/namespaces/{ns}]/persistentvolumeclaims` | https://kubernetes.io/docs/reference/kubernetes-api/core/persistent-volume-claim-v1/#get-list |
| `kubernetes_persistentvolumes` | `GET /api/v1/persistentvolumes` | https://kubernetes.io/docs/reference/kubernetes-api/core/persistent-volume-v1/#get-list |
| `kubernetes_storageclasses` | `GET /apis/storage.k8s.io/v1/storageclasses` | https://kubernetes.io/docs/reference/kubernetes-api/storage/storage-class-v1/#get-list |
| `kubernetes_resourcequotas` | `GET /api/v1[/namespaces/{ns}]/resourcequotas` (one row per quota'd resource) | https://kubernetes.io/docs/reference/kubernetes-api/core/resource-quota-v1/#get-list |
| `kubernetes_limitranges` | `GET /api/v1[/namespaces/{ns}]/limitranges` (one row per limit item) | https://kubernetes.io/docs/reference/kubernetes-api/core/limit-range-v1/#get-list |
| `kubernetes_horizontalpodautoscalers` | `GET /apis/autoscaling/v2[/namespaces/{ns}]/horizontalpodautoscalers` | https://kubernetes.io/docs/reference/kubernetes-api/autoscaling/horizontal-pod-autoscaler-v2/#get-list |
| `kubernetes_poddisruptionbudgets` | `GET /apis/policy/v1[/namespaces/{ns}]/poddisruptionbudgets` | https://kubernetes.io/docs/reference/kubernetes-api/policy/pod-disruption-budget-v1/#get-list |
| `kubernetes_nodes` | `GET /api/v1/nodes` | https://kubernetes.io/docs/reference/kubernetes-api/core/node-v1/#get-list |
| `kubernetes_namespaces` | `GET /api/v1/namespaces` | https://kubernetes.io/docs/reference/kubernetes-api/core/namespace-v1/#get-list |
| `kubernetes_validating_webhooks` | `GET /apis/admissionregistration.k8s.io/v1/validatingwebhookconfigurations` (one row per webhook) | https://kubernetes.io/docs/reference/kubernetes-api/admissionregistration/validating-webhook-configuration-v1/#get-list |
| `kubernetes_mutating_webhooks` | `GET /apis/admissionregistration.k8s.io/v1/mutatingwebhookconfigurations` (one row per webhook) | https://kubernetes.io/docs/reference/kubernetes-api/admissionregistration/mutating-webhook-configuration-v1/#get-list |
| `kubernetes_pod_metrics` | `GET /apis/metrics.k8s.io/{v1beta1\|v1}[/namespaces/{ns}]/pods` (one row per container) | https://kubernetes.io/docs/reference/external-api/metrics.v1beta1/ |
| `kubernetes_node_metrics` | `GET /apis/metrics.k8s.io/{v1beta1\|v1}/nodes` | https://kubernetes.io/docs/reference/external-api/metrics.v1beta1/ |

Supporting pages:
- Chunked lists (`limit` / `continue`, 410 Gone, `remainingItemCount`): https://kubernetes.io/docs/reference/using-api/api-concepts/#retrieving-large-results-sets-in-chunks
- Field selectors (supported fields per kind, `=`/`!=` only): https://kubernetes.io/docs/concepts/overview/working-with-objects/field-selectors/
- events.k8s.io field-selector names (`regarding.*`, `reportingController`) are translated server-side (`pkg/apis/events/v1/conversion.go`).
- `PodLogOptions.sinceTime` (RFC3339; accepted as a query parameter, omitted from the generated OpenAPI): https://github.com/kubernetes/api/blob/master/core/v1/types.go
- metrics.k8s.io sample responses (used for the metrics fixtures) and GA status (stable since 1.37): https://kubernetes.io/docs/tasks/debug/debug-cluster/resource-metrics-pipeline/

## Roles bound

| Role | Table(s) | Notes |
|---|---|---|
| `k8s_state` | `kubernetes_pods`, `kubernetes_deployments`, `kubernetes_statefulsets`, `kubernetes_daemonsets`, `kubernetes_persistentvolumeclaims`, `kubernetes_jobs` | canonical columns mapped for the seed digest (phase, ready, restarts, waiting, terminated, exit_code, replicas, ready_replicas) |
| `k8s_events` | `kubernetes_events` (filter `type = 'Warning'`) | |
| `k8s_logs` | `kubernetes_pod_logs` | namespace + pod required, `previous = true` for crashloops |
| `hosts` | `kubernetes_nodes` | |
| `change_events` | `kubernetes_replicasets` (rollouts: `created`), `kubernetes_configmaps` (`last_modified` from managedFields) | |

## Auth, base URL

- `auth: kubeconfig` - `KUBECONFIG` (path; the first entry of a `:` list) and `KUBE_CONTEXT`
  (falls back to `KUBERNETES_CONTEXT`, then `current-context`). Supported credentials: client
  certificate + key (inline `*-data` or files), static bearer `token`, CA data / file,
  `insecure-skip-tls-verify`. **exec credential plugins (EKS, GKE, AKS kubelogin) and
  auth-provider are not executed**: use a token kubeconfig or `kubectl proxy`.
- Base URL = the kubeconfig cluster `server`, overridable with `K8S_API_URL`. Because the engine
  loads the kubeconfig lazily (inside the first `send`), the very first request of a fresh runtime is
  rendered with an empty base URL; set `K8S_API_URL` to the kubeconfig server
  (`kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}'`) until the engine
  resolves it up front (listed as an engine gap).
- Minimum RBAC: `get`/`list` on pods, pods/log, events (core + events.k8s.io), the apps/batch
  workloads, services, endpointslices, configmaps, networkpolicies, ingresses, PVC/PV,
  storageclasses, resourcequotas, limitranges, HPAs, PDBs, nodes, namespaces, webhook
  configurations, metrics.k8s.io pods/nodes. `secrets` list is optional (the table only maps key
  names, but the API sends values to the engine; deny it if that is unacceptable). 403 = missing
  RBAC, not an empty cluster.
- Metrics: `K8S_METRICS_API_VERSION` = `v1beta1` (default; every metrics-server release serves it)
  or `v1` (metrics.k8s.io is stable since Kubernetes 1.37).

## Pagination, limits

- Lists send `limit=500` and follow `metadata.continue` (`pagination: cursor`, max 10 pages =
  5000 objects per request). Continue tokens expire after ~5 minutes (HTTP 410 Gone; restart the
  query). `remainingItemCount` is omitted when a label/field selector is used.
- Server-side filtering is only by `labelSelector` and `fieldSelector`; `=` predicates on
  namespace / name / node / phase / type / reason / involved object are pushed down; `!=` is
  pushed as `field!=value`; LIKE and other operators are evaluated locally after listing.
  `namespace IN (...)` fans out one request per namespace.
- API Priority and Fairness may return 429 with `Retry-After` (retried, max 3). Concurrency 6.
- Pod logs: one kubelet-proxied request per pod/container; `tailLines` (default 2000, previous:
  500) bounds the response; `limitBytes` truncates the END of the log.

## Retention

- Live objects only - no history. Deleted pods, their logs and finished Jobs past
  `ttlSecondsAfterFinished` are gone.
- Events: `kube-apiserver --event-ttl` (default 1h) after the LAST occurrence of a series.
- Logs: the current and the one previous container instance on the node, rotated by the kubelet
  (`containerLogMaxSize` 10Mi x `containerLogMaxFiles` 5 by default).
- ReplicaSets: `revisionHistoryLimit` (default 10) old revisions per Deployment.

## Time formats

- Object timestamps are RFC3339 seconds (`metav1.Time`, e.g. `2026-10-01T11:52:19Z`);
  `eventTime` / `series.lastObservedTime` are `MicroTime` (RFC3339 with microseconds).
- Logs with `timestamps=true` prefix every line with RFC3339 / RFC3339Nano; `sinceTime` takes
  RFC3339 (the window start is rendered with `rfc3339`); `sinceSeconds` is relative seconds and is
  not used (it would ignore the anchor). There is no "until": the end of the window is applied
  locally.
- No list endpoint filters by time: `ts` predicates on events are applied locally after listing.
- Quantities are Kubernetes quantity strings (`250m`, `64Mi`, `9559630n`); the metrics tables
  add `cpu_cores` and `memory_bytes`.

## Secrets and credentials

- `kubernetes_secrets` maps only key names (`entries` over `data`) and decoded sizes; values and
  annotations (the kubectl `last-applied-configuration` annotation embeds the whole Secret) are
  never mapped.
- Every table drops the `kubectl.kubernetes.io/last-applied-configuration` annotation (it embeds
  env values); ConfigMaps/Secrets expose annotation names only.
- Container env: only references (`valueFrom`, `envFrom`) in the pods / workload tables; literal
  values only in `kubernetes_container_env`, masked by `redact_value` (URL userinfo passwords,
  `password=`/`token=` pairs, PEM/JWT/AWS/GitHub/Slack tokens, whole value for secret-looking
  names). This matters in the Arena: product-catalog's `DB_CONNECTION_STRING` embeds the
  database password in direct-deploy mode.

## Pitfalls (summary)

- `kubernetes_pods` rows are per container; count `DISTINCT pod`.
- The cause of a CrashLoopBackOff / OOMKilled is in `last_termination_reason` / `last_exit_code`
  (137 = SIGKILL/OOMKilled) and the previous instance's log (`previous = true`).
- Admission failures (ResourceQuota requiring `requests.cpu`, a `failurePolicy: Fail` webhook
  whose Service is missing, LimitRange max) create NO pod: look for `FailedCreate` events on the
  ReplicaSet/Job and `kubernetes_replicasets.failure_message`.
- `CreateContainerConfigError` names the missing ConfigMap/Secret key in `waiting_message`;
  check the key with `kubernetes_configmaps.keys`.
- Pending pods: `unschedulable_message` (PVC unbound / volume node affinity conflict /
  insufficient resources). A PVC naming a StorageClass missing from `kubernetes_storageclasses`
  never binds; a local PV whose `node_affinity` matches no node label blocks the pod while the
  claim is Bound.
- NetworkPolicy denials do not change pod readiness or endpoints; `denies_all_ingress` flags
  policies without allow rules.
- A program's own log line ("connection refused", "gateway timeout") is not proof of a network
  fault; correlate with exit codes, events and the callee's state.
- Endpoint slices: a selector matching no ready pod gives a slice with no endpoints (row with
  null address); a missing Service gives no slice.

## Arena coverage (Edge Delta Arena faults -> tables)

| Fault | Evidence |
|---|---|
| crashloop, oom, probe-fail, image-pull, missing-config-key, shm-exhaustion, slow-leak | `kubernetes_pods` (reason, last termination, exit code, probes, volumes), `kubernetes_pod_logs previous`, `kubernetes_events`, `kubernetes_container_env` (SCRATCH_MIB), `kubernetes_workload_containers` (/dev/shm, emptyDir sizeLimit, limits), `kubernetes_pod_metrics` |
| quota-trap, admission-webhook-outage | `kubernetes_events` FailedCreate, `kubernetes_replicasets.failure_message`, `kubernetes_resourcequotas`, `kubernetes_workload_containers.cpu_request`, `kubernetes_validating_webhooks` + `kubernetes_services` / `kubernetes_endpointslices` |
| netpol-isolation | `kubernetes_networkpolicies` (`denies_all_ingress`), `kubernetes_endpointslices` (still ready) |
| pending-volume-claim, volume-affinity-conflict | `kubernetes_persistentvolumeclaims`, `kubernetes_storageclasses`, `kubernetes_persistentvolumes.node_affinity`, `kubernetes_nodes.labels`, `kubernetes_pods.unschedulable_message` |
| pg-lock-hold, stale-db-credentials, redis-pressure, multi-fault | `kubernetes_jobs` (who ran at the onset), `kubernetes_container_env` (inline credential, redacted), `kubernetes_secrets` key names |
| cart-failure, payment-failure, traffic-flood | `kubernetes_configmaps` (`flagd-config` data, `last_modified`, `last_manager`) |
