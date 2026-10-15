from datetime import date, time

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ClinicDateTime

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class WorkingHoursOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    weekday: int = Field(description="0 = Monday ... 6 = Sunday")
    start_time: time
    end_time: time


class DoctorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    specialty: str
    is_active: bool
    working_hours: list[WorkingHoursOut]


class SlotOut(BaseModel):
    start_at: ClinicDateTime
    end_at: ClinicDateTime


class AvailabilityOut(BaseModel):
    doctor_id: int
    date: date
    timezone: str
    slot_minutes: int
    slots: list[SlotOut]
