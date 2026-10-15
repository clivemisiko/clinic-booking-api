import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import ClinicDateTime

START_AT_DESCRIPTION = (
    "Slot start time, ISO 8601. Include an offset (e.g. 2026-10-20T09:00:00+03:00); "
    "a value without an offset is interpreted in the clinic's timezone."
)


def _strip(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class AppointmentCreate(BaseModel):
    doctor_id: int = Field(gt=0, examples=[1])
    patient_id: int = Field(gt=0, examples=[1])
    start_at: datetime = Field(description=START_AT_DESCRIPTION, examples=["2026-10-20T09:00:00+03:00"])
    reason: str | None = Field(default=None, max_length=500, examples=["Follow-up on blood pressure"])

    @field_validator("reason")
    @classmethod
    def _clean_reason(cls, value: str | None) -> str | None:
        return _strip(value)


class AppointmentCancel(BaseModel):
    reason: str = Field(min_length=3, max_length=500, examples=["Patient is travelling"])

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("A cancellation reason of at least 3 characters is required")
        return value


class AppointmentReschedule(BaseModel):
    start_at: datetime = Field(description=START_AT_DESCRIPTION, examples=["2026-10-21T10:30:00+03:00"])


class DoctorSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    specialty: str


class PatientSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str


class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    start_at: ClinicDateTime
    end_at: ClinicDateTime
    reason: str | None
    cancellation_reason: str | None
    cancelled_at: ClinicDateTime | None
    reschedule_count: int
    doctor: DoctorSummary
    patient: PatientSummary
    created_at: ClinicDateTime
    updated_at: ClinicDateTime
