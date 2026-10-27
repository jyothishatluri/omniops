"""Slack entry point (Bolt, Socket Mode): alert channels, mentions, DMs, /investigate and buttons.

Slack is always the entry point; investigations and chat run in-process so threads, buttons,
previews, follow-ups, feedback and stop signals stay interactive (D1).

Lessons encoded (OpsIntelligence retrospective):
- D10: the conclusion card is the primary result in its own message. An early card is posted when
  the agents finish and the final card edits it in place; the report follows as thread replies.
  Interactive elements (buttons, action ids, values) are built here by code, never by a model, and
  render output is stripped of anything interactive that is not a plain link.
- D11: staged writes get an Approve/Cancel card, expire after 30 minutes and are claimed atomically,
  so a retried or double click runs the write once; ``write_mode=deny`` never offers the buttons.
- D12: /investigate results go to the requester as a private preview with "Post for everyone" /
  "Keep private"; mentions and automated alerts post publicly in the alert thread.
- D20: everything a button references lives in a token registry; button values carry only tokens.
- D23 / 4.5: every run has a watchdog; a failed, stopped or interrupted run freezes its status trail
  without the Stop button and replaces any "writing the report" placeholder, so nothing frozen looks
  live. Real cancellations (shutdown) are told apart from a requested stop.
- 3.11: follow-up questions in a thread go back through the agent's tool loop with the thread as
  context instead of asking the human to look things up.
- 4.4: content goes in blocks and the text field is a short notification. Long answers are paged
  (page 1 edits the placeholder, buttons on the last page); a page Slack rejects is degraded to plain
  text instead of being lost, and rate-limited calls are retried. DMs answer first through Bolt's
  say() (Assistant panel). Link buttons send action events too, so every action is acknowledged.
- 4.5: two-layer de-duplication (event id, then the post itself) because redeliveries, double
  clicks and message+app_mention pairs are normal; feedback counts one vote per user and run. Listeners only filter and hand off: all slow work
  (model calls, sqlite, Slack I/O) runs in tracked background tasks, so Bolt acks at once and the
  event loop never blocks. The bot never acts on its own posts (Bolt's self-event filter plus a
  local check).
- Part 5 (flapping monitors): recoveries from automated senders are skipped; an automated
  re-trigger of the same alert key within the flap window links to the earlier run; humans always
  get a run (customer reports posted in an alert channel are triaged and investigated); if the flap
  check itself fails, the run goes ahead.
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, Protocol

from slack_bolt.async_app import AsyncApp
from slack_sdk.errors import SlackApiError
from slack_sdk.http_retry.builtin_async_handlers import AsyncRateLimitErrorRetryHandler
from slack_sdk.web.async_client import AsyncWebClient

from omniops.agent.runner import DefaultRunner
from omniops.agent.types import Alert, ConclusionCard, RunResult
from omniops.slack.dedup import ClaimRegistry, EventDeduper, FlapRecord, FlapRegistry
from omniops.slack.render import escape_mrkdwn
from omniops.slack.status import STATUS_EVENT_TYPE, STOP_ACTION, StatusTrail

if TYPE_CHECKING:
    from omniops.agent.context import Deps

log = logging.getLogger("omniops.slack")

ACTION_APPROVE = "omniops_write_approve"
ACTION_CANCEL = "omniops_write_cancel"
ACTION_VOTE_UP = "omniops_feedback_up"
ACTION_VOTE_DOWN = "omniops_feedback_down"
ACTION_SHARE = "omniops_preview_share"
ACTION_KEEP = "omniops_preview_keep"

META_CARD = "omniops_card"
META_REPORT = "omniops_report"
META_APPROVAL = "omniops_approval"
META_NOTICE = "omniops_notice"
META_ERROR = "omniops_error"
META_PREVIEW = "omniops_preview"

WRITE_TTL_S = 1800.0
PREVIEW_TTL_S = 24 * 3600.0
RUN_TTL_S = 24 * 3600.0
SECTION_CHARS = 2900
PAGE_SECTIONS = 8
MAX_BLOCKS = 50
# The agents keep the first 3,000 characters of the thread context (orchestrator, hypotheses; chat keeps
# 4,000), so the transcript is trimmed here to fit: root plus the newest replies, never cut from the end.
THREAD_CONTEXT_CHARS = 3000
THREAD_PAGE_SIZE = 200
THREAD_MAX_PAGES = 25
_BLOCK_ERRORS = {"invalid_blocks", "invalid_blocks_format", "msg_too_long", "too_many_blocks", "invalid_arguments"}
# The bot cannot post in that conversation (a slash command from a private channel or someone else's DM).
_NO_ACCESS = {"channel_not_found", "not_in_channel", "is_archived", "restricted_action",
              "restricted_action_read_only_channel", "team_access_not_granted", "ekm_access_denied"}
_MESSAGE_SUBTYPES = {None, "bot_message", "file_share"}
_CHANNEL_ID = re.compile(r"[CGD][A-Z0-9]{6,}")
_INVESTIGATE = re.compile(r"^\s*(please\s+)?(investigate|rca|root[- ]?cause|look into|dig into|analy[sz]e|triage)"
                          r"(\s+(this|it|that|the alert|again))?\s*[.!?]*\s*$", re.IGNORECASE)
_SECRETS = re.compile(r"(?i)(xox[abposr]-[A-Za-z0-9-]+|xapp-[A-Za-z0-9-]+|bearer\s+[A-Za-z0-9._~+/=-]+|"
                      r"((?:api[_-]?key|app[_-]?key|access[_-]?token|token|secret|password|signature|sig)=)[^&\s]+)")
HELP_TEXT = ("I investigate alerts and answer questions about your systems with live data. Mention me in an "
             "alert thread to investigate it, ask me a question here, DM me, or use `/investigate <symptom>` "
             "for a private run.")

_RUN_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar("omniops_run_id", default=None)


class Runner(Protocol):
    """What the Slack layer calls. Tests inject stubs; production uses ``AgentRunner``."""

    async def investigate(self, alert: Alert, deps: Deps, *, status: StatusTrail | None = None,
                          thread: str | None = None, private: bool = False) -> RunResult: ...

    async def answer(self, question: str, deps: Deps, *, thread: str | None = None,
                     status: StatusTrail | None = None) -> RunResult: ...


def _call_investigate(runner: Any, alert: Alert, deps: Deps, **kw: Any) -> Any:
    """Call runner.investigate with the keyword arguments it accepts (test stubs may predate ``private``)."""
    params = inspect.signature(runner.investigate).parameters
    if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        kw = {k: v for k, v in kw.items() if k in params}
    return runner.investigate(alert, deps, status=None, **kw)


# The real investigation and chat flows (imported on first use, 4.5); one runner for Slack, the CLI and the
# benchmarks, so their flows cannot drift apart (D9).
AgentRunner = DefaultRunner


# ---------------------------------------------------------------------------------------------
# Rendering adapter
# ---------------------------------------------------------------------------------------------


class Presenter:
    """Adapter over ``slack.render`` with a plain-text degrade path (a render or block problem must
    never cost the user the answer, 4.4)."""

    def __init__(self, renderer: Any | None = None) -> None:
        self._renderer = renderer

    @property
    def renderer(self) -> Any:
        if self._renderer is None:
            from omniops.slack import render

            self._renderer = render
        return self._renderer

    def _call(self, name: str, *args: Any) -> Any:
        try:
            return getattr(self.renderer, name)(*args)
        except Exception as exc:  # noqa: BLE001 - render bug or contract drift: degrade loudly
            log.warning("render.%s failed, degrading to plain text: %s", name, exc)
            return None

    def card(self, card: ConclusionCard) -> list[dict[str, Any]]:
        return _content_only(_as_blocks(self._call("card_blocks", card))) or _plain_card(card)

    def pages(self, markdown: str) -> list[list[dict[str, Any]]]:
        raw = self._call("report_pages", markdown)
        pages = []
        for page in raw if isinstance(raw, list) else []:
            blocks = _sections(page) if isinstance(page, str) else _content_only(_as_blocks(page))
            if blocks:
                pages.append(blocks)
        return pages or plain_pages(markdown)

    def approval(self, write: dict[str, Any]) -> list[dict[str, Any]]:
        blocks = _content_only(_as_blocks(self._call("approval_blocks", write)))
        return blocks or _sections(f"*Staged write* `{_escape(str(write.get('id', '?')))}`: "
                                   f"{_escape(str(write.get('summary', '')))}")

    def note(self, obj: ConclusionCard | str) -> str:
        if isinstance(obj, ConclusionCard):
            s = self._call("notification_text", obj)
            return _clip(s if isinstance(s, str) and s.strip() else f"{obj.title}: {obj.verdict}", 300)
        return _clip(obj, 300)

    def to_text(self, blocks: list[dict[str, Any]]) -> str:
        s = self._call("blocks_to_text", blocks)
        return s if isinstance(s, str) else _blocks_text(blocks)


# ---------------------------------------------------------------------------------------------
# Run state (token registries; button values carry only tokens, D20)
# ---------------------------------------------------------------------------------------------


@dataclass
class RunState:
    run_id: str
    kind: str                                  # investigation | chat | write
    channel: str
    thread_ts: str | None
    private_user: str | None = None            # everything goes to this user only (ephemeral)
    trail: StatusTrail | None = None
    task: asyncio.Task[Any] | None = None      # set only while the agent call runs (Stop targets it)
    card_ts: str | None = None
    placeholder_ts: str | None = None
    stopped_by: str | None = None
    result: RunResult | None = None


@dataclass
class PendingWrite:
    write: dict[str, Any]
    engine: Any                                # has ``async execute_write(write_id) -> QueryResult``
    owned: bool                                # created here from the write's table/filters: close after
    run_id: str
    channel: str
    thread_ts: str | None


@dataclass
class Preview:
    result: RunResult
    channel: str
    user: str
    run_id: str


class OmniOpsBot:
    """Owns the Bolt app, the registries and the background tasks."""

    def __init__(
        self,
        deps: Deps,
        *,
        client: AsyncWebClient | None = None,
        runner: Runner | None = None,
        alerts: Any | None = None,
        renderer: Any | None = None,
        bot_token: str | None = None,
        signing_secret: str | None = None,
        request_verification_enabled: bool = True,
        status_interval_s: float = 1.5,
    ) -> None:
        self.deps = deps
        self.runner: Runner = runner or AgentRunner()
        self._alerts = alerts
        self.present = Presenter(renderer)
        self.dedup = EventDeduper()
        self.votes = EventDeduper(ttl_s=RUN_TTL_S)
        self.flaps = FlapRegistry()
        self.runs: ClaimRegistry[RunState] = ClaimRegistry(RUN_TTL_S)
        self.writes: ClaimRegistry[PendingWrite] = ClaimRegistry(WRITE_TTL_S)
        self.previews: ClaimRegistry[Preview] = ClaimRegistry(PREVIEW_TTL_S)
        self.tasks: set[asyncio.Task[Any]] = set()
        # POSTs to a slash command's response_url; the simulator swaps in its own (no network there).
        self.post_to_response_url: Callable[[str, dict[str, Any]], Awaitable[bool]] = _post_response_url
        self.stop_grace_s = 20.0
        self.status_interval_s = status_interval_s
        self._channel_names: dict[str, str] = {}
        self._bot_uid: str | None = None
        self._own_ids: set[str] = set()
        kwargs: dict[str, Any] = {}
        if client is not None:
            # Bolt builds a fresh client per request; keep every call (say() included) on the injected one.
            kwargs.update(client=client, before_authorize=self._use_client)
        else:
            kwargs["token"] = bot_token
        # Listeners return in milliseconds (they only filter and spawn tasks), so acking after the
        # listener is immediate and dispatch is deterministic for the simulator.
        self.app = AsyncApp(signing_secret=signing_secret, request_verification_enabled=request_verification_enabled,
                            process_before_response=True, **kwargs)
        self.web: AsyncWebClient = self.app.client
        if client is None:
            # Rate-limited posts and edits are retried, not lost (4.4); per-request clients copy the handlers.
            self.web.retry_handlers.append(AsyncRateLimitErrorRetryHandler(max_retry_count=2))
        self._register()

    @property
    def alerts(self) -> Any:
        if self._alerts is None:
            from omniops.agent import alert

            self._alerts = alert
        return self._alerts

    async def _use_client(self, context: dict[str, Any], next: Callable[[], Awaitable[Any]]) -> None:
        context["client"] = self.app.client
        context.pop("say", None)         # Bolt built say() with the per-request client while resolving our args
        await next()

    # -- registration -------------------------------------------------------------------------

    def _register(self) -> None:
        app = self.app
        app.event("message")(self._on_message)
        app.event("app_mention")(self._on_mention)
        app.command("/investigate")(self._on_investigate_command)
        app.action(ACTION_APPROVE)(self._on_write_decision)
        app.action(ACTION_CANCEL)(self._on_write_decision)
        app.action(ACTION_VOTE_UP)(self._on_feedback)
        app.action(ACTION_VOTE_DOWN)(self._on_feedback)
        app.action(STOP_ACTION)(self._on_stop)
        app.action(ACTION_SHARE)(self._on_preview)
        app.action(ACTION_KEEP)(self._on_preview)
        app.action(re.compile(".*"))(self._ack_only)       # link buttons send action events too (4.4)
        app.error(self._on_error)

    # -- background tasks -----------------------------------------------------------------------

    def spawn(self, coro: Coroutine[Any, Any, Any], *, where: tuple[str, str | None, str | None] | None = None
              ) -> asyncio.Task[Any]:
        """Run slow work off the listener; failures post a short error with the run id (``where``)."""
        task = asyncio.get_running_loop().create_task(self._guard(coro, where))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def _guard(self, coro: Coroutine[Any, Any, Any], where: tuple[str, str | None, str | None] | None) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # every failure reaches the user, never silently
            ref = _RUN_ID.get() or "e" + uuid.uuid4().hex[:8]
            log.exception("[%s] background task failed", ref)
            if where is not None:
                channel, thread_ts, user = where
                await self._notice(channel, thread_ts, f":warning: Something went wrong (run `{ref}`): "
                                   f"{_error_text(exc)}", user=user, kind=META_ERROR, run_id=ref)

    async def idle(self, timeout: float | None = None) -> None:
        """Wait until no background task is running (the simulator and graceful drain use this)."""
        async with asyncio.timeout(timeout):
            while True:
                pending = {t for t in self.tasks if not t.done()}
                if not pending:
                    await asyncio.sleep(0)             # let done-callbacks and just-spawned tasks register
                    if not any(not t.done() for t in self.tasks):
                        return
                    continue
                await asyncio.wait(pending)

    async def shutdown(self, drain_s: float) -> None:
        """Graceful drain (D23): let in-flight runs finish, then cancel the rest (they post a notice)."""
        try:
            await self.idle(drain_s)
        except TimeoutError:
            log.warning("drain timed out after %.0f s; interrupting %d run(s)", drain_s, len(self.tasks))
            pending = [t for t in self.tasks if not t.done()]
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    # -- identity (never act on our own posts) ----------------------------------------------------

    def _learn_identity(self, context: Any) -> None:
        uid, bid = getattr(context, "bot_user_id", None), getattr(context, "bot_id", None)
        if uid and not self._bot_uid:
            self._bot_uid = str(uid)
        self._own_ids.update(str(x) for x in (uid, bid) if x)

    def _is_own(self, event: dict[str, Any]) -> bool:
        return bool(self._own_ids) and (event.get("bot_id") in self._own_ids or event.get("user") in self._own_ids)

    def _mentions_me(self, event: dict[str, Any]) -> bool:
        return bool(self._bot_uid) and f"<@{self._bot_uid}" in str(event.get("text") or "")

    # -- listeners (fast: filter, de-dup, hand off) ---------------------------------------------

    async def _on_message(self, body: dict[str, Any], event: dict[str, Any], say: Any, context: Any) -> None:
        self._learn_identity(context)
        if event.get("subtype") not in _MESSAGE_SUBTYPES or event.get("hidden") or self._is_own(event):
            return
        if not self.dedup.first(_event_key(body)):
            return
        channel = event.get("channel", "")
        if event.get("channel_type") == "im":
            if not _from_bot(event):
                self.spawn(self._dm(event, say), where=(channel, event.get("thread_ts"), None))
            return
        if event.get("thread_ts") not in (None, event.get("ts")):
            return                                  # replies are never new alerts; people reach us by mention
        if self._mentions_me(event):
            return                                  # the app_mention delivery of this post handles it
        self.spawn(self._alert_candidate(event), where=(channel, event.get("ts"), None))

    async def _on_mention(self, body: dict[str, Any], event: dict[str, Any], context: Any) -> None:
        self._learn_identity(context)
        if self._is_own(event) or not self.dedup.first(_event_key(body)):
            return
        self.spawn(self._mention(event), where=(event.get("channel", ""), event.get("thread_ts") or event.get("ts"),
                                               None))

    async def _on_investigate_command(self, ack: Any, command: dict[str, Any]) -> None:
        text = (command.get("text") or "").strip()
        if not text:
            await ack(text=f"Usage: `/investigate <alert text, monitor link or symptom>`. {HELP_TEXT}")
            return
        await ack(text=":mag: Investigating privately. You will get a preview to post for everyone or keep private.")
        channel, user = command.get("channel_id", ""), command.get("user_id", "")
        self.spawn(self._private_investigation(text, channel, user, command.get("response_url")),
                   where=(channel, None, user))

    async def _on_write_decision(self, ack: Any, body: dict[str, Any], action: dict[str, Any]) -> None:
        await ack()
        user, channel, msg_ts = _actor(body)
        wid = str(action.get("value", ""))
        approve = action.get("action_id") == ACTION_APPROVE
        pending, status, by = self.writes.claim(wid, by=user)      # atomic: a second click is refused
        if pending is None:
            why = {"claimed": f"was already handled by <@{by}>" if by else "was already handled",
                   "expired": "expired (staged writes are valid for 30 minutes)"}.get(status, "is unknown")
            self.spawn(self._notice(channel, None, f"Staged write `{wid}` {why}; nothing was executed.", user=user))
            return
        self.spawn(self._decide_write(pending, wid, approve, user, msg_ts),
                   where=(pending.channel, pending.thread_ts, None))

    async def _on_feedback(self, ack: Any, body: dict[str, Any], action: dict[str, Any]) -> None:
        await ack()
        user, channel, _ = _actor(body)
        value = 1 if action.get("action_id") == ACTION_VOTE_UP else -1
        self.spawn(self._vote(str(action.get("value", "")), value, user, channel), where=(channel, None, user))

    async def _on_stop(self, ack: Any, body: dict[str, Any], action: dict[str, Any]) -> None:
        await ack()
        user, channel, _ = _actor(body)
        run = self.runs.get(str(action.get("value", "")))
        if run is None or run.task is None or run.task.done():
            self.spawn(self._notice(channel, None, "That run has already finished.", user=user))
            return
        run.stopped_by = user
        if run.trail is not None:
            run.trail.request_stop(user)
        self.spawn(self._enforce_stop(run))

    async def _on_preview(self, ack: Any, body: dict[str, Any], action: dict[str, Any]) -> None:
        await ack()
        user, channel, _ = _actor(body)
        preview, status, _by = self.previews.claim(str(action.get("value", "")), by=user)
        if preview is None:
            msg = "This preview has expired." if status == "expired" else "This preview was already handled."
            self.spawn(self._notice(channel, None, msg, user=user))
            return
        share = action.get("action_id") == ACTION_SHARE
        self.spawn(self._resolve_preview(preview, share), where=(preview.channel, None, preview.user))

    async def _ack_only(self, ack: Any) -> None:
        await ack()

    async def _on_error(self, error: Exception) -> None:
        log.error("bolt listener error: %s", _error_text(error))

    # -- alert channels --------------------------------------------------------------------------

    async def _alert_candidate(self, event: dict[str, Any]) -> None:
        if not await self._is_alert_channel(event.get("channel", "")):
            return
        if not self.dedup.first(*_post_keys(event)):
            return
        automated = bool(await _maybe(self.alerts.is_automated(event)))
        await self._handle_alert_post(event, automated=automated)

    async def _parse(self, event: dict[str, Any], *, quiet: bool) -> Alert | None:
        """Normalise a post into an Alert. ``quiet``: a failure on a human's post is logged, not posted."""
        ev = {**event, "type": "message"}           # app_mention payloads carry the same message fields
        own = sorted(self._own_ids)
        try:
            fn = self.alerts.parse_slack_alert
            kwargs = {"own_bot_ids": own} if own and _accepts(fn, "own_bot_ids") else {}
            alert = await _maybe(fn(ev, self.deps.llm, **kwargs))
        except Exception:
            if not quiet:
                raise
            log.warning("could not triage message %s/%s", ev.get("channel"), ev.get("ts"), exc_info=True)
            return None
        if alert is not None:
            alert.source_channel = alert.source_channel or ev.get("channel")
            alert.source_ts = alert.source_ts or ev.get("ts")
        return alert

    async def _handle_alert_post(self, event: dict[str, Any], *, automated: bool) -> None:
        channel, ts = event.get("channel", ""), event.get("ts", "")
        alert = await self._parse(event, quiet=not automated)
        if alert is None:
            log.info("message %s/%s is not an alert or incident report; ignored", channel, ts)
            return
        if automated and await _maybe(self.alerts.is_recovery(alert)):
            log.info("skipping automated recovery notice %s", alert.key())
            return
        if automated:
            earlier = self._flap_check(alert)
            if earlier is not None:
                await self._link_earlier(channel, ts, earlier)
                return
        await self._investigate(alert, channel, ts)  # humans always get a run

    def _flap_check(self, alert: Alert) -> FlapRecord | None:
        try:
            return self.flaps.recent(alert.key(), self.deps.settings.flap_window_s)
        except Exception as exc:  # noqa: BLE001 - if the check fails, the run goes ahead
            log.warning("flap check failed (%s); investigating anyway", exc)
            return None

    async def _link_earlier(self, channel: str, ts: str, rec: FlapRecord) -> None:
        link = await self._permalink(rec.channel, rec.ts)
        mins = max(0, int((time.time() - rec.started) // 60))
        where = f"<{link}|the earlier investigation>" if link else "the earlier investigation"
        await self._notice(channel, ts, f":repeat: This alert re-triggered {mins} min after it was last investigated. "
                           f"See {where} (run `{rec.run_id}`). Mention me here for a fresh investigation.",
                           kind=META_NOTICE, run_id=rec.run_id)

    async def _is_alert_channel(self, channel: str) -> bool:
        configured = self.deps.settings.alert_channels
        if channel in configured:
            return True
        names = {c.lstrip("#").lower() for c in configured if not _CHANNEL_ID.fullmatch(c)}
        if not names or not channel:
            return False
        if channel not in self._channel_names:
            try:
                info = await self.web.conversations_info(channel=channel)
                self._channel_names[channel] = str((info.get("channel") or {}).get("name", ""))
            except SlackApiError as exc:
                log.warning("conversations.info %s failed: %s", channel, _slack_error(exc))
                return False
        return self._channel_names[channel].lower() in names

    # -- mentions, DMs, slash command --------------------------------------------------------------

    async def _mention(self, event: dict[str, Any]) -> None:
        if not self.dedup.first(*_post_keys(event)):
            return
        channel, ts = event.get("channel", ""), event.get("ts", "")
        thread_ts = event.get("thread_ts")
        question = _strip_mentions(event.get("text", ""), await self._bot_user_id())
        if thread_ts and thread_ts != ts:
            await self._mention_in_thread(question, channel, thread_ts, event.get("user"))
            return
        if await self._is_alert_channel(channel):
            if await _maybe(self.alerts.is_automated(event)):
                await self._handle_alert_post(event, automated=True)
                return
            report = await self._parse(event, quiet=True) if question else None
            if report is not None:                  # a human incident report: humans always get a run
                await self._investigate(report, channel, ts)
                return
        if not question:
            await self._notice(channel, ts, HELP_TEXT)
        else:
            await self._chat(question, channel, ts)

    async def _mention_in_thread(self, question: str, channel: str, thread_ts: str, user: str | None) -> None:
        root, transcript, answered = await self._thread(channel, thread_ts)
        alert = await self._parse(root, quiet=True) if root is not None else None
        if alert is None or (answered and question and not _INVESTIGATE.match(question)):
            await self._chat(question or "Summarise this thread and what is known so far.", channel, thread_ts,
                             thread=_keep_ends(transcript, THREAD_CONTEXT_CHARS))
            return
        head = f"Requested by <@{user}>: {_clip(question, 1000)}\n\nThread so far:\n" if question else ""
        context = head + _keep_ends(transcript, max(THREAD_CONTEXT_CHARS // 2, THREAD_CONTEXT_CHARS - len(head)))
        await self._investigate(alert, channel, thread_ts, thread=context)   # humans always get a run

    async def _dm(self, event: dict[str, Any], say: Any) -> None:
        if not self.dedup.first(*_post_keys(event)):
            return
        channel, thread_ts = event.get("channel", ""), event.get("thread_ts")
        question = _strip_mentions(event.get("text", ""), await self._bot_user_id())
        if not question:
            await say(text=HELP_TEXT, thread_ts=thread_ts)
            return
        transcript = None
        if thread_ts:
            transcript = _keep_ends((await self._thread(channel, thread_ts))[1], THREAD_CONTEXT_CHARS)
        await self._chat(question, channel, thread_ts, say=say, thread=transcript)

    async def _private_investigation(self, text: str, channel: str, user: str, response_url: str | None = None
                                     ) -> None:
        event = {"type": "message", "text": text, "user": user, "channel": channel}
        alert = await self._parse(event, quiet=True) or Alert(
            title=_clip(text, 120), text=text, parsed_by="manual", customer_report=True, source_channel=channel)
        run = self._new_run("investigation", channel, None, private_user=user)
        budget = self.deps.settings.investigation_guard_s
        # private=True: nothing reaches shared memory unless the requester posts it for everyone (D12, 3.17).
        result = await self._run_guarded(run, lambda: _call_investigate(self.runner, alert, self.deps, private=True),
                                         budget)
        if result is None:
            return
        token = uuid.uuid4().hex
        self.previews.put(token, Preview(result, channel, user, run.run_id))
        content = self.present.card(result.card) if result.card else self.present.pages(result.answer_markdown)[0]
        blocks = content[: MAX_BLOCKS - 2] + [
            _context("Private preview: only you can see this. The full report is attached when you share it."),
            _actions("omniops_preview", _button("Post for everyone", ACTION_SHARE, token, "primary"),
                     _button("Keep private", ACTION_KEEP, token))]
        note = self.present.note(result.card) if result.card else "Investigation preview"
        # The command may come from a conversation the bot is not in (a private channel, someone else's DM),
        # where chat.postEphemeral is refused: the command's response_url works there.
        await self._to_user(channel, user, note, blocks, response_url=response_url, kind=META_PREVIEW,
                            run_id=run.run_id)

    async def _remember_shared(self, preview: Preview) -> None:
        """A private run is remembered only once its requester shares it."""
        memory = self.deps.memory
        result = preview.result
        if memory is None or result is None or getattr(result, "kind", None) != "investigation" \
                or getattr(result, "status", None) != "ok":
            return
        try:
            await asyncio.wait_for(memory.remember(result, self.deps.llm), timeout=60)
        except Exception as exc:  # noqa: BLE001 - memory never fails a delivery
            log.warning("[%s] memory write after sharing failed: %s", preview.run_id, exc)

    async def _resolve_preview(self, preview: Preview, share: bool) -> None:
        if share:
            run = RunState(preview.run_id, "investigation", preview.channel, None)
            try:
                await self._deliver(run, preview.result, shared_by=preview.user)
            except SlackApiError as exc:
                if _slack_error(exc) not in _NO_ACCESS:
                    raise
                log.warning("[%s] cannot post in %s (%s); the report goes to the requester's DM", preview.run_id,
                            preview.channel, _slack_error(exc))
                await self._deliver_to_dm(preview)
                await self._notice(preview.channel, None, f"I cannot post in this conversation "
                                   f"(`{_slack_error(exc)}`): invite me with `/invite @OmniOps` to share here. "
                                   "The full report is in your DMs.", user=preview.user)
                return
            await self._notice(preview.channel, None, "Posted for everyone.", user=preview.user)
            await self._remember_shared(preview)
            return
        await self._deliver_to_dm(preview)
        await self._notice(preview.channel, None, "Kept private: the full report is in your DMs.", user=preview.user)

    async def _deliver_to_dm(self, preview: Preview) -> None:
        dm = await self.web.conversations_open(users=preview.user)
        run = RunState(preview.run_id, "investigation", str(dm["channel"]["id"]), None)
        await self._deliver(run, preview.result)

    # -- runs ------------------------------------------------------------------------------------

    def _new_run(self, kind: str, channel: str, thread_ts: str | None, *, private_user: str | None = None
                 ) -> RunState:
        run = RunState(uuid.uuid4().hex[:10], kind, channel, thread_ts, private_user=private_user)
        self.runs.put(run.run_id, run)
        _RUN_ID.set(run.run_id)
        return run

    def _trail(self, run: RunState) -> StatusTrail:
        run.trail = StatusTrail(self.web, run.channel, run.thread_ts, run_id=run.run_id, stop_value=run.run_id,
                                on_card=partial(self._early_card, run), min_interval_s=self.status_interval_s)
        return run.trail

    async def _investigate(self, alert: Alert, channel: str, thread_ts: str, *, thread: str | None = None) -> None:
        run = self._new_run("investigation", channel, thread_ts)
        key: str | None = None
        try:                                        # recorded up front, so a re-trigger during the run links to it
            key = alert.key()
            self.flaps.record(key, run.run_id, channel, thread_ts)
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s] could not record flap key: %s", run.run_id, exc)
        delivered = False
        try:
            trail = self._trail(run)
            await trail.start(f"Investigating {alert.title or alert.rule_name or 'the alert'}")
            budget = self.deps.settings.investigation_guard_s
            result = await self._run_guarded(
                run, lambda: self.runner.investigate(alert, self.deps, status=trail, thread=thread), budget)
            if result is not None:
                delivered = await self._deliver_or_fail(run, result) and result.status != "error"
        finally:
            if key is not None and not delivered:   # a failed run must not swallow the next re-trigger
                self.flaps.forget(key, run.run_id)

    async def _chat(self, question: str, channel: str, thread_ts: str | None, *, say: Any = None,
                    thread: str | None = None) -> None:
        run = self._new_run("chat", channel, thread_ts)
        trail = self._trail(run)
        await trail.start("Looking into it", say=say)
        budget = self.deps.settings.chat_guard_s
        result = await self._run_guarded(
            run, lambda: self.runner.answer(question, self.deps, thread=thread, status=trail), budget)
        if result is not None:
            await self._deliver_or_fail(run, result)

    async def _deliver_or_fail(self, run: RunState, result: RunResult) -> bool:
        """Delivery failures (Slack down, rate limited past retries) still freeze the trail (D23).
        True when the answer was delivered."""
        try:
            await self._deliver(run, result)
        except Exception as exc:
            log.exception("[%s] delivery failed", run.run_id)
            await self._fail(run, f"the answer could not be posted: {_error_text(exc)}")
            return False
        return True

    async def _run_guarded(self, run: RunState, call: Callable[[], Awaitable[RunResult]], budget_s: float
                           ) -> RunResult | None:
        """Watchdog + stop + error handling around one agent run. None means the user was told why."""
        run.task = asyncio.current_task()
        watchdog = asyncio.timeout(budget_s)
        try:
            async with watchdog:
                return await call()
        except TimeoutError as exc:                 # the watchdog, or a timeout raised inside the agent
            log.warning("[%s] run timed out", run.run_id, exc_info=not watchdog.expired())
            await self._fail(run, f"timed out after {int(budget_s)} s (watchdog)" if watchdog.expired()
                             else _error_text(exc))
        except asyncio.CancelledError:
            if run.stopped_by is None:              # a real cancellation (shutdown), not our stop
                await self._fail(run, "interrupted: the bot is restarting. Mention me to run it again.")
                raise
            if run.task is not None:
                run.task.uncancel()
            await self._fail(run, f"stopped by <@{run.stopped_by}>", notice=False)
        except Exception as exc:
            log.exception("[%s] run failed", run.run_id)
            await self._fail(run, _error_text(exc))
        finally:
            run.task = None                         # delivery is never cancelled by a late Stop click
        return None

    async def _enforce_stop(self, run: RunState) -> None:
        """Cooperative first (the agent sees ``status.stop_requested``), then cancel after a grace period."""
        task = run.task
        if task is None:
            return
        await asyncio.wait({task}, timeout=self.stop_grace_s)
        if run.task is task and not task.done():
            task.cancel()

    async def _fail(self, run: RunState, msg: str, *, notice: bool = True) -> None:
        """Freeze everything that could look live and tell the user. Never raises: it is the reporter."""
        if run.trail is not None:
            await run.trail.fail(msg)
        if run.placeholder_ts:                      # a "writing the report" placeholder must not look live
            try:
                await self._send(run, [_context(f"_Report not available: {msg}_")], META_REPORT,
                                 update_ts=run.placeholder_ts)
            except Exception as exc:  # noqa: BLE001
                log.warning("[%s] could not replace the report placeholder: %s", run.run_id, _error_text(exc))
            run.placeholder_ts = None
        if notice:
            await self._notice(run.channel, run.thread_ts, f":warning: Run `{run.run_id}` failed: {msg}",
                               user=run.private_user, kind=META_ERROR, run_id=run.run_id)

    async def _early_card(self, run: RunState, card: ConclusionCard) -> None:
        """Card first (D10): post the preliminary card and a report placeholder as soon as agents finish."""
        card.run_id = card.run_id or run.run_id
        if run.private_user:
            return                                  # ephemeral early cards were noise and cannot be edited (D12)
        blocks = self.present.card(card)
        if run.card_ts is None:
            run.card_ts = await self._send(run, blocks, META_CARD, note=self.present.note(card))
            run.placeholder_ts = await self._send(run, [_context("_Writing the full report..._")], META_REPORT)
        else:
            await self._send(run, blocks, META_CARD, note=self.present.note(card), update_ts=run.card_ts)

    async def _deliver(self, run: RunState, result: RunResult, *, shared_by: str | None = None) -> None:
        """Final card (edited in place), report pages in the thread, approval cards, frozen trail."""
        run.result = result
        if result.card is not None:
            result.card.run_id = result.card.run_id or result.run_id
            blocks = self.present.card(result.card)
            if shared_by:
                blocks = blocks[: MAX_BLOCKS - 1] + [_context(f"Shared by <@{shared_by}> - run `{run.run_id}`")]
            run.card_ts = await self._send(run, blocks, META_CARD, note=self.present.note(result.card),
                                           update_ts=run.card_ts)
            if run.thread_ts is None:               # top-level card (shared preview / DM): report in its thread
                run.thread_ts = run.card_ts
        pages = self.present.pages(result.answer_markdown) if result.answer_markdown.strip() else []
        if not pages and result.card is None:
            log.warning("[%s] run returned neither a card nor an answer", run.run_id)
            pages = plain_pages("_The run finished without producing an answer._")
        # The value is the agent's run id: memories are keyed by it, so a vote still lands after a restart.
        feedback = _actions("omniops_feedback", _button(":+1: Helpful", ACTION_VOTE_UP, result.run_id),
                            _button(":-1: Not helpful", ACTION_VOTE_DOWN, result.run_id))
        if pages and len(pages[-1]) < MAX_BLOCKS:
            pages[-1] = pages[-1] + [feedback]
        else:
            pages.append([feedback])
        for i, page in enumerate(pages):
            await self._send(run, page, META_REPORT, update_ts=run.placeholder_ts if i == 0 else None)
            if i == 0:
                run.placeholder_ts = None           # page 1 is delivered: a later failure must not overwrite it
        for write in result.staged_writes:
            await self._stage_approval(run, write)
        if run.trail is not None:
            line = _finish_line(result)
            if result.status == "error":
                await run.trail.fail(line)
            else:
                await run.trail.finish(line)

    # -- staged writes (D11) ------------------------------------------------------------------------

    async def _stage_approval(self, run: RunState, write: dict[str, Any]) -> None:
        wid = str(write.get("id") or write.get("write_id") or "")
        if self.deps.settings.write_mode == "deny":
            await self._notice(run.channel, run.thread_ts, f"Staged write `{wid or '?'}` was not offered for approval: "
                               "writes are disabled (OMNIOPS_WRITE_MODE=deny).", run_id=run.run_id)
            return
        engine, owned = self._engine_for(wid, write)
        if not wid or engine is None:
            await self._notice(run.channel, run.thread_ts, f":warning: Staged write `{wid or '?'}` cannot be approved "
                               "from Slack (its engine is gone); nothing was executed.", run_id=run.run_id)
            return
        ttl = WRITE_TTL_S
        if isinstance(write.get("expires"), (int, float)):
            ttl = max(0.0, float(write["expires"]) - time.time())
        self.writes.put(wid, PendingWrite(write, engine, owned, run.run_id, run.channel, run.thread_ts), ttl_s=ttl)
        confirm = {"title": {"type": "plain_text", "text": "Run this write?"},
                   "text": {"type": "mrkdwn", "text": _clip(str(write.get("summary", wid)), 300)},
                   "confirm": {"type": "plain_text", "text": "Approve"}, "deny": {"type": "plain_text", "text": "Back"}}
        blocks = self.present.approval(write)[: MAX_BLOCKS - 2] + [
            _context("Staged, not executed. A human must approve it; it expires in 30 minutes."),
            _actions(f"omniops_write_{wid}", _button("Approve", ACTION_APPROVE, wid, "primary", confirm),
                     _button("Cancel", ACTION_CANCEL, wid, "danger"))]
        await self._send(run, blocks, META_APPROVAL,
                         note=f"Approval needed: {_escape(_clip(str(write.get('summary', wid)), 200))}")

    def _engine_for(self, wid: str, write: dict[str, Any]) -> tuple[Any, bool]:
        """The run's engine when the write carries it, else one rebuilt from the staged table/filters."""
        eng = write.get("engine")
        if eng is not None and hasattr(eng, "execute_write"):
            return eng, False
        if not wid or not write.get("table") or not isinstance(write.get("filters"), dict):
            return None, False
        from omniops.engine.engine import SQLEngine, StagedWrite

        now = time.time()
        eng = SQLEngine(self.deps.catalog, write_mode=self.deps.settings.write_mode)
        eng.staged_writes[wid] = StagedWrite(wid, str(write["table"]), dict(write["filters"]),
                                             str(write.get("summary", "")), float(write.get("created", now)),
                                             float(write.get("expires", now + WRITE_TTL_S)))
        return eng, True

    async def _decide_write(self, pending: PendingWrite, wid: str, approve: bool, user: str, msg_ts: str | None
                            ) -> None:
        run = RunState(pending.run_id, "write", pending.channel, pending.thread_ts)
        _RUN_ID.set(pending.run_id)
        content = self.present.approval(pending.write)[: MAX_BLOCKS - 1]
        try:
            if not approve:
                line = f":no_entry_sign: Cancelled by <@{user}>; nothing was executed."
            else:
                try:
                    res = await pending.engine.execute_write(wid)
                    first = res.as_dicts(1)[0] if res.rows else {}
                    inputs = _write_inputs(pending.engine, wid, pending.write)
                    detail = "; ".join(f"{k}={_escape(_clip(str(v), 200))}" for k, v in first.items()
                                       if v is not None and k not in inputs)
                    line = f":white_check_mark: Approved by <@{user}> and executed ({res.total_rows} row(s)). {detail}"
                except Exception as exc:
                    log.exception("[%s] staged write %s failed", pending.run_id, wid)
                    line = f":x: Approved by <@{user}>, but the write failed: {_error_text(exc)}"
        finally:
            if pending.owned:
                pending.engine.close()
        line = _clip(line, SECTION_CHARS)
        if msg_ts:
            await self._send(run, content + [_context(line)], META_APPROVAL, note=line, update_ts=msg_ts)
        else:
            await self._notice(pending.channel, pending.thread_ts, line, run_id=pending.run_id)

    # -- feedback -----------------------------------------------------------------------------------

    async def _vote(self, run_id: str, value: int, user: str, channel: str) -> None:
        if not run_id:
            return
        if not self.votes.first(f"{run_id}:{user}"):          # double clicks and repeat votes count once
            await self._notice(channel, None, "You already voted on this answer.", user=user)
            return
        memory = self.deps.memory
        if memory is None or not hasattr(memory, "vote"):
            log.warning("[%s] feedback %+d from %s not stored: long-term memory is off", run_id, value, user)
            await self._notice(channel, None, "Thanks for the feedback.", user=user)
            return
        if inspect.iscoroutinefunction(memory.vote):
            await memory.vote(run_id, value)
        else:
            await asyncio.to_thread(memory.vote, run_id, value)        # sqlite stays off the event loop (4.5)
        log.info("[%s] feedback %+d from %s", run_id, value, user)
        await self._notice(channel, None, "Thanks, feedback recorded.", user=user)

    # -- Slack I/O helpers ---------------------------------------------------------------------------

    async def _send(self, run: RunState, blocks: list[dict[str, Any]], kind: str, *, note: str | None = None,
                    update_ts: str | None = None) -> str:
        """Post (or edit) one message; if Slack rejects the blocks, degrade to plain-text chunks (4.4).

        The text view of blocks is unescaped, so it is escaped again before it goes back to Slack: a quoted
        ``<!channel>`` must not become a live broadcast in the notification text or the plain-text pages."""
        text = note or _escape(self.present.note(self.present.to_text([b for b in blocks
                                                                       if b.get("type") != "actions"])))
        try:
            return await self._raw_send(run, blocks, text or "OmniOps", kind, update_ts)
        except SlackApiError as exc:
            if _slack_error(exc) not in _BLOCK_ERRORS:
                raise
            log.warning("[%s] Slack rejected a %s message (%s); degrading to plain text", run.run_id, kind,
                        _slack_error(exc))
        actions = [b for b in blocks if b.get("type") == "actions"]
        plain = _escape(self.present.to_text([b for b in blocks if b.get("type") != "actions"])) or text
        chunks = _chunks(plain, SECTION_CHARS)
        groups = [chunks[i:i + PAGE_SECTIONS] for i in range(0, len(chunks), PAGE_SECTIONS)] or [[text]]
        first = ""
        for i, group in enumerate(groups):
            page = [_section(c) for c in group] + (actions if i == len(groups) - 1 else [])
            ts = await self._raw_send(run, page, _clip(text, 300), kind, update_ts if i == 0 else None)
            first = first or ts
        return first

    async def _raw_send(self, run: RunState, blocks: list[dict[str, Any]], text: str, kind: str,
                        update_ts: str | None) -> str:
        meta = {"event_type": kind, "event_payload": {"run_id": run.run_id}}
        if run.private_user:
            await self.web.chat_postEphemeral(channel=run.channel, user=run.private_user, text=text, blocks=blocks,
                                              thread_ts=run.thread_ts)
            return ""
        if update_ts:
            await self.web.chat_update(channel=run.channel, ts=update_ts, text=text, blocks=blocks, metadata=meta)
            return update_ts
        resp = await self.web.chat_postMessage(channel=run.channel, thread_ts=run.thread_ts, text=text, blocks=blocks,
                                               metadata=meta, unfurl_links=False, unfurl_media=False)
        return str(resp["ts"])

    async def _notice(self, channel: str, thread_ts: str | None, text: str, *, user: str | None = None,
                      kind: str = META_NOTICE, run_id: str = "") -> None:
        """A short message; only for ``user`` when given (their DM if the bot cannot post ephemerally in
        ``channel``). Never raises (it reports other failures)."""
        text = _clip(text, SECTION_CHARS)
        try:
            if user:
                await self._to_user(channel, user, text, [_section(text)], thread_ts=thread_ts, kind=kind,
                                    run_id=run_id)
            else:
                await self.web.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text, blocks=[_section(text)],
                                                metadata={"event_type": kind, "event_payload": {"run_id": run_id}})
        except Exception as exc:  # noqa: BLE001
            log.warning("could not post notice to %s: %s", channel, _error_text(exc))

    async def _to_user(self, channel: str, user: str, text: str, blocks: list[dict[str, Any]], *,
                       thread_ts: str | None = None, response_url: str | None = None, kind: str = META_NOTICE,
                       run_id: str = "") -> None:
        """Something only ``user`` may see, delivered even where the bot is not a member (a slash command
        from a private channel or a DM between people): the command's response_url, else an ephemeral
        message in ``channel``, else the user's DM with the bot. Raises only when every route failed."""
        if response_url:
            try:
                if await self.post_to_response_url(response_url, {"response_type": "ephemeral",
                                                                  "replace_original": False, "text": text,
                                                                  "blocks": blocks}):
                    return
                log.warning("response_url refused the message (expired or used up); trying chat.postEphemeral")
            except Exception as exc:  # noqa: BLE001 - unreachable: the next route still reaches the user
                log.warning("response_url delivery failed: %s", _error_text(exc))
        try:
            await self.web.chat_postEphemeral(channel=channel, user=user, text=text, blocks=blocks,
                                              thread_ts=thread_ts)
            return
        except SlackApiError as exc:
            log.warning("ephemeral message to %s in %s failed (%s); sending it to their DM", user, channel,
                        _slack_error(exc))
        dm = await self.web.conversations_open(users=user)
        await self.web.chat_postMessage(channel=str(dm["channel"]["id"]), text=text, blocks=blocks,
                                        metadata={"event_type": kind, "event_payload": {"run_id": run_id}})

    async def _permalink(self, channel: str, ts: str) -> str | None:
        try:
            resp = await self.web.chat_getPermalink(channel=channel, message_ts=ts)
            return str(resp.get("permalink") or "") or None
        except SlackApiError as exc:
            log.warning("chat.getPermalink failed: %s", _slack_error(exc))
            return None

    async def _bot_user_id(self) -> str:
        if self._bot_uid is None:
            resp = await self.web.auth_test()
            self._bot_uid = str(resp.get("user_id") or "")
            self._own_ids.update(str(x) for x in (resp.get("user_id"), resp.get("bot_id")) if x)
        return self._bot_uid

    async def _thread(self, channel: str, thread_ts: str) -> tuple[dict[str, Any] | None, str, bool]:
        """(root message, whole transcript for the model, whether OmniOps already answered in this thread).

        Replies come oldest first, a page at a time: every page is read (up to ``THREAD_MAX_PAGES``), so the
        newest replies and the latest answer of a long incident thread are not lost. Callers trim the
        transcript with ``_keep_ends``."""
        msgs: list[dict[str, Any]] = []
        seen: set[str] = set()
        cursor: str | None = None
        for _ in range(THREAD_MAX_PAGES):
            resp = await self.web.conversations_replies(channel=channel, ts=thread_ts, limit=THREAD_PAGE_SIZE,
                                                        include_all_metadata=True, cursor=cursor)
            for m in resp.get("messages") or []:
                if str(m.get("ts")) not in seen:    # the root can repeat on every page
                    seen.add(str(m.get("ts")))
                    msgs.append(m)
            cursor = str((resp.get("response_metadata") or {}).get("next_cursor") or "") or None
            if not resp.get("has_more") or cursor is None:
                break
        else:
            log.warning("thread %s/%s has more than %d pages of replies; newer ones are not read", channel,
                        thread_ts, THREAD_MAX_PAGES)
        bot_uid = await self._bot_user_id()
        root = {**msgs[0], "channel": channel} if msgs and msgs[0].get("ts") == thread_ts else None
        lines: list[str] = []
        answered = False
        for m in msgs:
            kind = str((m.get("metadata") or {}).get("event_type", ""))
            mine = kind.startswith("omniops_") or (bool(bot_uid) and m.get("user") == bot_uid)
            if kind == STATUS_EVENT_TYPE or (mine and kind in (META_NOTICE, META_ERROR)):
                continue
            answered = answered or kind in (META_CARD, META_REPORT)
            body = self.present.to_text(m["blocks"]) if mine and m.get("blocks") else _message_text(m)
            lines.append(f"[{_speaker(m, mine)}] {body}")
        return root, "\n".join(lines), answered


def build_bot(deps: Deps, *, client: AsyncWebClient | None = None, runner: Runner | None = None,
              alerts: Any | None = None, renderer: Any | None = None, bot_token: str | None = None,
              signing_secret: str | None = None, request_verification_enabled: bool = True,
              status_interval_s: float = 1.5) -> OmniOpsBot:
    """The bot with its registries and task tracking (the simulator and ``serve`` need both)."""
    return OmniOpsBot(deps, client=client, runner=runner, alerts=alerts, renderer=renderer, bot_token=bot_token,
                      signing_secret=signing_secret, request_verification_enabled=request_verification_enabled,
                      status_interval_s=status_interval_s)


def build_app(deps: Deps, *, client: AsyncWebClient | None = None, runner: Runner | None = None,
              alerts: Any | None = None, renderer: Any | None = None, bot_token: str | None = None,
              signing_secret: str | None = None, request_verification_enabled: bool = True) -> AsyncApp:
    """The Bolt app. ``runner``/``alerts``/``renderer`` default to the real agent, alert and render modules.

    ``AsyncApp`` never calls auth.test at construction (Bolt verifies the token lazily on the first
    request, through ``client`` when one is injected), so building it needs no network.
    """
    return build_bot(deps, client=client, runner=runner, alerts=alerts, renderer=renderer, bot_token=bot_token,
                     signing_secret=signing_secret, request_verification_enabled=request_verification_enabled).app


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


async def _maybe(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _accepts(fn: Callable[..., Any], name: str) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _event_key(body: dict[str, Any]) -> str | None:
    eid = body.get("event_id")
    return f"event:{eid}" if eid else None


def _post_keys(event: dict[str, Any]) -> tuple[str | None, str | None]:
    """The post itself: message and app_mention deliveries of one post share these."""
    cid = event.get("client_msg_id")
    return f"post:{event.get('channel')}:{event.get('ts')}", (f"client_msg:{cid}" if cid else None)


def _from_bot(event: dict[str, Any]) -> bool:
    return bool(event.get("bot_id")) or event.get("subtype") == "bot_message"


def _actor(body: dict[str, Any]) -> tuple[str, str, str | None]:
    user = str((body.get("user") or {}).get("id", ""))
    container = body.get("container") or {}
    channel = str((body.get("channel") or {}).get("id") or container.get("channel_id", ""))
    return user, channel, container.get("message_ts")


def _write_inputs(engine: Any, wid: str, write: dict[str, Any]) -> set[str]:
    """Columns a write only sends (filter-only), so its outcome line lists what came back, not the payload."""
    staged = (getattr(engine, "staged_writes", None) or {}).get(wid)
    table = getattr(staged, "table", None) or write.get("table")
    catalog = getattr(engine, "catalog", None)
    entry = catalog.get(table) if catalog is not None and table else None
    if entry is not None:
        return {c.name for c in entry.table.columns if c.filter_only}
    return set(getattr(staged, "filters", None) or write.get("filters") or {})


def _strip_mentions(text: str, bot_uid: str) -> str:
    """Drop the bot's own mention and collapse spaces within lines; line breaks and single blank lines stay
    (multi-line JSON, PromQL, numbered requirements reach the flows as written)."""
    if bot_uid:
        text = re.sub(rf"<@{re.escape(bot_uid)}(\|[^>]*)?>", " ", text)
    lines = "\n".join(" ".join(line.split()) for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", lines).strip()


def _speaker(m: dict[str, Any], mine: bool) -> str:
    if mine:
        return "OmniOps"
    if m.get("bot_id") or m.get("subtype") == "bot_message":
        return str(m.get("username") or (m.get("bot_profile") or {}).get("name") or "integration")
    return f"<@{m['user']}>" if m.get("user") else "unknown"


def _message_text(m: dict[str, Any]) -> str:
    parts = [str(m.get("text") or "")]
    for a in m.get("attachments") or []:
        parts += [str(a.get(k)) for k in ("pretext", "title", "text") if a.get(k)]
        parts += [f"{f.get('title')}: {f.get('value')}" for f in a.get("fields") or [] if isinstance(f, dict)]
    return " | ".join(p for p in parts if p.strip())


def _finish_line(result: RunResult) -> str:
    label = {"ok": "Done", "insufficient_data": "Done: not enough data for a conclusion",
             "timeout": "Done with partial results (time budget reached)", "stopped": "Stopped: partial results",
             "error": "Failed"}.get(result.status, "Done")
    s = result.stats
    return f"{label} - {s.sql_queries} queries, {s.llm_calls} model calls"


def _error_text(exc: BaseException) -> str:
    return _clip(f"{type(exc).__name__}: {_escape(_redact(str(exc)))}", 300)


def _escape(text: str) -> str:
    """Plain text -> mrkdwn that shows it literally (no links, mentions or broadcasts survive)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def _post_response_url(url: str, payload: dict[str, Any]) -> bool:
    """POST to a slash command's response_url (5 uses within 30 minutes); True when Slack accepted it."""
    from slack_sdk.webhook.async_client import AsyncWebhookClient

    resp = await AsyncWebhookClient(url).send_dict(payload)
    return resp.status_code == 200


def _redact(text: str) -> str:
    return _SECRETS.sub(lambda m: (m.group(2) or "") + "***", text)


def _slack_error(exc: SlackApiError) -> str:
    try:
        return str(exc.response.get("error") or "")
    except Exception:  # noqa: BLE001
        return ""


def _clip(text: str, limit: int) -> str:
    s = str(text).strip()
    return s if len(s) <= limit else s[: limit - 3].rstrip() + "..."


def _keep_ends(text: str, limit: int) -> str:
    """Keep the first line (the root) and the newest lines; say how much was dropped."""
    if len(text) <= limit:
        return text
    first, _, rest = text.partition("\n")
    first = first[: limit // 3]
    tail = rest[-(limit - len(first) - 60):]           # room for the marker line: the result fits in limit
    return f"{first}\n[... {len(rest) - len(tail)} chars of older replies omitted ...]\n{tail}"


def _chunks(text: str, size: int) -> list[str]:
    """Split on line boundaries into pieces of at most ``size`` characters; never drops text."""
    out: list[str] = []
    cur = ""
    for line in text.split("\n"):
        while len(line) > size:
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:size])
            line = line[size:]
        if cur and len(cur) + 1 + len(line) > size:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur.strip():
        out.append(cur)
    return [c for c in out if c.strip()]


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:3000] or " "}}


