"""Time handling: one time source per run, epochs read as epochs, and explicit API formats.

Lessons encoded here (OpsIntelligence 3.3):
- Epoch strings are read as epochs first, with the unit inferred from magnitude. On Python 3.11+
  ``datetime.fromisoformat`` accepts compact digit strings, which once turned an epoch-ms value
  into the year 1791.
- Relative times are pinned to the run's anchor, never to the wall clock.
- Future starts are rejected; future ends are clamped to the anchor.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

_DURATION_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(ns|us|µs|ms|s|m|h|d|w|y)?\s*$")
_COMPOUND_RE = re.compile(r"(\d+(?:\.\d+)?)(ns|us|µs|ms|s|m|h|d|w|y)")
_UNIT_SECONDS = {
    "ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1, "m": 60, "h": 3600, "d": 86400,
    "w": 604800, "y": 31536000,
}
_RELATIVE_RE = re.compile(r"^\s*now\s*(?:([+-])\s*(\S+))?\s*$", re.I)
_NUMERIC_RE = re.compile(r"^\s*-?\d+(?:\.\d+)?\s*$")


class TimeParseError(ValueError):
    pass


def parse_duration(value: str | int | float | timedelta) -> timedelta:
    """'15m' -> 15 minutes. Accepts compound forms ('1h30m'); bare numbers are seconds."""
    if isinstance(value, timedelta):
        return value
    if isinstance(value, (int, float)):
        return timedelta(seconds=float(value))
    s = str(value).strip()
    m = _DURATION_RE.match(s)
    if m:
        return timedelta(seconds=float(m.group(1)) * _UNIT_SECONDS[m.group(2) or "s"])
    parts = _COMPOUND_RE.findall(s)
    if parts and "".join(a + b for a, b in parts) == s.replace(" ", ""):
        return timedelta(seconds=sum(float(a) * _UNIT_SECONDS[b] for a, b in parts))
    raise TimeParseError(f"cannot parse duration {value!r}")


def epoch_to_datetime(x: float) -> datetime:
    """Infer the unit from magnitude: s < 1e11 <= ms < 1e14 <= us < 1e17 <= ns."""
    ax = abs(x)
    if ax < 1e11:
        secs = x
    elif ax < 1e14:
        secs = x / 1e3
    elif ax < 1e17:
        secs = x / 1e6
    else:
        secs = x / 1e9
    return datetime.fromtimestamp(secs, tz=UTC)


def to_datetime(value: Any, anchor: datetime | None = None) -> datetime:
    """Parse anything time-like into an aware UTC datetime."""
    if value is None:
        raise TimeParseError("time value is None")
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, bool):
        raise TimeParseError("boolean is not a time")
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            raise TimeParseError("NaN is not a time")
        return epoch_to_datetime(float(value))
    s = str(value).strip()
    if not s:
        raise TimeParseError("empty time string")
    if _NUMERIC_RE.match(s):
        return epoch_to_datetime(float(s))
    rel = _RELATIVE_RE.match(s)
    if rel:
        base = anchor or datetime.now(UTC)
        if rel.group(1):
            d = parse_duration(rel.group(2))
            return base - d if rel.group(1) == "-" else base + d
        return base
    iso = s.replace(" ", "T", 1) if re.match(r"^\d{4}-\d{2}-\d{2} \d", s) else s
    if iso.endswith("Z") or iso.endswith("z"):
        iso = iso[:-1] + "+00:00"
    # Trim sub-microsecond precision (RFC3339Nano from Go APIs) which fromisoformat rejects.
    iso = re.sub(r"(\.\d{6})\d+", r"\1", iso)
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        for fmt in ("%d/%b/%Y:%H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%Y/%m/%d %H:%M:%S"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        else:
            raise TimeParseError(f"cannot parse time {value!r}") from None
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def fmt_epoch_s(dt: datetime) -> int:
    return int(to_datetime(dt).timestamp())


def fmt_epoch_ms(dt: datetime) -> int:
    return int(round(to_datetime(dt).timestamp() * 1000))


def fmt_epoch_us(dt: datetime) -> int:
    return int(round(to_datetime(dt).timestamp() * 1_000_000))


def fmt_epoch_ns(dt: datetime) -> int:
    d = to_datetime(dt)
    return int(d.timestamp()) * 1_000_000_000 + d.microsecond * 1000


def fmt_rfc3339(dt: datetime) -> str:
    return to_datetime(dt).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_rfc3339_ms(dt: datetime) -> str:
    d = to_datetime(dt).astimezone(UTC)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def fmt_prom_duration(td: timedelta | float | int) -> str:
    secs = td.total_seconds() if isinstance(td, timedelta) else float(td)
    secs = max(1, int(round(secs)))
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs % n == 0:
            return f"{secs // n}{unit}"
    return f"{secs}s"


_NICE_STEPS = [1, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400]


def auto_step(start: datetime, end: datetime, target_points: int = 300, min_step: str = "10s") -> int:
    """Pick a 'nice' step (seconds) giving about ``target_points`` points (OpsIntelligence 3.9.3)."""
    span = max(1.0, (end - start).total_seconds())
    raw = span / max(1, target_points)
    floor = parse_duration(min_step).total_seconds()
    for s in _NICE_STEPS:
        if s >= raw and s >= floor:
            return s
    return int(max(raw, floor))


@dataclass
class TimeAnchor:
    """The run's single time source. ``now`` is what relative times are pinned to."""

    now: datetime
    window_start: datetime | None = None
    window_end: datetime | None = None
    source: str = "wall_clock"
    notes: list[str] = field(default_factory=list)

    @classmethod
    def wall_clock(cls) -> TimeAnchor:
        return cls(now=datetime.now(UTC).replace(microsecond=0))

    @classmethod
    def for_trigger(
        cls,
        trigger: datetime,
        *,
        before: str = "15m",
        after: str = "5m",
        actual_onset: datetime | None = None,
        wall_now: datetime | None = None,
        source: str = "trigger",
    ) -> TimeAnchor:
        """Datadog-style window: 15 min before the trigger to 5 min after (clamped to now).

        If the notification trailed the real onset, the window reaches back to the onset.
        """
        wall = wall_now or datetime.now(UTC)
        trigger = to_datetime(trigger)
        start = trigger - parse_duration(before)
        notes = []
        if actual_onset is not None:
            onset = to_datetime(actual_onset)
            if onset < start:
                notes.append(f"window extended back to actual onset {fmt_rfc3339(onset)}")
                start = onset - timedelta(minutes=5)
        end = min(trigger + parse_duration(after), wall)
        return cls(now=end, window_start=start, window_end=end, source=source, notes=notes)

    def default_window(self, lookback: str) -> tuple[datetime, datetime]:
        if self.window_start is not None and self.window_end is not None:
            return self.window_start, self.window_end
        return self.now - parse_duration(lookback), self.now

    def describe(self) -> str:
        if self.window_start and self.window_end:
            return (
                f"Anchor ({self.source}): analysis window {fmt_rfc3339(self.window_start)} to "
                f"{fmt_rfc3339(self.window_end)}; 'now' means {fmt_rfc3339(self.now)}."
            )
        return f"Anchor ({self.source}): 'now' means {fmt_rfc3339(self.now)}."


_RETENTION_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(ns|us|ms|s|sec|secs|seconds?|m|min|mins|minutes?|h|hours?|d|days?|w|weeks?|"
    r"mo|months?|y|years?)\b", re.I)
_RETENTION_UNIT = {"ns": 1e-9, "us": 1e-6, "ms": 1e-3, "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
                   "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60, "h": 3600, "hour": 3600,
                   "hours": 3600, "d": 86400, "day": 86400, "days": 86400, "w": 604800, "week": 604800,
                   "weeks": 604800, "mo": 2592000, "month": 2592000, "months": 2592000, "y": 31536000,
                   "year": 31536000, "years": 31536000}


def parse_retention(value: str | None) -> timedelta | None:
    """Retention written for humans ("35d (Log Monitoring Classic)", "150 days by default", "set by ILM").

    Only an unambiguous leading duration counts; ranges ("1-3y") and prose return None, meaning
    "do not clamp" - clamping on a guess would hide data that still exists.
    """
    if not value:
        return None
    m = _RETENTION_RE.match(str(value))
    if not m or re.match(r"^\s*\d+(?:\.\d+)?\s*-", str(value)):
        return None
    return timedelta(seconds=float(m.group(1)) * _RETENTION_UNIT[m.group(2).lower()])
