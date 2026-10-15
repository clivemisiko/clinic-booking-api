from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.common import ClinicDateTime


class PatientCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=120, examples=["Amina Wanjiku"])
    email: EmailStr = Field(examples=["amina@example.com"])
    phone: str | None = Field(default=None, max_length=32, examples=["+254712345678"])


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: str
    phone: str | None
    created_at: ClinicDateTime
