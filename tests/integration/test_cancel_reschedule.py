"""PATCH /appointments/{id}/cancel and /reschedule"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models import AppointmentEvent
from tests.integration.helpers import assert_error, local, tue


@pytest.fixture
async def appointment(client, doctor, patient) -> dict:
    response = await client.post(
        "/appointments", json={"doctor_id": doctor.id, "patient_id": patient.id, "start_at": tue(9, 0)}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def cancel(client, appointment_id, reason="Patient is travelling"):
    return await client.patch(f"/appointments/{appointment_id}/cancel", json={"reason": reason})


async def reschedule(client, appointment_id, start_at):
    return await client.patch(f"/appointments/{appointment_id}/reschedule", json={"start_at": start_at})


# --- cancel -------------------------------------------------------------------


async def test_cancel_sets_status_reason_and_timestamp(client, appointment):
    response = await cancel(client, appointment["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["cancellation_reason"] == "Patient is travelling"
    assert body["cancelled_at"] == "2026-10-19T08:00:00+03:00"


async def test_cancelled_slot_can_be_booked_by_someone_else(client, doctor, other_patient, appointment):
    await cancel(client, appointment["id"])
    response = await client.post(
        "/appointments", json={"doctor_id": doctor.id, "patient_id": other_patient.id, "start_at": tue(9, 0)}
    )
    assert response.status_code == 201, response.text


async def test_cancel_twice_returns_409(client, appointment):
    await cancel(client, appointment["id"])
    error = assert_error(await cancel(client, appointment["id"]), 409, "APPOINTMENT_ALREADY_CANCELLED")
    assert error["details"]["cancelled_at"] == "2026-10-19T08:00:00+03:00"


@pytest.mark.parametrize("payload", [{}, {"reason": ""}, {"reason": "   "}, {"reason": "x" * 501}])
async def test_cancel_requires_a_meaningful_reason(client, appointment, payload):
    response = await client.patch(f"/appointments/{appointment['id']}/cancel", json=payload)
    assert_error(response, 422, "VALIDATION_ERROR")


async def test_cancel_unknown_appointment_returns_404(client):
    assert_error(await cancel(client, "00000000-0000-0000-0000-000000000000"), 404, "APPOINTMENT_NOT_FOUND")


async def test_cancel_with_malformed_id_returns_422(client):
    assert_error(await cancel(client, "not-a-uuid"), 422, "VALIDATION_ERROR")


async def test_cannot_cancel_an_appointment_that_already_started(client, clock, appointment):
    clock.instant = clock.instant + timedelta(days=1, hours=1, minutes=5)  # Tue 09:05 EAT
    assert_error(await cancel(client, appointment["id"]), 422, "APPOINTMENT_ALREADY_STARTED")


# --- reschedule ---------------------------------------------------------------


async def test_reschedule_moves_the_appointment_and_frees_the_old_slot(client, doctor, other_patient, appointment):
    response = await reschedule(client, appointment["id"], tue(14, 0))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == appointment["id"]
    assert body["start_at"] == "2026-10-20T14:00:00+03:00"
    assert body["end_at"] == "2026-10-20T14:30:00+03:00"
    assert body["reschedule_count"] == 1

    availability = await client.get(f"/doctors/{doctor.id}/availability", params={"date": "2026-10-20"})
    times = [s["start_at"][11:16] for s in availability.json()["slots"]]
    assert "09:00" in times and "14:00" not in times

    rebook = await client.post(
        "/appointments", json={"doctor_id": doctor.id, "patient_id": other_patient.id, "start_at": tue(9, 0)}
    )
    assert rebook.status_code == 201, rebook.text


async def test_reschedule_to_a_taken_slot_returns_409_and_changes_nothing(client, doctor, other_patient, appointment):
    await client.post(
        "/appointments", json={"doctor_id": doctor.id, "patient_id": other_patient.id, "start_at": tue(10, 0)}
    )

    assert_error(await reschedule(client, appointment["id"], tue(10, 0)), 409, "SLOT_UNAVAILABLE")

    unchanged = (await client.get(f"/appointments/{appointment['id']}")).json()
    assert unchanged["start_at"] == appointment["start_at"]
    assert unchanged["reschedule_count"] == 0


async def test_reschedule_cancelled_appointment_returns_409(client, appointment):
    await cancel(client, appointment["id"])
    assert_error(await reschedule(client, appointment["id"], tue(14, 0)), 409, "APPOINTMENT_CANCELLED")


@pytest.mark.parametrize(
    ("start_at", "code"),
    [
        (local(2026, 10, 16, 9, 0), "SLOT_IN_PAST"),
        (local(2026, 10, 19, 8, 30), "BOOKING_TOO_SOON"),
        (tue(12, 30), "OUTSIDE_WORKING_HOURS"),
        (tue(9, 45), "INVALID_SLOT_BOUNDARY"),
        (local(2027, 1, 18, 9, 0), "BOOKING_TOO_FAR_AHEAD"),  # Mon, first day past the 90-day cutoff
    ],
)
async def test_reschedule_validates_new_slot_like_a_fresh_booking(client, appointment, start_at, code):
    assert_error(await reschedule(client, appointment["id"], start_at), 422, code)


async def test_reschedule_to_the_same_slot_is_rejected(client, appointment):
    assert_error(await reschedule(client, appointment["id"], tue(9, 0)), 422, "SAME_SLOT")


async def test_reschedule_unknown_appointment_returns_404(client):
    assert_error(
        await reschedule(client, "00000000-0000-0000-0000-000000000000", tue(14, 0)), 404, "APPOINTMENT_NOT_FOUND"
    )


async def test_reschedule_into_a_slot_overlapping_own_other_booking_is_rejected(client, doctor, patient, appointment):
    second = await client.post(
        "/appointments", json={"doctor_id": doctor.id, "patient_id": patient.id, "start_at": tue(11, 0)}
    )
    assert second.status_code == 201
    # Moving the 09:00 appointment onto 11:00 clashes with the patient's own booking
    # (and the doctor's), so it must be refused.
    response = await reschedule(client, appointment["id"], tue(11, 0))
    assert response.status_code == 409


async def test_history_is_kept_in_the_audit_trail(client, appointment, session):
    await reschedule(client, appointment["id"], tue(14, 0))
    await cancel(client, appointment["id"], reason="Changed my mind")

    events = (await session.scalars(select(AppointmentEvent).order_by(AppointmentEvent.id))).all()
    assert [e.event_type for e in events] == ["booked", "rescheduled", "cancelled"]
    rescheduled = events[1]
    assert rescheduled.previous_start_at.isoformat().startswith("2026-10-20T06:00")  # 09:00 EAT in UTC
    assert rescheduled.new_start_at.isoformat().startswith("2026-10-20T11:00")  # 14:00 EAT in UTC
    assert events[2].note == "Changed my mind"
