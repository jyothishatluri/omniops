"""HTTP execution for manifest tables: auth, retries, pagination, multi-step jobs, polling.

Direct REST only (no MCP). Every call is logged as a ``RequestLog`` so evidence records and case
files can show exactly what was asked, and every failure is loud and labelled with the right
lesson: a rate limit is "not a schema problem"; a 400 means "describe the table".
"""

from __future__ import annotations

import asyncio
import base64
import csv
import io
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urljoin

import httpx
import yaml

from omniops.engine import jsonpath, templating, timeutil
from omniops.engine.manifest import AuthSpec, PaginationSpec, RequestSpec, SourceManifest, TableSpec

_LINK_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="?next"?')
_SECRET_KEYS = re.compile(r"(key|token|secret|password|authorization|signature|credential)", re.I)


class FetchError(RuntimeError):
    """Base class for fetch failures. ``lesson`` is what the model should learn from it."""

    lesson = "describe the table and check the filters; do not repeat the same query"

    def __init__(self, message: str, *, status: int | None = None, url: str = "", body: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url
        self.body = body


class RateLimitError(FetchError):
    lesson = "rate limited or timed out: NOT a schema problem; narrow the window or retry later"


class AuthError(FetchError):
    lesson = "credentials or permission scope problem: not fixable by changing the query"


class NotFoundError(FetchError):
    lesson = ("that id / name / path does not exist on this platform (not a schema problem): look it up with the "
              "list or search table first, then retry with a real value")


class NotConfiguredError(FetchError):
    lesson = "this source is not configured in this deployment; use another source"


@dataclass
class RequestLog:
    source: str
    table: str
    method: str
    url: str
    params: dict[str, Any]
    body: Any
    status: int | None
    elapsed_ms: int
    rows: int = 0
    error: str | None = None
    page: int = 1

    def redacted(self) -> dict[str, Any]:
        def red(d: Any) -> Any:
            if isinstance(d, dict):
                return {k: ("***" if _SECRET_KEYS.search(str(k)) else red(v)) for k, v in d.items()}
            return d

        return {
            "source": self.source, "table": self.table, "method": self.method, "url": self.url,
            "params": red(self.params), "body": red(self.body), "status": self.status,
            "elapsed_ms": self.elapsed_ms, "rows": self.rows, "error": self.error, "page": self.page,
        }


@dataclass
class Response:
    status: int
    headers: httpx.Headers
    body: Any
    text: str
    url: str


@dataclass
class TokenCache:
    token: str | None = None
    expires_at: float = 0.0


@dataclass
class SourceRuntime:
    """A configured instance of a manifest: resolved env, auth, HTTP client and limits."""

    manifest: SourceManifest
    env: dict[str, str]
    instance: str = ""
    base_url: str = ""
    client: httpx.AsyncClient | None = None
    semaphore: asyncio.Semaphore | None = None
    _token: TokenCache = field(default_factory=TokenCache)
    _kube: dict[str, Any] | None = None
    _tmpfiles: list[str] = field(default_factory=list)
    _sent_at: list[float] = field(default_factory=list)
    _rpm_lock: asyncio.Lock | None = None

    async def throttle(self) -> None:
        """Enforce rate_limit.requests_per_minute (sliding 60 s window) across all tables of the source."""
        rpm = self.manifest.rate_limit.requests_per_minute
        if not rpm:
            return
        if self._rpm_lock is None:
            self._rpm_lock = asyncio.Lock()
        async with self._rpm_lock:
            while True:
                now = time.monotonic()
                self._sent_at = [t for t in self._sent_at if now - t < 60.0]
                if len(self._sent_at) < rpm:
                    self._sent_at.append(now)
                    return
                await asyncio.sleep(max(0.05, 60.0 - (now - self._sent_at[0])))

    @property
    def name(self) -> str:
        return self.instance or self.manifest.source

    def template_ctx(self) -> dict[str, Any]:
        return {"env": self.env, "source": {"name": self.name, "base_url": self.base_url}}

    async def ensure_client(self) -> httpx.AsyncClient:
        if self.client is None:
            verify: Any = self.env.get("OMNIOPS_TLS_VERIFY", "true").lower() not in {"0", "false", "no"}
            cert = None
            auth = self.manifest.auth
            if auth.type == "kubeconfig":
                kube = self._load_kubeconfig(auth)
                self._kube = kube
                verify = kube.get("verify", verify)
                cert = kube.get("cert")
                if not self.base_url:
                    self.base_url = kube["server"]
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=10.0),
                verify=verify,
                cert=cert,
                follow_redirects=True,
                headers={"User-Agent": "omniops/0.1", "Accept": "application/json"},
            )
            self.semaphore = asyncio.Semaphore(max(1, self.manifest.rate_limit.concurrency))
        return self.client

    async def aclose(self) -> None:
        if self.client is not None:
            await self.client.aclose()
            self.client = None
        for p in self._tmpfiles:
            try:
                os.unlink(p)
            except OSError:
                pass

    # -- auth ------------------------------------------------------------------------------

    def _tmp(self, data: bytes, suffix: str) -> str:
        fd, path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        self._tmpfiles.append(path)
        return path

    def _load_kubeconfig(self, auth: AuthSpec) -> dict[str, Any]:
        ctx = self.template_ctx()
        path = templating.render_value(auth.kubeconfig or "", ctx) or os.environ.get(
            "KUBECONFIG", os.path.expanduser("~/.kube/config")
        )
        path = os.path.expanduser(str(path).split(os.pathsep)[0])
        with open(path) as fh:
            cfg = yaml.safe_load(fh) or {}
        want = templating.render_value(auth.context or "", ctx) or cfg.get("current-context")
        context = next((c["context"] for c in cfg.get("contexts", []) if c.get("name") == want), None)
        if context is None:
            raise NotConfiguredError(f"kubeconfig context {want!r} not found in {path}")
        cluster = next(c["cluster"] for c in cfg.get("clusters", []) if c.get("name") == context.get("cluster"))
        user = next((u["user"] for u in cfg.get("users", []) if u.get("name") == context.get("user")), {}) or {}
        out: dict[str, Any] = {"server": cluster["server"].rstrip("/")}
        if cluster.get("insecure-skip-tls-verify"):
            out["verify"] = False
        elif cluster.get("certificate-authority-data"):
            out["verify"] = self._tmp(base64.b64decode(cluster["certificate-authority-data"]), ".crt")
        elif cluster.get("certificate-authority"):
            out["verify"] = cluster["certificate-authority"]
        if user.get("client-certificate-data") and user.get("client-key-data"):
            out["cert"] = (
                self._tmp(base64.b64decode(user["client-certificate-data"]), ".crt"),
                self._tmp(base64.b64decode(user["client-key-data"]), ".key"),
            )
        elif user.get("client-certificate") and user.get("client-key"):
            out["cert"] = (user["client-certificate"], user["client-key"])
        if user.get("token"):
            out["token"] = user["token"]
        return out

    async def _oauth_token(self, auth: AuthSpec) -> str:
        if self._token.token and time.time() < self._token.expires_at - 60:
            return self._token.token
        ctx = self.template_ctx()
        token_url = templating.render_value(auth.token_url or "", ctx)
        data = {
            "grant_type": "client_credentials",
            "client_id": templating.render_value(auth.client_id or "", ctx),
            "client_secret": templating.render_value(auth.client_secret or "", ctx),
        }
        if auth.scope:
            data["scope"] = templating.render_value(auth.scope, ctx)
        if auth.audience:
            data["audience"] = templating.render_value(auth.audience, ctx)
        client = await self.ensure_client()
        r = await client.post(token_url, data=data, headers={"Accept": "application/json"})
        if r.status_code >= 400:
            raise AuthError(f"token request failed: HTTP {r.status_code} {r.text[:300]}", status=r.status_code)
        payload = r.json()
        self._token = TokenCache(payload["access_token"], time.time() + float(payload.get("expires_in", 3600)))
        return self._token.token  # type: ignore[return-value]

    def _gcp_token(self, auth: AuthSpec) -> str:
        if self._token.token and time.time() < self._token.expires_at - 60:
            return self._token.token
        try:
            import google.auth
            import google.auth.transport.requests
            from google.oauth2 import service_account
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise NotConfiguredError("GCP auth needs the 'gcp' extra: uv pip install 'omniops[gcp]'") from exc
        scopes = [templating.render_value(auth.scope or "https://www.googleapis.com/auth/cloud-platform",
                                          self.template_ctx())]
        path = templating.render_value(auth.credentials_file or "", self.template_ctx())
        if path:
            creds = service_account.Credentials.from_service_account_file(path, scopes=scopes)
        else:
            creds, _ = google.auth.default(scopes=scopes)
        creds.refresh(google.auth.transport.requests.Request())
        self._token = TokenCache(creds.token, time.time() + 3000)
        return creds.token

    def _sigv4(self, request: httpx.Request, auth: AuthSpec) -> None:
        try:
            from botocore.auth import SigV4Auth
            from botocore.awsrequest import AWSRequest
            from botocore.session import Session
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise NotConfiguredError("AWS auth needs the 'aws' extra: uv pip install 'omniops[aws]'") from exc
        ctx = self.template_ctx()
        creds = Session().get_credentials()
        if creds is None:
            raise AuthError("no AWS credentials found (env, profile or instance role)")
        aws_req = AWSRequest(method=request.method, url=str(request.url), data=request.content,
                             headers={k: v for k, v in request.headers.items() if k.lower() != "user-agent"})
        SigV4Auth(creds.get_frozen_credentials(), templating.render_value(auth.service or "", ctx),
                  templating.render_value(auth.region or "", ctx)).add_auth(aws_req)
        for k, v in aws_req.headers.items():
            request.headers[k] = v

    async def authorize(self, request: httpx.Request) -> None:
        auth = self.manifest.auth
        ctx = self.template_ctx()
        if auth.type == "bearer":
            token = templating.render_value(auth.token or "", ctx)
            if not token:
                raise AuthError(f"{self.name}: bearer token is empty (check {self.manifest.required_env})")
            request.headers["Authorization"] = f"Bearer {token}"
        elif auth.type == "headers":
            for k, v in auth.headers.items():
                val = templating.render_value(v, ctx)
                if val:
                    request.headers[k] = str(val)
        elif auth.type == "basic":
            user = str(templating.render_value(auth.username or "", ctx) or "")
            pw = str(templating.render_value(auth.password or "", ctx) or "")
            request.headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
        elif auth.type == "query":
            params = {k: templating.render_value(v, ctx) for k, v in auth.query.items()}
            request.url = request.url.copy_merge_params(params)
        elif auth.type in {"oauth2_client_credentials", "azure_ad"}:
            request.headers["Authorization"] = f"Bearer {await self._oauth_token(auth)}"
        elif auth.type == "gcp_service_account":
            token = await asyncio.to_thread(self._gcp_token, auth)
            request.headers["Authorization"] = f"Bearer {token}"
        elif auth.type == "kubeconfig":
            if self._kube and self._kube.get("token"):
                request.headers["Authorization"] = f"Bearer {self._kube['token']}"
        elif auth.type == "aws_sigv4":
            self._sigv4(request, auth)


