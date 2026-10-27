"""Slack simulator: a fake workspace and Web API driving the *real* Bolt app in-process.

Used by the functionality tests, the CLI (``omniops ask`` / ``omniops investigate``) and the
benchmarks (o11y-bench DMs the task to the bot; the Arena posts alerts and customer reports).

Lessons encoded (OpsIntelligence retrospective):
- D27 / 4.1: test the path production uses. Events enter through ``AsyncApp.async_dispatch`` with the
  Socket Mode envelopes Slack sends (``event_callback``, ``block_actions``, slash commands), so the
  real middleware (auth, self-event filter), listeners and background tasks run unchanged.
- 4.4: the fake Web API enforces the Slack limits that bit production (50 blocks per message, ~3,000
  characters per section, 150-character headers, unique action ids per actions block, ``chat.update``
  failing above 4,000 text characters, silent truncation above 40,000) and keeps every version of
  every edited message, so tests and benchmark archives see exactly what a user saw.
- 4.5: deliveries can be replayed with the same event id (Slack's retry), and a post that mentions
  the bot is delivered twice (``message`` + ``app_mention``), like Slack does. The bot's own posts
  are echoed back as events, so the self-event filter is exercised too.
- D11 / D12: ``auto_approve_writes`` clicks Approve on approval cards as a recorded simulated human;
  ephemeral messages are kept per user, and clicks on them omit the message body (4.4). A slash command's
  ``response_url`` works like Slack's (ephemeral to the invoker, 5 uses within 30 minutes) even in a
  conversation the bot is not a member of, where posts are refused (``add_channel(member=False)``).
- 3.3: an ``anchor`` (pinned clock) is passed to agent flows that accept one.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import itertools
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from slack_bolt.request.async_request import AsyncBoltRequest
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.async_slack_response import AsyncSlackResponse

from omniops.agent.types import Alert, RunResult
from omniops.slack.app import ACTION_APPROVE, META_CARD, META_REPORT, AgentRunner, Runner, build_bot
from omniops.slack.status import STATUS_EVENT_TYPE

if TYPE_CHECKING:
    from omniops.agent.context import Deps
    from omniops.engine.timeutil import TimeAnchor
    from omniops.slack.status import StatusTrail

log = logging.getLogger("omniops.slack.simulator")

TEAM_ID = "T0SIMTEAM"
APP_ID = "A0OMNIOPS"
BOT_USER_ID = "U0OMNIOPS"
BOT_ID = "B0OMNIOPS"
INTEGRATION_BOT_ID = "B0INTEGRATN"
DEFAULT_USER = "U_USER"
APPROVER = "U0APPROVER"
WORKSPACE_URL = "https://omniops-sim.slack.com/"

MAX_BLOCKS = 50
SECTION_MAX = 3000
FIELD_MAX = 2000
HEADER_MAX = 150
BUTTON_TEXT_MAX = 75
BUTTON_VALUE_MAX = 2000
UPDATE_TEXT_MAX = 4000
POST_TEXT_MAX = 40_000
RESPONSE_URL_USES = 5
RESPONSE_URL_TTL_S = 1800.0
_KNOWN_BLOCKS = {"section", "header", "context", "actions", "divider", "image", "rich_text", "table", "input",
                 "file", "markdown", "video"}


# ---------------------------------------------------------------------------------------------
# Workspace state
# ---------------------------------------------------------------------------------------------


@dataclass
class Workspace:
    """Everything the fake Slack knows. Messages are dicts shaped like Slack's, plus ``edits``."""

    channels: dict[str, dict[str, Any]] = field(default_factory=dict)
    users: dict[str, dict[str, Any]] = field(default_factory=dict)
    messages: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    ephemeral: list[dict[str, Any]] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    reactions: list[dict[str, Any]] = field(default_factory=list)
    views: list[dict[str, Any]] = field(default_factory=list)
    assistant: list[dict[str, Any]] = field(default_factory=list)
    log: list[dict[str, Any]] = field(default_factory=list)
    _last_ts: float = 0.0
    _seq: itertools.count[int] = field(default_factory=itertools.count)

    def next_ts(self) -> str:
        """Slack-style ``seconds.micros`` timestamps, strictly increasing (they are message ids)."""
        self._last_ts = max(time.time(), self._last_ts + 0.000001)
        return f"{self._last_ts:.6f}"

    def note_ts(self, ts: str) -> None:
        try:
            self._last_ts = max(self._last_ts, float(ts))
        except ValueError:
            pass

    def record(self, kind: str, **data: Any) -> None:
        self.log.append({"seq": next(self._seq), "at": time.time(), "kind": kind, **data})

    def add_channel(self, cid: str, name: str | None = None, *, user: str | None = None,
                    member: bool = True) -> dict[str, Any]:
        if cid not in self.channels:
            is_im = cid.startswith("D")
            self.channels[cid] = {"id": cid, "name": name or ("" if is_im else cid.lower()), "is_im": is_im,
                                  "is_channel": cid.startswith("C"), "is_group": cid.startswith("G"),
                                  "is_private": not cid.startswith("C"), "is_member": member,
                                  "created": int(time.time()), **({"user": user} if user else {})}
            self.messages.setdefault(cid, [])
        return self.channels[cid]

    def add_user(self, uid: str, name: str | None = None, *, is_bot: bool = False) -> dict[str, Any]:
        if uid not in self.users:
            nm = name or uid.lower()
            self.users[uid] = {"id": uid, "team_id": TEAM_ID, "name": nm, "real_name": nm, "is_bot": is_bot,
                               "deleted": False, "tz": "UTC", "profile": {"display_name": nm, "real_name": nm}}
        return self.users[uid]

    def dm_channel(self, user: str) -> str:
        for cid, ch in self.channels.items():
            if ch.get("is_im") and ch.get("user") == user:
                return cid
        cid = "D" + uuid.uuid5(uuid.NAMESPACE_OID, user).hex[:9].upper()
        self.add_channel(cid, user=user)
        return cid

    def channel_type(self, cid: str) -> str:
        ch = self.channels.get(cid) or {}
        return "im" if ch.get("is_im") else ("group" if ch.get("is_group") else "channel")

    def find(self, channel: str, ts: str) -> dict[str, Any] | None:
        return next((m for m in self.messages.get(channel, []) if m["ts"] == ts), None)

    def store(self, channel: str, message: dict[str, Any]) -> dict[str, Any]:
        self.add_channel(channel)
        msgs = self.messages[channel]
        message.setdefault("edits", [])
        message.setdefault("edited_at", time.time())
        msgs.append(message)
        msgs.sort(key=lambda m: float(m["ts"]))
        root_ts = message.get("thread_ts")
        if root_ts and root_ts != message["ts"]:
            root = self.find(channel, root_ts)
            if root is not None:
                root["thread_ts"] = root_ts
                root["reply_count"] = int(root.get("reply_count", 0)) + 1
                root["latest_reply"] = message["ts"]
        return message


