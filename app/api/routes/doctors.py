from datetime import date

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import NowDep, SessionDep, SettingsDep
from app.models import Doctor
from app.schemas.common import error_responses
from app.schemas.doctor import AvailabilityOut, DoctorOut, SlotOut
from app.services.availability import available_slots
from app.services.scheduling import get_doctor

router = APIRouter(prefix="/doctors", tags=["Doctors"])


@router.get("", response_model=list[DoctorOut], summary="List doctors and their working hours")
async def list_doctors(session: SessionDep):
    return list(await session.scalars(select(Doctor).order_by(Doctor.id)))


@router.get("/{doctor_id}", response_model=DoctorOut, summary="Get a doctor", responses=error_responses(404))
async def read_doctor(doctor_id: int, session: SessionDep):
    return await get_doctor(session, doctor_id)


@router.get(
    "/{doctor_id}/availability",
    response_model=AvailabilityOut,
    summary="Free 30-minute slots for a doctor on a date",
    responses=error_responses(404, 422),
)
async def doctor_availability(
    doctor_id: int,
    session: SessionDep,
    settings: SettingsDep,
    now: NowDep,
    on: date = Query(alias="date", description="Clinic-local date, YYYY-MM-DD", examples=["2026-10-20"]),
):
    """Only slots that can actually be booked right now are returned: past
    slots, slots less than an hour away and taken slots are excluded."""
    slots = await available_slots(session, doctor_id, on, now, settings)
    return AvailabilityOut(
        doctor_id=doctor_id,
        date=on,
        timezone=settings.clinic_timezone,
        slot_minutes=settings.slot_minutes,
        slots=[SlotOut(start_at=s.start, end_at=s.end) for s in slots],
    )
