from __future__ import annotations

from datetime import datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Doctor(Base):
    __tablename__ = "doctors"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120))
    specialty: Mapped[str] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    working_hours: Mapped[list[WorkingHours]] = relationship(
        back_populates="doctor",
        cascade="all, delete-orphan",
        order_by="(WorkingHours.weekday, WorkingHours.start_time)",
        lazy="selectin",
    )


class WorkingHours(Base):
    """A recurring weekly working window, in clinic-local time.

    Several rows per weekday are allowed, which models split shifts such as
    09:00-12:30 and 13:30-17:00 with a lunch break in between.
    """

    __tablename__ = "doctor_working_hours"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_range"),
        CheckConstraint("end_time > start_time", name="end_after_start"),
        UniqueConstraint("doctor_id", "weekday", "start_time", name="uq_working_hours_doctor_day_start"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id", ondelete="CASCADE"), index=True)
    weekday: Mapped[int] = mapped_column(SmallInteger)  # 0 = Monday ... 6 = Sunday (Python's convention)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)

    doctor: Mapped[Doctor] = relationship(back_populates="working_hours")