def _view(m: dict[str, Any], *, metadata: bool = True) -> dict[str, Any]:
    """A message as the Web API returns it (no simulator bookkeeping unless asked for)."""
    out = {k: copy.deepcopy(v) for k, v in m.items() if k not in ("edits", "edited_at", "deleted")}
    if not metadata:
        out.pop("metadata", None)
    return out


def _block_problems(blocks: Any) -> list[str]:
    """The checks Slack applies before accepting blocks (one bad block rejects the whole message, 4.4)."""
    if not isinstance(blocks, list):
        return ["blocks must be an array"]
    problems: list[str] = []
    if len(blocks) > MAX_BLOCKS:
        problems.append(f"no more than {MAX_BLOCKS} items allowed [json-pointer:/blocks]")
    ids: set[str] = set()
    for n, b in enumerate(blocks):
        where = f"[json-pointer:/blocks/{n}]"
        if not isinstance(b, dict) or b.get("type") not in _KNOWN_BLOCKS:
            problems.append(f"unsupported block type {where}")
            continue
        bid = b.get("block_id")
        if bid is not None:
            if not isinstance(bid, str) or not bid or len(bid) > 255 or bid in ids:
                problems.append(f"invalid or duplicate block_id {where}")
            ids.add(str(bid))
        t = b["type"]
        if t == "section":
            text, fields = (b.get("text") or {}).get("text"), b.get("fields") or []
            if text is None and not fields:
                problems.append(f"section needs text or fields {where}")
            if text is not None and not 0 < len(text) <= SECTION_MAX:
                problems.append(f"must be between 1 and {SECTION_MAX} characters {where}/text")
            if len(fields) > 10 or any(not 0 < len(str(f.get("text", ""))) <= FIELD_MAX for f in fields):
                problems.append(f"invalid fields {where}/fields")
        elif t == "header":
            text = (b.get("text") or {}).get("text", "")
            if (b.get("text") or {}).get("type") != "plain_text" or not 0 < len(text) <= HEADER_MAX:
                problems.append(f"header must be plain_text of 1-{HEADER_MAX} characters {where}")
        elif t == "context":
            if not 0 < len(b.get("elements") or []) <= 10:
                problems.append(f"context needs 1-10 elements {where}")
        elif t == "actions":
            els = b.get("elements") or []
            aids = [e.get("action_id") for e in els if isinstance(e, dict)]
            if not 0 < len(els) <= 25 or len(set(aids)) != len(aids):
                problems.append(f"action_ids must be unique within an actions block {where}")
            for e in els:
                if len(str((e.get("text") or {}).get("text", ""))) > BUTTON_TEXT_MAX or \
                        len(str(e.get("value", ""))) > BUTTON_VALUE_MAX:
                    problems.append(f"button text or value too long {where}")
    return problems


# ---------------------------------------------------------------------------------------------
# Fake Web API
# ---------------------------------------------------------------------------------------------


