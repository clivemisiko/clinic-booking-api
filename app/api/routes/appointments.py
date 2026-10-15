import uuid

from fastapi import APIRouter, status

from app.api.deps import NowDep, SessionDep, SettingsDep
from app.schemas.appointment import AppointmentCancel, AppointmentCreate, AppointmentOut, AppointmentReschedule
from app.schemas.common import error_responses
from app.services import appointments as service

router = APIRouter(prefix="/appointments", tags=["Appointments"])


@router.post(
    "",
    response_model=AppointmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Book a slot",
    responses=error_responses(404, 409, 422),
)
async def book_appointment(body: AppointmentCreate, session: SessionDep, settings: SettingsDep, now: NowDep):
    """Book a 30-minute slot.

    Rejected with **422** if the slot is in the past, less than an hour away,
    outside the doctor's working hours or not on a slot boundary; with **409**
    if the slot is already taken or the patient is busy at that time.
    """
    return await service.book(
        session,
        doctor_id=body.doctor_id,
        patient_id=body.patient_id,
        start_at=body.start_at,
        reason=body.reason,
        now=now,
        settings=settings,
    )


@router.get(
    "/{appointment_id}", response_model=AppointmentOut, summary="Get an appointment", responses=error_responses(404)
)
async def get_appointment(appointment_id: uuid.UUID, session: SessionDep):
    return await service.get_appointment(session, appointment_id)


@router.patch(
    "/{appointment_id}/cancel",
    response_model=AppointmentOut,
    summary="Cancel an appointment",
    responses=error_responses(404, 409, 422),
)
async def cancel_appointment(
    appointment_id: uuid.UUID, body: AppointmentCancel, session: SessionDep, settings: SettingsDep, now: NowDep
):
    """Cancel with a reason. The slot becomes bookable again immediately.
    Returns **409** if the appointment is already cancelled."""
    return await service.cancel(session, appointment_id, reason=body.reason, now=now, settings=settings)


@router.patch(
    "/{appointment_id}/reschedule",
    response_model=AppointmentOut,
    summary="Move an appointment to a new slot",
    responses=error_responses(404, 409, 422),
)
async def reschedule_appointment(
    appointment_id: uuid.UUID, body: AppointmentReschedule, session: SessionDep, settings: SettingsDep, now: NowDep
):
    """Move to a new slot with the same doctor. The new slot is validated
    exactly like a fresh booking and the original slot is released atomically.
    Returns **409** if the appointment is cancelled."""
    return await service.reschedule(session, appointment_id, start_at=body.start_at, now=now, settings=settings)
