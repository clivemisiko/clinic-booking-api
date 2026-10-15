from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, PlainSerializer

from app.core.config import get_settings


def _to_clinic_time(value: datetime) -> str:
    """Render datetimes in the clinic's timezone with an explicit offset,
    e.g. 2026-10-20T09:00:00+03:00, so clients never have to guess."""
    return value.astimezone(get_settings().tz).isoformat()


ClinicDateTime = Annotated[datetime, PlainSerializer(_to_clinic_time, return_type=str, when_used="json")]


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


def error_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI documentation for the error envelope on the given status codes."""
    descriptions = {
        404: "Resource not found",
        409: "Conflicts with the current state (e.g. slot already taken)",
        422: "Validation error or business-rule violation",
    }
    return {code: {"model": ErrorResponse, "description": descriptions.get(code, "Error")} for code in codes}
