"""Python fetcher for the Kubernetes source (manifest: sources/manifests/kubernetes.yaml).

``container_env`` backs ``kubernetes_container_env``: one row per environment variable (``env``)
or ``envFrom`` source of every container / init container of the selected workloads, with literal
values REDACTED when they look like credentials.

Why Python: the declarative engine has no way to rewrite part of a value (no regex replace /
redaction transform) and no per-element conditional mapping of a list. Literal env values carry
real credentials in practice - the Arena shop's product-catalog has
``DB_CONNECTION_STRING=postgres://shop_user:<password>@postgres...`` - and an investigation must be
able to say "the connection string embeds a stale password" without ever printing the password.
The other Kubernetes tables therefore only expose env *references* (``valueFrom`` / ``envFrom``);
this table is the one place literal values appear, and only after ``redact_value``.

Requests (official API reference, v1.37):
    GET /api/v1[/namespaces/{ns}]/pods                      PodList             spec
    GET /apis/apps/v1[/namespaces/{ns}]/deployments         DeploymentList      spec.template.spec
    GET /apis/apps/v1[/namespaces/{ns}]/statefulsets        StatefulSetList     spec.template.spec
    GET /apis/apps/v1[/namespaces/{ns}]/daemonsets          DaemonSetList       spec.template.spec
    GET /apis/apps/v1[/namespaces/{ns}]/replicasets         ReplicaSetList      spec.template.spec
    GET /apis/batch/v1[/namespaces/{ns}]/jobs               JobList             spec.template.spec
    GET /apis/batch/v1[/namespaces/{ns}]/cronjobs           CronJobList         spec.jobTemplate.spec.template.spec
with ``limit=500`` chunks followed through ``metadata.continue`` (at most MAX_PAGES pages) and an
optional ``fieldSelector=metadata.name=<workload>`` / ``labelSelector``.

Redaction (``redact_value``):
- URL userinfo passwords are masked in place: ``postgres://user:***@host:5432/db`` (host, port,
  database and user stay visible - they are what an investigation compares);
- ``password=...`` / ``pwd=...`` / ``secret=...`` / ``token=...`` / ``apikey=...`` pairs inside
  connection strings are masked;
- PEM blocks, JWTs, AWS access keys, GitHub / Slack / Stripe-style tokens are masked;
- when the variable NAME looks sensitive (PASSWORD, SECRET, TOKEN, API_KEY, CREDENTIAL, PRIVATE,
  ...) and nothing structural was masked, the whole value is replaced by ``<redacted: N chars>``,
  except plain booleans and short integers (``TOKEN_TTL=3600``).
``redacted`` is true whenever anything was masked. Secret VALUES are never read: ``secretKeyRef`` /
``secretRef`` rows carry only the Secret name and key.
"""

from __future__ import annotations

import re
from typing import Any

from omniops.engine import http, transform
from omniops.engine.engine import FetchOutcome
from omniops.engine.http import FetchError, RequestLog, SourceRuntime
from omniops.engine.manifest import TableSpec

MAX_PAGES = 10
PAGE_SIZE = 500

# kind (plural, lowercase) -> (API prefix, resource, path to the pod spec inside an item)
KINDS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "pods": ("/api/v1", "pods", ("spec",)),
    "deployments": ("/apis/apps/v1", "deployments", ("spec", "template", "spec")),
    "statefulsets": ("/apis/apps/v1", "statefulsets", ("spec", "template", "spec")),
    "daemonsets": ("/apis/apps/v1", "daemonsets", ("spec", "template", "spec")),
    "replicasets": ("/apis/apps/v1", "replicasets", ("spec", "template", "spec")),
    "jobs": ("/apis/batch/v1", "jobs", ("spec", "template", "spec")),
    "cronjobs": ("/apis/batch/v1", "cronjobs", ("spec", "jobTemplate", "spec", "template", "spec")),
}
_KIND_ALIASES = {
    "pod": "pods", "deployment": "deployments", "deploy": "deployments", "statefulset": "statefulsets",
    "sts": "statefulsets", "daemonset": "daemonsets", "ds": "daemonsets", "replicaset": "replicasets",
    "rs": "replicasets", "job": "jobs", "cronjob": "cronjobs", "cj": "cronjobs",
}
_KIND_NAMES = {
    "pods": "Pod", "deployments": "Deployment", "statefulsets": "StatefulSet", "daemonsets": "DaemonSet",
    "replicasets": "ReplicaSet", "jobs": "Job", "cronjobs": "CronJob",
}

