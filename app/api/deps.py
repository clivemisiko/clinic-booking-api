from datetime import datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import get_session


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_now(request: Request) -> datetime:
    return request.app.state.clock.now()


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
NowDep = Annotated[datetime, Depends(get_now)]
