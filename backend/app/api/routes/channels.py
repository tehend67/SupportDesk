from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...schemas import TelegramHeartbeat, TelegramStatusOut
from ...services import workspaces as workspace_service
from ..deps import require_worker_key

router = APIRouter()


@router.get("/telegram/assignments")
async def telegram_assignments(
    _: str = Depends(require_worker_key),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    return list(await workspace_service.telegram_assignments(session))


@router.post("/telegram/heartbeat", response_model=TelegramStatusOut)
async def telegram_heartbeat(
    payload: TelegramHeartbeat,
    _: str = Depends(require_worker_key),
    session: AsyncSession = Depends(get_session),
) -> TelegramStatusOut:
    """Воркер сообщает, что бот поднялся (или упал) — комната сразу это видит."""
    workspace = await workspace_service.report_bot_heartbeat(
        session, payload.workspace_id, payload.ok, payload.error
    )
    if workspace is None:
        raise HTTPException(status_code=404, detail="Комната не найдена")
    await session.commit()
    return workspace_service.telegram_status_payload(workspace)