# ---------------------------------------------------------------------------------------------
# Request rendering and execution
# ---------------------------------------------------------------------------------------------


def _clean_query(q: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in q.items():
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, bool):
            v = "true" if v else "false"
        elif isinstance(v, (dict,)):
            v = json.dumps(v, separators=(",", ":"))
        elif isinstance(v, datetime):
            v = timeutil.fmt_rfc3339(v)
        out[k] = v
    return out


@dataclass
class RenderedRequest:
    method: str
    url: str
    params: dict[str, Any]
    headers: dict[str, str]
    body: Any
    body_format: str
    response_format: str
    timeout_s: float


def render_request(rt: SourceRuntime, spec: RequestSpec, ctx: dict[str, Any]) -> RenderedRequest:
    full = {**rt.template_ctx(), **ctx}
    if spec.url:
        url = str(templating.render_value(spec.url, full))
    else:
        path = str(templating.render_value(spec.path, full))
        base = rt.base_url.rstrip("/")
        url = path if path.startswith("http") else f"{base}/{path.lstrip('/')}"
    params = _clean_query(templating.render_value(spec.query, full) or {})
    headers = {**rt.manifest.headers, **spec.headers}
    headers = {k: str(v) for k, v in (templating.render_value(headers, full) or {}).items() if v not in (None, "")}
    body = templating.render_value(spec.body, full) if spec.body is not None else None
    return RenderedRequest(spec.method, url, params, headers, body, spec.body_format, spec.response_format,
                           spec.timeout_s)


