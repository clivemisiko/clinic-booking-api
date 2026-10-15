from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import SessionDep, SettingsDep

router = APIRouter(tags=["Health"])


@router.get("/health", summary="Liveness and database connectivity")
async def health(session: SessionDep, settings: SettingsDep):
    try:
        await session.execute(text("SELECT 1"))
    except Exception:  # any DB failure means "unhealthy"
        return JSONResponse(status_code=503, content={"status": "unhealthy", "database": "unreachable"})
    return {"status": "ok", "database": "ok", "version": settings.render_git_commit or "local"}
