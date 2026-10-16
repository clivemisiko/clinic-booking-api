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
* Writers that could collide are queued before they write. Without this,
  several transactions inserting overlapping rows at the same moment can each
  wait on another's uncommitted index entry, and PostgreSQL breaks the cycle
  by aborting one with a deadlock error (40P01) instead of an exclusion
  violation. So booking and rescheduling first take two transaction-scoped
  advisory locks: one for the doctor, then one for the patient. Every
  transaction takes them in that same order, so they cannot deadlock, and by
  the time a writer inserts, any competing booking has already committed and
  the exclusion constraint reports a clean conflict (409). The locks are
  released automatically at commit or rollback. The exclusion constraints
  remain the guarantee; the locks only make conflicts arrive in order.
* As a safety net, a transaction that still fails with a transient error
  (deadlock or serialization failure) is re-run a few times.
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
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

logger = logging.getLogger(__name__)

# deadlock_detected, serialization_failure: the transaction can safely be re-run.
RETRYABLE_SQLSTATES = frozenset({"40P01", "40001"})
MAX_ATTEMPTS = 5


def _is_retryable(exc: DBAPIError) -> bool:
    return getattr(exc.orig, "sqlstate", None) in RETRYABLE_SQLSTATES


async def _with_retry[T](session: AsyncSession, operation: Callable[[], Awaitable[T]]) -> T:
    """Run ``operation`` (a full read-validate-write transaction), re-running it
    from scratch if PostgreSQL aborts it with a transient concurrency error."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await operation()
        except IntegrityError:
            raise  # a real constraint violation: never retried
        except DBAPIError as exc:
            await session.rollback()
            if not _is_retryable(exc) or attempt == MAX_ATTEMPTS:
                raise
            logger.info("Retrying after transient database error (attempt %d): %s", attempt, exc.orig)
            # Small random back-off so the retrying transactions don't collide again in lockstep.
            await asyncio.sleep(random.uniform(0.01, 0.05) * attempt)
    raise AssertionError("unreachable")


# Namespaces for the two-key form pg_advisory_xact_lock(int, int), so doctor 7
# and patient 7 are different locks.
_DOCTOR_LOCK_NAMESPACE = 1
_PATIENT_LOCK_NAMESPACE = 2
_ADVISORY_LOCK = text("SELECT pg_advisory_xact_lock(:namespace, :key)")


async def _queue_conflicting_writers(session: AsyncSession, *, doctor_id: int, patient_id: int) -> None:
    """Wait until no other transaction is writing for this doctor or patient.

    Must run before anything is added to the session: the session flushes
    pending objects before executing a statement, which would insert the row
    before the lock is held. Two separate statements guarantee the order.
    """
    await session.execute(_ADVISORY_LOCK, {"namespace": _DOCTOR_LOCK_NAMESPACE, "key": doctor_id})
    await session.execute(_ADVISORY_LOCK, {"namespace": _PATIENT_LOCK_NAMESPACE, "key": patient_id})


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
    async def attempt() -> Appointment:
        return await _book_once(
            session,
            doctor_id=doctor_id,
            patient_id=patient_id,
            start_at=start_at,
            reason=reason,
            now=now,
            settings=settings,
        )

    return await _with_retry(session, attempt)


async def _book_once(
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
    await _queue_conflicting_writers(session, doctor_id=doctor_id, patient_id=patient_id)

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
    async def attempt() -> Appointment:
        return await _reschedule_once(session, appointment_id, start_at=start_at, now=now, settings=settings)

    return await _with_retry(session, attempt)


async def _reschedule_once(
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
    await _queue_conflicting_writers(session, doctor_id=appointment.doctor_id, patient_id=appointment.patient_id)

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
            # Strictly after now: once the start time arrives the appointment has
            # started, which is also when cancel and reschedule stop accepting it.
            Appointment.start_at > now,
        )
        .order_by(Appointment.start_at)
    )
    return list(result)
