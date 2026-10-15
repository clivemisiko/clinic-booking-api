"""Booking, cancelling and rescheduling.

Concurrency model
-----------------
* "Is the slot free?" is never answered with a SELECT-then-INSERT, which would
  let two simultaneous requests both see the slot as free. Instead we simply
  write, and let PostgreSQL's exclusion constraint reject the loser. The
  constraint violation is translated into a 409 here.
* Cancel and reschedule lock the appointment row (SELECT ... FOR UPDATE) so two
  concurrent operations on the same appointment are applied one after the
  other, and the second sees the first one's result (e.g. "already cancelled").
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import (
    AppointmentAlreadyCancelled,
    AppointmentAlreadyStarted,
    AppointmentCancelled,
    AppointmentNotFound,
    PatientDoubleBooked,
    SameSlot,
    SlotUnavailable,
)
from app.domain.slots import ensure_aware
from app.models import (
    DOCTOR_OVERLAP_CONSTRAINT,
    PATIENT_OVERLAP_CONSTRAINT,
    Appointment,
    AppointmentEvent,
    AppointmentEventType,
    AppointmentStatus,
)
from app.services.patients import get_patient
from app.services.scheduling import get_doctor, validate_requested_slot


def _violated_constraint(exc: IntegrityError) -> str:
    """Name of the constraint behind an IntegrityError (asyncpg exposes it on
    the original driver exception); falls back to the error text."""
    driver_error = getattr(exc.orig, "__cause__", None)
    name = getattr(driver_error, "constraint_name", None)
    return name or str(exc.orig)


def _raise_for_overlap(exc: IntegrityError, details: dict) -> None:
    constraint = _violated_constraint(exc)
    if DOCTOR_OVERLAP_CONSTRAINT in constraint:
        raise SlotUnavailable(
            "This slot has just been booked by someone else. Please pick another slot.", details
        ) from exc
    if PATIENT_OVERLAP_CONSTRAINT in constraint:
        raise PatientDoubleBooked("The patient already has another appointment at this time.", details) from exc
    raise exc


async def get_appointment(session: AsyncSession, appointment_id: uuid.UUID, *, for_update: bool = False) -> Appointment:
    # populate_existing: always return fresh database state (including
    # server-generated columns such as updated_at) even if the object is
    # already in this session's identity map.
    query = select(Appointment).where(Appointment.id == appointment_id).execution_options(populate_existing=True)
    if for_update:
        # Lock only the appointment row, not the joined doctor/patient rows.
        query = query.with_for_update(of=Appointment)
    appointment = await session.scalar(query)
    if appointment is None:
        raise AppointmentNotFound(
            f"Appointment {appointment_id} does not exist", {"appointment_id": str(appointment_id)}
        )
    return appointment


async def book(
    session: AsyncSession,
    *,
    doctor_id: int,
    patient_id: int,
    start_at: datetime,
    reason: str | None,
    now: datetime,
    settings: Settings,
) -> Appointment:
    doctor = await get_doctor(session, doctor_id)
    await get_patient(session, patient_id)
    start, end = validate_requested_slot(doctor, start_at, now, settings)

    appointment = Appointment(
        id=uuid.uuid4(),
        doctor_id=doctor_id,
        patient_id=patient_id,
        start_at=start,
        end_at=end,
        status=AppointmentStatus.BOOKED,
        reason=reason,
    )
    session.add(appointment)
    session.add(
        AppointmentEvent(appointment_id=appointment.id, event_type=AppointmentEventType.BOOKED, new_start_at=start)
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        _raise_for_overlap(
            exc,
            {"doctor_id": doctor_id, "patient_id": patient_id, "start_at": start.astimezone(settings.tz).isoformat()},
        )
    return await get_appointment(session, appointment.id)


async def cancel(
    session: AsyncSession, appointment_id: uuid.UUID, *, reason: str, now: datetime, settings: Settings
) -> Appointment:
    appointment = await get_appointment(session, appointment_id, for_update=True)
    details = {"appointment_id": str(appointment_id)}

    if appointment.status == AppointmentStatus.CANCELLED:
        raise AppointmentAlreadyCancelled(
            "This appointment has already been cancelled",
            {**details, "cancelled_at": appointment.cancelled_at.astimezone(settings.tz).isoformat()},
        )
    if appointment.start_at <= now:
        raise AppointmentAlreadyStarted("Appointments that have already started or passed cannot be cancelled", details)

    appointment.status = AppointmentStatus.CANCELLED
    appointment.cancellation_reason = reason
    appointment.cancelled_at = now
    session.add(AppointmentEvent(appointment_id=appointment.id, event_type=AppointmentEventType.CANCELLED, note=reason))
    await session.commit()
    return await get_appointment(session, appointment_id)


async def reschedule(
    session: AsyncSession, appointment_id: uuid.UUID, *, start_at: datetime, now: datetime, settings: Settings
) -> Appointment:
    appointment = await get_appointment(session, appointment_id, for_update=True)
    details = {"appointment_id": str(appointment_id)}

    if appointment.status == AppointmentStatus.CANCELLED:
        raise AppointmentCancelled(
            "Cancelled appointments cannot be rescheduled. Please book a new appointment.", details
        )
    if appointment.start_at <= now:
        raise AppointmentAlreadyStarted(
            "Appointments that have already started or passed cannot be rescheduled", details
        )

    if ensure_aware(start_at, settings.tz) == appointment.start_at:
        raise SameSlot("The appointment is already booked for this slot", details)

    # Same validation as a fresh booking.
    new_start, new_end = validate_requested_slot(appointment.doctor, start_at, now, settings)

    # Updating in place releases the old range and claims the new one in a
    # single statement, so the original slot becomes bookable the moment this
    # commits, and if the new slot is taken nothing changes at all.
    previous_start = appointment.start_at
    appointment.start_at = new_start
    appointment.end_at = new_end
    appointment.reschedule_count += 1
    session.add(
        AppointmentEvent(
            appointment_id=appointment.id,
            event_type=AppointmentEventType.RESCHEDULED,
            previous_start_at=previous_start,
            new_start_at=new_start,
        )
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        _raise_for_overlap(exc, {**details, "start_at": new_start.astimezone(settings.tz).isoformat()})
    return await get_appointment(session, appointment_id)


async def upcoming_for_patient(session: AsyncSession, patient_id: int, *, now: datetime) -> list[Appointment]:
    await get_patient(session, patient_id)
    result = await session.scalars(
        select(Appointment)
        .where(
            Appointment.patient_id == patient_id,
            Appointment.status == AppointmentStatus.BOOKED,
            Appointment.start_at >= now,
        )
        .order_by(Appointment.start_at)
    )
    return list(result)