_SENSITIVE_NAME = re.compile(
    r"(PASS(WORD|WD)?|PWD|SECRET|TOKEN|API_?KEY|ACCESS_?KEY|PRIVATE|CREDENTIAL|CREDS?\b|AUTH|SESSION|"
    r"COOKIE|SIGNATURE|SALT|DSN|CONN(ECTION)?_?STR(ING)?|DATABASE_URL|DB_URL|_KEY$|^KEY$)", re.I)
_URL_USERINFO = re.compile(r"(?P<pre>[A-Za-z][A-Za-z0-9+.\-]*://[^:/@\s]*:)(?P<pw>[^@/\s]+)(?P<at>@)")
_KV_SECRET = re.compile(r"(?i)(?P<k>\b(?:password|passwd|pwd|secret|token|api_?key|access_?key)\s*[=:]\s*)"
                        r"(?P<v>\"[^\"]*\"|'[^']*'|[^\s;&,]+)")
_TOKENS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),  # JWT
    re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),                                      # AWS access key id
    re.compile(r"\b(ghp|gho|ghs|ghu|github_pat)_[A-Za-z0-9_]{20,}\b"),               # GitHub
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"),                                # Slack
    re.compile(r"\b(sk|rk)_(live|test)_[A-Za-z0-9]{16,}\b"),                         # Stripe-style
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b"),                                    # GitLab
]
_HARMLESS = re.compile(r"^(true|false|yes|no|on|off|enabled|disabled|\d{1,6})$", re.I)
MASK = "***"


def redact_value(name: str, value: Any) -> tuple[Any, bool]:
    """Return (value with credentials masked, whether anything was masked)."""
    if value is None:
        return None, False
    s = str(value)
    out = _URL_USERINFO.sub(lambda m: m.group("pre") + MASK + m.group("at"), s)
    out = _KV_SECRET.sub(lambda m: m.group("k") + MASK, out)
    for rx in _TOKENS:
        out = rx.sub(MASK, out)
    structural = out != s
    if _SENSITIVE_NAME.search(name or "") and not structural and s and not _HARMLESS.match(s.strip()):
        return f"<redacted: {len(s)} chars>", True
    return out, structural


def _dig(obj: Any, path: tuple[str, ...]) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _ref(value_from: dict[str, Any]) -> tuple[str | None, str | None, str | None, Any]:
    """(value_from kind, referenced object name, key / field, optional) of an EnvVarSource."""
    for kind in ("configMapKeyRef", "secretKeyRef", "fileKeyRef"):
        ref = value_from.get(kind)
        if isinstance(ref, dict):
            return kind, ref.get("name") or ref.get("volumeName"), ref.get("key") or ref.get("path"), ref.get("optional")
    ref = value_from.get("fieldRef")
    if isinstance(ref, dict):
        return "fieldRef", None, ref.get("fieldPath"), None
    ref = value_from.get("resourceFieldRef")
    if isinstance(ref, dict):
        return "resourceFieldRef", ref.get("containerName"), ref.get("resource"), None
    kinds = [k for k in value_from if k]
    return (kinds[0] if kinds else None), None, None, None


