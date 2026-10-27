"""Live status trail (coalesced in-place edits, Slack limits, never looks live when frozen) and the
de-dup / flap / claim registries, against the simulator's fake Slack Web API."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest
from slack_sdk.errors import SlackApiError

from omniops.slack.dedup import ClaimRegistry, EventDeduper, FlapRegistry
from omniops.slack.simulator import BOT_USER_ID, FakeAsyncWebClient, Workspace
from omniops.slack.status import STATUS_EVENT_TYPE, STOP_ACTION, StatusTrail


def fake_slack() -> tuple[Workspace, FakeAsyncWebClient]:
    ws = Workspace()
    ws.add_channel("C_OPS", "ops")
    return ws, FakeAsyncWebClient(ws)


def updates(ws: Workspace) -> list[dict[str, Any]]:
    return [e for e in ws.log if e["kind"] == "api" and e["method"] == "chat.update"]


def has_stop(blocks: list[dict[str, Any]]) -> bool:
    return any(e.get("action_id") == STOP_ACTION for b in blocks if b.get("type") == "actions"
               for e in b.get("elements") or [])


async def test_trail_posts_once_and_coalesces_edits() -> None:
    ws, client = fake_slack()
    trail = StatusTrail(client, "C_OPS", run_id="r1", stop_value="r1", min_interval_s=0.5)
    ts = await trail.start("Investigating HighErrorRate")
    assert ts is not None
    msg = ws.find("C_OPS", ts)
    assert msg is not None and msg["metadata"]["event_type"] == STATUS_EVENT_TYPE and has_stop(msg["blocks"])
    for i in range(20):
        trail.step(f"step {i}")                    # sync call: agent code never waits on Slack
        await asyncio.sleep(0.002)
    await trail.done("seeds finished")             # awaiting works too
    await asyncio.sleep(0.6)
    assert len(updates(ws)) == 1, "a burst of steps costs one chat.update"
    await trail.finish("all done")
    edits = updates(ws)
    assert len(edits) == 2
    assert edits[1]["at"] - edits[0]["at"] >= 0.49, "updates stay at least min_interval_s apart"
    final = ws.find("C_OPS", ts)
    assert final is not None and "step 19" in final["text"] and "all done" in final["text"]
    assert "done in" in final["text"] and not has_stop(final["blocks"])
    assert len([m for m in ws.messages["C_OPS"]]) == 1, "one message, edited in place"


async def test_trail_stays_within_slack_limits_and_says_what_it_folded() -> None:
    ws, client = fake_slack()
    trail = StatusTrail(client, "C_OPS", run_id="r2", min_interval_s=0.0)
    ts = await trail.start("Investigating a very chatty run")
    for i in range(200):
        trail.step(f"query {i}: " + "x" * 400)
    await trail.finish()
    msg = ws.find("C_OPS", ts)
    assert msg is not None
    assert len(msg["text"]) <= 3500
    assert all(len(b["text"]["text"]) <= 3000 for b in msg["blocks"] if b["type"] == "section")
    assert "earlier step(s)" in msg["text"] and "query 199" in msg["text"]
    assert all(e["ok"] for e in ws.log if e["kind"] == "api"), "no edit was rejected by Slack"


async def test_failed_trail_is_frozen_without_stop_button() -> None:
    ws, client = fake_slack()
    trail = StatusTrail(client, "C_OPS", run_id="r3", stop_value="r3", min_interval_s=0.0)
    ts = await trail.start("Investigating")
    trail.step("running seeds")
    await trail.fail("timed out after 900 s (watchdog)")
    trail.step("late step from a straggler")       # ignored once frozen
    await asyncio.sleep(0.05)
    msg = ws.find("C_OPS", ts)
    assert msg is not None and "failed after" in msg["text"] and "watchdog" in msg["text"]
    assert "straggler" not in msg["text"] and not has_stop(msg["blocks"])
    assert msg["metadata"]["event_payload"]["state"] == "failed"


async def test_stop_request_hides_the_button_and_is_visible() -> None:
    ws, client = fake_slack()
    trail = StatusTrail(client, "C_OPS", run_id="r4", stop_value="r4", min_interval_s=0.0)
    ts = await trail.start("Investigating")
    trail.request_stop("U_ONCALL")
    await asyncio.sleep(0.05)
    msg = ws.find("C_OPS", ts)
    assert trail.stop_requested and msg is not None
    assert "Stop requested by <@U_ONCALL>" in msg["text"] and not has_stop(msg["blocks"])


async def test_slack_failures_never_break_the_run() -> None:
    class Broken(FakeAsyncWebClient):
        async def api_call(self, api_method: str, **kwargs: Any) -> Any:
            raise SlackApiError("down", {"ok": False, "error": "service_unavailable"})

    ws = Workspace()
    trail = StatusTrail(Broken(ws), "C_OPS", run_id="r5", min_interval_s=0.0)
    assert await trail.start("Investigating") is None
    trail.step("still fine")
    await trail.finish("done")


async def test_early_card_is_forwarded_and_its_failure_contained() -> None:
    _, client = fake_slack()
    seen: list[str] = []

    async def on_card(card: Any) -> None:
        seen.append(card)
        raise RuntimeError("render bug")

    trail = StatusTrail(client, "C_OPS", run_id="r6", on_card=on_card)
    await trail.early_card("card")  # type: ignore[arg-type]
    assert seen == ["card"]


async def test_fake_web_api_answers_sdk_calls_like_slack() -> None:
    ws, client = fake_slack()
    ws.add_user("U1", "oncall")
    assert (await client.auth_test())["user_id"] == BOT_USER_ID
    root = await client.chat_postMessage(channel="C_OPS", text="root")
    reply = await client.chat_postMessage(channel="C_OPS", text="reply", thread_ts=root["ts"])
    assert float(reply["ts"]) > float(root["ts"])
    thread = (await client.conversations_replies(channel="C_OPS", ts=root["ts"]))["messages"]
    assert [m["text"] for m in thread] == ["root", "reply"]
    assert [m["text"] for m in (await client.conversations_history(channel="C_OPS"))["messages"]] == ["root"]
    assert (await client.users_info(user="U1"))["user"]["name"] == "oncall"
    assert (await client.conversations_info(channel="C_OPS"))["channel"]["name"] == "ops"
    dm = (await client.conversations_open(users="U1"))["channel"]["id"]
    assert dm.startswith("D") and (await client.conversations_open(users="U1"))["channel"]["id"] == dm

    await client.reactions_add(channel="C_OPS", name="eyes", timestamp=root["ts"])
    with pytest.raises(SlackApiError, match="already_reacted"):
        await client.reactions_add(channel="C_OPS", name="eyes", timestamp=root["ts"])
    view = await client.views_open(trigger_id="t1", view={"type": "modal", "title": {"type": "plain_text",
                                                                                      "text": "RCA"}})
    assert view["view"]["id"].startswith("V") and ws.views[0]["type"] == "modal"
    await client.files_upload_v2(channel=dm, content="full report", filename="report.md", initial_comment="Report")
    assert ws.files[0]["content"] == "full report" and ws.messages[dm][-1]["subtype"] == "file_share"
    await client.assistant_threads_setStatus(channel_id=dm, thread_ts=root["ts"], status="is thinking...")
    await client.assistant_threads_setTitle(channel_id=dm, thread_ts=root["ts"], title="Checkout latency")
    assert [a["kind"] for a in ws.assistant] == ["status", "title"]
    await client.chat_postEphemeral(channel="C_OPS", user="U1", text="only you")
    assert ws.ephemeral[0]["to_user"] == "U1"
    await client.chat_delete(channel="C_OPS", ts=reply["ts"])
    assert ws.find("C_OPS", reply["ts"]) is None

    # The Slack limits that bit production (4.4) are enforced, so tests see real rejections.
    with pytest.raises(SlackApiError, match="invalid_blocks"):
        await client.chat_postMessage(channel="C_OPS", text="x", blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": "y" * 3001}}])
    with pytest.raises(SlackApiError, match="invalid_blocks"):
        await client.chat_postMessage(channel="C_OPS", text="x", blocks=[{"type": "divider"}] * 51)
    with pytest.raises(SlackApiError, match="msg_too_long"):
        await client.chat_update(channel="C_OPS", ts=root["ts"], text="z" * 4001)
    assert [e["method"] for e in ws.log if e["kind"] == "api" and not e["ok"]] == [
        "reactions.add", "chat.postMessage", "chat.postMessage", "chat.update"], "every call is recorded"


def test_event_deduper_put_if_absent_ttl_and_lru() -> None:
    d = EventDeduper(ttl_s=0.05, max_items=3)
    assert d.first("event:1", "post:C:1")
    assert not d.first("event:1")                  # redelivery
    assert not d.first("event:2", "post:C:1")      # same post, other subscription
    assert d.first(None, None)                     # nothing to key on: let it through
    time.sleep(0.06)
    assert d.first("event:1")                      # TTL expired
    for k in ("a", "b", "c", "d"):
        assert d.first(k)
    assert d.first("event:1") and not d.seen("a"), "bounded: the oldest key is evicted"


def test_flap_registry_window() -> None:
    reg = FlapRegistry()
    rec = reg.record("grafana:HighErrorRate:", "run1", "C_ALERTS", "1.0", started=1000.0)
    assert reg.recent("grafana:HighErrorRate:", 3600, now=1000.0 + 3599) is rec
    assert reg.recent("grafana:HighErrorRate:", 3600, now=1000.0 + 3601) is None
    assert reg.recent("other", 3600) is None


def test_claim_registry_claims_once_and_expires() -> None:
    reg: ClaimRegistry[str] = ClaimRegistry(ttl_s=60)
    reg.put("W1", "write")
    assert reg.claim("W1", by="U1") == ("write", "ok", "U1")
    assert reg.claim("W1", by="U2") == (None, "claimed", "U1")
    reg.put("W2", "write", ttl_s=-1)
    assert reg.claim("W2") == (None, "expired", None)
    assert reg.claim("nope") == (None, "unknown", None)
    assert reg.get("W2") is None and reg.get("W1") == "write"


def test_flap_registry_forgets_only_the_failed_run() -> None:
    reg = FlapRegistry()
    reg.record("grafana:HighErrorRate:", "run1", "C_ALERTS", "1.0")
    reg.forget("grafana:HighErrorRate:", "run0")             # an older run: the newer record stays
    assert reg.recent("grafana:HighErrorRate:", 3600) is not None
    reg.forget("grafana:HighErrorRate:", "run1")
    assert reg.recent("grafana:HighErrorRate:", 3600) is None
    reg.forget("unknown", "run1")                             # no-op


async def test_trail_title_and_agent_steps_cannot_ping_anyone() -> None:
    ws, client = fake_slack()
    trail = StatusTrail(client, "C_OPS", run_id="r1", stop_value="r1", min_interval_s=0.0)
    ts = await trail.start("Investigating <!channel> disk full")
    trail.step("log line: <!here> restart <@U9>")
    await trail.done("dependency: <!subteam^S1|@oncall> paged")
    trail.request_stop("U_USER")
    await trail.finish("Done")
    msg = ws.find("C_OPS", ts)
    assert msg is not None
    for version in [msg, *msg["edits"]]:
        shown = version["text"] + json.dumps(version["blocks"])
        for token in ("<!channel>", "<!here>", "<@U9>", "<!subteam^"):
            assert token not in shown
    assert "&lt;!channel&gt;" in msg["text"] and "Stop requested by <@U_USER>" in msg["text"]