def _sections(text: str) -> list[dict[str, Any]]:
    return [_section(c) for c in _chunks(text, SECTION_CHARS)]


def plain_pages(markdown: str) -> list[list[dict[str, Any]]]:
    """Plain mrkdwn pages: the last-resort rendering that always fits Slack's limits."""
    sections = _sections(escape_mrkdwn(markdown)) or [_section("_(empty)_")]
    return [sections[i:i + PAGE_SECTIONS] for i in range(0, len(sections), PAGE_SECTIONS)]


def _context(text: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text[:3000]}]}


def _button(text: str, action_id: str, value: str, style: str | None = None,
            confirm: dict[str, Any] | None = None) -> dict[str, Any]:
    b: dict[str, Any] = {"type": "button", "action_id": action_id, "value": value,
                         "text": {"type": "plain_text", "text": text, "emoji": True}}
    if style:
        b["style"] = style
    if confirm:
        b["confirm"] = confirm
    return b


def _actions(block_id: str, *buttons: dict[str, Any]) -> dict[str, Any]:
    return {"type": "actions", "block_id": block_id, "elements": list(buttons)}


def _as_blocks(x: Any) -> list[dict[str, Any]]:
    if isinstance(x, dict) and isinstance(x.get("blocks"), list):
        x = x["blocks"]
    return [b for b in x if isinstance(b, dict)] if isinstance(x, list) else []


