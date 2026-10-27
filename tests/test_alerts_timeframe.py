"""Trigger-window resolution (retrospective 3.3), driven through ``resolve_anchor`` with alerts parsed
from the Slack fixtures where possible."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from omniops.agent.alert import parse_slack_alert
from omniops.agent.timeframe import TriggerAnchor, local_label, resolve_anchor
from omniops.agent.types import Alert
from omniops.engine.timeutil import TimeAnchor, fmt_rfc3339

FIXTURES = Path(__file__).parent / "fixtures" / "slack_alerts"
WALL = datetime(2026, 10, 20, 16, 0, tzinfo=UTC)
TRIGGER = datetime(2026, 10, 20, 14, 2, tzinfo=UTC)
MSG_TS = "1792504957.000200"                                   # 2026-10-20T14:02:37Z


def window(anchor: TimeAnchor) -> tuple[str, str]:
    assert anchor.window_start is not None and anchor.window_end is not None
    return fmt_rfc3339(anchor.window_start), fmt_rfc3339(anchor.window_end)


async def datadog_alert(**changes: str) -> Alert:
    fx = json.loads((FIXTURES / "datadog_triggered.json").read_text(encoding="utf-8"))
    att = fx["event"]["attachments"][0]
    att.update(changes)
    alert = await parse_slack_alert(fx["event"])
    assert alert is not None
    return alert


# ---------------------------------------------------------------------------------------------
# Source order: link window, then trigger, then message time
# ---------------------------------------------------------------------------------------------


async def test_link_window_wins_over_trigger_and_message_time() -> None:
    alert = await datadog_alert()
    anchor = resolve_anchor(alert, WALL)
    assert anchor.source == "link_window"
    assert window(anchor) == ("2026-10-20T13:47:00Z", "2026-10-20T14:07:00Z")
    assert anchor.now == anchor.window_end                     # relative times pin to the window end
    assert anchor.trigger == TRIGGER


async def test_trigger_time_when_the_link_has_no_window() -> None:
    alert = await datadog_alert(title_link="https://app.datadoghq.com/monitors/48213977")
    anchor = resolve_anchor(alert, WALL)
    assert anchor.source == "trigger"
    assert window(anchor) == ("2026-10-20T13:47:00Z", "2026-10-20T14:07:00Z")


async def test_message_time_when_the_alert_carries_no_time() -> None:
    fx = json.loads((FIXTURES / "datadog_triggered.json").read_text(encoding="utf-8"))
    att = fx["event"]["attachments"][0]
    att["title_link"] = "https://app.datadoghq.com/monitors/48213977"
    att["text"] = "CPU usage on web-01 has been above 90% for 5 minutes."
    alert = await parse_slack_alert(fx["event"])
    assert alert is not None and alert.triggered_at is None
    anchor = resolve_anchor(alert, WALL)
    assert anchor.source == "message_ts"
    assert window(anchor) == ("2026-10-20T13:47:37Z", "2026-10-20T14:07:37Z")


def test_wall_clock_without_an_alert_or_any_time() -> None:
    anchor = resolve_anchor(None, WALL)
    assert anchor.source == "wall_clock" and anchor.now == WALL and anchor.window_start is None
    timeless = resolve_anchor(Alert(title="x"), WALL)
    assert timeless.source == "wall_clock"
    assert window(timeless) == ("2026-10-20T15:45:00Z", "2026-10-20T16:00:00Z")
    assert any("no usable time" in n for n in timeless.notes)


def test_anchor_is_a_time_anchor_for_the_engine() -> None:
    anchor = resolve_anchor(Alert(title="x", triggered_at=TRIGGER), WALL)
    assert isinstance(anchor, TimeAnchor) and isinstance(anchor, TriggerAnchor)
    assert anchor.default_window("1h") == (anchor.window_start, anchor.window_end)


# ---------------------------------------------------------------------------------------------
# Late notifications, future guards, clamping, no staleness guard
# ---------------------------------------------------------------------------------------------


def test_late_notification_is_anchored_on_the_trigger() -> None:
    late_ts = str((TRIGGER + timedelta(minutes=13)).timestamp())
    anchor = resolve_anchor(Alert(title="x", triggered_at=TRIGGER, source_ts=late_ts), WALL)
    assert anchor.source == "trigger"
    assert window(anchor) == ("2026-10-20T13:47:00Z", "2026-10-20T14:07:00Z")
    assert any("13 min after the trigger" in n for n in anchor.notes)


def test_window_reaches_back_to_an_onset_the_notification_trailed() -> None:
    notified = str((TRIGGER + timedelta(minutes=23)).timestamp())
    alert = Alert(title="x", triggered_at=TRIGGER, window_start=TRIGGER + timedelta(minutes=8),
                  window_end=TRIGGER + timedelta(minutes=28), source_ts=notified)
    anchor = resolve_anchor(alert, WALL)
    assert anchor.source == "link_window"
    assert window(anchor) == ("2026-10-20T13:47:00Z", "2026-10-20T14:30:00Z")
    assert any("extended back to 15 min before the trigger 2026-10-20T14:02:00Z" in n for n in anchor.notes)


def test_link_window_ending_at_or_before_the_trigger_covers_the_minutes_after_it() -> None:
    fixed = Alert(title="x", triggered_at=TRIGGER, window_start=TRIGGER - timedelta(hours=2),
                  window_end=TRIGGER - timedelta(hours=1), source_ts=MSG_TS)
    anchor = resolve_anchor(fixed, WALL)
    assert anchor.source == "link_window"
    assert window(anchor) == ("2026-10-20T12:02:00Z", "2026-10-20T14:07:00Z")
    assert anchor.now == anchor.window_end
    assert any("extended to 5 min after the trigger" in n for n in anchor.notes)
    evaluated = Alert(title="x", triggered_at=TRIGGER, window_start=TRIGGER - timedelta(minutes=5),
                      window_end=TRIGGER, source_ts=MSG_TS)            # Kibana / Sumo evaluation window
    assert window(resolve_anchor(evaluated, WALL)) == ("2026-10-20T13:47:00Z", "2026-10-20T14:07:00Z")


def test_renotification_with_an_old_trigger_keeps_the_link_window() -> None:
    renotify = Alert(title="x", triggered_at=TRIGGER - timedelta(days=3), source_ts=MSG_TS,
                     window_start=TRIGGER - timedelta(minutes=15), window_end=TRIGGER + timedelta(minutes=5))
    anchor = resolve_anchor(renotify, WALL)
    assert window(anchor) == ("2026-10-20T13:47:00Z", "2026-10-20T14:07:00Z")
    assert any("more than 24h before the link window" in n for n in anchor.notes)


def test_explicit_onset_extra_reaches_back_but_not_beyond_a_day() -> None:
    near = resolve_anchor(Alert(title="x", triggered_at=TRIGGER, onset=TRIGGER - timedelta(minutes=40)), WALL)
    assert window(near)[0] == "2026-10-20T13:17:00Z"
    far = resolve_anchor(Alert(title="x", triggered_at=TRIGGER, onset=TRIGGER - timedelta(days=3)), WALL)
    assert window(far)[0] == "2026-10-20T13:47:00Z"
    assert any("more than 24h" in n for n in far.notes)


def test_future_trigger_is_rejected_and_the_next_source_used() -> None:
    alert = Alert(title="x", triggered_at=WALL + timedelta(hours=2), source_ts=MSG_TS)
    anchor = resolve_anchor(alert, WALL)
    assert anchor.source == "message_ts" and anchor.trigger is None
    assert any("in the future" in n for n in anchor.notes)


def test_future_link_window_start_is_rejected_and_end_is_clamped() -> None:
    future = Alert(title="x", window_start=WALL + timedelta(hours=1), window_end=WALL + timedelta(hours=2),
                   source_ts=MSG_TS)
    assert resolve_anchor(future, WALL).source == "message_ts"
    open_ended = Alert(title="x", window_start=WALL - timedelta(minutes=10),
                       window_end=WALL + timedelta(minutes=10))
    anchor = resolve_anchor(open_ended, WALL)
    assert anchor.source == "link_window" and anchor.window_end == WALL and anchor.now == WALL
    assert any("clamped to now" in n for n in anchor.notes)


def test_recent_trigger_end_is_clamped_to_now() -> None:
    anchor = resolve_anchor(Alert(title="x", triggered_at=WALL - timedelta(minutes=2)), WALL)
    assert window(anchor) == ("2026-10-20T15:43:00Z", "2026-10-20T16:00:00Z")
    assert anchor.now == WALL
    assert any("data may still be arriving" in n for n in anchor.notes)


def test_old_alert_keeps_its_own_window() -> None:
    old = datetime(2025, 3, 4, 5, 6, tzinfo=UTC)
    anchor = resolve_anchor(Alert(title="x", triggered_at=old), WALL)
    assert window(anchor) == ("2025-03-04T04:51:00Z", "2025-03-04T05:11:00Z")


# ---------------------------------------------------------------------------------------------
# Epochs in strings (3.3: an epoch-ms string once became the year 1791)
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1792504920", "1792504920000", "1792504920000000", "1792504920000000000"])
def test_epoch_strings_are_epochs_with_unit_by_magnitude(value: str) -> None:
    alert = Alert.model_construct(title="x", triggered_at=value, customer_report=False)
    anchor = resolve_anchor(alert, WALL)
    assert anchor.source == "trigger" and anchor.trigger == TRIGGER


async def test_epoch_ms_link_window_from_a_notification() -> None:
    alert = await datadog_alert(
        title_link="https://app.datadoghq.com/monitors/48213977?from_ts=1792504020000&to_ts=1792505220000")
    assert window(resolve_anchor(alert, WALL)) == ("2026-10-20T13:47:00Z", "2026-10-20T14:07:00Z")


async def test_epoch_seconds_link_window_and_iso_link_window() -> None:
    secs = await datadog_alert(
        title_link="https://app.datadoghq.com/monitors/48213977?from_ts=1792504020&to_ts=1792505220")
    iso = await datadog_alert(
        title_link="https://app.datadoghq.com/monitors/48213977?start=2026-10-20T13:47:00Z&end=2026-10-20T14:07:00Z")
    assert window(resolve_anchor(secs, WALL)) == window(resolve_anchor(iso, WALL)) == (
        "2026-10-20T13:47:00Z", "2026-10-20T14:07:00Z")


def test_implausible_times_are_ignored() -> None:
    alert = Alert.model_construct(title="x", triggered_at="17", source_ts=MSG_TS, customer_report=False)
    assert resolve_anchor(alert, WALL).source == "message_ts"


# ---------------------------------------------------------------------------------------------
# Customer reports
# ---------------------------------------------------------------------------------------------


def test_customer_report_gets_a_wider_window() -> None:
    anchor = resolve_anchor(Alert(title="checkout broken", customer_report=True, source_ts=MSG_TS), WALL)
    assert anchor.source == "customer_report"
    assert window(anchor) == ("2026-10-20T13:02:37Z", "2026-10-20T14:07:37Z")


def test_customer_report_reaches_back_to_the_stated_onset() -> None:
    alert = Alert(title="checkout broken", customer_report=True, source_ts=MSG_TS, reported_minutes_ago=120)
    anchor = resolve_anchor(alert, WALL)
    assert window(anchor) == ("2026-10-20T11:57:37Z", "2026-10-20T14:07:37Z")


def test_customer_report_without_a_message_time_ends_now() -> None:
    anchor = resolve_anchor(Alert(title="checkout broken", customer_report=True), WALL)
    assert window(anchor) == ("2026-10-20T15:00:00Z", "2026-10-20T16:00:00Z")


# ---------------------------------------------------------------------------------------------
# Display labels: tz database, never a fixed offset (the "01:13 CST (UTC-5)" bug)
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("when", "zone", "label"), [
    (datetime(2026, 10, 20, 14, 2, tzinfo=UTC), "America/Chicago", "2026-10-20 09:02 CDT (UTC-05:00)"),
    (datetime(2026, 11, 10, 14, 2, tzinfo=UTC), "America/Chicago", "2026-11-10 08:02 CST (UTC-06:00)"),
    (datetime(2026, 10, 24, 12, 0, tzinfo=UTC), "Europe/London", "2026-10-24 13:00 BST (UTC+01:00)"),
    (datetime(2026, 10, 26, 12, 0, tzinfo=UTC), "Europe/London", "2026-10-26 12:00 GMT (UTC+00:00)"),
    (datetime(2026, 10, 20, 14, 2, tzinfo=UTC), "Asia/Kolkata", "2026-10-20 19:32 IST (UTC+05:30)"),
])
def test_local_labels_follow_daylight_saving(when: datetime, zone: str, label: str) -> None:
    assert local_label(when, zone) == label


def test_describe_uses_the_configured_display_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNIOPS_DISPLAY_TZ", "America/Chicago")
    summer = resolve_anchor(Alert(title="x", triggered_at=TRIGGER), WALL).describe()
    winter_trigger = datetime(2026, 11, 10, 14, 2, tzinfo=UTC)
    winter = resolve_anchor(Alert(title="x", triggered_at=winter_trigger),
                            winter_trigger + timedelta(hours=2))
    assert "2026-10-20T14:02:00Z (2026-10-20 09:02 CDT (UTC-05:00))" in summer
    assert "08:02 CST (UTC-06:00)" in winter.describe()
    assert "timestamp unavailable" in summer and "never from the wall clock" in summer


def test_describe_in_utc_has_no_local_labels_and_bad_zone_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    text = resolve_anchor(Alert(title="x", triggered_at=TRIGGER), WALL, display_tz="UTC").describe()
    assert "Analysis window: 2026-10-20T13:47:00Z to 2026-10-20T14:07:00Z." in text and "(UTC" not in text
    monkeypatch.setenv("OMNIOPS_DISPLAY_TZ", "Mars/Olympus_Mons")
    assert resolve_anchor(Alert(title="x", triggered_at=TRIGGER), WALL).display_tz == "UTC"
