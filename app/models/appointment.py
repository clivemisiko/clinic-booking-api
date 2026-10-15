from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.doctor import Doctor
from app.models.patient import Patient


class AppointmentStatus(enum.StrEnum):
    BOOKED = "booked"
    CANCELLED = "cancelled"


# Names of the PostgreSQL exclusion constraints created in the initial
# migration. They are the real guarantee against double booking: the database
# rejects any two *booked* appointments whose time ranges overlap for the same
# doctor (or the same patient), even under concurrent requests.
DOCTOR_OVERLAP_CONSTRAINT = "ex_appointments_doctor_no_overlap"
PATIENT_OVERLAP_CONSTRAINT = "ex_appointments_patient_no_overlap"


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint("end_at > start_at", name="end_after_start"),
        CheckConstraint("status IN ('booked', 'cancelled')", name="status_valid"),
        CheckConstraint(
            "status <> 'cancelled' OR (cancellation_reason IS NOT NULL AND cancelled_at IS NOT NULL)",
            name="cancellation_has_reason",
        ),
        Index("ix_appointments_doctor_start", "doctor_id", "start_at"),
        Index("ix_appointments_patient_start", "patient_id", "start_at"),
        # The two EXCLUDE USING gist constraints are defined in the migration
        # (alembic/versions/0001_initial_schema.py) with raw SQL.
    )

    # UUIDs instead of sequential ids: appointment ids end up in URLs and
    # emails, and should not let anyone enumerate other patients' bookings.
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id", ondelete="RESTRICT"))
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="RESTRICT"))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default=AppointmentStatus.BOOKED)
    reason: Mapped[str | None] = mapped_column(Text)
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reschedule_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    doctor: Mapped[Doctor] = relationship(lazy="joined", innerjoin=True)
    patient: Mapped[Patient] = relationship(lazy="joined", innerjoin=True)
    events: Mapped[list[AppointmentEvent]] = relationship(
        back_populates="appointment",
        order_by="AppointmentEvent.created_at",
        cascade="all, delete-orphan",
        lazy="noload",
    )


class AppointmentEventType(enum.StrEnum):
    BOOKED = "booked"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"


class AppointmentEvent(Base):
    """Append-only audit trail. Rescheduling mutates the appointment row in
    place, so this table is where the history of *what used to be* lives."""

    __tablename__ = "appointment_events"
    __table_args__ = (CheckConstraint("event_type IN ('booked', 'rescheduled', 'cancelled')", name="event_type_valid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("appointments.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(16))
    previous_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    new_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    appointment: Mapped[Appointment] = relationship(back_populates="events")
