"""Injectable clock.

Booking rules depend on "now" (no past bookings, 1-hour lead time, upcoming
appointments). Reading the system clock directly would make those rules
untestable, so the current time is a dependency that tests replace with a
frozen value.
"""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("FrozenClock needs a timezone-aware datetime")
        self.instant = instant

    def now(self) -> datetime:
        return self.instant
