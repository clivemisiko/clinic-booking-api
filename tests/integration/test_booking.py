"""POST /appointments"""

from datetime import time, timedelta

from sqlalchemy import select

from app.models import AppointmentEvent, Doctor, WorkingHours
from tests.integration.helpers import assert_error, local, tue


async def book(client, doctor_id, patient_id, start_at, **extra):
    return await client.post(
        "/appointments", json={"doctor_id": doctor_id, "patient_id": patient_id, "start_at": start_at, **extra}
    )


async def test_book_valid_slot(client, doctor, patient):
    response = await book(client, doctor.id, patient.id, tue(9, 0), reason="Check-up")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "booked"
    assert body["start_at"] == "2026-10-20T09:00:00+03:00"
    assert body["end_at"] == "2026-10-20T09:30:00+03:00"
    assert body["doctor"]["id"] == doctor.id
    assert body["patient"]["id"] == patient.id
    assert body["reason"] == "Check-up"
    assert body["reschedule_count"] == 0


async def test_utc_and_naive_inputs_are_normalised_to_clinic_time(client, doctor, patient, other_patient):
    utc = await book(client, doctor.id, patient.id, "2026-10-20T07:00:00Z")  # 10:00 EAT
    naive = await book(client, doctor.id, other_patient.id, "2026-10-20T11:00:00")  # read as EAT

    assert utc.status_code == 201 and utc.json()["start_at"] == "2026-10-20T10:00:00+03:00"
    assert naive.status_code == 201 and naive.json()["start_at"] == "2026-10-20T11:00:00+03:00"


async def test_slot_already_taken_returns_409(client, doctor, patient, other_patient):
    assert (await book(client, doctor.id, patient.id, tue(9, 0))).status_code == 201

    response = await book(client, doctor.id, other_patient.id, tue(9, 0))

    assert_error(response, 409, "SLOT_UNAVAILABLE")


async def test_same_instant_expressed_in_another_timezone_is_still_taken(client, doctor, patient, other_patient):
    await book(client, doctor.id, patient.id, tue(9, 0))
    response = await book(client, doctor.id, other_patient.id, "2026-10-20T06:00:00Z")
    assert_error(response, 409, "SLOT_UNAVAILABLE")


async def test_patient_cannot_be_in_two_places_at_once(client, doctor, patient, session):
    dermatologist = Doctor(
        full_name="Dr. Other",
        specialty="Dermatology",
        working_hours=[WorkingHours(weekday=1, start_time=time(9, 0), end_time=time(12, 0))],
    )
    session.add(dermatologist)
    await session.commit()

    assert (await book(client, doctor.id, patient.id, tue(9, 0))).status_code == 201
    response = await book(client, dermatologist.id, patient.id, tue(9, 0))

    assert_error(response, 409, "PATIENT_HAS_CONFLICTING_APPOINTMENT")


async def test_adjacent_slots_do_not_conflict(client, doctor, patient, other_patient):
    assert (await book(client, doctor.id, patient.id, tue(9, 0))).status_code == 201
    assert (await book(client, doctor.id, other_patient.id, tue(9, 30))).status_code == 201


async def test_past_slot_rejected(client, doctor, patient):
    # Last Friday.
    error = assert_error(await book(client, doctor.id, patient.id, local(2026, 10, 16, 9, 0)), 422, "SLOT_IN_PAST")
    assert error["details"]["doctor_id"] == doctor.id


async def test_booking_exactly_one_hour_ahead_is_allowed(client, clock, doctor, patient):
    clock.instant = clock.instant + timedelta(minutes=60)  # Mon 09:00 EAT
    assert (await book(client, doctor.id, patient.id, local(2026, 10, 19, 10, 0))).status_code == 201


async def test_booking_within_one_hour_rejected(client, clock, doctor, patient):
    clock.instant = clock.instant + timedelta(minutes=45)  # Mon 08:45 EAT; 09:00 is 15 minutes away
    error = assert_error(await book(client, doctor.id, patient.id, local(2026, 10, 19, 9, 0)), 422, "BOOKING_TOO_SOON")
    assert error["details"]["earliest_allowed"] == "2026-10-19T09:45:00+03:00"


async def test_before_opening_rejected(client, doctor, patient):
    error = assert_error(await book(client, doctor.id, patient.id, tue(8, 0)), 422, "OUTSIDE_WORKING_HOURS")
    assert error["details"]["working_hours"] == ["09:00-12:30", "13:30-17:00"]


async def test_lunch_break_rejected(client, doctor, patient):
    assert_error(await book(client, doctor.id, patient.id, tue(12, 30)), 422, "OUTSIDE_WORKING_HOURS")


async def test_after_closing_rejected(client, doctor, patient):
    assert_error(await book(client, doctor.id, patient.id, tue(17, 0)), 422, "OUTSIDE_WORKING_HOURS")


async def test_day_off_rejected_with_helpful_message(client, doctor, patient):
    error = assert_error(
        await book(client, doctor.id, patient.id, local(2026, 10, 25, 10, 0)), 422, "OUTSIDE_WORKING_HOURS"
    )
    assert "Sunday" in error["message"]


async def test_misaligned_start_rejected(client, doctor, patient):
    assert_error(await book(client, doctor.id, patient.id, tue(9, 15)), 422, "INVALID_SLOT_BOUNDARY")


async def test_beyond_booking_horizon_rejected(client, doctor, patient):
    assert_error(await book(client, doctor.id, patient.id, local(2027, 3, 2, 9, 0)), 422, "BOOKING_TOO_FAR_AHEAD")


async def test_unknown_doctor_returns_404(client, patient):
    assert_error(await book(client, 999, patient.id, tue(9, 0)), 404, "DOCTOR_NOT_FOUND")


async def test_unknown_patient_returns_404(client, doctor):
    assert_error(await book(client, doctor.id, 999, tue(9, 0)), 404, "PATIENT_NOT_FOUND")


async def test_inactive_doctor_rejected(client, doctor, patient, session):
    doctor.is_active = False
    await session.commit()
    assert_error(await book(client, doctor.id, patient.id, tue(9, 0)), 422, "DOCTOR_INACTIVE")


async def test_malformed_body_returns_field_level_errors(client):
    response = await client.post("/appointments", json={"doctor_id": "abc", "start_at": "not-a-date"})

    error = assert_error(response, 422, "VALIDATION_ERROR")
    fields = {f["field"] for f in error["details"]["fields"]}
    assert {"doctor_id", "patient_id", "start_at"} <= fields


async def test_booking_is_recorded_in_audit_trail(client, doctor, patient, session):
    body = (await book(client, doctor.id, patient.id, tue(9, 0))).json()
    events = (await session.scalars(select(AppointmentEvent))).all()
    assert [(str(e.appointment_id), e.event_type) for e in events] == [(body["id"], "booked")]


async def test_get_appointment(client, doctor, patient):
    created = (await book(client, doctor.id, patient.id, tue(9, 0))).json()
    response = await client.get(f"/appointments/{created['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


async def test_get_unknown_appointment_returns_404(client):
    assert_error(await client.get("/appointments/00000000-0000-0000-0000-000000000000"), 404, "APPOINTMENT_NOT_FOUND")
