"""De-duplication and token registries for the Slack entry point.

Lessons encoded (OpsIntelligence retrospective):
- 4.5: duplicate deliveries are normal. Slack redelivers slow acknowledgements, the ``message`` and
  ``app_mention`` handlers both fire for one post, and users double-click. ``EventDeduper`` is the
  fast local layer (put-if-absent with a TTL, LRU-bounded) keyed by event id, then by the post
  itself (channel + ts / client_msg_id) so only one handler acts on a post.
- D11: staged writes and previews are claimed atomically and expire, so a retried click runs once
  (``ClaimRegistry``).
- D20: anything a button references lives in a registry keyed by a token; button values carry only
  that token. These registries are process-local: a multi-replica deployment swaps them for a
  shared put-if-absent store with the same interface.
- Part 5 (flapping monitors): ``FlapRegistry`` remembers which run handled an alert key so an
  automated re-trigger within the flap window links to the earlier run instead of starting another;
  a run that failed is forgotten, so it never stands in for the next re-trigger.

All operations are synchronous with no awaits inside, so they are atomic on the event loop.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal

ClaimStatus = Literal["ok", "claimed", "expired", "unknown"]


class EventDeduper:
    """Put-if-absent markers with a TTL, bounded as an LRU."""

    def __init__(self, ttl_s: float = 900.0, max_items: int = 10_000) -> None:
        self.ttl_s = ttl_s
        self.max_items = max_items
        self._seen: OrderedDict[str, float] = OrderedDict()

    def _alive(self, key: str, now: float) -> bool:
        exp = self._seen.get(key)
        if exp is None:
            return False
        if exp < now:
            del self._seen[key]
            return False
        return True

    def first(self, *keys: str | None) -> bool:
        """True (and mark every key) only if none of the non-empty keys was seen within the TTL."""
        now = time.monotonic()
        live = [k for k in keys if k]
        if any(self._alive(k, now) for k in live):
            return False
        for k in live:
            self._seen[k] = now + self.ttl_s
            self._seen.move_to_end(k)
        while len(self._seen) > self.max_items:
            self._seen.popitem(last=False)
        return True

    def seen(self, key: str) -> bool:
        return self._alive(key, time.monotonic())


@dataclass
class _Entry[T]:
    value: T
    expires: float
    claimed_by: str | None = None
    claimed: bool = False


class ClaimRegistry[T]:
    """Token -> value with expiry and an atomic claim-once (approvals, previews)."""

    def __init__(self, ttl_s: float, max_items: int = 5_000) -> None:
        self.ttl_s = ttl_s
        self.max_items = max_items
        self._items: OrderedDict[str, _Entry[T]] = OrderedDict()

    def put(self, token: str, value: T, ttl_s: float | None = None) -> None:
        self._items[token] = _Entry(value, time.time() + (self.ttl_s if ttl_s is None else ttl_s))
        self._items.move_to_end(token)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)

    def get(self, token: str) -> T | None:
        e = self._items.get(token)
        return e.value if e is not None and e.expires >= time.time() else None

    def claim(self, token: str, by: str | None = None) -> tuple[T | None, ClaimStatus, str | None]:
        """(value, "ok", by) exactly once; later calls get (None, "claimed", first_claimer)."""
        e = self._items.get(token)
        if e is None:
            return None, "unknown", None
        if e.claimed:
            return None, "claimed", e.claimed_by
        if e.expires < time.time():
            return None, "expired", None
        e.claimed, e.claimed_by = True, by
        return e.value, "ok", by


@dataclass
class FlapRecord:
    run_id: str
    channel: str
    ts: str                      # the message to link to (the earlier alert / its status trail)
    started: float               # wall-clock epoch seconds


class FlapRegistry:
    """Alert key -> the run that handled it most recently."""

    def __init__(self, max_items: int = 5_000) -> None:
        self.max_items = max_items
        self._runs: OrderedDict[str, FlapRecord] = OrderedDict()

    def recent(self, key: str, window_s: float, now: float | None = None) -> FlapRecord | None:
        rec = self._runs.get(key)
        if rec is None:
            return None
        return rec if (now if now is not None else time.time()) - rec.started <= window_s else None

    def record(self, key: str, run_id: str, channel: str, ts: str, started: float | None = None) -> FlapRecord:
        rec = FlapRecord(run_id, channel, ts, started if started is not None else time.time())
        self._runs[key] = rec
        self._runs.move_to_end(key)
        while len(self._runs) > self.max_items:
            self._runs.popitem(last=False)
        return rec

    def forget(self, key: str, run_id: str) -> None:
        """Drop the key if ``run_id`` still holds it (that run failed: a re-trigger deserves a fresh run)."""
        rec = self._runs.get(key)
        if rec is not None and rec.run_id == run_id:
            del self._runs[key]