class FakeAsyncWebClient(AsyncWebClient):
    """``AsyncWebClient`` whose transport is the in-memory workspace. Every call is recorded."""

    def __init__(self, workspace: Workspace, *, user_id: str = BOT_USER_ID, bot_id: str = BOT_ID,
                 on_message: Callable[[str, str, dict[str, Any]], None] | None = None) -> None:
        super().__init__(token="xoxb-simulator-not-a-secret", base_url="https://slack.simulator.invalid/api/")
        self.ws = workspace
        self.user_id = user_id
        self.bot_id = bot_id
        self.on_message = on_message           # (method, channel, message) after a post/update/ephemeral

    async def api_call(self, api_method: str, *, http_verb: str = "POST", files: dict[str, Any] | None = None,
                       data: Any = None, params: dict[str, Any] | None = None, json: dict[str, Any] | None = None,
                       headers: dict[str, Any] | None = None, auth: dict[str, Any] | None = None
                       ) -> AsyncSlackResponse:
        await asyncio.sleep(0)                 # a network hop: let other tasks interleave
        args: dict[str, Any] = {}
        for part in (params, data if isinstance(data, dict) else None, json):
            if part:
                args.update(part)
        handler = getattr(self, "_m_" + api_method.replace(".", "_"), None)
        payload = handler(args) if handler is not None else {"ok": False, "error": "unknown_method"}
        self.ws.record("api", method=api_method, ok=bool(payload.get("ok")), error=payload.get("error"),
                       args=copy.deepcopy(args))
        return AsyncSlackResponse(client=self, http_verb=http_verb, api_url=self.base_url + api_method,
                                  req_args={"json": args}, data=payload, headers={}, status_code=200).validate()

    async def files_upload_v2(self, **kwargs: Any) -> AsyncSlackResponse:  # type: ignore[override]
        """Files skip Slack's external-upload HTTP dance; a message with the file is posted when shared."""
        fid = "F" + uuid.uuid4().hex[:10].upper()
        content = kwargs.get("content")
        f = {"id": fid, "name": kwargs.get("filename") or kwargs.get("title") or fid, "title": kwargs.get("title"),
             "size": len(content) if isinstance(content, (str, bytes)) else 0, "user": self.user_id}
        self.ws.files.append({**f, "content": content})
        args = {"channel": kwargs.get("channel"), "thread_ts": kwargs.get("thread_ts"), "files": [f],
                "text": kwargs.get("initial_comment") or ""}
        if kwargs.get("channel"):
            res = self._post(args, subtype="file_share")
            if not res["ok"]:
                return await self._fail("files.upload", res)
        self.ws.record("api", method="files.upload_v2", ok=True, error=None, args={"file": f["name"]})
        return AsyncSlackResponse(client=self, http_verb="POST", api_url=self.base_url + "files.completeUploadExternal",
                                  req_args={}, data={"ok": True, "files": [f]}, headers={}, status_code=200)

    async def _fail(self, method: str, payload: dict[str, Any]) -> AsyncSlackResponse:
        return AsyncSlackResponse(client=self, http_verb="POST", api_url=self.base_url + method, req_args={},
                                  data=payload, headers={}, status_code=200).validate()

    # -- auth / users / conversations -----------------------------------------------------------

    def _m_auth_test(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "url": WORKSPACE_URL, "team": "OmniOps Simulator", "user": "omniops", "team_id": TEAM_ID,
                "user_id": self.user_id, "bot_id": self.bot_id, "is_enterprise_install": False}

    def _m_users_info(self, args: dict[str, Any]) -> dict[str, Any]:
        u = self.ws.users.get(str(args.get("user")))
        return {"ok": True, "user": copy.deepcopy(u)} if u else {"ok": False, "error": "user_not_found"}

    def _m_conversations_info(self, args: dict[str, Any]) -> dict[str, Any]:
        ch = self.ws.channels.get(str(args.get("channel")))
        return {"ok": True, "channel": copy.deepcopy(ch)} if ch else {"ok": False, "error": "channel_not_found"}

    def _m_conversations_open(self, args: dict[str, Any]) -> dict[str, Any]:
        users = [u for u in str(args.get("users") or "").split(",") if u]
        if len(users) != 1:
            return {"ok": False, "error": "not_enough_users" if not users else "too_many_users"}
        self.ws.add_user(users[0])
        return {"ok": True, "channel": {"id": self.ws.dm_channel(users[0])}}

    def _m_conversations_replies(self, args: dict[str, Any]) -> dict[str, Any]:
        channel, ts = str(args.get("channel")), str(args.get("ts"))
        if channel not in self.ws.channels:
            return {"ok": False, "error": "channel_not_found"}
        root = self.ws.find(channel, ts)
        if root is None:
            return {"ok": False, "error": "thread_not_found"}
        meta = _truthy(args.get("include_all_metadata"))
        thread = [m for m in self.ws.messages[channel] if m["ts"] == ts or m.get("thread_ts") == ts]
        limit = int(args.get("limit") or 1000)
        start = int(str(args.get("cursor") or "off:0").removeprefix("off:") or 0)   # oldest first, like Slack
        page = thread[start:start + limit]
        more = start + limit < len(thread)
        return {"ok": True, "messages": [_view(m, metadata=meta) for m in page], "has_more": more,
                "response_metadata": {"next_cursor": f"off:{start + limit}" if more else ""}}

    def _m_conversations_history(self, args: dict[str, Any]) -> dict[str, Any]:
        channel = str(args.get("channel"))
        if channel not in self.ws.channels:
            return {"ok": False, "error": "channel_not_found"}
        meta = _truthy(args.get("include_all_metadata"))
        top = [m for m in self.ws.messages[channel] if m.get("thread_ts") in (None, m["ts"])]
        if args.get("latest"):
            top = [m for m in top if float(m["ts"]) <= float(args["latest"])]
        if args.get("oldest"):
            top = [m for m in top if float(m["ts"]) >= float(args["oldest"])]
        limit = int(args.get("limit") or 100)
        newest = list(reversed(top))
        return {"ok": True, "messages": [_view(m, metadata=meta) for m in newest[:limit]],
                "has_more": len(newest) > limit}

    # -- chat ------------------------------------------------------------------------------------

    def _content_error(self, args: dict[str, Any]) -> dict[str, Any] | None:
        if not (args.get("text") or args.get("blocks") or args.get("attachments") or args.get("files")):
            return {"ok": False, "error": "no_text"}
        blocks = _json_arg(args.get("blocks"))
        if blocks is not None:
            problems = _block_problems(blocks)
            if problems:
                return {"ok": False, "error": "invalid_blocks", "errors": problems,
                        "response_metadata": {"messages": [f"[ERROR] {p}" for p in problems]}}
        return None

    def _access_error(self, channel: str, *, ephemeral: bool = False) -> dict[str, Any] | None:
        """Slack hides conversations the bot is not in: private ones do not exist for it; a public channel
        takes posts (chat:write.public) but not ephemeral messages."""
        ch = self.ws.channels.get(channel)
        if ch is None or (not ch.get("is_member") and ch.get("is_private")):
            return {"ok": False, "error": "channel_not_found"}
        if not ch.get("is_member") and ephemeral:
            return {"ok": False, "error": "not_in_channel"}
        return None

    def _post(self, args: dict[str, Any], *, subtype: str | None = None) -> dict[str, Any]:
        channel = str(args.get("channel") or "")
        err = self._access_error(channel) or self._content_error(args)
        if err:
            return err
        thread_ts = args.get("thread_ts")
        if thread_ts and self.ws.find(channel, str(thread_ts)) is None:
            return {"ok": False, "error": "thread_not_found"}
        ts = self.ws.next_ts()
        msg: dict[str, Any] = {"type": "message", "ts": ts, "user": self.user_id, "bot_id": self.bot_id,
                               "app_id": APP_ID, "team": TEAM_ID, "text": str(args.get("text") or "")[:POST_TEXT_MAX],
                               "bot_profile": {"id": self.bot_id, "app_id": APP_ID, "name": "OmniOps",
                                               "team_id": TEAM_ID}}
        for k in ("blocks", "attachments", "metadata"):
            v = _json_arg(args.get(k))
            if v is not None:
                msg[k] = v
        if args.get("files"):
            msg["files"] = args["files"]
        if subtype:
            msg["subtype"] = subtype
        if thread_ts:
            msg["thread_ts"] = str(thread_ts)
        self.ws.store(channel, msg)
        if self.on_message is not None:
            self.on_message("chat.postMessage", channel, msg)
        return {"ok": True, "channel": channel, "ts": ts, "message": _view(msg)}

    def _m_chat_postMessage(self, args: dict[str, Any]) -> dict[str, Any]:
        return self._post(args)

    def _m_chat_update(self, args: dict[str, Any]) -> dict[str, Any]:
        channel, ts = str(args.get("channel") or ""), str(args.get("ts") or "")
        msg = self.ws.find(channel, ts)
        if msg is None:
            return {"ok": False, "error": "message_not_found"}
        if msg.get("user") != self.user_id:
            return {"ok": False, "error": "cant_update_message"}
        if len(str(args.get("text") or "")) > UPDATE_TEXT_MAX:
            return {"ok": False, "error": "msg_too_long"}
        err = self._content_error({**args, "attachments": msg.get("attachments")})
        if err:
            return err
        msg["edits"].append({"text": msg.get("text", ""), "blocks": copy.deepcopy(msg.get("blocks") or []),
                             "attachments": copy.deepcopy(msg.get("attachments") or []),
                             "metadata": copy.deepcopy(msg.get("metadata")), "edited_at": msg["edited_at"]})
        msg["text"] = str(args.get("text") or "")
        for k in ("blocks", "attachments", "metadata"):
            v = _json_arg(args.get(k))
            if v is not None:
                msg[k] = v
        msg["edited_at"] = time.time()
        msg["edited"] = {"user": self.user_id, "ts": self.ws.next_ts()}
        if self.on_message is not None:
            self.on_message("chat.update", channel, msg)
        return {"ok": True, "channel": channel, "ts": ts, "text": msg["text"], "message": _view(msg)}

    def _m_chat_postEphemeral(self, args: dict[str, Any]) -> dict[str, Any]:
        channel, user = str(args.get("channel") or ""), str(args.get("user") or "")
        err = self._access_error(channel, ephemeral=True)
        if err:
            return err
        if not user:
            return {"ok": False, "error": "user_not_in_channel"}
        err = self._content_error(args)
        if err:
            return err
        msg: dict[str, Any] = {"type": "message", "subtype": "ephemeral", "ts": self.ws.next_ts(), "channel": channel,
                               "to_user": user, "user": self.user_id, "bot_id": self.bot_id,
                               "text": str(args.get("text") or ""), "blocks": _json_arg(args.get("blocks")) or [],
                               "thread_ts": args.get("thread_ts"), "edited_at": time.time()}
        self.ws.ephemeral.append(msg)
        if self.on_message is not None:
            self.on_message("chat.postEphemeral", channel, msg)
        return {"ok": True, "message_ts": msg["ts"]}

    def _m_chat_delete(self, args: dict[str, Any]) -> dict[str, Any]:
        channel, ts = str(args.get("channel") or ""), str(args.get("ts") or "")
        msg = self.ws.find(channel, ts)
        if msg is None:
            return {"ok": False, "error": "message_not_found"}
        if msg.get("user") != self.user_id:
            return {"ok": False, "error": "cant_delete_message"}
        self.ws.messages[channel].remove(msg)
        return {"ok": True, "channel": channel, "ts": ts}

    def _m_chat_getPermalink(self, args: dict[str, Any]) -> dict[str, Any]:
        channel, ts = str(args.get("channel") or ""), str(args.get("message_ts") or "")
        if self.ws.find(channel, ts) is None:
            return {"ok": False, "error": "message_not_found"}
        return {"ok": True, "channel": channel, "permalink": f"{WORKSPACE_URL}archives/{channel}/p{ts.replace('.', '')}"}

    # -- reactions, views, assistant ---------------------------------------------------------------

    def _m_reactions_add(self, args: dict[str, Any]) -> dict[str, Any]:
        key = {"channel": args.get("channel"), "ts": args.get("timestamp"), "name": args.get("name"),
               "user": self.user_id}
        if self.ws.find(str(key["channel"]), str(key["ts"])) is None:
            return {"ok": False, "error": "message_not_found"}
        if key in self.ws.reactions:
            return {"ok": False, "error": "already_reacted"}
        self.ws.reactions.append(key)
        return {"ok": True}

    def _m_views_open(self, args: dict[str, Any]) -> dict[str, Any]:
        view = {"id": "V" + uuid.uuid4().hex[:10].upper(), **(_json_arg(args.get("view")) or {})}
        self.ws.views.append(view)
        return {"ok": True, "view": copy.deepcopy(view)}

    def _m_assistant_threads_setStatus(self, args: dict[str, Any]) -> dict[str, Any]:
        self.ws.assistant.append({"kind": "status", **args})
        return {"ok": True}

    def _m_assistant_threads_setTitle(self, args: dict[str, Any]) -> dict[str, Any]:
        self.ws.assistant.append({"kind": "title", **args})
        return {"ok": True}