def _parse_body(r: httpx.Response, fmt: str) -> tuple[Any, str]:
    text = r.text
    if fmt == "text":
        return text, text
    if fmt == "ndjson":
        items = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return items, text
    if fmt == "csv":
        return list(csv.DictReader(io.StringIO(text))), text
    if not text.strip():
        return None, text
    try:
        return r.json(), text
    except (json.JSONDecodeError, ValueError):
        return text, text


async def send(rt: SourceRuntime, rr: RenderedRequest, *, table: str, logs: list[RequestLog],
               accept_status: list[int] | None = None, page: int = 1) -> Response:
    client = await rt.ensure_client()
    assert rt.semaphore is not None
    kwargs: dict[str, Any] = {}
    if rr.body is not None:
        if rr.body_format in ("json", "graphql"):
            kwargs["content"] = json.dumps(rr.body, default=str).encode()
            rr.headers.setdefault("Content-Type", "application/json")
        elif rr.body_format == "form":
            kwargs["data"] = rr.body
        else:
            kwargs["content"] = str(rr.body).encode()
    limits = rt.manifest.rate_limit
    attempt = 0
    while True:
        attempt += 1
        # params=None keeps a next/link URL's own query string (httpx replaces it when params={}).
        req = client.build_request(rr.method, rr.url, params=rr.params or None, headers=rr.headers,
                                   timeout=rr.timeout_s, **kwargs)
        await rt.authorize(req)
        t0 = time.monotonic()
        log = RequestLog(rt.name, table, rr.method, rr.url, dict(rr.params), rr.body, None, 0, page=page)
        try:
            await rt.throttle()
            async with rt.semaphore:
                r = await client.send(req)
        except httpx.TimeoutException as exc:
            log.elapsed_ms = int((time.monotonic() - t0) * 1000)
            log.error = "timeout"
            logs.append(log)
            if attempt <= limits.max_retries:
                await asyncio.sleep(min(8, 2 ** attempt))
                continue
            raise RateLimitError(f"{rt.name}/{table}: request timed out after {rr.timeout_s}s", url=rr.url) from exc
        except httpx.HTTPError as exc:
            log.elapsed_ms = int((time.monotonic() - t0) * 1000)
            log.error = f"{type(exc).__name__}: {exc}"
            logs.append(log)
            if attempt <= limits.max_retries:
                await asyncio.sleep(min(8, 2 ** attempt))
                continue
            raise FetchError(f"{rt.name}/{table}: connection failed: {exc}", url=rr.url) from exc
        log.elapsed_ms = int((time.monotonic() - t0) * 1000)
        log.status = r.status_code
        logs.append(log)
        throttled_body = (400 <= r.status_code < 500 and limits.retry_body_patterns
                          and any(p in r.text[:4000] for p in limits.retry_body_patterns))
        if (r.status_code in limits.retry_statuses or throttled_body) and attempt <= limits.max_retries:
            retry_after = r.headers.get("Retry-After") or r.headers.get("X-RateLimit-Reset")
            try:
                delay = float(retry_after) if retry_after else min(10.0, 1.5 ** attempt)
            except ValueError:
                delay = min(10.0, 1.5 ** attempt)
            await asyncio.sleep(min(delay, 20.0))
            continue
        if r.status_code >= 400 and r.status_code not in (accept_status or []):
            snippet = r.text[:1200]
            log.error = f"HTTP {r.status_code}"
            if r.status_code == 429 or throttled_body:
                raise RateLimitError(f"{rt.name}/{table}: HTTP 429 rate limited", status=429, url=rr.url, body=snippet)
            if r.status_code in (401, 403):
                raise AuthError(f"{rt.name}/{table}: HTTP {r.status_code}: {snippet}", status=r.status_code,
                                url=rr.url, body=snippet)
            if r.status_code == 404:
                raise NotFoundError(f"{rt.name}/{table}: HTTP 404 not found: {snippet}", status=404, url=rr.url,
                                    body=snippet)
            raise with_hint(FetchError(f"{rt.name}/{table}: HTTP {r.status_code}: {snippet}", status=r.status_code,
                                       url=rr.url, body=snippet), snippet)
        body, text = _parse_body(r, rr.response_format)
        return Response(r.status_code, r.headers, body, text, str(r.url))


