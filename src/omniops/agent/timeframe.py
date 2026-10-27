"""Trigger-window resolution: the run's single time source, computed once by code.

Retrospective lessons encoded here (OpsIntelligence 3.3: time was the most persistent source of
wrong answers):
- Sources in order: the notification link's own window, then the trigger time (triggering event /
  last-triggered time), then the alert message time; the wall clock only when nothing else exists.
- The window is Datadog's: 15 minutes before the trigger to 5 minutes after, clamped to now. A link
  window is widened to contain that trigger window, so a notification that trailed the real trigger
  (a downtime ended, a slow pipeline) reaches back to it, and a short evaluation window ending at the
  trigger, or a fixed range ending before it, still covers the minutes after it. An explicit earlier
  onset reaches further back (within 24 h). Customer reports get 60 minutes before the report, since
  humans notice late.
- Future starts are rejected (that source is skipped and the reason noted); future ends are clamped.
- No staleness guard: an old alert keeps its own window. A blanket "use recent windows" rule once
  undid alert anchoring, and a 4-hour guard silently rewrote historical windows.
- Epoch strings are read as epochs first, unit by magnitude (an epoch-ms string once became 1791).
- Every datetime is timezone-aware UTC; local labels come from the tz database (``zoneinfo``,
  ``OMNIOPS_DISPLAY_TZ``, default UTC), never a fixed offset ("01:13 CST (UTC-5)" in October).
- ``describe()`` is the one shared time rule rendered into every agent's context, and it tells the
  model to write "timestamp unavailable" instead of guessing.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from omniops.engine.timeutil import TimeAnchor, TimeParseError, fmt_rfc3339, to_datetime

if TYPE_CHECKING:
    from omniops.agent.types import Alert

log = logging.getLogger("omniops.timeframe")

__all__ = ["TriggerAnchor", "display_zone", "local_label", "resolve_anchor"]

BEFORE_TRIGGER = timedelta(minutes=15)
AFTER_TRIGGER = timedelta(minutes=5)
BEFORE_REPORT = timedelta(minutes=60)        # customer reports: humans notice and write late
ONSET_PAD = timedelta(minutes=5)             # context before a reached-back onset
MAX_REACH_BACK = timedelta(hours=24)         # an onset further back than this is not this incident
FUTURE_TOLERANCE = timedelta(minutes=5)      # clock skew between the tool, Slack and us
LATE_NOTICE = timedelta(minutes=10)          # notification this long after the trigger is "late"
_MIN_YEAR = 2000

SOURCE_LABELS = {
    "link_window": "the notification link's own window",
    "trigger": "the alert's trigger time",
    "message_ts": "the alert message time",
    "customer_report": "the customer report time",
    "wall_clock": "the current time (no alert time available)",
}


# ---------------------------------------------------------------------------------------------
# Display time zone (labels only; all computation is UTC)
# ---------------------------------------------------------------------------------------------


def display_zone(name: str | None = None) -> ZoneInfo:
    """The display zone from ``name`` or ``OMNIOPS_DISPLAY_TZ``; UTC when unset or unknown."""
    key = (name or os.environ.get("OMNIOPS_DISPLAY_TZ") or "UTC").strip()
    try:
        return ZoneInfo(key)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("unknown display time zone %r; using UTC", key)
        return ZoneInfo("UTC")


def _is_utc(zone: ZoneInfo) -> bool:
    return zone.key in ("UTC", "Etc/UTC", "Etc/Universal", "Universal", "Zulu", "Etc/Zulu", "GMT", "Etc/GMT")


def local_label(dt: datetime, zone: ZoneInfo | str | None = None) -> str:
    """'2026-10-20 09:02 CDT (UTC-05:00)': abbreviation and offset come from the tz database for that
    instant, so daylight saving is always right."""
    z = zone if isinstance(zone, ZoneInfo) else display_zone(zone)
    local = _aware(dt).astimezone(z)
    off = local.strftime("%z")
    return f"{local:%Y-%m-%d %H:%M} {local.tzname()} (UTC{off[:3]}:{off[3:]})"


# ---------------------------------------------------------------------------------------------
# The anchor
# ---------------------------------------------------------------------------------------------


@dataclass
class TriggerAnchor(TimeAnchor):
    """A ``TimeAnchor`` that also knows the trigger and renders the shared time rule for prompts."""

    trigger: datetime | None = None
    display_tz: str = "UTC"

    def label(self, dt: datetime) -> str:
        """UTC RFC 3339, plus the local label when a display zone other than UTC is configured."""
        zone = display_zone(self.display_tz)
        utc = fmt_rfc3339(dt)
        return utc if _is_utc(zone) else f"{utc} ({local_label(dt, zone)})"

    def describe(self) -> str:
        parts = [f"Time anchor: {SOURCE_LABELS.get(self.source, self.source)}."]
        if self.window_start is not None and self.window_end is not None:
            start, end = self.label(self.window_start), self.label(self.window_end)
            parts.append(f"Analysis window: {start} to {end}.")
        if self.trigger is not None:
            parts.append(f"Alert triggered at {self.label(self.trigger)}.")
        parts.append(f"'now' means {self.label(self.now)}: relative times (\"1h ago\", \"yesterday\") count "
                     "back from it, never from the wall clock.")
        parts.append("Quote times from this anchor or from the data; when a time is not in the data, write "
                     "\"timestamp unavailable\" instead of guessing.")
        return " ".join(parts)


def resolve_anchor(alert: Alert | None, wall_now: datetime | None = None, *,
                   display_tz: str | None = None) -> TriggerAnchor:
    """Resolve the run's trigger window once (3.3). ``wall_now`` is injectable for tests and replays."""
    wall = _aware(wall_now) if wall_now is not None else datetime.now(UTC).replace(microsecond=0)
    tz = display_zone(display_tz).key
    notes: list[str] = [str(n) for n in alert.time_notes] if alert is not None else []
    if alert is None:
        return TriggerAnchor(now=wall, source="wall_clock", notes=notes, display_tz=tz)

    msg_time = _time(alert.source_ts, wall)
    trigger = _not_future(_time(alert.triggered_at, wall), wall, "trigger time", notes)
    if trigger is not None and msg_time is not None and msg_time - trigger > LATE_NOTICE:
        minutes = int((msg_time - trigger).total_seconds() // 60)
        notes.append(f"notification arrived {minutes} min after the trigger; anchored on the trigger")

    if alert.customer_report:
        return _customer_window(alert, msg_time, wall, notes, tz)

    onset = _time(alert.onset, wall)      # an explicit earlier onset, if a parser found one
    link = _link_window(alert, wall, notes)
    if link is not None:
        start, end = _widen_to_trigger(link, trigger, wall, notes)
        start = _reach_back(start, [onset], notes)
        return TriggerAnchor(now=end, window_start=start, window_end=end, source="link_window",
                             notes=notes, trigger=trigger, display_tz=tz)
    if trigger is not None:
        return _around(trigger, BEFORE_TRIGGER, wall, "trigger", notes, tz, trigger=trigger, onset=onset)
    msg_time = _not_future(msg_time, wall, "message time", notes)
    if msg_time is not None:
        return _around(msg_time, BEFORE_TRIGGER, wall, "message_ts", notes, tz, onset=onset)
    notes.append("the alert carries no usable time; anchored to the current time")
    return _around(wall, BEFORE_TRIGGER, wall, "wall_clock", notes, tz)


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


def _aware(dt: datetime) -> datetime:
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(UTC)


def _time(value: Any, wall: datetime) -> datetime | None:
    """Any time-like value -> aware UTC; epoch strings as epochs (unit by magnitude); implausible -> None."""
    if value is None or value == "":
        return None
    try:
        dt = _aware(value) if isinstance(value, datetime) else to_datetime(value, anchor=wall)
    except (TimeParseError, ValueError, OverflowError, OSError):
        return None
    dt = _aware(dt)
    return dt if _MIN_YEAR <= dt.year <= wall.year + 1 else None


def _not_future(dt: datetime | None, wall: datetime, what: str, notes: list[str]) -> datetime | None:
    if dt is not None and dt > wall + FUTURE_TOLERANCE:
        notes.append(f"ignored {what} {fmt_rfc3339(dt)}: it is in the future")
        return None
    return dt


def _link_window(alert: Alert, wall: datetime, notes: list[str]) -> tuple[datetime, datetime] | None:
    start, end = _time(alert.window_start, wall), _time(alert.window_end, wall)
    if start is None or end is None:
        return None
    if start > wall:
        notes.append(f"ignored link window starting {fmt_rfc3339(start)}: the start is in the future")
        return None
    if end > wall:
        notes.append(f"link window end {fmt_rfc3339(end)} clamped to now {fmt_rfc3339(wall)}")
        end = wall
    if end <= start:
        notes.append("ignored link window: it ends before it starts")
        return None
    return start, end


def _reach_back(start: datetime, onsets: list[datetime | None], notes: list[str]) -> datetime:
    """Extend the start back to an earlier onset (late notification), within ``MAX_REACH_BACK``."""
    earlier = [o for o in onsets if o is not None and o < start]
    if not earlier:
        return start
    onset = min(earlier)
    if start - onset > MAX_REACH_BACK:
        notes.append(f"onset {fmt_rfc3339(onset)} is more than 24h before the window; not reached back")
        return start
    notes.append(f"window extended back to the actual onset {fmt_rfc3339(onset)}")
    return onset - ONSET_PAD


def _widen_to_trigger(link: tuple[datetime, datetime], trigger: datetime | None, wall: datetime,
                      notes: list[str]) -> tuple[datetime, datetime]:
    """The link window widened to contain the trigger window (15 min before to 5 min after, clamped to
    now). Nothing in the link's range is dropped; a trigger more than 24 h before it (a re-notification
    keeps the first trigger time) does not stretch the window back."""
    start, end = link
    if trigger is None:
        return start, end
    want_start, want_end = trigger - BEFORE_TRIGGER, min(trigger + AFTER_TRIGGER, wall)
    if want_start < start:
        if start - want_start > MAX_REACH_BACK:
            notes.append(f"trigger {fmt_rfc3339(trigger)} is more than 24h before the link window; "
                         "not reached back")
        else:
            notes.append(f"link window extended back to 15 min before the trigger {fmt_rfc3339(trigger)}")
            start = want_start
    if want_end > end:
        notes.append(f"link window extended to 5 min after the trigger {fmt_rfc3339(trigger)}")
        end = want_end
    return start, end


def _around(when: datetime, before: timedelta, wall: datetime, source: str, notes: list[str], tz: str, *,
            trigger: datetime | None = None, onset: datetime | None = None) -> TriggerAnchor:
    """``before`` the reference time to 5 minutes after it, end clamped to now. ``when`` is never more
    than ``FUTURE_TOLERANCE`` ahead of ``wall``, so the window is never empty."""
    start = _reach_back(when - before, [onset], notes)
    end = when + AFTER_TRIGGER
    if end > wall:
        if source != "wall_clock":
            notes.append(f"window end clamped to now ({fmt_rfc3339(wall)}): data may still be arriving")
        end = wall
    return TriggerAnchor(now=end, window_start=start, window_end=end, source=source, notes=notes,
                         trigger=trigger, display_tz=tz)


def _customer_window(alert: Alert, msg_time: datetime | None, wall: datetime, notes: list[str],
                     tz: str) -> TriggerAnchor:
    """A human report: 60 minutes before the report (now, if it has no time), back to a stated onset."""
    base = _not_future(msg_time, wall, "report time", notes) or wall
    onset = _time(alert.reported_onset, wall)
    minutes = alert.reported_minutes_ago
    if onset is None and minutes is not None and minutes > 0:
        onset = base - timedelta(minutes=float(minutes))
    return _around(base, BEFORE_REPORT, wall, "customer_report", notes, tz, onset=onset)
