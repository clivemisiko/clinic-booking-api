"""Test fixtures.

Integration tests run against a real PostgreSQL database (not SQLite) because
the double-booking guarantee lives in a PostgreSQL exclusion constraint; a
fake database would not test the thing that matters most.

Set TEST_DATABASE_URL to point at a disposable database. Its schema is built
from the real Alembic migrations, so the migrations are tested too.
"""

import os
from datetime import UTC, datetime, time
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.clock import FrozenClock
from app.core.config import Settings
from app.main import create_app
from app.models import Doctor, Patient, WorkingHours

ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/clinic_test"
)

# "Now" for every test: Monday 19 Oct 2026, 08:00 in Nairobi (05:00 UTC).
NOW = datetime(2026, 10, 19, 5, 0, tzinfo=UTC)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(database_url=TEST_DATABASE_URL, environment="test")


@pytest.fixture(scope="session", autouse=True)
def _migrated_database(settings: Settings):
    if not os.environ.get("TEST_DATABASE_URL") and os.environ.get("CI"):
        pytest.exit("TEST_DATABASE_URL must be set in CI", returncode=2)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["database_url"] = settings.database_url
    cfg.attributes["configure_logger"] = False
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture
async def engine(settings: Settings):
    # NullPool: every session gets its own real connection, so concurrent
    # requests in a test really are concurrent at the database.
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE appointment_events, appointments, patients, doctor_working_hours, doctors "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield engine
    await engine.dispose()


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(NOW)


@pytest.fixture
async def client(settings: Settings, engine, clock: FrozenClock):
    app = create_app(settings, engine=engine, clock=clock)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
async def session(engine):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
async def doctor(session: AsyncSession) -> Doctor:
    """Works Mon-Fri 09:00-12:30 and 13:30-17:00 (lunch break in between)."""
    doc = Doctor(
        full_name="Dr. Test Otieno",
        specialty="General Practice",
        working_hours=[
            WorkingHours(weekday=d, start_time=s, end_time=e)
            for d in range(5)
            for s, e in ((time(9, 0), time(12, 30)), (time(13, 30), time(17, 0)))
        ],
    )
    session.add(doc)
    await session.commit()
    return doc


async def _make_patient(session: AsyncSession, name: str, email: str) -> Patient:
    patient = Patient(full_name=name, email=email)
    session.add(patient)
    await session.commit()
    return patient


@pytest.fixture
async def patient(session: AsyncSession) -> Patient:
    return await _make_patient(session, "Amina Wanjiku", "amina@example.com")


@pytest.fixture
async def other_patient(session: AsyncSession) -> Patient:
    return await _make_patient(session, "Peter Odhiambo", "peter@example.com")


@pytest.fixture
def make_patient(session: AsyncSession):
    async def factory(index: int) -> Patient:
        return await _make_patient(session, f"Patient {index}", f"patient{index}@example.com")

    return factory
