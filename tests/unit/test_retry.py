"""The retry wrapper used by booking and rescheduling (no database needed)."""

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.services import appointments
from app.services.appointments import MAX_ATTEMPTS, _with_retry


class FakeDriverError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"sqlstate {sqlstate}")
        self.sqlstate = sqlstate


def db_error(sqlstate: str) -> DBAPIError:
    return DBAPIError("INSERT ...", {}, FakeDriverError(sqlstate))


class FakeSession:
    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(appointments.asyncio, "sleep", no_sleep)


def failing(times: int, error: Exception, result: str = "ok"):
    calls = {"n": 0}

    async def operation() -> str:
        calls["n"] += 1
        if calls["n"] <= times:
            raise error
        return result

    return operation, calls


@pytest.mark.parametrize("sqlstate", ["40P01", "40001"])
async def test_deadlock_and_serialization_failures_are_retried(sqlstate):
    session = FakeSession()
    operation, calls = failing(2, db_error(sqlstate))

    assert await _with_retry(session, operation) == "ok"
    assert calls["n"] == 3
    assert session.rollbacks == 2


async def test_gives_up_after_max_attempts():
    session = FakeSession()
    operation, calls = failing(100, db_error("40P01"))

    with pytest.raises(DBAPIError):
        await _with_retry(session, operation)
    assert calls["n"] == MAX_ATTEMPTS


async def test_other_database_errors_are_not_retried():
    session = FakeSession()
    operation, calls = failing(1, db_error("42P01"))  # undefined_table

    with pytest.raises(DBAPIError):
        await _with_retry(session, operation)
    assert calls["n"] == 1


async def test_constraint_violations_are_not_retried():
    session = FakeSession()
    operation, calls = failing(1, IntegrityError("INSERT ...", {}, FakeDriverError("23P01")))

    with pytest.raises(IntegrityError):
        await _with_retry(session, operation)
    assert calls["n"] == 1


async def test_success_on_first_try_does_not_roll_back():
    session = FakeSession()
    operation, _ = failing(0, db_error("40P01"))

    assert await _with_retry(session, operation) == "ok"
    assert session.rollbacks == 0