def _apply_param(rr: RenderedRequest, target: str, value: Any) -> None:
    kind, _, name = target.partition(".")
    if kind == "query":
        rr.params[name] = value
    elif kind == "body":
        if isinstance(rr.body, str):
            # Bodies rendered as JSON text (body_format text) can still carry cursors.
            try:
                parsed = json.loads(rr.body)
            except json.JSONDecodeError as exc:
                raise ValueError(f"cannot set {target} on a non-JSON text body") from exc
            if isinstance(parsed, dict):
                jsonpath.set_path(parsed, name, value)
                rr.body = json.dumps(parsed)
                return
        if not isinstance(rr.body, dict):
            rr.body = {}
        jsonpath.set_path(rr.body, name, value)
    else:
        raise ValueError(f"pagination target must start with query. or body.: {target}")


@dataclass
class PageResult:
    responses: list[Response]
    truncated: bool
    notes: list[str]


async def run_table_requests(
    rt: SourceRuntime,
    table: TableSpec,
    ctx: dict[str, Any],
    *,
    logs: list[RequestLog],
    row_counter: Any,
    max_rows: int,
) -> PageResult:
    """Run the table's step(s) for one fan-out context; paginate the final step.

    ``row_counter(response)`` tells pagination what a page produced: an int, or ``(items, rows)``
    when expand steps make rows differ from the API's items. Offsets, page-size checks and totals
    use items (the API's unit); ``max_rows`` counts rows.
    """
    await rt.ensure_client()  # resolves the kubeconfig server into base_url before the first render
    captures: dict[str, Any] = {}
    notes: list[str] = []
    steps = table.all_steps
    for idx, step in enumerate(steps):
        sctx = {**ctx, "captures": captures}
        if step.when is not None and not templating.evaluate(step.when, {**rt.template_ctx(), **sctx}):
            continue
        final = idx == len(steps) - 1
        if not final:
            resp = await _run_with_poll(rt, step, sctx, table, logs)
            for k, path in step.capture.items():
                captures[k] = jsonpath.resolve(path, resp.body) if not isinstance(resp.body, str) else resp.text
            continue
        return await _paginate(rt, step, table.pagination, sctx, table, logs, row_counter, max_rows, notes)
    return PageResult([], False, notes)