def env_rows(kind: str, item: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows for one list item (Pod / Deployment / ...). ``kind`` is the plural lowercase kind."""
    meta = item.get("metadata") or {}
    pod_spec = _dig(item, KINDS[kind][2]) or {}
    rows: list[dict[str, Any]] = []
    base = {"namespace": meta.get("namespace"), "workload_kind": _KIND_NAMES[kind], "workload": meta.get("name")}
    for init, key in ((False, "containers"), (True, "initContainers")):
        for c in pod_spec.get(key) or []:
            if not isinstance(c, dict):
                continue
            cbase = {**base, "container": c.get("name"), "init_container": init, "image": c.get("image")}
            for pos, e in enumerate(c.get("env") or []):
                if not isinstance(e, dict):
                    continue
                name = e.get("name")
                row = {**cbase, "position": pos, "source": "env", "env_name": name, "value": None,
                       "redacted": False, "value_from": None, "ref_name": None, "ref_key": None,
                       "optional": None, "prefix": None}
                vf = e.get("valueFrom")
                if isinstance(vf, dict):
                    row["value_from"], row["ref_name"], row["ref_key"], row["optional"] = _ref(vf)
                else:
                    row["value"], row["redacted"] = redact_value(name or "", e.get("value", ""))
                rows.append(row)
            for pos, ef in enumerate(c.get("envFrom") or []):
                if not isinstance(ef, dict):
                    continue
                vf_kind, ref = next(((k, ef[k]) for k in ("configMapRef", "secretRef") if isinstance(ef.get(k), dict)),
                                    (None, {}))
                rows.append({**cbase, "position": pos, "source": "envFrom", "env_name": None, "value": None,
                             "redacted": False, "value_from": vf_kind, "ref_name": ref.get("name"),
                             "ref_key": None, "optional": ref.get("optional"), "prefix": ef.get("prefix")})
    return rows


def _norm_kind(v: Any) -> str:
    k = str(v or "deployments").strip().lower()
    k = _KIND_ALIASES.get(k, k)
    if k not in KINDS:
        raise FetchError(f"kubernetes_container_env: kind must be one of {sorted(KINDS)}, not {v!r}")
    return k


def _selector(filters: dict[str, Any]) -> str:
    parts = []
    ns, ns_op = filters.get("namespace"), filters.get("namespace__op")
    if ns and ns_op == "!=":
        parts.append(f"metadata.namespace!={ns}")
    w, w_op = filters.get("workload"), filters.get("workload__op")
    if w and not w_op and not isinstance(w, list):
        parts.append(f"metadata.name={w}")
    elif w and w_op == "!=":
        parts.append(f"metadata.name!={w}")
    if filters.get("field_selector"):
        parts.append(str(filters["field_selector"]))
    return ",".join(parts)


async def container_env(rt: SourceRuntime, table: TableSpec, ctx: dict[str, Any],
                        logs: list[RequestLog]) -> FetchOutcome:
    """Fetch the selected workloads and return redacted env rows for ``kubernetes_container_env``.

    Stopping at MAX_PAGES while the API still has a ``continue`` token, or cutting at max_rows, is
    reported as truncation (a silent cap would read as "no other workload sets this variable").
    """
    filters = ctx.get("filters") or {}
    kind = _norm_kind(filters.get("kind"))
    prefix, resource, _ = KINDS[kind]
    await rt.ensure_client()  # resolves the kubeconfig server into rt.base_url
    ns = filters.get("namespace")
    scoped = ns and not filters.get("namespace__op") and not isinstance(ns, list)
    url = f"{rt.base_url.rstrip('/')}{prefix}{'/namespaces/' + str(ns) if scoped else ''}/{resource}"
    params: dict[str, Any] = {"limit": PAGE_SIZE}
    sel = _selector(filters)
    if sel:
        params["fieldSelector"] = sel
    if filters.get("label_selector"):
        params["labelSelector"] = str(filters["label_selector"])
    rows: list[dict[str, Any]] = []
    rules = list(rt.manifest.errors) + list(table.response.errors)
    cont = None
    for page in range(1, MAX_PAGES + 1):
        rr = http.RenderedRequest("GET", url, dict(params), {}, None, "json", "json", 60.0)
        resp = await http.send(rt, rr, table=table.name, logs=logs, page=page)
        body = resp.body if isinstance(resp.body, dict) else {}
        transform.check_errors(body, rules)
        for item in body.get("items") or []:
            if isinstance(item, dict):
                rows.extend(env_rows(kind, item))
        if logs:
            logs[-1].rows = len(rows)
        cont = (body.get("metadata") or {}).get("continue")
        if not cont or len(rows) >= table.max_rows:
            break
        params["continue"] = cont
    for r in rows:  # filter_only columns echo the request's filter values
        for k in ("kind", "label_selector", "field_selector"):
            v = filters.get(k)
            r.setdefault(k, v if not isinstance(v, list) else None)
    notes: list[str] = []
    if len(rows) > table.max_rows:
        notes.append(f"env rows cut at max_rows={table.max_rows} of {len(rows)} fetched")
    if cont:
        notes.append(f"stopped listing {resource} after {page} page(s) of {PAGE_SIZE}; more workloads exist "
                     "(narrow namespace / workload / label_selector)")
    return FetchOutcome(rows[: table.max_rows], notes, logs, [], bool(notes))
