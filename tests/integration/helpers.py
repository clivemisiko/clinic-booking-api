from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Africa/Nairobi")


def local(year: int, month: int, day: int, hour: int, minute: int = 0) -> str:
    """ISO string for a Nairobi wall-clock time, as a client would send it."""
    return datetime(year, month, day, hour, minute, tzinfo=TZ).isoformat()


# Tuesday 20 Oct 2026, the day after the frozen "now" (Mon 19 Oct, 08:00 EAT).
def tue(hour: int, minute: int = 0) -> str:
    return local(2026, 10, 20, hour, minute)


def assert_error(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["error"]["code"] == code, body
    assert body["error"]["message"]
    return body["error"]