def _truthy(v: Any) -> bool:
    return v is True or str(v).lower() in {"1", "true"}


def _json_arg(v: Any) -> Any:
    """The SDK sends blocks/metadata as lists/dicts, or as JSON strings in form-encoded calls."""
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v
    return v


# ---------------------------------------------------------------------------------------------
# Recording runner
# ---------------------------------------------------------------------------------------------


class _RecordingRunner:
    """Delegates to the real (or stub) runner and keeps every RunResult for benchmarks and tests."""

    def __init__(self, inner: Runner, results: list[RunResult]) -> None:
        self.inner, self.results = inner, results

    async def investigate(self, alert: Alert, deps: Deps, *, status: StatusTrail | None = None,
                          thread: str | None = None) -> RunResult:
        r = await self.inner.investigate(alert, deps, status=status, thread=thread)
        if r.alert is None:
            r.alert = alert
        self.results.append(r)
        return r

    async def answer(self, question: str, deps: Deps, *, thread: str | None = None,
                     status: StatusTrail | None = None) -> RunResult:
        r = await self.inner.answer(question, deps, thread=thread, status=status)
        self.results.append(r)
        return r


# ---------------------------------------------------------------------------------------------
# The simulator
# ---------------------------------------------------------------------------------------------


class SlackSimulator:
    """A workspace with an alert channel, a general channel and users, wired to the real Bolt app."""

    def __init__(
        self,
        deps: Deps,
        runner: Runner | None = None,
        *,
        auto_approve_writes: bool = False,
        alert_channel: str = "C_ALERTS",
        anchor: TimeAnchor | None = None,
        alerts: Any | None = None,
        renderer: Any | None = None,
        status_interval_s: float = 1.5,
    ) -> None:
        self.workspace = ws = Workspace()
        self.alert_channel = alert_channel
        self.general_channel = "C_GENERAL"
        ws.add_channel(alert_channel, "alerts")
        ws.add_channel(self.general_channel, "general")
        ws.add_user(DEFAULT_USER, "oncall")
        ws.add_user(APPROVER, "approver")
        ws.add_user(BOT_USER_ID, "omniops", is_bot=True)
        self.bot_user_id, self.bot_id = BOT_USER_ID, BOT_ID
        self.client = FakeAsyncWebClient(ws, on_message=self._after_message)
        settings = deps.settings
        if alert_channel not in settings.alert_channels:   # copies: the caller's settings stay untouched
            deps = dataclasses.replace(deps, settings=dataclasses.replace(
                settings, alert_channels=[*settings.alert_channels, alert_channel]))
        self.deps = deps
        self.run_results: list[RunResult] = []
        self.runner = _RecordingRunner(runner or AgentRunner(anchor=anchor), self.run_results)
        self.bot = build_bot(deps, client=self.client, runner=self.runner, alerts=alerts, renderer=renderer,
                             request_verification_enabled=False, status_interval_s=status_interval_s)
        self.bot.post_to_response_url = self._response_url
        self._response_urls: dict[str, dict[str, Any]] = {}
        self.app = self.bot.app
        self.auto_approve_writes = auto_approve_writes
        self.approvals: list[dict[str, Any]] = []
        self._deliveries: list[dict[str, Any]] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self._clicked: set[tuple[str, str, str]] = set()

    # -- injecting traffic ----------------------------------------------------------------------

    async def inject_message(self, event: dict[str, Any]) -> str:
        """Store a message event as posted and deliver it like Slack: a ``message`` event, plus an
        ``app_mention`` when it mentions the bot (an injected ``app_mention`` gets both, too)."""
        ev = copy.deepcopy(event.get("event") if isinstance(event.get("event"), dict) else event)
        channel = str(ev.get("channel") or self.alert_channel)
        self.workspace.add_channel(channel)
        if ev.get("ts"):
            self.workspace.note_ts(str(ev["ts"]))
        else:
            ev["ts"] = self.workspace.next_ts()
        ev.setdefault("text", "")
        mentioned = ev.get("type") == "app_mention" or f"<@{self.bot_user_id}" in str(ev["text"])
        ev["type"] = "message"
        ev.update(channel=channel, event_ts=ev.get("event_ts") or ev["ts"],
                  channel_type=ev.get("channel_type") or self.workspace.channel_type(channel))
        if ev.get("user"):
            self.workspace.add_user(str(ev["user"]))
        stored = copy.deepcopy({k: v for k, v in ev.items() if k not in ("channel", "channel_type", "event_ts")})
        self.workspace.store(channel, stored)
        await self._deliver(ev)
        if mentioned and ev["channel_type"] != "im":
            mention = {k: v for k, v in ev.items() if k not in ("subtype", "channel_type")}
            await self._deliver({**mention, "type": "app_mention"})
        return str(ev["ts"])

    async def post_alert(self, event: dict[str, Any], channel: str | None = None) -> str:
        """An integration's alert post (bot message) in the alert channel; returns its ts."""
        ev = copy.deepcopy(event.get("event") if isinstance(event.get("event"), dict) else event)
        if not ev.get("user") and not ev.get("bot_id"):
            ev.update(bot_id=INTEGRATION_BOT_ID, subtype=ev.get("subtype") or "bot_message")
        ev["channel"] = channel or ev.get("channel") or self.alert_channel
        return await self.inject_message(ev)

    async def post_user_message(self, channel: str, text: str, user: str = DEFAULT_USER,
                                thread_ts: str | None = None) -> str:
        ev: dict[str, Any] = {"type": "message", "channel": channel, "user": user, "text": text, "team": TEAM_ID,
                              "client_msg_id": str(uuid.uuid4())}
        if thread_ts:
            ev["thread_ts"] = thread_ts
        return await self.inject_message(ev)

    async def dm(self, text: str, user: str = DEFAULT_USER) -> str:
        """A direct message to the bot; returns the message ts."""
        self.workspace.add_user(user)
        return await self.post_user_message(self.workspace.dm_channel(user), text, user=user)

    async def slash(self, command: str, text: str, channel: str | None = None, user: str = DEFAULT_USER) -> str:
        """A slash command; returns the immediate acknowledgement text (shown ephemerally, like Slack)."""
        channel = channel or self.general_channel
        self.workspace.add_channel(channel)
        response_url = f"https://hooks.slack.simulator.invalid/commands/{uuid.uuid4().hex}"
        self._response_urls[response_url] = {"channel": channel, "user": user, "uses": 0,
                                             "expires": time.time() + RESPONSE_URL_TTL_S}
        body = {"token": "sim", "team_id": TEAM_ID, "team_domain": "omniops-sim", "channel_id": channel,
                "channel_name": self.workspace.channels[channel].get("name", ""), "user_id": user,
                "user_name": self.workspace.add_user(user)["name"], "command": command, "text": text,
                "api_app_id": APP_ID, "is_enterprise_install": "false", "response_url": response_url,
                "trigger_id": uuid.uuid4().hex}
        resp = await self._dispatch(body, kind="command")
        ack = _ack_text(resp.body)
        if ack:
            self.workspace.ephemeral.append({"type": "message", "subtype": "ephemeral", "ts": self.workspace.next_ts(),
                                             "channel": channel, "to_user": user, "user": self.bot_user_id,
                                             "text": ack, "blocks": [], "source": "command_ack"})
        return ack

    async def click(self, action_id: str, value: str | None = None, message_ts: str | None = None,
                    channel: str | None = None, *, user: str = DEFAULT_USER) -> int:
        """Click a button on a posted (or ephemeral) message; returns the HTTP-like status of the ack."""
        msg, button, block_id = self._find_button(action_id, value, message_ts, channel)
        ephemeral = msg.get("subtype") == "ephemeral"
        cid = str(msg.get("channel") or channel or self._channel_of(msg))
        body: dict[str, Any] = {
            "type": "block_actions", "token": "sim", "api_app_id": APP_ID, "trigger_id": uuid.uuid4().hex,
            "user": {"id": user, "username": self.workspace.add_user(user)["name"], "team_id": TEAM_ID},
            "team": {"id": TEAM_ID, "domain": "omniops-sim"},
            "container": {"type": "message", "message_ts": msg["ts"], "channel_id": cid, "is_ephemeral": ephemeral,
                          **({"thread_ts": msg["thread_ts"]} if msg.get("thread_ts") else {})},
            "channel": {"id": cid, "name": self.workspace.channels.get(cid, {}).get("name", "")},
            "response_url": f"https://hooks.slack.simulator.invalid/actions/{uuid.uuid4().hex}",
            "actions": [{"type": button.get("type", "button"), "action_id": action_id, "block_id": block_id,
                         "value": button.get("value"), "text": button.get("text"),
                         "action_ts": self.workspace.next_ts()}],
            "state": {"values": {}},
        }
        if not ephemeral:                      # clicks on ephemeral messages omit their blocks (4.4)
            body["message"] = _view(msg)
        resp = await self._dispatch(body, kind="block_actions")
        return int(resp.status)

    async def redeliver(self, index: int = -1, *, same_event_id: bool = True) -> None:
        """Deliver an injected event again (4.5): with the same event id, like Slack's retry after a slow
        ack, or under a new event id, like a second subscription firing for the same post."""
        body = copy.deepcopy(self._deliveries[index])
        if not same_event_id:
            body["event_id"] = "Ev" + uuid.uuid4().hex[:12].upper()
        await self._dispatch(body, kind="event", retry=same_event_id)

    # -- waiting and reading --------------------------------------------------------------------

    async def wait_idle(self, timeout: float | None = None) -> bool:
        """Wait until the bot and the simulator have no background work. False on timeout."""
        try:
            async with asyncio.timeout(timeout):
                while True:
                    await self.bot.idle()
                    pending = {t for t in self._tasks if not t.done()}
                    if pending:
                        await asyncio.wait(pending)
                        continue
                    await asyncio.sleep(0)
                    if not any(not t.done() for t in self.bot.tasks | self._tasks):
                        return True
        except TimeoutError:
            return False

    def messages(self, channel: str, thread_ts: str | None = None) -> list[dict[str, Any]]:
        """Top-level messages of a channel, or one thread (root + replies). Each keeps its ``edits``."""
        msgs = self.workspace.messages.get(channel, [])
        if thread_ts is None:
            picked = [m for m in msgs if m.get("thread_ts") in (None, m["ts"])]
        else:
            picked = [m for m in msgs if m["ts"] == thread_ts or m.get("thread_ts") == thread_ts]
        return [{**copy.deepcopy(m), "channel": channel} for m in picked]

    def thread(self, channel: str, ts: str) -> list[dict[str, Any]]:
        return self.messages(channel, ts)

    def ephemerals(self, user: str | None = None, channel: str | None = None) -> list[dict[str, Any]]:
        return [copy.deepcopy(m) for m in self.workspace.ephemeral
                if (user is None or m.get("to_user") == user) and (channel is None or m.get("channel") == channel)]

    def api_calls(self, method: str | None = None) -> list[dict[str, Any]]:
        return [copy.deepcopy(e) for e in self.workspace.log
                if e["kind"] == "api" and (method is None or e["method"] == method)]

    def find_buttons(self, action_id: str) -> list[tuple[str, str, str]]:
        """(channel, message ts, value) of every visible button with this action id, oldest first."""
        out = []
        for m in self._all_messages():
            for b in m.get("blocks") or []:
                for e in b.get("elements") or [] if b.get("type") == "actions" else []:
                    if e.get("action_id") == action_id:
                        out.append((str(m.get("channel") or self._channel_of(m)), m["ts"], str(e.get("value"))))
        return out

    def final_answer_text(self, channel: str | None = None, thread_ts: str | None = None) -> str:
        """The bot's final answer as displayed: card + report pages of the latest run, as plain markdown.

        With no location, the latest run anywhere. Falls back to the newest non-status bot message
        (an error notice, say) so a caller always sees what Slack shows.
        """
        candidates = self._bot_messages(channel, thread_ts)
        answer = [m for m in candidates if _meta(m)[0] in (META_CARD, META_REPORT)]
        if answer:
            latest = max(answer, key=lambda m: float(m["ts"]))
            run_id = _meta(latest)[1]
            if channel is None:
                where = self._channel_of(latest)
                answer = [m for m in answer if self._channel_of(m) == where]
            parts = [self._text(m) for m in sorted(answer, key=lambda m: float(m["ts"])) if _meta(m)[1] == run_id]
            return "\n\n".join(p for p in parts if p.strip())
        rest = [m for m in candidates if _meta(m)[0] != STATUS_EVENT_TYPE]
        return self._text(max(rest, key=lambda m: float(m["ts"]))) if rest else ""

    def transcript(self) -> list[dict[str, Any]]:
        """Everything that happened, in order: deliveries, acks, Web API calls and simulated approvals."""
        return copy.deepcopy(self.workspace.log)

    async def close(self) -> None:
        """Cancel outstanding work (runs post their 'interrupted' notice) and wait for it to unwind."""
        pending = [t for t in self.bot.tasks | self._tasks if not t.done()]
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    # -- internals ------------------------------------------------------------------------------

    async def _response_url(self, url: str, payload: dict[str, Any]) -> bool:
        """A slash command's response_url: an ephemeral message to the invoker where the command ran, member
        or not, at most ``RESPONSE_URL_USES`` times within ``RESPONSE_URL_TTL_S`` (False once used up)."""
        await asyncio.sleep(0)
        hook = self._response_urls.get(url)
        err = "expired_url" if hook is None or hook["uses"] >= RESPONSE_URL_USES or time.time() > hook["expires"] \
            else (self.client._content_error(payload) or {}).get("error")
        self.workspace.record("api", method="response_url", ok=err is None, error=err, args=copy.deepcopy(payload))
        if err is not None or hook is None:
            return False
        hook["uses"] += 1
        msg: dict[str, Any] = {"type": "message", "subtype": "ephemeral", "ts": self.workspace.next_ts(),
                               "channel": hook["channel"], "to_user": hook["user"], "user": self.bot_user_id,
                               "bot_id": self.bot_id, "text": str(payload.get("text") or ""),
                               "blocks": _json_arg(payload.get("blocks")) or [], "edited_at": time.time(),
                               "source": "response_url"}
        self.workspace.ephemeral.append(msg)
        return True

    async def _deliver(self, event: dict[str, Any], *, echo: bool = False) -> None:
        envelope = {"token": "sim", "team_id": TEAM_ID, "api_app_id": APP_ID, "event": event, "type": "event_callback",
                    "event_id": "Ev" + uuid.uuid4().hex[:12].upper(), "event_time": int(time.time()),
                    "authorizations": [{"enterprise_id": None, "team_id": TEAM_ID, "user_id": self.bot_user_id,
                                        "is_bot": True, "is_enterprise_install": False}],
                    "is_ext_shared_channel": False, "event_context": uuid.uuid4().hex}
        if not echo:                           # replayable deliveries are the injected ones
            self._deliveries.append(copy.deepcopy(envelope))
        await self._dispatch(envelope, kind="event")

    async def _dispatch(self, body: dict[str, Any], *, kind: str, retry: bool = False) -> Any:
        headers = {"x-slack-retry-num": ["1"], "x-slack-retry-reason": ["timeout"]} if retry else {}
        self.workspace.record("delivery", type=kind, retry=retry, body=copy.deepcopy(body))
        resp = await self.app.async_dispatch(AsyncBoltRequest(body=body, mode="socket_mode", headers=headers))
        self.workspace.record("ack", type=kind, status=resp.status, body=resp.body)
        return resp

    def _after_message(self, method: str, channel: str, msg: dict[str, Any]) -> None:
        """Bot posts are echoed as events (the self-event filter must drop them); approvals may be clicked."""
        if method == "chat.postMessage":
            ev = {k: copy.deepcopy(v) for k, v in msg.items() if k not in ("edits", "edited_at")}
            ev.update(channel=channel, event_ts=msg["ts"], channel_type=self.workspace.channel_type(channel))
            self._track(self._deliver(ev, echo=True))
        if self.auto_approve_writes:
            for b in msg.get("blocks") or []:
                for e in b.get("elements") or [] if b.get("type") == "actions" else []:
                    key = (channel, msg["ts"], str(e.get("value")))
                    if e.get("action_id") == ACTION_APPROVE and key not in self._clicked:
                        self._clicked.add(key)
                        self._track(self._approve(channel, msg["ts"], str(e.get("value"))))

    async def _approve(self, channel: str, ts: str, write_id: str) -> None:
        await asyncio.sleep(0)                 # after the post returns, like a human reading the card
        record = {"write_id": write_id, "channel": channel, "message_ts": ts, "by": APPROVER, "at": time.time()}
        self.approvals.append(record)
        self.workspace.record("approval", simulated_human=True, **record)
        await self.click(ACTION_APPROVE, write_id, ts, channel, user=APPROVER)

    def _track(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _all_messages(self) -> list[dict[str, Any]]:
        msgs = [{**m, "channel": cid} for cid, ms in self.workspace.messages.items() for m in ms]
        return sorted(msgs + list(self.workspace.ephemeral), key=lambda m: float(m["ts"]))

    def _channel_of(self, msg: dict[str, Any]) -> str:
        if msg.get("channel"):
            return str(msg["channel"])
        return next((cid for cid, ms in self.workspace.messages.items() if any(m is msg or m["ts"] == msg["ts"]
                                                                              for m in ms)), "")

    def _find_button(self, action_id: str, value: str | None, message_ts: str | None, channel: str | None
                     ) -> tuple[dict[str, Any], dict[str, Any], str | None]:
        for m in reversed(self._all_messages()):
            if (message_ts and m["ts"] != message_ts) or (channel and m.get("channel") != channel):
                continue
            for b in m.get("blocks") or []:
                if b.get("type") != "actions":
                    continue
                for e in b.get("elements") or []:
                    if e.get("action_id") == action_id and (value is None or str(e.get("value")) == str(value)):
                        return m, e, b.get("block_id")
        raise LookupError(f"no {action_id!r} button (value={value!r}) on message {message_ts} in {channel}")

    def _bot_messages(self, channel: str | None, thread_ts: str | None) -> list[dict[str, Any]]:
        if channel is None:
            msgs = [m for m in self._all_messages() if m.get("subtype") != "ephemeral"]
        elif thread_ts is None:
            msgs = [{**m, "channel": channel} for m in self.workspace.messages.get(channel, [])]
        else:
            msgs = self.messages(channel, thread_ts)
        return [m for m in msgs if m.get("user") == self.bot_user_id]

    def _text(self, m: dict[str, Any]) -> str:
        blocks = [b for b in m.get("blocks") or [] if b.get("type") != "actions"]
        return self.bot.present.to_text(blocks) if blocks else str(m.get("text") or "")


def _meta(m: dict[str, Any]) -> tuple[str, str]:
    md = m.get("metadata") or {}
    return str(md.get("event_type") or ""), str((md.get("event_payload") or {}).get("run_id") or "")


def _ack_text(body: Any) -> str:
    if isinstance(body, dict):
        return str(body.get("text") or "")
    if isinstance(body, str) and body.strip().startswith("{"):
        try:
            return str(json.loads(body).get("text") or "")
        except json.JSONDecodeError:
            return body
    return str(body or "")
