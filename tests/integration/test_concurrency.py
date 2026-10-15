"""Race conditions: the requirement "once booked, that slot must not be
available to others" has to hold when requests arrive at the same moment, not
only one after another.

Each request below runs on its own database connection (NullPool), so these
are genuinely concurrent transactions racing at PostgreSQL.
"""

import asyncio

from tests.integration.helpers import tue

CONTENDERS = 10


async def test_only_one_of_many_simultaneous_bookings_for_a_slot_wins(client, doctor, make_patient):
    patients = [await make_patient(i) for i in range(CONTENDERS)]

    responses = await asyncio.gather(
        *(
            client.post("/appointments", json={"doctor_id": doctor.id, "patient_id": p.id, "start_at": tue(9, 0)})
            for p in patients
        )
    )

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [201] + [409] * (CONTENDERS - 1), [r.text for r in responses]
    assert all(r.json()["error"]["code"] == "SLOT_UNAVAILABLE" for r in responses if r.status_code == 409)


async def test_simultaneous_reschedules_into_the_same_slot_only_one_wins(client, doctor, make_patient):
    starts = [tue(9, 0), tue(9, 30), tue(10, 0), tue(10, 30)]
    ids = []
    for index, start in enumerate(starts):
        patient = await make_patient(index)
        created = await client.post(
            "/appointments", json={"doctor_id": doctor.id, "patient_id": patient.id, "start_at": start}
        )
        ids.append(created.json()["id"])

    responses = await asyncio.gather(
        *(client.patch(f"/appointments/{i}/reschedule", json={"start_at": tue(14, 0)}) for i in ids)
    )

    assert sorted(r.status_code for r in responses) == [200, 409, 409, 409]


async def test_simultaneous_cancels_of_one_appointment_succeed_exactly_once(client, doctor, make_patient):
    patient = await make_patient(0)
    created = await client.post(
        "/appointments", json={"doctor_id": doctor.id, "patient_id": patient.id, "start_at": tue(9, 0)}
    )
    appointment_id = created.json()["id"]

    responses = await asyncio.gather(
        *(client.patch(f"/appointments/{appointment_id}/cancel", json={"reason": f"attempt {i}"}) for i in range(5))
    )

    assert sorted(r.status_code for r in responses) == [200, 409, 409, 409, 409]
