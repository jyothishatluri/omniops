#!/usr/bin/env python3
"""Stdlib-only helper for the deploy/arena shell scripts (Python 3.10+; no OmniOps imports).

Every subcommand that touches a secret reads it from a file or stdin and writes it to a mode-600
file or to stdout for ``kubectl apply -f -``; status lines go to stderr and never contain the
secret itself.

Lessons encoded (OpsIntelligence retrospective):
- 4.1 verify the effect, not the config: ``selftest`` queries Prometheus, Loki, Tempo and Grafana
  for the series, streams, traces and rules the alert rules and OmniOps depend on, and reports every
  missing one loudly instead of trusting that the manifests applied.
- D11 / Part 8 #34: OmniOps gets read-only identities (Grafana Viewer, Argo CD role:readonly minus the
  Arena's control application, a read-only Kubernetes ServiceAccount behind ``kube-proxy``).
- 3.16: ground truth stays judge-only. The Arena keeps the active scenario id in ConfigMap
  benchmark-control/suite-state; ``kube-proxy`` hides that namespace from OmniOps' Kubernetes view.
- 4.6: credentials never in URLs or command lines.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

SA_NAME = "omniops-arena"
PG_HOST = "postgres.datastore.svc.cluster.local:5432"
PG_DATABASE = "shop_db"
PG_USER = "monitoring_user"
FOLDER = "Platform alerts"


def err(msg: str) -> None:
    print(msg, file=sys.stderr)


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(path, 0o600)


def http(method: str, url: str, *, body: Any = None, headers: dict[str, str] | None = None,
         timeout: float = 20.0) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "Accept": "application/json",
                                          **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, raw.decode(errors="replace")[:300]


def basic(user: str, password: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token}


def secret_manifest(name: str, namespace: str, data: dict[str, str]) -> dict[str, Any]:
    return {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
            "metadata": {"name": name, "namespace": namespace, "labels": {"app.kubernetes.io/part-of": "omniops-arena"}},
            "stringData": data}


# ---------------------------------------------------------------------------------------------
# Grafana notifications (contact points + policy tree), rendered into a Secret
# ---------------------------------------------------------------------------------------------


def cmd_notifications(a: argparse.Namespace) -> int:
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    token = os.environ.get("GRAFANA_SLACK_TOKEN", "").strip()
    channel = (os.environ.get("GRAFANA_SLACK_CHANNEL") or os.environ.get("OMNIOPS_ARENA_CHANNEL") or "").strip()
    if token and token == os.environ.get("SLACK_BOT_TOKEN", "").strip():
        err("GRAFANA_SLACK_TOKEN must not be OmniOps' own bot token: the bot ignores its own posts. "
            "Use an incoming webhook (SLACK_WEBHOOK_URL) or a second Slack app.")
        return 2
    receivers: list[dict[str, Any]] = [{
        "orgId": 1, "name": "omniops-bridge",
        "receivers": [{"uid": "omniops-bridge", "type": "webhook", "disableResolveMessage": False,
                       "settings": {"url": a.bridge_url, "httpMethod": "POST"}}]}]
    slack_mode = "none"
    if webhook:
        slack_mode, settings = "webhook", {"url": webhook}
    elif token and channel:
        slack_mode, settings = "token", {"token": token, "recipient": channel}
    elif token:
        err("GRAFANA_SLACK_TOKEN is set but no channel (GRAFANA_SLACK_CHANNEL or OMNIOPS_ARENA_CHANNEL)")
        return 2
    routes: list[dict[str, Any]] = []
    if slack_mode != "none":
        receivers.append({"orgId": 1, "name": "omniops-slack",
                          "receivers": [{"uid": "omniops-slack", "type": "slack", "disableResolveMessage": False,
                                         "settings": settings}]})
        routes.append({"receiver": "omniops-slack", "object_matchers": [["omniops", "=", "arena"]],
                       "continue": True})
    routes.append({"receiver": "omniops-bridge", "object_matchers": [["omniops", "=", "arena"]]})
    policies = {"apiVersion": 1, "policies": [{
        "orgId": 1, "receiver": "omniops-bridge", "group_by": ["grafana_folder"], "group_wait": "30s",
        "group_interval": "1m", "repeat_interval": "4h", "routes": routes}]}
    contact_points = {"apiVersion": 1, "contactPoints": receivers}
    manifest = secret_manifest("grafana-notifications", a.namespace, {
        "contact-points.yaml": json.dumps(contact_points, indent=2),   # JSON is valid YAML
        "policies.yaml": json.dumps(policies, indent=2)})
    print(json.dumps(manifest))
    if a.mode_file:
        Path(a.mode_file).write_text(slack_mode + "\n")
    where = {"webhook": "incoming webhook", "token": f"bot token -> channel {channel}",
             "none": "not configured (set SLACK_WEBHOOK_URL, or GRAFANA_SLACK_TOKEN + GRAFANA_SLACK_CHANNEL)"}
    err(f"Grafana contact points: webhook bridge {a.bridge_url}; Slack: {where[slack_mode]}")
    return 0


# ---------------------------------------------------------------------------------------------
# Read-only service identities for OmniOps
# ---------------------------------------------------------------------------------------------


def cmd_grafana_token(a: argparse.Namespace) -> int:
    base, out = a.url.rstrip("/"), Path(a.out)
    if out.is_file():
        status, _ = http("GET", f"{base}/api/search?limit=1", headers=bearer(out.read_text().strip()))
        if status == 200:
            err("Grafana: existing OmniOps service-account token is valid")
            return 0
        err(f"Grafana: stored token rejected (HTTP {status}); creating a new one")
    auth = basic("admin", Path(a.password_file).read_text().strip())
    status, found = http("GET", f"{base}/api/serviceaccounts/search?query={SA_NAME}", headers=auth)
    if status != 200:
        err(f"Grafana: service-account search failed (HTTP {status}): {found}")
        return 1
    match = [s for s in (found or {}).get("serviceAccounts", []) if s.get("name") == SA_NAME]
    if match:
        sa_id = match[0]["id"]
    else:
        status, created = http("POST", f"{base}/api/serviceaccounts", headers=auth,
                               body={"name": SA_NAME, "role": "Viewer", "isDisabled": False})
        if status not in (200, 201):
            err(f"Grafana: creating the service account failed (HTTP {status}): {created}")
            return 1
        sa_id = created["id"]
    status, tok = http("POST", f"{base}/api/serviceaccounts/{sa_id}/tokens", headers=auth,
                       body={"name": f"{SA_NAME}-{int(time.time())}"})
    if status not in (200, 201) or not (tok or {}).get("key"):
        err(f"Grafana: creating the token failed (HTTP {status})")
        return 1
    write_private(out, tok["key"] + "\n")
    err(f"Grafana: Viewer service-account token written to {out}")
    return 0


def cmd_argocd_token(a: argparse.Namespace) -> int:
    base, out = a.url.rstrip("/"), Path(a.out)
    if out.is_file():
        status, _ = http("GET", f"{base}/api/v1/applications", headers=bearer(out.read_text().strip()))
        if status == 200:
            err("Argo CD: existing read-only token is valid")
            return 0
    password = sys.stdin.read().strip()
    if not password:
        err("Argo CD: no admin password on stdin")
        return 1
    status, session = http("POST", f"{base}/api/v1/session", body={"username": "admin", "password": password})
    if status != 200 or not (session or {}).get("token"):
        err(f"Argo CD: admin login failed (HTTP {status})")
        return 1
    status, tok = http("POST", f"{base}/api/v1/account/{a.account}/token", headers=bearer(session["token"]),
                       body={"name": a.account, "id": f"omniops-arena-{int(time.time())}", "expiresIn": 0})
    if status != 200 or not (tok or {}).get("token"):
        err(f"Argo CD: token for account {a.account!r} failed (HTTP {status}): is accounts.{a.account}: apiKey set?")
        return 1
    write_private(out, tok["token"] + "\n")
    status, _ = http("GET", f"{base}/api/v1/applications", headers=bearer(tok["token"]))
    err(f"Argo CD: read-only token for '{a.account}' written to {out} (list applications: HTTP {status})")
    return 0


# ---------------------------------------------------------------------------------------------
# OmniOps' Kubernetes view: a read-only ServiceAccount behind a namespace-filtering proxy
# ---------------------------------------------------------------------------------------------
#
# The Arena writes the active scenario id into ConfigMap benchmark-control/suite-state at fault time,
# so any cluster-wide reader - OmniOps' kubernetes_configmaps table and its change feed - would read
# the answer. RBAC cannot carve one namespace out of a cluster-wide list (and OmniOps lists configmaps
# cluster-wide for the change feed), so OmniOps gets no Kubernetes credential at all: its kubeconfig
# points at this proxy, which holds the read-only token, forwards GETs only, refuses every path in a
# hidden namespace and drops hidden-namespace objects from every list it returns.

STREAMING_PARAMS = ("watch", "follow")
BLOCKED_SUBRESOURCES = frozenset({"exec", "attach", "portforward", "proxy"})


def _resource_parts(path: str) -> list[str]:
    """Path segments after /api/<v> or /apis/<group>/<v>: [namespaces, <ns>,] <resource>[, <name>[, <sub>...]]."""
    parts = [x for x in urllib.parse.urlsplit(path).path.split("/") if x]
    if parts[:1] == ["api"]:
        return parts[2:]
    if parts[:1] == ["apis"]:
        return parts[3:]
    return []


def hidden_namespace(path: str, hidden: frozenset[str]) -> str | None:
    """The hidden namespace a request path addresses (``.../namespaces/<ns>[/...]``), if any."""
    rest = _resource_parts(path)
    return rest[1] if rest[:1] == ["namespaces"] and len(rest) > 1 and rest[1] in hidden else None


def subresource(path: str) -> str | None:
    rest = _resource_parts(path)
    if rest[:1] == ["namespaces"] and len(rest) > 2:
        rest = rest[2:]
    return rest[2] if len(rest) > 2 else None


def refusal(method: str, path: str, hidden: frozenset[str]) -> tuple[int, str] | None:
    """(HTTP status, message) when the proxy must not forward this request."""
    if method != "GET":
        return 405, f"{method} refused: OmniOps' Kubernetes view is read-only"
    url = urllib.parse.urlsplit(path)
    query = urllib.parse.parse_qs(url.query)
    if (any(query.get(k, [""])[0].lower() in ("true", "1") for k in STREAMING_PARAMS)
            or _resource_parts(path)[:1] == ["watch"]):            # legacy /api/v1/watch/... paths
        return 400, "watch/follow streams are not forwarded: list or read logs without them"
    if subresource(path) in BLOCKED_SUBRESOURCES:
        return 403, "exec, attach, port-forward and proxy subresources are not part of OmniOps' view"
    ns = hidden_namespace(path, hidden)
    if ns:
        return 403, f"namespace {ns!r} is outside OmniOps' view"
    return None


def drop_hidden(doc: Any, path: str, hidden: frozenset[str]) -> int:
    """Remove hidden-namespace objects from a list or Table response in place; returns how many."""
    if not isinstance(doc, dict):
        return 0
    namespaces_list = urllib.parse.urlsplit(path).path.rstrip("/") == "/api/v1/namespaces"

    def hide(meta: Any) -> bool:
        meta = meta if isinstance(meta, dict) else {}
        return meta.get("namespace") in hidden or (namespaces_list and meta.get("name") in hidden)

    key, get_meta = "items", lambda item: (item or {}).get("metadata")
    if doc.get("kind") == "Table":
        key, get_meta = "rows", lambda row: ((row or {}).get("object") or {}).get("metadata")
    items = doc.get(key)
    if not isinstance(items, list):
        return 0
    kept = [x for x in items if not hide(get_meta(x))]
    doc[key] = kept
    return len(items) - len(kept)


def status_body(code: int, message: str) -> bytes:
    reason = {400: "BadRequest", 403: "Forbidden", 405: "MethodNotAllowed", 502: "ServiceUnavailable"}.get(code, "")
    return json.dumps({"kind": "Status", "apiVersion": "v1", "metadata": {}, "status": "Failure",
                       "message": message, "reason": reason, "code": code}).encode()


def kube_proxy_server(upstream: dict[str, str], hidden: frozenset[str], port: int,
                      host: str = "127.0.0.1") -> ThreadingHTTPServer:
    server_url, token = upstream["server"].rstrip("/"), upstream["token"]
    context = ssl.create_default_context(cadata=upstream["ca"]) if upstream.get("ca") else None

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.forward()

        do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_GET   # refused in forward()

        def reply(self, code: int, ctype: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def forward(self) -> None:
            refused = refusal(self.command, self.path, hidden)
            if refused:
                err(f"kube-proxy: refused {self.command} {urllib.parse.urlsplit(self.path).path}: {refused[1]}")
                self.reply(refused[0], "application/json", status_body(*refused))
                return
            accept = self.headers.get("Accept") or "application/json"
            if "protobuf" in accept:                     # the proxy filters JSON only
                accept = "application/json"
            req = urllib.request.Request(server_url + self.path, method="GET",
                                         headers={"Authorization": "Bearer " + token, "Accept": accept,
                                                  "User-Agent": self.headers.get("User-Agent") or "omniops-arena"})
            try:
                with urllib.request.urlopen(req, timeout=60, context=context) as resp:
                    code, ctype, body = resp.status, resp.headers.get("Content-Type", ""), resp.read()
            except urllib.error.HTTPError as exc:
                code, ctype, body = exc.code, exc.headers.get("Content-Type", ""), exc.read()
            except (urllib.error.URLError, OSError) as exc:
                self.reply(502, "application/json", status_body(502, f"API server not reachable: {exc}"))
                return
            if code == 200 and "json" in ctype:
                try:
                    doc = json.loads(body)
                except ValueError:
                    doc = None
                if drop_hidden(doc, self.path, hidden):
                    body = json.dumps(doc).encode()
            self.reply(code, ctype or "application/json", body)

        def log_message(self, format: str, *args: Any) -> None:   # quiet: refusals are logged
            return None

    return ThreadingHTTPServer((host, port), Handler)


def cmd_kube_upstream(a: argparse.Namespace) -> int:
    """The ServiceAccount token Secret (JSON on stdin) -> the proxy's upstream file (mode 600)."""
    data = (json.loads(sys.stdin.read() or "{}").get("data") or {})
    if not data.get("token"):
        err("Kubernetes: the read-only token is not issued yet (the token controller fills the Secret)")
        return 1
    ca = base64.b64decode(data["ca.crt"]).decode() if data.get("ca.crt") else ""
    write_private(Path(a.out), json.dumps({"server": a.server, "token": base64.b64decode(data["token"]).decode(),
                                           "ca": ca}) + "\n")
    err(f"Kubernetes: read-only ServiceAccount token written to {a.out}")
    return 0