async def _run_with_poll(rt: SourceRuntime, step: RequestSpec, ctx: dict[str, Any], table: TableSpec,
                         logs: list[RequestLog]) -> Response:
    rr = render_request(rt, step, ctx)
    resp = await send(rt, rr, table=table.name, logs=logs, accept_status=step.accept_status + step.accept_status_rows)
    if step.poll is None:
        return resp
    deadline = time.monotonic() + step.poll.timeout_s
    while True:
        pctx = {**rt.template_ctx(), **ctx, "response": resp.body}
        if step.poll.fail_if and templating.evaluate(step.poll.fail_if, pctx):
            raise FetchError(f"{rt.name}/{table.name}: async job failed: {json.dumps(resp.body, default=str)[:500]}")
        if templating.evaluate(step.poll.until, pctx):
            return resp
        if time.monotonic() > deadline:
            raise RateLimitError(f"{rt.name}/{table.name}: async job not done after {step.poll.timeout_s}s")
        await asyncio.sleep(step.poll.interval_s)
        rr = render_request(rt, step, ctx)
        resp = await send(rt, rr, table=table.name, logs=logs, accept_status=step.accept_status + step.accept_status_rows)


async def _paginate(rt: SourceRuntime, step: RequestSpec, pag: PaginationSpec, ctx: dict[str, Any],
                    table: TableSpec, logs: list[RequestLog], row_counter: Any, max_rows: int,
                    notes: list[str]) -> PageResult:
    responses: list[Response] = []
    total_rows = 0
    page_no = 1
    offset = pag.start
    page_param_value = pag.start if pag.type == "page" else None
    rr = render_request(rt, step, {**ctx, "page": {"offset": offset, "number": page_param_value, "size": pag.page_size}})
    if step.poll is not None:
        resp = await _run_with_poll(rt, step, ctx, table, logs)
    else:
        if pag.type == "offset" and pag.offset_param:
            _apply_param(rr, pag.offset_param, offset)
            if pag.limit_param:
                _apply_param(rr, pag.limit_param, pag.page_size)
        elif pag.type == "page" and pag.page_param:
            _apply_param(rr, pag.page_param, page_param_value)
            if pag.limit_param:
                _apply_param(rr, pag.limit_param, pag.page_size)
        resp = await send(rt, rr, table=table.name, logs=logs, accept_status=step.accept_status + step.accept_status_rows, page=page_no)
    truncated = False
    time_cursor_prev: Any = None
    while True:
        responses.append(resp)
        counted = row_counter(resp)
        # n: API items on this page (pagination's unit); rows: rows after expand steps (max_rows).
        n, rows = counted if isinstance(counted, tuple) else (counted, counted)
        if logs:
            logs[-1].rows = rows
        total_rows += rows
        if pag.type == "none":
            break
        if total_rows >= max_rows:
            truncated = True
            notes.append(f"stopped paginating at {total_rows} rows (max_rows={max_rows})")
            break
        if page_no >= pag.max_pages:
            more = _has_more(pag, resp, n)
            if more:
                truncated = True
                notes.append(f"stopped after {pag.max_pages} pages; more data exists (narrow the filters)")
            break
        if pag.has_more_path and isinstance(resp.body, (dict, list)):
            if not jsonpath.resolve(pag.has_more_path, resp.body):
                break
        page_no += 1
        if pag.type == "cursor":
            cursor = jsonpath.resolve(pag.cursor_path or "", resp.body) if isinstance(resp.body, (dict, list)) else None
            if cursor in (None, "", [], 0) or not pag.cursor_param:
                break
            if pag.cursor_exclusive and pag.cursor_param.startswith("query."):
                rr.params = {k: v for k, v in rr.params.items() if k in pag.cursor_keep_params}
            _apply_param(rr, pag.cursor_param, cursor)
        elif pag.type == "offset":
            if n == 0 or n < pag.page_size:
                break
            offset += n
            if pag.total_path:
                total = jsonpath.resolve(pag.total_path, resp.body)
                if isinstance(total, (int, float)) and offset >= total:
                    break
            _apply_param(rr, pag.offset_param or "query.offset", offset)
        elif pag.type == "page":
            if n == 0 or n < pag.page_size:
                break
            page_param_value = (page_param_value or 0) + 1
            _apply_param(rr, pag.page_param or "query.page", page_param_value)
        elif pag.type == "link_header":
            m = _LINK_NEXT.search(resp.headers.get("link", ""))
            if not m:
                break
            rr.url, rr.params = m.group(1), {}
        elif pag.type == "time_cursor":
            if n == 0 or n < pag.page_size or not pag.cursor_param:
                break
            stamps = jsonpath.resolve_list(pag.cursor_path or "", resp.body) if isinstance(resp.body, (dict, list)) else []
            nums = []
            for v in stamps:
                try:
                    nums.append((float(v), v))
                except (TypeError, ValueError):
                    continue
            if not nums:
                break
            cursor = (min(nums) if pag.time_direction == "backward" else max(nums))[1]
            if cursor == time_cursor_prev:
                break
            time_cursor_prev = cursor
            _apply_param(rr, pag.cursor_param, cursor)
        elif pag.type == "next_url":
            nxt = jsonpath.resolve(pag.next_url_path or "", resp.body) if isinstance(resp.body, dict) else None
            if not nxt:
                break
            rr.url = nxt if str(nxt).startswith("http") else urljoin(rt.base_url.rstrip("/") + "/", str(nxt).lstrip("/"))
            rr.params = {}
        resp = await send(rt, rr, table=table.name, logs=logs, accept_status=step.accept_status + step.accept_status_rows, page=page_no)
    return PageResult(responses, truncated, notes)


