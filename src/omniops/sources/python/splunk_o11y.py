"""Python fetchers for Splunk Observability Cloud (manifest: sources/manifests/splunk_o11y.yaml).

``signalflow_execute`` backs the ``splunk_o11y_signalflow`` table. The declarative engine cannot
express it because SignalFlow's HTTP transport is a Server-Sent Events stream, not a JSON body:

    POST https://stream.<realm>.observability.splunkcloud.com/v2/signalflow/execute
         ?start=<epoch ms>&stop=<epoch ms>&resolution=<ms>&immediate=true
    X-SF-TOKEN: <token>          Content-Type: text/plain          body: the SignalFlow program

The response is ``text/event-stream``. Each message is ``event: <type>`` followed by one or more
``data: ...`` lines whose concatenation is ONE pretty-printed JSON document (so line-by-line JSON
parsing fails), terminated by a blank line. Message types (dev.splunk.com, "SignalFlow stream
messages specification"):

    control-message  {"event": "STREAM_START" | "JOB_START" | "JOB_PROGRESS" | "CHANNEL_ABORT"
                      | "END_OF_CHANNEL", "timestampMs": ..., "abortInfo": {...}}
    message          information messages (e.g. JOB_RUNNING_RESOLUTION, FIND_MATCHED_NO_TIMESERIES)
    metadata         {"tsId": "...", "properties": {"sf_metric": ..., "sf_streamLabel": ...,
                      "<dimension>": "<value>", ...}}  -- sent before data for that tsId
    data             {"data": [{"tsId": "...", "value": 1.5}, ...], "logicalTimestampMs": ...}
    event            detector events (ignored here)
    expired-tsid     {"tsId": "..."} (ignored)
    error            fatal computation error -> raised

One output row per (tsId, logicalTimestampMs) with the series metadata joined in. Rows are coerced
with the table's declared column types, exactly like ``transform.build_rows`` output.
A documented sample stream lives in ``sources/fixtures/splunk_o11y/signalflow_execute.sse.txt``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from omniops.engine import timeutil, transform
from omniops.engine.engine import FetchOutcome
from omniops.engine.http import AuthError, FetchError, RateLimitError, RequestLog, SourceRuntime
from omniops.engine.manifest import TableSpec

STREAM_PATH = "/v2/signalflow/execute"
DEFAULT_TIMEOUT_S = 120.0

# Metadata properties that describe the computation rather than the series' identity.
_INTERNAL_PROPS = {
    "jobId", "computationId", "sf_key", "sf_metric", "sf_originatingMetric", "sf_streamLabel",
    "sf_resolutionMs", "sf_type", "sf_isPreQuantized", "sf_organizationID", "sf_organizationId",
    "sf_singletonFixedDimensions", "sf_maxDelayMs", "sf_programId",
}


class SignalFlowError(FetchError):
    lesson = "SignalFlow rejected or aborted the program: fix the program text; do not retry unchanged"


def iter_sse(lines: Iterable[str]) -> Iterator[tuple[str, str, str | None]]:
    """Yield (event, data, id) for each Server-Sent Event; multi-line data is joined with \\n."""
    event, data, eid = "message", [], None
    for raw in lines:
        line = raw.rstrip("\r\n")
        if line == "":
            if data:
                yield event, "\n".join(data), eid
            event, data, eid = "message", [], None
            continue
        if line.startswith(":"):
            continue  # SSE comment / keep-alive
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event = value
        elif field == "data":
            data.append(value)
        elif field == "id":
            eid = value
    if data:
        yield event, "\n".join(data), eid


class SignalFlowCollector:
    """Accumulates stream messages into rows. Feed it (event, payload) pairs."""

    def __init__(self, max_rows: int = 5000):
        self.max_rows = max_rows
        self.meta: dict[str, dict[str, Any]] = {}
        self.rows: list[dict[str, Any]] = []
        self.info: list[dict[str, Any]] = []
        self.done = False
        self.truncated = False

    def feed(self, event: str, payload: str) -> None:
        try:
            msg = json.loads(payload)
        except json.JSONDecodeError:
            self.info.append({"unparsed": payload[:300], "event": event})
            return
        if event == "metadata":
            ts_id = msg.get("tsId")
            if ts_id:
                self.meta[ts_id] = msg.get("properties") or {}
        elif event == "data":
            ts_ms = msg.get("logicalTimestampMs")
            for point in msg.get("data") or []:
                if len(self.rows) >= self.max_rows:
                    self.truncated = True
                    self.done = True
                    return
                self.rows.append(self._row(point.get("tsId"), ts_ms, point.get("value")))
        elif event == "control-message":
            kind = msg.get("event")
            if kind == "END_OF_CHANNEL":
                self.done = True
            elif kind == "CHANNEL_ABORT":
                info = msg.get("abortInfo") or {}
                raise SignalFlowError(
                    f"SignalFlow channel aborted: {info.get('sf_job_abortReason')} ({info.get('sf_job_abortState')})")
        elif event == "error":
            raise SignalFlowError(f"SignalFlow error: {json.dumps(msg)[:600]}")
        elif event == "message":
            self.info.append(msg)

    def _row(self, ts_id: str | None, ts_ms: Any, value: Any) -> dict[str, Any]:
        props = self.meta.get(ts_id or "", {})
        dims = {k: v for k, v in props.items() if k not in _INTERNAL_PROPS}
        return {
            "ts": ts_ms,
            "value": value,
            "ts_id": ts_id,
            "label": props.get("sf_streamLabel"),
            "metric": props.get("sf_originatingMetric") or props.get("sf_metric"),
            "dimensions": dims,
            "properties": props,
        }


def parse_stream(lines: Iterable[str], max_rows: int = 5000) -> SignalFlowCollector:
    """Parse a complete SSE stream (used for offline checks of the documented sample)."""
    col = SignalFlowCollector(max_rows=max_rows)
    for event, payload, _ in iter_sse(lines):
        col.feed(event, payload)
        if col.done:
            break
    return col


def _coerce_rows(table: TableSpec, rows: list[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        row: dict[str, Any] = {}
        for col in table.columns:
            if col.filter_only:
                v = filters.get(col.name)
                row[col.name] = transform.coerce(v if not isinstance(v, list) else None, col.type)
                continue
            v = r.get(col.name)
            if col.name == "ts" and v is not None:
                v = transform.apply_transform(v, "epoch_ms")
            row[col.name] = transform.coerce(v, col.type)
        out.append(row)
    return out


def _window(ctx: dict[str, Any]) -> tuple[datetime, datetime, int]:
    t = ctx.get("time") or {}
    end = timeutil.to_datetime(t["end"]) if t.get("end") else datetime.now(UTC)
    start = timeutil.to_datetime(t["start"]) if t.get("start") else end - timedelta(hours=1)
    step_s = int(t.get("step_s") or timeutil.auto_step(start, end))
    return start, end, step_s


async def signalflow_execute(rt: SourceRuntime, table: TableSpec, ctx: dict[str, Any],
                             logs: list[RequestLog]) -> FetchOutcome:
    """Run a SignalFlow program over the window and return rows for ``splunk_o11y_signalflow``.

    Data messages arrive in time order, so a stream cut at ``max_rows`` keeps the start of the window
    and loses its end: that is reported as truncation, never returned as if complete.
    """
    filters = ctx.get("filters") or {}
    program = filters.get("program")
    if not program or not str(program).strip():
        raise FetchError(f"{rt.name}/{table.name}: filter 'program' (SignalFlow text) is required")
    start, end, step_s = _window(ctx)
    resolution = filters.get("resolution_ms") or max(10, step_s) * 1000
    realm = rt.env.get("SPLUNK_O11Y_REALM", "")
    base = (rt.env.get("SPLUNK_O11Y_STREAM_URL") or f"https://stream.{realm}.observability.splunkcloud.com").rstrip("/")
    url = base + STREAM_PATH
    params = {
        "start": timeutil.fmt_epoch_ms(start),
        "stop": timeutil.fmt_epoch_ms(end),
        "resolution": int(resolution),
        "immediate": "true",
    }
    client = await rt.ensure_client()
    assert rt.semaphore is not None
    req = client.build_request(
        "POST", url, params=params, content=str(program).encode(),
        headers={"Content-Type": "text/plain", "Accept": "text/event-stream"},
        timeout=httpx.Timeout(DEFAULT_TIMEOUT_S, connect=10.0),
    )
    await rt.authorize(req)
    log = RequestLog(rt.name, table.name, "POST", url, dict(params), str(program)[:2000], None, 0)
    logs.append(log)
    t0 = time.monotonic()
    collector = SignalFlowCollector(max_rows=table.max_rows)
    try:
        await rt.throttle()  # rate_limit.requests_per_minute is shared with the declarative tables
        async with rt.semaphore:
            resp = await client.send(req, stream=True)
            try:
                log.status = resp.status_code
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")[:1200]
                    log.error = f"HTTP {resp.status_code}"
                    if resp.status_code in (401, 403):
                        raise AuthError(f"{rt.name}/{table.name}: HTTP {resp.status_code}: {body}",
                                        status=resp.status_code, url=url, body=body)
                    if resp.status_code == 429:
                        raise RateLimitError(f"{rt.name}/{table.name}: HTTP 429 rate limited", status=429, url=url)
                    raise SignalFlowError(f"{rt.name}/{table.name}: HTTP {resp.status_code}: {body}",
                                          status=resp.status_code, url=url, body=body)
                deadline = time.monotonic() + DEFAULT_TIMEOUT_S

                event, data = "message", []
                async for raw in resp.aiter_lines():
                    line = raw.rstrip("\r\n")
                    if line == "":
                        if data:
                            collector.feed(event, "\n".join(data))
                        event, data = "message", []
                        if collector.done:
                            break
                    elif not line.startswith(":"):
                        field, _, value = line.partition(":")
                        value = value[1:] if value.startswith(" ") else value
                        if field == "event":
                            event = value
                        elif field == "data":
                            data.append(value)
                    if time.monotonic() > deadline:
                        raise RateLimitError(f"{rt.name}/{table.name}: SignalFlow stream still open after "
                                             f"{DEFAULT_TIMEOUT_S:.0f}s; narrow the window or the program")
                if data and not collector.done:
                    collector.feed(event, "\n".join(data))
            finally:
                await resp.aclose()
    except httpx.TimeoutException as exc:
        log.error = "timeout"
        raise RateLimitError(f"{rt.name}/{table.name}: SignalFlow request timed out", url=url) from exc
    except httpx.HTTPError as exc:
        log.error = f"{type(exc).__name__}: {exc}"
        raise FetchError(f"{rt.name}/{table.name}: SignalFlow connection failed: {exc}", url=url) from exc
    finally:
        log.elapsed_ms = int((time.monotonic() - t0) * 1000)
    rows = _coerce_rows(table, collector.rows, filters)
    log.rows = len(rows)
    notes: list[str] = []
    if collector.truncated:
        notes.append(f"SignalFlow stream stopped at {len(rows)} points (max_rows={table.max_rows}); points arrive "
                     "in time order, so the END of the window is missing - aggregate in the program "
                     "(fewer series, coarser resolution_ms) or narrow the window")
    return FetchOutcome(rows, notes, logs, [], collector.truncated)
