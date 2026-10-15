from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables (or a local .env)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Clinic Booking API"
    environment: str = "development"
    # Set automatically by Render on every deploy; lets the CI pipeline confirm
    # that the commit it just merged is the one actually serving traffic.
    render_git_commit: str | None = None

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/clinic"
    db_pool_size: int = 5
    db_max_overflow: int = 5

    # Scheduling rules. Changing slot_minutes after go-live would misalign
    # existing appointments, so treat it as fixed per deployment.
    clinic_timezone: str = "Africa/Nairobi"
    slot_minutes: int = Field(default=30, gt=0, le=240)
    min_booking_lead_minutes: int = Field(default=60, ge=0)
    booking_horizon_days: int = Field(default=90, gt=0)

    @field_validator("database_url")
    @classmethod
    def _use_asyncpg_driver(cls, value: str) -> str:
        # Render / Heroku style URLs ("postgres://" or "postgresql://") need the
        # async driver spelled out for SQLAlchemy.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+asyncpg://" + value[len(prefix) :]
        return value

    @field_validator("clinic_timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)  # raises if unknown
        return value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.clinic_timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()
