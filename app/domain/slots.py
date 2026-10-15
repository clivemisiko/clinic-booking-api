"""Pure slot arithmetic.

Nothing in this module touches the database or the web framework, so the core
scheduling rules can be unit-tested in isolation and reused by any caller
(availability listing, booking validation, rescheduling).

Conventions
-----------
* Working hours are expressed in the clinic's *local* wall-clock time.
* Every datetime returned from here is timezone-aware.
* A slot is a half-open interval ``[start, end)``: a slot ending at 10:00 does
  not overlap one starting at 10:00.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class WorkingWindow:
    """A continuous block of working time on a given weekday, e.g. 09:00-12:30."""

    start: time
    end: time

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError("Working window must end after it starts (overnight shifts are not supported)")


@dataclass(frozen=True, slots=True)
class Slot:
    start: datetime
    end: datetime


class SlotCheck(enum.Enum):
    OK = "ok"
    OUTSIDE_WORKING_HOURS = "outside_working_hours"
    MISALIGNED = "misaligned"


def ensure_aware(value: datetime, tz: ZoneInfo) -> datetime:
    """Interpret a naive datetime as clinic-local time; leave aware ones untouched."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=tz)
    return value


def generate_slots(
    day: date,
    windows: Iterable[WorkingWindow],
    tz: ZoneInfo,
    slot_minutes: int,
) -> list[Slot]:
    """Every slot a doctor works on ``day``, in chronological order.

    A trailing fragment shorter than a full slot (e.g. the last 15 minutes of a
    09:00-16:45 window) is never offered: a slot must fit fully inside its
    window. Overlapping windows are de-duplicated.
    """
    step = timedelta(minutes=slot_minutes)
    by_start: dict[datetime, Slot] = {}
    for window in windows:
        cursor = datetime.combine(day, window.start, tzinfo=tz)
        window_end = datetime.combine(day, window.end, tzinfo=tz)
        while cursor + step <= window_end:
            by_start.setdefault(cursor, Slot(cursor, cursor + step))
            cursor += step
    return [by_start[k] for k in sorted(by_start)]


def check_slot(
    start: datetime,
    windows: Iterable[WorkingWindow],
    tz: ZoneInfo,
    slot_minutes: int,
) -> SlotCheck:
    """Classify a requested start time against the doctor's working windows.

    ``windows`` must be the windows for the *local* date of ``start``.
    """
    step = timedelta(minutes=slot_minutes)
    local = start.astimezone(tz)
    day = local.date()
    for window in windows:
        window_start = datetime.combine(day, window.start, tzinfo=tz)
        window_end = datetime.combine(day, window.end, tzinfo=tz)
        if not (window_start <= local < window_end):
            continue
        if (local - window_start) % step != timedelta(0):
            return SlotCheck.MISALIGNED
        if local + step > window_end:
            return SlotCheck.OUTSIDE_WORKING_HOURS
        return SlotCheck.OK
    return SlotCheck.OUTSIDE_WORKING_HOURS


def local_day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[start, end) of a clinic-local calendar day as aware datetimes."""
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start, datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
