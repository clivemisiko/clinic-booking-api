"""Scheduling rules shared by availability, booking and rescheduling.

Keeping "is this slot bookable?" in one place guarantees the three endpoints
can never disagree: a slot shown as available is exactly a slot that would
pass booking validation (apart from a race with another patient, which the
database constraint settles).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import (
    BookingTooFarAhead,
    BookingTooSoon,
    DoctorInactive,
    DoctorNotFound,
    InvalidSlotBoundary,
    OutsideWorkingHours,
    SlotInPast,
)
from app.domain.slots import SlotCheck, WorkingWindow, check_slot, ensure_aware
from app.models import Doctor
from app.schemas.doctor import WEEKDAY_NAMES


async def get_doctor(session: AsyncSession, doctor_id: int) -> Doctor:
    doctor = await session.scalar(select(Doctor).where(Doctor.id == doctor_id))
    if doctor is None:
        raise DoctorNotFound(f"Doctor {doctor_id} does not exist", {"doctor_id": doctor_id})
    return doctor


def windows_for(doctor: Doctor, day: date) -> list[WorkingWindow]:
    weekday = day.weekday()
    return [WorkingWindow(wh.start_time, wh.end_time) for wh in doctor.working_hours if wh.weekday == weekday]


def earliest_bookable(now: datetime, settings: Settings) -> datetime:
    return now + timedelta(minutes=settings.min_booking_lead_minutes)


def latest_bookable_day(now: datetime, settings: Settings) -> date:
    return now.astimezone(settings.tz).date() + timedelta(days=settings.booking_horizon_days)


def validate_requested_slot(
    doctor: Doctor, start_at: datetime, now: datetime, settings: Settings
) -> tuple[datetime, datetime]:
    """Apply every rule a fresh booking must satisfy, except "slot not taken".

    Returns the normalised (start, end). "Not taken" is deliberately left to the
    database's exclusion constraint, the only check that is race-free.
    """
    tz = settings.tz
    start = ensure_aware(start_at, tz)
    local = start.astimezone(tz)
    details = {"doctor_id": doctor.id, "start_at": local.isoformat()}

    if not doctor.is_active:
        raise DoctorInactive(f"Dr. {doctor.full_name} is not currently taking appointments", details)

    if start <= now:
        raise SlotInPast("Appointments cannot be booked in the past", details)

    earliest = earliest_bookable(now, settings)
    if start < earliest:
        raise BookingTooSoon(
            f"Appointments must be booked at least {settings.min_booking_lead_minutes} minutes in advance",
            {**details, "earliest_allowed": earliest.astimezone(tz).isoformat()},
        )

    latest_day = latest_bookable_day(now, settings)
    if local.date() > latest_day:
        raise BookingTooFarAhead(
            f"Appointments can be booked at most {settings.booking_horizon_days} days ahead",
            {**details, "latest_allowed_date": latest_day.isoformat()},
        )

    windows = windows_for(doctor, local.date())
    result = check_slot(start, windows, tz, settings.slot_minutes)
    if result is SlotCheck.OUTSIDE_WORKING_HOURS:
        if not windows:
            message = f"Dr. {doctor.full_name} does not work on {WEEKDAY_NAMES[local.weekday()]}s"
        else:
            hours = ", ".join(f"{w.start:%H:%M}-{w.end:%H:%M}" for w in windows)
            message = f"Requested time is outside Dr. {doctor.full_name}'s working hours on that day ({hours})"
        raise OutsideWorkingHours(
            message, {**details, "working_hours": [f"{w.start:%H:%M}-{w.end:%H:%M}" for w in windows]}
        )
    if result is SlotCheck.MISALIGNED:
        raise InvalidSlotBoundary(
            f"Appointments start on {settings.slot_minutes}-minute boundaries within working hours "
            "(e.g. 09:00, 09:30). Use GET /doctors/{id}/availability to see valid start times.",
            details,
        )

    return start, start + timedelta(minutes=settings.slot_minutes)
