"""Patients and GET /patients/{id}/appointments (bonus)."""

from datetime import timedelta

from tests.integration.helpers import assert_error, local, tue


async def test_register_patient(client):
    response = await client.post(
        "/patients", json={"full_name": "Amina Wanjiku", "email": "Amina@Example.com", "phone": "+254712345678"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == "amina@example.com"
    assert (await client.get(f"/patients/{body['id']}")).json()["full_name"] == "Amina Wanjiku"


async def test_duplicate_email_returns_409(client):
    payload = {"full_name": "Amina Wanjiku", "email": "amina@example.com"}
    await client.post("/patients", json=payload)
    assert_error(
        await client.post("/patients", json={**payload, "email": "AMINA@example.com"}), 409, "EMAIL_ALREADY_REGISTERED"
    )


async def test_invalid_email_returns_422(client):
    assert_error(await client.post("/patients", json={"full_name": "Amina", "email": "nope"}), 422, "VALIDATION_ERROR")


async def test_unknown_patient_returns_404(client):
    assert_error(await client.get("/patients/999"), 404, "PATIENT_NOT_FOUND")
    assert_error(await client.get("/patients/999/appointments"), 404, "PATIENT_NOT_FOUND")


async def test_upcoming_appointments_sorted_and_exclude_cancelled_and_past(client, clock, doctor, patient):
    async def book(start_at):
        response = await client.post(
            "/appointments", json={"doctor_id": doctor.id, "patient_id": patient.id, "start_at": start_at}
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    # Booked out of order on purpose.
    thursday = await book(local(2026, 10, 22, 10, 0))
    monday_morning = await book(local(2026, 10, 19, 9, 30))
    tuesday = await book(tue(15, 0))
    cancelled = await book(tue(9, 0))
    await client.patch(f"/appointments/{cancelled}/cancel", json={"reason": "No longer needed"})

    ids = [a["id"] for a in (await client.get(f"/patients/{patient.id}/appointments")).json()]
    assert ids == [monday_morning, tuesday, thursday]

    # Once Monday's appointment is in the past it drops off the list.
    clock.instant = clock.instant + timedelta(hours=3)  # Mon 11:00 EAT
    ids = [a["id"] for a in (await client.get(f"/patients/{patient.id}/appointments")).json()]
    assert ids == [tuesday, thursday]


async def test_patient_with_no_appointments_gets_empty_list(client, patient):
    response = await client.get(f"/patients/{patient.id}/appointments")
    assert response.status_code == 200
    assert response.json() == []


async def test_health(client):
    assert (await client.get("/health")).json() == {"status": "ok", "database": "ok", "version": "local"}


async def test_unknown_route_uses_error_envelope(client):
    assert_error(await client.get("/nope"), 404, "NOT_FOUND")
