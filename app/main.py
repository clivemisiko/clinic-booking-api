from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.routes import appointments, doctors, health, patients
from app.core.clock import Clock, SystemClock
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.db.session import build_engine, build_sessionmaker

DESCRIPTION = """
Online appointment booking for a small clinic.

* Doctors work recurring weekly hours, split into **30-minute slots** (clinic timezone: Africa/Nairobi).
* A slot can be booked by exactly one patient; double booking is prevented by the database itself.
* Appointments can be cancelled (slot is released) or rescheduled (old slot released, new slot validated).

Every error uses the same envelope: `{"error": {"code": "...", "message": "...", "details": {...}}}`.
"""


def create_app(
    settings: Settings | None = None, *, engine: AsyncEngine | None = None, clock: Clock | None = None
) -> FastAPI:
    """Application factory. Tests pass their own engine and a frozen clock."""
    settings = settings or get_settings()
    engine = engine or build_engine(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await engine.dispose()

    app = FastAPI(title=settings.app_name, version="1.0.0", description=DESCRIPTION, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = build_sessionmaker(engine)
    app.state.clock = clock or SystemClock()

    register_exception_handlers(app)
    for module in (health, doctors, patients, appointments):
        app.include_router(module.router)

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/docs")

    return app


app = create_app()
