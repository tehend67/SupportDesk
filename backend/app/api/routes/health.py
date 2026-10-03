from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ... import __version__
from ...ai.llm import get_llm_client
from ...config import settings
from ...db import get_session
from ...schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(session: AsyncSession = Depends(get_session)) -> HealthResponse:
    database = "up"
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        database = "down"
    return HealthResponse(
        status="ok" if database == "up" else "degraded",
        version=__version__,
        database=database,
        llm_mode=get_llm_client().mode,
        environment=settings.environment,
    )
