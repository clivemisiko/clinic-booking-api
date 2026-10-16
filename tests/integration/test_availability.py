"""GET /doctors/{id}/availability"""

from datetime import time, timedelta

from app.models import Doctor, WorkingHours
from tests.integration.helpers import assert_error, tue


async def starts(client, doctor_id, day="2026-10-20"):
    response = await client.get(f"/doctors/{doctor_id}/availability", params={"date": day})
    assert response.status_code == 200, response.text
    return [slot["start_at"][11:16] for slot in response.json()["slots"]]


async def test_full_day_lists_every_slot_and_skips_lunch(client, doctor):
    response = await client.get(f"/doctors/{doctor.id}/availability", params={"date": "2026-10-20"})

    body = response.json()
    assert body["doctor_id"] == doctor.id
    assert body["timezone"] == "Africa/Nairobi"
    assert body["slot_minutes"] == 30
    assert body["slots"][0] == {"start_at": "2026-10-20T09:00:00+03:00", "end_at": "2026-10-20T09:30:00+03:00"}
    times = [s["start_at"][11:16] for s in body["slots"]]
    assert len(times) == 14
    assert "12:00" in times and "12:30" not in times and "13:00" not in times and "13:30" in times
    assert times[-1] == "16:30"


async def test_booked_slot_disappears_and_reappears_after_cancel(client, doctor, patient):
    booked = await client.post(
        "/appointments", json={"doctor_id": doctor.id, "patient_id": patient.id, "start_at": tue(10, 0)}
    )
    assert "10:00" not in await starts(client, doctor.id)

    await client.patch(f"/appointments/{booked.json()['id']}/cancel", json={"reason": "Feeling better"})
    assert "10:00" in await starts(client, doctor.id)


async def test_other_doctors_bookings_do_not_affect_availability(client, doctor, patient, session):
    other = Doctor(
        full_name="Dr. Other",
        specialty="Paediatrics",
        working_hours=[WorkingHours(weekday=1, start_time=time(9, 0), end_time=time(12, 0))],
    )
    session.add(other)
    await session.commit()
    await client.post("/appointments", json={"doctor_id": other.id, "patient_id": patient.id, "start_at": tue(9, 0)})

    assert "09:00" in await starts(client, doctor.id)


async def test_today_hides_past_slots_and_slots_inside_the_lead_time(client, clock, doctor):
    clock.instant = clock.instant + timedelta(hours=2, minutes=10)  # Mon 10:10 EAT
    times = await starts(client, doctor.id, "2026-10-19")
    # 11:10 is the earliest bookable instant, so 11:30 is the first slot offered.
    assert times[0] == "11:30"


async def test_past_date_has_no_slots(client, doctor):
    assert await starts(client, doctor.id, "2026-10-16") == []


async def test_day_off_has_no_slots(client, doctor):
    assert await starts(client, doctor.id, "2026-10-24") == []  # Saturday


async def test_horizon_boundary_for_availability(client, clock, doctor):
    clock.instant = clock.instant + timedelta(days=1)  # Tue 20 Oct: cutoff is Mon 18 Jan 2027
    assert len(await starts(client, doctor.id, "2027-01-18")) == 14  # last bookable day: full list
    assert await starts(client, doctor.id, "2027-01-19") == []  # first day past the horizon


async def test_unknown_doctor_returns_404(client):
    assert_error(await client.get("/doctors/999/availability", params={"date": "2026-10-20"}), 404, "DOCTOR_NOT_FOUND")


async def test_missing_or_invalid_date_returns_422(client, doctor):
    assert_error(await client.get(f"/doctors/{doctor.id}/availability"), 422, "VALIDATION_ERROR")
    assert_error(
        await client.get(f"/doctors/{doctor.id}/availability", params={"date": "20-10-2026"}), 422, "VALIDATION_ERROR"
    )


async def test_every_listed_slot_is_bookable(client, doctor, make_patient):
    """Availability and booking share the same rules, so each listed slot must book."""
    response = await client.get(f"/doctors/{doctor.id}/availability", params={"date": "2026-10-20"})
    for index, slot in enumerate(response.json()["slots"]):
        patient = await make_patient(index)
        booked = await client.post(
            "/appointments", json={"doctor_id": doctor.id, "patient_id": patient.id, "start_at": slot["start_at"]}
        )
        assert booked.status_code == 201, booked.text
    assert await starts(client, doctor.id) == []


async def test_list_doctors(client, doctor):
    response = await client.get("/doctors")
    assert response.status_code == 200
    [listed] = response.json()
    assert listed["full_name"] == doctor.full_name
    assert len(listed["working_hours"]) == 10
