"""Unit tests for the pure slot engine (no database, no HTTP)."""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.domain.slots import (
    SlotCheck,
    WorkingWindow,
    check_slot,
    ensure_aware,
    generate_slots,
    local_day_bounds,
)

TZ = ZoneInfo("Africa/Nairobi")
DAY = date(2026, 10, 20)
MORNING = WorkingWindow(time(9, 0), time(12, 30))
AFTERNOON = WorkingWindow(time(13, 30), time(17, 0))


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 10, 20, hour, minute, second, tzinfo=TZ)


class TestGenerateSlots:
    def test_single_window_produces_back_to_back_30_minute_slots(self):
        slots = generate_slots(DAY, [WorkingWindow(time(9, 0), time(11, 0))], TZ, 30)
        assert [s.start for s in slots] == [at(9), at(9, 30), at(10), at(10, 30)]
        assert all((s.end - s.start).total_seconds() == 1800 for s in slots)

    def test_split_shift_skips_the_break(self):
        starts = [s.start for s in generate_slots(DAY, [MORNING, AFTERNOON], TZ, 30)]
        assert at(12) in starts
        assert at(12, 30) not in starts and at(13) not in starts
        assert starts[0] == at(9) and starts[-1] == at(16, 30)
        assert len(starts) == 14

    def test_partial_trailing_slot_is_not_offered(self):
        slots = generate_slots(DAY, [WorkingWindow(time(9, 0), time(10, 15))], TZ, 30)
        assert [s.start for s in slots] == [at(9), at(9, 30)]

    def test_windows_are_sorted_and_deduplicated(self):
        overlapping = [WorkingWindow(time(10, 0), time(11, 0)), WorkingWindow(time(9, 0), time(10, 30))]
        starts = [s.start for s in generate_slots(DAY, overlapping, TZ, 30)]
        assert starts == [at(9), at(9, 30), at(10), at(10, 30)]

    def test_no_windows_means_no_slots(self):
        assert generate_slots(DAY, [], TZ, 30) == []

    def test_slots_are_timezone_aware(self):
        slot = generate_slots(DAY, [MORNING], TZ, 30)[0]
        assert slot.start.utcoffset() is not None
        assert slot.start.astimezone(UTC).hour == 6  # 09:00 EAT == 06:00 UTC


class TestCheckSlot:
    @pytest.mark.parametrize("start", [at(9), at(12), at(13, 30), at(16, 30)])
    def test_valid_slots(self, start):
        assert check_slot(start, [MORNING, AFTERNOON], TZ, 30) is SlotCheck.OK

    @pytest.mark.parametrize("start", [at(8, 30), at(12, 30), at(13), at(17), at(20)])
    def test_outside_working_hours(self, start):
        assert check_slot(start, [MORNING, AFTERNOON], TZ, 30) is SlotCheck.OUTSIDE_WORKING_HOURS

    @pytest.mark.parametrize("start", [at(9, 15), at(10, 1), at(9, 0, 30)])
    def test_misaligned(self, start):
        assert check_slot(start, [MORNING, AFTERNOON], TZ, 30) is SlotCheck.MISALIGNED

    def test_slot_that_would_overrun_the_window_is_outside_hours(self):
        window = WorkingWindow(time(9, 0), time(10, 15))
        assert check_slot(at(10), [window], TZ, 30) is SlotCheck.OUTSIDE_WORKING_HOURS

    def test_no_windows_is_outside_hours(self):
        assert check_slot(at(10), [], TZ, 30) is SlotCheck.OUTSIDE_WORKING_HOURS

    def test_utc_input_is_compared_in_clinic_time(self):
        # 06:00 UTC is 09:00 in Nairobi -> first slot of the day.
        assert check_slot(datetime(2026, 10, 20, 6, 0, tzinfo=UTC), [MORNING], TZ, 30) is SlotCheck.OK


class TestHelpers:
    def test_naive_datetime_is_interpreted_as_clinic_time(self):
        assert ensure_aware(datetime(2026, 10, 20, 9, 0), TZ) == at(9)

    def test_aware_datetime_is_untouched(self):
        value = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)
        assert ensure_aware(value, TZ) is value

    def test_local_day_bounds(self):
        start, end = local_day_bounds(DAY, TZ)
        assert start == datetime(2026, 10, 20, 0, 0, tzinfo=TZ)
        assert (end - start).days == 1

    def test_invalid_window_rejected(self):
        with pytest.raises(ValueError):
            WorkingWindow(time(17, 0), time(9, 0))