def _content_only(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Interactive elements are owned by this module (stable action ids, D10); keep only link buttons."""
    out = []
    for b in blocks:
        if b.get("type") == "actions":
            links = [e for e in b.get("elements", []) if isinstance(e, dict) and e.get("url")]
            if links:
                out.append({**b, "elements": links})
        else:
            out.append(b)
    return out


def _plain_card(card: ConclusionCard) -> list[dict[str, Any]]:
    esc = escape_mrkdwn
    lines = [f"*{esc(card.title)}*", esc(card.verdict)]
    if card.root_cause:
        lines.append(f"*Root cause ({card.causal_level}):* {esc(card.root_cause)}")
    lines.append(f"*Confidence:* {card.confidence:.0%} - *Status:* {card.status}")
    if card.mitigation:
        lines.append("*Mitigation:*\n" + "\n".join(f"- {esc(m)}" for m in card.mitigation[:5]))
    return _sections("\n".join(lines))


def _blocks_text(blocks: list[dict[str, Any]]) -> str:
    out: list[str] = []
    for b in blocks:
        if b.get("type") in ("section", "header"):
            out.append(str((b.get("text") or {}).get("text") or ""))
            out += [str(f.get("text", "")) for f in b.get("fields") or [] if isinstance(f, dict)]
        elif b.get("type") == "context":
            out += [str(e.get("text", "")) for e in b.get("elements") or [] if isinstance(e, dict)]
    return "\n".join(s for s in out if s.strip())
