"""Initial schema: doctors, working hours, patients, appointments, audit events.

Revision ID: 0001
Revises:
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # btree_gist lets a GiST index combine plain equality (doctor_id = ...) with
    # range overlap (tstzrange && ...), which is what the exclusion constraints
    # below need. It is a trusted extension, so a database owner can install it.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "doctors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("specialty", sa.String(80), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_doctors"),
    )

    op.create_table(
        "doctor_working_hours",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("doctor_id", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_doctor_working_hours"),
        sa.ForeignKeyConstraint(
            ["doctor_id"], ["doctors.id"], ondelete="CASCADE", name="fk_doctor_working_hours_doctor_id_doctors"
        ),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_doctor_working_hours_weekday_range"),
        sa.CheckConstraint("end_time > start_time", name="ck_doctor_working_hours_end_after_start"),
        sa.UniqueConstraint("doctor_id", "weekday", "start_time", name="uq_working_hours_doctor_day_start"),
    )
    op.create_index("ix_doctor_working_hours_doctor_id", "doctor_working_hours", ["doctor_id"])

    op.create_table(
        "patients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_patients"),
        sa.UniqueConstraint("email", name="uq_patients_email"),
    )

    op.create_table(
        "appointments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("doctor_id", sa.Integer(), nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reschedule_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_appointments"),
        sa.ForeignKeyConstraint(
            ["doctor_id"], ["doctors.id"], ondelete="RESTRICT", name="fk_appointments_doctor_id_doctors"
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["patients.id"], ondelete="RESTRICT", name="fk_appointments_patient_id_patients"
        ),
        sa.CheckConstraint("end_at > start_at", name="ck_appointments_end_after_start"),
        sa.CheckConstraint("status IN ('booked', 'cancelled')", name="ck_appointments_status_valid"),
        sa.CheckConstraint(
            "status <> 'cancelled' OR (cancellation_reason IS NOT NULL AND cancelled_at IS NOT NULL)",
            name="ck_appointments_cancellation_has_reason",
        ),
    )
    op.create_index("ix_appointments_doctor_start", "appointments", ["doctor_id", "start_at"])
    op.create_index("ix_appointments_patient_start", "appointments", ["patient_id", "start_at"])

    # The core guarantee of the system: no two *booked* appointments may
    # overlap in time for the same doctor. Cancelled rows are ignored by the
    # WHERE clause, which is what makes a cancelled slot bookable again.
    # Enforced by PostgreSQL itself, so it holds under concurrent requests,
    # across multiple app instances, and even for writes that bypass the API.
    op.execute(
        """
        ALTER TABLE appointments
        ADD CONSTRAINT ex_appointments_doctor_no_overlap
        EXCLUDE USING gist (doctor_id WITH =, tstzrange(start_at, end_at, '[)') WITH &&)
        WHERE (status = 'booked')
        """
    )
    # Same rule per patient: one person cannot be in two consultations at once.
    op.execute(
        """
        ALTER TABLE appointments
        ADD CONSTRAINT ex_appointments_patient_no_overlap
        EXCLUDE USING gist (patient_id WITH =, tstzrange(start_at, end_at, '[)') WITH &&)
        WHERE (status = 'booked')
        """
    )

    op.create_table(
        "appointment_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("appointment_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("previous_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("new_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_appointment_events"),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
            ondelete="CASCADE",
            name="fk_appointment_events_appointment_id_appointments",
        ),
        sa.CheckConstraint(
            "event_type IN ('booked', 'rescheduled', 'cancelled')", name="ck_appointment_events_event_type_valid"
        ),
    )
    op.create_index("ix_appointment_events_appointment_id", "appointment_events", ["appointment_id"])


def downgrade() -> None:
    op.drop_table("appointment_events")
    op.drop_table("appointments")
    op.drop_table("patients")
    op.drop_table("doctor_working_hours")
    op.drop_table("doctors")
    # btree_gist is left installed: other objects in the database may use it.
