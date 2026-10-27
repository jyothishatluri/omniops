"""Live status trail: one Slack message per run, edited in place as the agents work.

Lessons encoded (OpsIntelligence retrospective):
- D10 / 4.4: users waited minutes for anything visible, so progress is shown immediately and the
  early conclusion card is forwarded as soon as the agents finish (``early_card``).
- 4.4: ``chat.update`` fails above 4,000 characters of text and sections hold ~3,000, so the trail
  keeps the title and the newest steps and says how many older steps it folded away.
- 4.4 / 4.5: updates are coalesced (at least 1.5 s apart) and run in the background, so agent code
  never waits on Slack and a burst of steps costs one API call. A Slack failure is logged, never
  raised into the investigation.
- D23: a frozen card must never look live. ``finish`` and ``fail`` always replace the message
  without the Stop button, and the header says the run is over.
- 3.16: the title (the alert's own) and agent step text are untrusted, so they are escaped and can never
  carry a live mention or broadcast; only the lines this module and the app build hold mentions.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterator
from typing import TYPE_CHECKING, Any

from omniops.slack.render import escape_mrkdwn

if TYPE_CHECKING:
    from slack_sdk.web.async_client import AsyncWebClient

    from omniops.agent.types import ConclusionCard

log = logging.getLogger("omniops.slack")

STOP_ACTION = "omniops_stop_run"
STATUS_EVENT_TYPE = "omniops_status"
SECTION_CHARS = 2900


class _Done:
    """A completed awaitable: trail methods work whether or not the caller awaits them."""

    def __await__(self) -> Iterator[Any]:
        return iter(())


class StatusTrail:
    """``start`` posts one message; ``step``/``done`` append lines; ``finish``/``fail`` freeze it."""

    def __init__(
        self,
        client: AsyncWebClient,
        channel: str,
        thread_ts: str | None = None,
        *,
        run_id: str | None = None,
        stop_value: str | None = None,
        on_card: Callable[[ConclusionCard], Awaitable[None]] | None = None,
        min_interval_s: float = 1.5,
        max_chars: int = 3500,
    ) -> None:
        self.client = client
        self.channel = channel
        self.thread_ts = thread_ts
        self.run_id = run_id
        self.stop_value = stop_value
        self.on_card = on_card
        self.min_interval_s = min_interval_s
        self.max_chars = max_chars
        self.ts: str | None = None
        self.title = ""
        self.lines: list[str] = []
        self.state = "idle"                      # idle | live | finished | failed
        self.stop_requested = False
        self.started = time.monotonic()
        self._last_update = 0.0
        self._flush_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    # -- public API -------------------------------------------------------------------------

    async def start(self, title: str, *, say: Callable[..., Awaitable[Any]] | None = None) -> str | None:
        """Post the trail message. ``say`` (Bolt) is used for the first reply in DMs (Assistant panel)."""
        self.title = title
        self.state = "live"
        self.started = time.monotonic()
        text, blocks = self._render()
        kwargs: dict[str, Any] = {"text": text, "blocks": blocks, "metadata": self._metadata()}
        if self.thread_ts:
            kwargs["thread_ts"] = self.thread_ts
        try:
            if say is not None:
                resp = await say(**kwargs)
            else:
                resp = await self.client.chat_postMessage(channel=self.channel, **kwargs)
            self.ts = resp["ts"]
        except Exception as exc:  # noqa: BLE001 - status must never break a run
            log.warning("[%s] status trail could not be posted: %s", self.run_id, exc)
        self._last_update = time.monotonic()
        return self.ts

    def step(self, text: str) -> _Done:
        """Append a progress line. Never blocks; the edit is coalesced in the background."""
        return self._append(f"• {escape_mrkdwn(_one_line(text))}")

    def done(self, text: str) -> _Done:
        """Append a completed-step line."""
        return self._append(f":white_check_mark: {escape_mrkdwn(_one_line(text))}")

    def request_stop(self, user: str | None = None) -> None:
        """Cooperative stop: agent code polls ``stop_requested``; the app cancels after a grace period."""
        self.stop_requested = True
        self._append(f":octagonal_sign: Stop requested{f' by <@{user}>' if user else ''}")

    async def early_card(self, card: ConclusionCard) -> None:
        """Called by the orchestrator when the agents finish: the app posts a preliminary card (D10)."""
        if self.on_card is None:
            return
        try:
            await self.on_card(card)
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s] early card failed: %s", self.run_id, exc)

    async def finish(self, text: str | None = None) -> None:
        """Freeze the trail as complete (no Stop button)."""
        if text:
            self.lines.append(f":white_check_mark: {_one_line(text)}")
        await self._freeze("finished")

    async def fail(self, msg: str) -> None:
        """Freeze the trail as failed (no Stop button), so it never looks live."""
        self.lines.append(f":x: {_one_line(msg)}")
        await self._freeze("failed")

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started

    # -- internals --------------------------------------------------------------------------

    def _append(self, line: str) -> _Done:
        if self.state in ("finished", "failed"):
            return _Done()
        self.lines.append(line)
        if self.ts is not None and self._flush_task is None:
            try:
                self._flush_task = asyncio.get_running_loop().create_task(self._flush_later())
            except RuntimeError:  # no running loop (called from a worker thread): next flush picks it up
                pass
        return _Done()

    async def _flush_later(self) -> None:
        try:
            wait = self._last_update + self.min_interval_s - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
        finally:
            self._flush_task = None
        await self._update()

    async def _freeze(self, state: str) -> None:
        self.state = state
        pending, self._flush_task = self._flush_task, None
        if pending is not None:
            pending.cancel()
        wait = self._last_update + self.min_interval_s - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        await self._update()

    async def _update(self) -> None:
        if self.ts is None:
            return
        async with self._lock:
            text, blocks = self._render()
            try:
                await self.client.chat_update(channel=self.channel, ts=self.ts, text=text, blocks=blocks,
                                              metadata=self._metadata())
            except Exception as exc:  # noqa: BLE001
                log.warning("[%s] status trail update failed: %s", self.run_id, exc)
            self._last_update = time.monotonic()

    def _header(self) -> str:
        secs = int(self.elapsed_s)
        title = escape_mrkdwn(_one_line(self.title))
        if self.state == "finished":
            return f"*{title}* - done in {secs} s"
        if self.state == "failed":
            return f"*{title}* - failed after {secs} s"
        return f":hourglass_flowing_sand: *{title}* - working ({secs} s)"

    def _fit(self, limit: int) -> str:
        """Header plus the newest lines that fit; folded lines are counted, never silently lost."""
        header = self._header()
        kept: list[str] = []
        size = len(header)
        for line in reversed(self.lines):
            if size + len(line) + 1 + 40 > limit:
                break
            kept.append(line)
            size += len(line) + 1
        kept.reverse()
        folded = len(self.lines) - len(kept)
        parts = [header] + ([f"_... {folded} earlier step(s)_"] if folded else []) + kept
        return "\n".join(parts)[:limit]

    def _render(self) -> tuple[str, list[dict[str, Any]]]:
        text = self._fit(self.max_chars)
        blocks: list[dict[str, Any]] = [
            {"type": "section", "block_id": "omniops_status", "text": {"type": "mrkdwn",
                                                                       "text": self._fit(SECTION_CHARS)}}]
        if self.run_id:
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"run `{self.run_id}`"}]})
        if self.state == "live" and self.stop_value and not self.stop_requested:
            blocks.append({"type": "actions", "block_id": "omniops_status_actions", "elements": [{
                "type": "button", "action_id": STOP_ACTION, "style": "danger", "value": self.stop_value,
                "text": {"type": "plain_text", "text": "Stop"}}]})
        return text, blocks

    def _metadata(self) -> dict[str, Any]:
        return {"event_type": STATUS_EVENT_TYPE, "event_payload": {"run_id": self.run_id or "", "state": self.state}}


def _one_line(text: str, limit: int = 300) -> str:
    s = " ".join(str(text).split())
    return s if len(s) <= limit else s[: limit - 3] + "..."
