from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...constants import SenderRole
from ...db import get_session
from ...models import Workspace
from ...ratelimit import client_ip, limiter
from ...schemas import IngestRequest, IngestResponse, KnowledgeHit
from ...services.conversation import handle_incoming_message
from ..deps import resolve_channel_workspace

router = APIRouter()


@router.post("/message", response_model=IngestResponse)
async def ingest_message(
    payload: IngestRequest,
    request: Request,
    workspace: Workspace = Depends(resolve_channel_workspace),
    session: AsyncSession = Depends(get_session),
) -> IngestResponse:
    ok, remaining, retry_after = await limiter.check(
        f"ingest:{workspace.id}", client_ip(request), settings.ingest_rate_limit_per_minute
    )
    if not ok:
        retry = retry_after or 60
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Слишком много запросов. Повторите через {retry} сек.",
            headers={"Retry-After": str(retry)},
        )

    result = await handle_incoming_message(
        session,
        workspace,
        channel=payload.channel,
        content=payload.content,
        customer_ref=payload.customer_ref,
        customer_name=payload.customer_name,
        ticket_ref=payload.ticket_ref,
        subject=payload.subject,
        metadata=payload.metadata,
    )
    await session.commit()
    return IngestResponse(
        ticket_id=result.ticket.id,
        public_id=result.ticket.public_id,
        status=result.ticket.status,
        reply=result.reply.content,
        sender=SenderRole.ASSISTANT.value,
        escalated=result.escalated,
        ai_handled=result.ai_handled,
        sources=[KnowledgeHit.model_validate(hit) for hit in result.hits],
        message_id=result.reply.id,
        created_at=result.reply.created_at,
    )
