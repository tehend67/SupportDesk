from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...models import Workspace
from ...schemas import AnalyticsOverview, TimeseriesPoint
from ...services import analytics as analytics_service
from ..deps import get_workspace_context

router = APIRouter()


@router.get("/overview", response_model=AnalyticsOverview)
async def overview(
    context: tuple[Workspace, str] = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> AnalyticsOverview:
    workspace, _ = context
    return AnalyticsOverview.model_validate(
        await analytics_service.build_overview(session, workspace.id)
    )


@router.get("/timeseries", response_model=list[TimeseriesPoint])
async def timeseries(
    days: int = Query(default=14, ge=1, le=90),
    context: tuple[Workspace, str] = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> list[TimeseriesPoint]:
    workspace, _ = context
    points = await analytics_service.build_timeseries(session, workspace.id, days=days)
    return [TimeseriesPoint.model_validate(point) for point in points]
