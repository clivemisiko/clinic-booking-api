from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.slots import Slot, generate_slots, local_day_bounds
from app.models import Appointment, AppointmentStatus
from app.services.scheduling import earliest_bookable, get_doctor, latest_bookable_day, windows_for


async def available_slots(session: AsyncSession, doctor_id: int, day: date, now, settings: Settings) -> list[Slot]:
    """Free, bookable slots for a doctor on a clinic-local date.

    Only slots that would actually pass booking validation are returned: slots
    in the past, inside the minimum lead time or beyond the booking horizon are
    left out rather than shown and then rejected.
    """
    doctor = await get_doctor(session, doctor_id)
    if not doctor.is_active or day > latest_bookable_day(now, settings):
        return []

    candidates = generate_slots(day, windows_for(doctor, day), settings.tz, settings.slot_minutes)
    if not candidates:
        return []

    day_start, day_end = local_day_bounds(day, settings.tz)
    rows = await session.execute(
        select(Appointment.start_at, Appointment.end_at).where(
            Appointment.doctor_id == doctor_id,
            Appointment.status == AppointmentStatus.BOOKED,
            Appointment.start_at < day_end,
            Appointment.end_at > day_start,
        )
    )
    booked = [(start, end) for start, end in rows]
    earliest = earliest_bookable(now, settings)

    def is_free(slot: Slot) -> bool:
        return slot.start >= earliest and all(slot.end <= s or slot.start >= e for s, e in booked)

    return [slot for slot in candidates if is_free(slot)]
