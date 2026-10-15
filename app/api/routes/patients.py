from fastapi import APIRouter, status

from app.api.deps import NowDep, SessionDep
from app.schemas.appointment import AppointmentOut
from app.schemas.common import error_responses
from app.schemas.patient import PatientCreate, PatientOut
from app.services import appointments as appointment_service
from app.services import patients as patient_service

router = APIRouter(prefix="/patients", tags=["Patients"])


@router.post(
    "",
    response_model=PatientOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a patient",
    responses=error_responses(409, 422),
)
async def register_patient(body: PatientCreate, session: SessionDep):
    return await patient_service.register(session, body)


@router.get("/{patient_id}", response_model=PatientOut, summary="Get a patient", responses=error_responses(404))
async def read_patient(patient_id: int, session: SessionDep):
    return await patient_service.get_patient(session, patient_id)


@router.get(
    "/{patient_id}/appointments",
    response_model=list[AppointmentOut],
    summary="Upcoming appointments, soonest first",
    responses=error_responses(404),
)
async def upcoming_appointments(patient_id: int, session: SessionDep, now: NowDep):
    return await appointment_service.upcoming_for_patient(session, patient_id, now=now)
