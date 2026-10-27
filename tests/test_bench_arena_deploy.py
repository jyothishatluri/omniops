"""deploy/arena scripts through their real entry points, offline: prerequisite checks, the Grafana
notification Secret, run configurations and the manifests' contracts. No cluster, Docker or network."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "arena"
SCRIPTS = sorted(DEPLOY.glob("*.sh"))


def test_every_script_parses_with_bash() -> None:
    assert {p.name for p in SCRIPTS} >= {"up.sh", "down.sh", "argocd.sh", "gitops.sh", "port-forward.sh",
                                          "observability.sh", "omniops-env.sh", "lib.sh"}
    for script in SCRIPTS:
        proc = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 0, f"{script.name}: {proc.stderr}"


def fake_tools(tmp: Path, *, server: str, mem_gib: float, arch: str = "x86_64") -> Path:
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    docker = f"""#!/bin/sh
case "$1" in
  version) echo "{server}" ;;
  info) case "$3" in
          *MemTotal*) echo {int(mem_gib * 1024 ** 3)} ;;
          *NCPU*) echo 6 ;;
          *Architecture*) echo {arch} ;;
        esac ;;
esac
"""
    (bin_dir / "docker").write_text(docker)
    for tool in ("kind", "kubectl", "helm"):
        (bin_dir / tool).write_text("#!/bin/sh\nexit 0\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    return bin_dir


def run_check(tmp: Path, bin_dir: Path) -> subprocess.CompletedProcess[str]:
    arena = tmp / "arena"
    (arena / "bench").mkdir(parents=True, exist_ok=True)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp), "ARENA_BIN_DIR": str(bin_dir),
           "ARENA_DIR": str(arena), "ARENA_PYTHON": sys.executable, "ARENA_STATE_DIR": str(tmp / "state")}
    return subprocess.run(["bash", str(DEPLOY / "up.sh"), "--check"], capture_output=True, text=True, env=env,
                          timeout=60, check=False)


def test_up_check_stops_on_the_old_docker_desktop_with_instructions(tmp_path: Path) -> None:
    proc = run_check(tmp_path, fake_tools(tmp_path, server="20.10.17", mem_gib=9.7))
    assert proc.returncode != 0
    assert "Upgrade Docker Desktop" in proc.stderr and "Prerequisites are not met" in proc.stderr
    assert not (tmp_path / "state").exists()                    # a check creates nothing


def test_up_check_passes_and_recommends_10_gb(tmp_path: Path) -> None:
    proc = run_check(tmp_path, fake_tools(tmp_path, server="28.4.0", mem_gib=8.0))
    assert proc.returncode == 0, proc.stderr
    assert "10 GB is recommended" in proc.stderr and "Prerequisites OK" in proc.stdout


def test_up_check_refuses_arm_docker_for_the_full_suite(tmp_path: Path) -> None:
    proc = run_check(tmp_path, fake_tools(tmp_path, server="28.4.0", mem_gib=10, arch="aarch64"))
    assert proc.returncode != 0 and "linux/amd64 only" in proc.stderr


def helper(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith(("SLACK_", "GRAFANA_SLACK", "OMNIOPS_ARENA"))}
    return subprocess.run([sys.executable, str(DEPLOY / "helper.py"), *args], capture_output=True, text=True,
                          env={**base, **(env or {})}, timeout=60, check=False)


def test_notifications_secret_routes_arena_alerts_to_slack_and_the_bridge(tmp_path: Path) -> None:
    hook = "https://hooks.slack.com/services/T000/B000/not-a-real-secret"
    mode = tmp_path / "mode"
    proc = helper("notifications", "--bridge-url", "http://host.docker.internal:8787/grafana", "--mode-file", str(mode),
                  env={"SLACK_WEBHOOK_URL": hook})
    assert proc.returncode == 0, proc.stderr
    assert hook not in proc.stderr and mode.read_text().strip() == "webhook"
    secret = json.loads(proc.stdout)
    assert secret["kind"] == "Secret" and secret["metadata"]["namespace"] == "observability"
    points = yaml.safe_load(secret["stringData"]["contact-points.yaml"])["contactPoints"]
    by_name = {p["name"]: p["receivers"][0] for p in points}
    assert by_name["omniops-slack"]["type"] == "slack" and by_name["omniops-slack"]["settings"]["url"] == hook
    assert by_name["omniops-bridge"]["settings"]["url"] == "http://host.docker.internal:8787/grafana"
    policy = yaml.safe_load(secret["stringData"]["policies.yaml"])["policies"][0]
    assert policy["group_by"] == ["grafana_folder"]
    assert [r["receiver"] for r in policy["routes"]] == ["omniops-slack", "omniops-bridge"]
    assert policy["routes"][0]["continue"] is True
    assert all(r["object_matchers"] == [["omniops", "=", "arena"]] for r in policy["routes"])

    bridge_only = helper("notifications", "--bridge-url", "http://b/grafana")
    assert bridge_only.returncode == 0
    names = [p["name"] for p in yaml.safe_load(json.loads(bridge_only.stdout)["stringData"]["contact-points.yaml"])
             ["contactPoints"]]
    assert names == ["omniops-bridge"] and "not configured" in bridge_only.stderr


def test_notifications_refuse_omniops_own_bot_token() -> None:
    proc = helper("notifications", "--bridge-url", "http://b/grafana",
                  env={"GRAFANA_SLACK_TOKEN": "xoxb-same", "SLACK_BOT_TOKEN": "xoxb-same", "GRAFANA_SLACK_CHANNEL": "C1"})
    assert proc.returncode == 2 and "ignores its own posts" in proc.stderr and "xoxb-same" not in proc.stderr


def test_run_config_is_an_arena_run_file_with_both_public_repos(tmp_path: Path) -> None:
    out = tmp_path / "arena-gitops.json"
    proc = helper("run-config", "--out", str(out), "--context", "kind-incident-bench", "--github-owner", "octo")
    assert proc.returncode == 0, proc.stderr
    run = json.loads(out.read_text())
    assert run["context"] == "kind-incident-bench" and run["suite"] == "full"
    assert run["gitops"]["app_repo"] == {"url": "https://github.com/octo/service-manifests.git",
                                         "checkout": "gitops/service-manifests", "revision": "main", "path": "arena-apps"}
    assert run["gitops"]["fault_repo"]["url"] == "https://github.com/octo/platform-config.git"


def test_gitops_script_prints_publishing_commands_instead_of_running_them() -> None:
    text = (DEPLOY / "gitops.sh").read_text()
    code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    executed = [ln for ln in code if "note " not in ln and "die " not in ln and "warn " not in ln]
    assert any("gh repo create" in ln and "--public" in ln for ln in code)
    assert not [ln for ln in executed if "gh repo create" in ln or "git push" in ln or "git -C" in ln and "push" in ln]
    assert "ls-remote" in text                                   # bootstrap verifies the push happened


def test_stack_contracts_datasource_uids_versions_and_shop_collector() -> None:
    lgtm = list(yaml.safe_load_all((DEPLOY / "observability" / "lgtm.yaml").read_text()))
    image = next(d for d in lgtm if d["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]["image"]
    assert image == "grafana/otel-lgtm:0.36.0"
    lib = (DEPLOY / "lib.sh").read_text()
    assert 'ARGOCD_CHART_VERSION="10.4.0"' in lib and 'CLUSTER_NAME="${ARENA_CLUSTER_NAME:-incident-bench}"' in lib
    collector = yaml.safe_load((DEPLOY / "observability" / "collector.yaml").read_text())
    assert collector["exporters"]["otlp/lgtm"]["endpoint"] == "lgtm.observability.svc.cluster.local:4317"
    assert set(collector["service"]["pipelines"]) == {"traces", "metrics", "logs"}
    assert "spanmetrics" in collector["service"]["pipelines"]["traces"]["exporters"]
    argo = yaml.safe_load((DEPLOY / "argocd" / "values.yaml").read_text())
    policy = [ln.strip() for ln in argo["configs"]["rbac"]["policy.csv"].strip().splitlines()]
    assert set(policy) == {"p, role:omniops, applications, get, */arena-control, deny",
                           "p, role:omniops, logs, get, */arena-control, deny",
                           "g, role:omniops, role:readonly", "g, omniops, role:omniops"}


@pytest.mark.parametrize("name", ["omniops-env.sh"])
def test_env_generator_masks_secrets_and_never_writes_github_token(name: str) -> None:
    text = (DEPLOY / name).read_text()
    for var in ("KUBECONFIG", "KUBE_CONTEXT", "GRAFANA_URL", "GRAFANA_TOKEN", "PROMETHEUS_URL", "LOKI_URL", "TEMPO_URL",
                "ARGOCD_URL", "ARGOCD_TOKEN", "GITHUB_REPOS"):
        assert f'echo "{var}=' in text, var
    assert 'echo "GITHUB_TOKEN=' not in text and "write_private" in text and '="<set>"' in text


# ---------------------------------------------------------------------------------------------
# OmniOps' Kubernetes view: no admin credential, the Arena's suite state hidden (3.16)
# ---------------------------------------------------------------------------------------------

SUITE_STATE = {"metadata": {"name": "suite-state", "namespace": "benchmark-control"},
               "data": {"scenario": "stale-db-credentials", "registry": "fixture.local"}}
FLAGS = {"metadata": {"name": "flagd-config", "namespace": "shop"}, "data": {"demo.flagd.json": "{}"}}


def load_helper() -> Any:
    spec = importlib.util.spec_from_file_location("arena_helper", DEPLOY / "helper.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def api_server() -> Iterator[tuple[str, list[tuple[str, str]]]]:
    """A fake API server: every object the admin identity would see, and the requests it received."""
    seen: list[tuple[str, str]] = []

    class Api(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append((self.path, self.headers.get("Authorization", "")))
            path = self.path.split("?")[0]
            if path == "/api/v1/configmaps" and "as=Table" in self.headers.get("Accept", ""):
                body: Any = {"kind": "Table", "rows": [{"cells": ["suite-state"], "object": SUITE_STATE},
                                                       {"cells": ["flagd-config"], "object": FLAGS}]}
            elif path == "/api/v1/configmaps":
                body = {"kind": "ConfigMapList", "metadata": {"continue": "c1"}, "items": [SUITE_STATE, FLAGS]}
            elif path == "/api/v1/namespaces":
                body = {"kind": "NamespaceList", "items": [{"metadata": {"name": n}} for n in ("benchmark-control", "shop")]}
            elif path == "/api/v1/namespaces/benchmark-control/configmaps/suite-state":
                body = SUITE_STATE
            else:
                body = {"kind": "Status", "status": "Failure", "code": 404}
            raw = json.dumps(body).encode()
            self.send_response(404 if body.get("kind") == "Status" else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Api)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", seen
    server.shutdown()
    server.server_close()


def call(url: str, method: str = "GET", accept: str = "application/json") -> tuple[int, Any]:
    req = urllib.request.Request(url, method=method, headers={"Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_kube_proxy_hides_the_arena_suite_state_and_is_read_only(api_server: tuple[str, list[tuple[str, str]]]) -> None:
    upstream_url, seen = api_server
    helper_mod = load_helper()
    proxy = helper_mod.kube_proxy_server({"server": upstream_url, "token": "sa-token", "ca": ""},
                                         frozenset({"benchmark-control"}), 0)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{proxy.server_address[1]}"
    try:
        # the change feed's cluster-wide list keeps the shop's flags, never the scenario id
        status, doc = call(base + "/api/v1/configmaps?limit=500")
        assert status == 200 and [i["metadata"]["name"] for i in doc["items"]] == ["flagd-config"]
        assert doc["metadata"]["continue"] == "c1" and "stale-db-credentials" not in json.dumps(doc)
        status, table = call(base + "/api/v1/configmaps", accept="application/json;as=Table;v=v1;g=meta.k8s.io")
        assert status == 200 and [r["cells"] for r in table["rows"]] == [["flagd-config"]]
        status, nss = call(base + "/api/v1/namespaces")
        assert [i["metadata"]["name"] for i in nss["items"]] == ["shop"]
        assert all(auth == "Bearer sa-token" for _, auth in seen)        # the token stays in the proxy
        # direct reads of the control namespace never reach the API server
        before = len(seen)
        status, refused = call(base + "/api/v1/namespaces/benchmark-control/configmaps/suite-state")
        assert status == 403 and refused["status"] == "Failure" and "stale-db-credentials" not in json.dumps(refused)
        assert call(base + "/apis/apps/v1/namespaces/benchmark-control/deployments")[0] == 403
        assert call(base + "/api/v1/namespaces/shop/pods/p/exec?command=sh")[0] == 403
        assert call(base + "/api/v1/namespaces/shop/configmaps/flagd-config", method="DELETE")[0] == 405
        assert call(base + "/api/v1/pods?watch=true")[0] == 400
        assert call(base + "/api/v1/watch/namespaces/benchmark-control/configmaps")[0] == 400
        assert len(seen) == before
    finally:
        proxy.shutdown()
        proxy.server_close()


def test_kube_upstream_and_kubeconfig_keep_the_token_out_of_omniops(tmp_path: Path) -> None:
    secret = {"data": {"token": "c2EtdG9rZW4=", "ca.crt": "LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tCg=="}}
    up = tmp_path / "kube-upstream.json"
    proc = subprocess.run([sys.executable, str(DEPLOY / "helper.py"), "kube-upstream", "--server", "https://127.0.0.1:6443",
                           "--out", str(up)], input=json.dumps(secret), capture_output=True, text=True, check=False)
    assert proc.returncode == 0 and "sa-token" not in proc.stdout + proc.stderr
    assert json.loads(up.read_text())["token"] == "sa-token" and (up.stat().st_mode & 0o777) == 0o600
    pending = subprocess.run([sys.executable, str(DEPLOY / "helper.py"), "kube-upstream", "--server", "s", "--out",
                              str(tmp_path / "x")], input=json.dumps({"data": {}}), capture_output=True, text=True,
                             check=False)
    assert pending.returncode == 1 and not (tmp_path / "x").exists()


def fake_kubectl(tmp: Path) -> Path:
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    (bin_dir / "kubectl").write_text("#!/bin/sh\nexit 1\n")        # no Argo CD, no cluster
    (bin_dir / "kubectl").chmod(0o755)
    return bin_dir


def test_omniops_env_points_kubernetes_at_the_filtered_view_not_the_admin_kubeconfig(tmp_path: Path) -> None:
    state = tmp_path / "state"
    env = {"PATH": f"{fake_kubectl(tmp_path)}:/usr/bin:/bin", "HOME": str(tmp_path), "ARENA_PYTHON": sys.executable,
           "ARENA_STATE_DIR": str(state), "ARENA_DIR": str(tmp_path / "arena"), "ARENA_BIN_DIR": str(tmp_path / "bin"),
           "KUBECONFIG": str(tmp_path / "admin-kubeconfig")}
    proc = subprocess.run(["bash", str(DEPLOY / "omniops-env.sh")], capture_output=True, text=True, env=env,
                          timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    values = dict(line.split("=", 1) for line in (state / "omniops.env").read_text().splitlines()
                  if "=" in line and not line.startswith("#"))
    view = state / "omniops-kubeconfig"
    assert values["KUBECONFIG"] == f'"{view}:{tmp_path / "admin-kubeconfig"}"'    # OmniOps reads the first only
    assert values["KUBE_CONTEXT"] == values["KUBERNETES_CONTEXT"] == '"omniops-arena"'
    assert values["K8S_API_URL"] == '"http://127.0.0.1:8001"'
    assert values["OMNIOPS_ARENA_KUBE_HIDDEN_NAMESPACES"] == '"benchmark-control"'
    config = yaml.safe_load(view.read_text())
    assert config["clusters"][0]["cluster"] == {"server": "http://127.0.0.1:8001"}
    assert config["users"] == [{"name": "omniops-arena", "user": {}}]               # no credential at all
    assert "current-context" not in config                                          # kubectl's default unchanged
    assert "no read-only identity yet" in proc.stderr

    again = subprocess.run(["bash", str(DEPLOY / "omniops-env.sh")], capture_output=True, text=True,
                           env={**env, "KUBECONFIG": f"{view}:{tmp_path / 'admin-kubeconfig'}"}, timeout=60, check=False)
    assert again.returncode == 0                                                    # rerun from a sourced shell
    assert f'KUBECONFIG="{view}:{tmp_path / "admin-kubeconfig"}"' in (state / "omniops.env").read_text()


def test_reader_role_is_read_only_and_has_no_proxy_or_exec() -> None:
    docs = {d["kind"]: d for d in yaml.safe_load_all((DEPLOY / "kubernetes" / "omniops-reader.yaml").read_text())}
    assert docs["Secret"]["type"] == "kubernetes.io/service-account-token"
    rules = docs["ClusterRole"]["rules"]
    assert all(set(r["verbs"]) <= {"get", "list"} for r in rules)
    resources = {res for r in rules for res in r["resources"]}
    assert "*" not in resources and not {"pods/exec", "pods/attach", "pods/portforward", "pods/proxy",
                                         "services/proxy", "nodes/proxy"} & resources
    assert {"pods/log", "configmaps", "events", "replicasets", "nodes", "storageclasses"} <= resources
    assert docs["ClusterRoleBinding"]["subjects"][0]["name"] == docs["ServiceAccount"]["metadata"]["name"]


# ---------------------------------------------------------------------------------------------
# up.sh: fault images
# ---------------------------------------------------------------------------------------------


def images_loaded(tmp: Path, loaded: list[str]) -> int:
    """up.sh's images_loaded() against a fake kind node whose crictl lists ``loaded``."""
    arena = tmp / "arena"
    (arena / "scenarios").mkdir(parents=True, exist_ok=True)
    (arena / "scenarios" / "scenarios.json").write_text(json.dumps({
        "crashloop": {"image": "report-generator"}, "oom": {"image": "thumbnailer"}, "payment-failure": {},
        "pg-lock-hold": {"image": "catalog-maintenance"}}))
    bin_dir = tmp / "bin"
    bin_dir.mkdir(exist_ok=True)
    listing = "IMAGE TAG IMAGE-ID SIZE\n" + "".join(f"fixture.local/{n} v1 0123456789ab 50MB\n" for n in loaded)
    (bin_dir / "docker").write_text(f"#!/bin/sh\nprintf '{listing}'\n")
    (bin_dir / "docker").chmod(0o755)
    script = (f'source "{DEPLOY}/lib.sh"; eval "$(sed -n \'/^images_loaded() {{/,/^}}/p\' "{DEPLOY}/up.sh")"; '
              "images_loaded")
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp), "ARENA_PYTHON": sys.executable,
           "ARENA_DIR": str(arena), "ARENA_STATE_DIR": str(tmp / "state"), "ARENA_BIN_DIR": str(bin_dir)}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=60,
                          check=False).returncode


def test_images_loaded_needs_every_fault_image_not_just_one(tmp_path: Path) -> None:
    # build-images failed after thumbnailer: the rerun must build the rest
    assert images_loaded(tmp_path, ["report-generator", "thumbnailer"]) != 0
    assert images_loaded(tmp_path, ["report-generator", "thumbnailer", "catalog-maintenance"]) == 0