def _has_more(pag: PaginationSpec, resp: Response, n: int) -> bool:
    body = resp.body
    if pag.has_more_path and isinstance(body, (dict, list)):
        return bool(jsonpath.resolve(pag.has_more_path, body))
    if pag.type == "cursor" and isinstance(body, (dict, list)):
        return jsonpath.resolve(pag.cursor_path or "", body) not in (None, "", [])
    if pag.type == "link_header":
        return bool(_LINK_NEXT.search(resp.headers.get("link", "")))
    if pag.type == "next_url" and isinstance(body, dict):
        return bool(jsonpath.resolve(pag.next_url_path or "", body))
    return n >= pag.page_size  # offset / page / time_cursor: a full page means more may exist


def time_ctx(start: datetime, end: datetime, target_points: int = 300, min_step: str = "10s") -> dict[str, Any]:
    """The ``time`` template context of one request window.

    ``bucket_start`` / ``bucket_points`` / ``bucket_cover_start`` describe step-sized buckets at the
    auto step that tile the window (``timeutil.BucketGrid``). ``buckets(step)`` returns that grid for
    an explicit step (seconds or a duration; empty = the auto step) and records it under
    ``buckets_used``, so the engine can report the effective step and the span the buckets cover.
    Templates call it for the query start when the range of a range query equals its step.
    """
    step_s = timeutil.auto_step(start, end, target_points, min_step)
    grid = timeutil.bucket_grid(start, end, step_s)
    out: dict[str, Any] = {
        "start": start,
        "end": end,
        "step_s": step_s,
        "step": timedelta(seconds=step_s),
        "range_s": int((end - start).total_seconds()),
        "bucket_start": grid.start,
        "bucket_points": grid.points,
        "bucket_cover_start": grid.cover_start,
    }

    def buckets(step: Any = None) -> timeutil.BucketGrid:
        used = grid
        if step:  # None, "" and Jinja's Undefined mean the auto step
            used = timeutil.bucket_grid(start, end, timeutil.parse_duration(step).total_seconds())
        out["buckets_used"] = used
        return used

    out["buckets"] = buckets
    return out


# Lessons for query-language errors whose server message names the symptom but not the fix. They are
# matched on the error text of any source: PromQL is spoken by Prometheus, Mimir, Thanos,
# VictoriaMetrics, managed Prometheus services and Grafana's data-source proxy alike.
_ERROR_HINTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'expected type scalar in call to function \\?"?vector\b', re.IGNORECASE),
     ("vector() takes only a scalar (a number, time(), scalar(x)); sum()/count()/avg()/max() ... already "
      "return an instant vector, so drop the vector() wrapper (to turn 'no series' into 0 append "
      "'or vector(0)')")),
]


def error_hint(text: str) -> str | None:
    """The fix for a known query-language error message, if ``text`` contains one."""
    for rx, hint in _ERROR_HINTS:
        if rx.search(text or ""):
            return hint
    return None


def with_hint(exc: BaseException, text: str) -> BaseException:
    """Prepend the known fix for ``text`` (an API error message) to the exception's lesson."""
    hint = error_hint(text)
    if hint:
        exc.lesson = f"{hint}; {getattr(exc, 'lesson', '')}".rstrip("; ")  # type: ignore[attr-defined]
    return exc