def cmd_kubeconfig(a: argparse.Namespace) -> int:
    """OmniOps' kubeconfig: the proxy and no credential (JSON is valid YAML for the engine and kubectl)."""
    name = a.context
    config = {"apiVersion": "v1", "kind": "Config", "preferences": {},
              "clusters": [{"name": name, "cluster": {"server": a.server}}],
              "users": [{"name": name, "user": {}}],
              "contexts": [{"name": name, "context": {"cluster": name, "user": name}}]}
    write_private(Path(a.out), json.dumps(config, indent=2) + "\n")
    return 0


def cmd_kube_proxy(a: argparse.Namespace) -> int:
    upstream = json.loads(Path(a.upstream).read_text())
    hidden = frozenset(n.strip() for n in a.hide_namespaces.split(",") if n.strip())
    server = kube_proxy_server(upstream, hidden, a.port)
    err(f"kube-proxy: OmniOps' Kubernetes view on http://127.0.0.1:{a.port} -> {upstream['server']} "
        f"(GET only; hidden namespaces: {', '.join(sorted(hidden)) or 'none'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


# ---------------------------------------------------------------------------------------------
# The Arena shop's collector: export to the LGTM stack instead of the debug exporter
# ---------------------------------------------------------------------------------------------


def cmd_collector_patch(a: argparse.Namespace) -> int:
    print(json.dumps({"data": {"collector.yaml": Path(a.config).read_text()}}))
    return 0


def cmd_gitops_collector(a: argparse.Namespace) -> int:
    path = Path(a.resources)
    doc = json.loads(path.read_text())
    hits = [o for o in doc.get("items", []) if o.get("kind") == "ConfigMap"
            and o.get("metadata", {}).get("name") == "otel-collector"]
    if len(hits) != 1:
        err(f"expected exactly one otel-collector ConfigMap in {path}, found {len(hits)}")
        return 1
    hits[0]["data"] = {"collector.yaml": Path(a.config).read_text()}
    path.write_text(json.dumps(doc, indent=2) + "\n")
    err(f"collector exporters pointed at the LGTM stack in {path}")
    return 0


def cmd_postgres_secret(a: argparse.Namespace) -> int:
    password = sys.stdin.read().strip()
    if not password:
        err("no monitoring password on stdin")
        return 1
    dsn = (f"postgresql://{PG_USER}:{urllib.parse.quote(password, safe='')}@{PG_HOST}/{PG_DATABASE}"
           "?sslmode=disable")
    print(json.dumps(secret_manifest("alloy-postgres", a.namespace, {"dsn": dsn})))
    return 0


# ---------------------------------------------------------------------------------------------
# Arena run configurations (setup-time; the benchmark runner writes its own per run)
# ---------------------------------------------------------------------------------------------


def cmd_run_config(a: argparse.Namespace) -> int:
    run: dict[str, Any] = {"run_id": a.run_id, "product": "OmniOps", "suite": "full", "scenario": "oom",
                           "output_dir": a.output_dir, "registry": "fixture.local", "tag": "v1", "context": a.context}
    if a.github_owner:
        def repo(name: str, path: str) -> dict[str, str]:
            return {"url": f"https://github.com/{a.github_owner}/{name}.git", "checkout": f"gitops/{name}",
                    "revision": "main", "path": path}
        run["gitops"] = {"app_repo": repo(a.app_repo, "arena-apps"), "fault_repo": repo(a.fault_repo, "arena-faults")}
    Path(a.out).write_text(json.dumps(run, indent=2) + "\n")
    return 0


# ---------------------------------------------------------------------------------------------
# Self-test: verify the effect, not the config
# ---------------------------------------------------------------------------------------------

PROM_CHECKS = (
    ("kube-state-metrics", "kube_pod_info", True),
    ("cAdvisor container metrics", "container_memory_working_set_bytes", True),
    ("shop traces -> span metrics", "traces_span_metrics_calls_total", True),
    ("shop OTLP metrics", "target_info", False),
    ("Redis exporter", "redis_up", True),
    ("PostgreSQL exporter", "pg_up", False),
)


def cmd_selftest(a: argparse.Namespace) -> int:
    failures = 0
    rows: list[tuple[str, str, str]] = []
    for label, metric, required in PROM_CHECKS:
        status, body = http("GET", f"{a.prometheus}/api/v1/query?" + urllib.parse.urlencode({"query": f"count({metric})"}))
        n = int(float(body["data"]["result"][0]["value"][1])) if status == 200 and body["data"]["result"] else 0
        ok = n > 0
        failures += (not ok) and required
        rows.append(("OK" if ok else ("MISSING" if required else "absent"), label, f"{metric}: {n} series"))
    status, body = http("GET", f"{a.loki}/loki/api/v1/label/namespace/values")
    namespaces = (body or {}).get("data", []) if status == 200 else []
    ok = "shop" in namespaces
    failures += not ok
    rows.append(("OK" if ok else "MISSING", "pod logs in Loki", f"namespaces: {', '.join(namespaces) or 'none'}"))
    status, body = http("GET", f"{a.loki}/loki/api/v1/label/job/values")
    jobs = (body or {}).get("data", []) if status == 200 else []
    ok = any("eventhandler" in j for j in jobs)
    failures += not ok
    rows.append(("OK" if ok else "MISSING", "Kubernetes events in Loki", f"{len(jobs)} jobs"))
    end = int(time.time())
    status, body = http("GET", f"{a.tempo}/api/search?" + urllib.parse.urlencode({"limit": 5, "start": end - 900, "end": end}))
    traces = len((body or {}).get("traces", []) or []) if status == 200 else 0
    failures += traces == 0
    rows.append(("OK" if traces else "MISSING", "traces in Tempo", f"{traces} in the last 15 min"))
    token = Path(a.token_file).read_text().strip() if a.token_file and Path(a.token_file).is_file() else ""
    status, body = http("GET", f"{a.grafana}/api/prometheus/grafana/api/v1/rules",
                        headers=bearer(token) if token else None)
    rules = [r for g in ((body or {}).get("data", {}).get("groups", []) if status == 200 else [])
             if g.get("file") == FOLDER for r in g.get("rules", [])]
    ok = len(rules) >= a.min_rules
    failures += not ok
    rows.append(("OK" if ok else "MISSING", "Grafana alert rules", f"{len(rules)} in '{FOLDER}' (HTTP {status})"))
    for state, label, detail in rows:
        print(f"  {state:<8} {label:<30} {detail}")
    if failures:
        err(f"{failures} required signal(s) missing: the alert rules and OmniOps' queries depend on them. "
            "Data needs a few minutes after deploy; rerun 'deploy/arena/observability.sh selftest'.")
    return 1 if failures else 0


def cmd_wait_http(a: argparse.Namespace) -> int:
    deadline = time.monotonic() + a.timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(a.url, timeout=5) as resp:
                if resp.status < 500:
                    return 0
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(1)
    err(f"{a.url} did not answer within {a.timeout:.0f} s")
    return 1


def cmd_gen_password(a: argparse.Namespace) -> int:
    out = Path(a.out)
    if not out.is_file():
        write_private(out, secrets.token_urlsafe(24) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("notifications", help="Grafana contact points + policies Secret (JSON on stdout)")
    s.add_argument("--namespace", default="observability")
    s.add_argument("--bridge-url", required=True)
    s.add_argument("--mode-file", help="write the Slack mode (webhook|token|none) here")
    s = sub.add_parser("grafana-token", help="create/verify the OmniOps Viewer service-account token")
    s.add_argument("--url", required=True)
    s.add_argument("--password-file", required=True)
    s.add_argument("--out", required=True)
    s = sub.add_parser("argocd-token", help="create/verify a read-only Argo CD token (admin password on stdin)")
    s.add_argument("--url", required=True)
    s.add_argument("--account", default="omniops")
    s.add_argument("--out", required=True)
    s = sub.add_parser("kube-upstream", help="the read-only ServiceAccount token Secret (JSON on stdin) -> proxy upstream file")
    s.add_argument("--server", required=True, help="API server URL (the admin kubeconfig's cluster server)")
    s.add_argument("--out", required=True)
    s = sub.add_parser("kubeconfig", help="OmniOps' credential-less kubeconfig pointing at kube-proxy")
    s.add_argument("--server", required=True)
    s.add_argument("--context", default=SA_NAME)
    s.add_argument("--out", required=True)
    s = sub.add_parser("kube-proxy", help="serve OmniOps' read-only, namespace-filtering Kubernetes view")
    s.add_argument("--upstream", required=True)
    s.add_argument("--port", type=int, default=8001)
    s.add_argument("--hide-namespaces", default="benchmark-control", help="comma-separated")
    s = sub.add_parser("collector-patch", help="merge patch for the shop's otel-collector ConfigMap")
    s.add_argument("--config", required=True)
    s = sub.add_parser("gitops-collector", help="rewrite the exported telemetry component's collector config")
    s.add_argument("--resources", required=True)
    s.add_argument("--config", required=True)
    s = sub.add_parser("postgres-secret", help="Alloy's Postgres DSN Secret (monitoring password on stdin)")
    s.add_argument("--namespace", default="observability")
    s = sub.add_parser("run-config", help="write an Arena run configuration for setup commands")
    s.add_argument("--out", required=True)
    s.add_argument("--context", required=True)
    s.add_argument("--run-id", default="omniops-setup")
    s.add_argument("--output-dir", default="setup-runs")
    s.add_argument("--github-owner", default="")
    s.add_argument("--app-repo", default="service-manifests")
    s.add_argument("--fault-repo", default="platform-config")
    s = sub.add_parser("selftest", help="check that the signals the alert rules need are flowing")
    s.add_argument("--prometheus", required=True)
    s.add_argument("--loki", required=True)
    s.add_argument("--tempo", required=True)
    s.add_argument("--grafana", required=True)
    s.add_argument("--token-file")
    s.add_argument("--min-rules", type=int, default=10)
    s = sub.add_parser("wait-http", help="wait until a URL answers")
    s.add_argument("url")
    s.add_argument("--timeout", type=float, default=60)
    s = sub.add_parser("gen-password", help="create a random password file once (mode 600)")
    s.add_argument("--out", required=True)
    a = p.parse_args(argv)
    handlers = {"notifications": cmd_notifications, "grafana-token": cmd_grafana_token,
                "argocd-token": cmd_argocd_token, "kube-upstream": cmd_kube_upstream,
                "kubeconfig": cmd_kubeconfig, "kube-proxy": cmd_kube_proxy, "collector-patch": cmd_collector_patch,
                "gitops-collector": cmd_gitops_collector, "postgres-secret": cmd_postgres_secret,
                "run-config": cmd_run_config, "selftest": cmd_selftest, "wait-http": cmd_wait_http,
                "gen-password": cmd_gen_password}
    return handlers[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
