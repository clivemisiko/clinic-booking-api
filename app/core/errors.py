"""Domain errors and their HTTP mapping.

Services raise these; they never build HTTP responses themselves. One handler
turns every error into the same JSON envelope so clients can branch on a
stable machine-readable ``code`` instead of parsing English messages:

    {"error": {"code": "SLOT_UNAVAILABLE", "message": "...", "details": {...}}}
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class DomainError(Exception):
    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "BAD_REQUEST"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


# --- 404 -------------------------------------------------------------------
class NotFoundError(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"


class DoctorNotFound(NotFoundError):
    code = "DOCTOR_NOT_FOUND"


class PatientNotFound(NotFoundError):
    code = "PATIENT_NOT_FOUND"


class AppointmentNotFound(NotFoundError):
    code = "APPOINTMENT_NOT_FOUND"


# --- 409: request is valid but clashes with the current state ---------------
class ConflictError(DomainError):
    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"


class SlotUnavailable(ConflictError):
    code = "SLOT_UNAVAILABLE"


class PatientDoubleBooked(ConflictError):
    code = "PATIENT_HAS_CONFLICTING_APPOINTMENT"


class AppointmentAlreadyCancelled(ConflictError):
    code = "APPOINTMENT_ALREADY_CANCELLED"


class AppointmentCancelled(ConflictError):
    code = "APPOINTMENT_CANCELLED"


class EmailAlreadyRegistered(ConflictError):
    code = "EMAIL_ALREADY_REGISTERED"


# --- 422: request is well-formed but breaks a business rule ------------------
class BusinessRuleViolation(DomainError):
    status_code = 422  # Unprocessable Content
    code = "BUSINESS_RULE_VIOLATION"


class SlotInPast(BusinessRuleViolation):
    code = "SLOT_IN_PAST"


class BookingTooSoon(BusinessRuleViolation):
    code = "BOOKING_TOO_SOON"


class BookingTooFarAhead(BusinessRuleViolation):
    code = "BOOKING_TOO_FAR_AHEAD"


class OutsideWorkingHours(BusinessRuleViolation):
    code = "OUTSIDE_WORKING_HOURS"


class InvalidSlotBoundary(BusinessRuleViolation):
    code = "INVALID_SLOT_BOUNDARY"


class DoctorInactive(BusinessRuleViolation):
    code = "DOCTOR_INACTIVE"


class AppointmentAlreadyStarted(BusinessRuleViolation):
    code = "APPOINTMENT_ALREADY_STARTED"


class SameSlot(BusinessRuleViolation):
    code = "SAME_SLOT"


def _envelope(status_code: int, code: str, message: str, details: Any = None) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message}
    if details:
        body["details"] = details
    return JSONResponse(status_code=status_code, content=jsonable_encoder({"error": body}))


_HTTP_CODES = {
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(_: Request, exc: DomainError) -> JSONResponse:
        return _envelope(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {
                "field": ".".join(str(p) for p in err.get("loc", ()) if p != "body"),
                "message": err.get("msg"),
                "type": err.get("type"),
            }
            for err in exc.errors()
        ]
        return _envelope(
            422,
            "VALIDATION_ERROR",
            "The request is invalid. See details for each field.",
            {"fields": fields},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        return _envelope(exc.status_code, code, str(exc.detail))
