"""Seed the clinic's five doctors and their weekly hours.

Idempotent: does nothing if any doctor already exists, so it is safe to run on
every deploy. Run with:  python -m app.seed
"""

import asyncio
from datetime import time

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import build_engine, build_sessionmaker
from app.models import Doctor, WorkingHours

WEEKDAYS = range(0, 5)  # Monday-Friday
MORNING = (time(9, 0), time(12, 30))
AFTERNOON = (time(13, 30), time(17, 0))

DOCTORS: list[tuple[str, str, list[tuple[int, time, time]]]] = [
    # Full weekday schedule with a lunch break.
    ("Dr. Achieng Otieno", "General Practice", [(d, *w) for d in WEEKDAYS for w in (MORNING, AFTERNOON)]),
    ("Dr. Brian Kamau", "Paediatrics", [(d, *w) for d in WEEKDAYS for w in (MORNING, AFTERNOON)]),
    # Mornings only, plus Saturday morning.
    (
        "Dr. Fatuma Hassan",
        "Obstetrics & Gynaecology",
        [(d, *MORNING) for d in WEEKDAYS] + [(5, time(9, 0), time(13, 0))],
    ),
    # Afternoons and early evening.
    ("Dr. Samuel Mwangi", "Dermatology", [(d, time(12, 0), time(18, 0)) for d in WEEKDAYS]),
    # Part-time: Monday, Wednesday, Friday.
    ("Dr. Grace Njeri", "Cardiology", [(d, time(8, 0), time(14, 0)) for d in (0, 2, 4)]),
]


async def seed() -> int:
    settings = get_settings()
    engine = build_engine(settings)
    sessionmaker = build_sessionmaker(engine)
    try:
        async with sessionmaker() as session:
            if await session.scalar(select(func.count()).select_from(Doctor)):
                return 0
            for name, specialty, hours in DOCTORS:
                session.add(
                    Doctor(
                        full_name=name,
                        specialty=specialty,
                        working_hours=[WorkingHours(weekday=d, start_time=s, end_time=e) for d, s, e in hours],
                    )
                )
            await session.commit()
            return len(DOCTORS)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    created = asyncio.run(seed())
    print(f"Seeded {created} doctors" if created else "Doctors already present; nothing to seed")
