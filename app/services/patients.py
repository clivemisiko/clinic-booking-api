from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import EmailAlreadyRegistered, PatientNotFound
from app.models import Patient
from app.schemas.patient import PatientCreate


async def get_patient(session: AsyncSession, patient_id: int) -> Patient:
    patient = await session.scalar(select(Patient).where(Patient.id == patient_id))
    if patient is None:
        raise PatientNotFound(f"Patient {patient_id} does not exist", {"patient_id": patient_id})
    return patient


async def register(session: AsyncSession, data: PatientCreate) -> Patient:
    email = str(data.email).strip().lower()
    patient = Patient(full_name=data.full_name.strip(), email=email, phone=data.phone)
    session.add(patient)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise EmailAlreadyRegistered("A patient with this email is already registered", {"email": email}) from exc
    await session.refresh(patient)
    return patient
