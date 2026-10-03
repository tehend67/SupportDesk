import logging
from typing import Optional

from fastapi import Request

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...constants import Channel, EventType, Priority, SenderRole, TicketStatus
from ...db import get_session
from ...models import Ticket, Workspace
from ...schemas import (
    MessageCreate,
    MessageOut,
    TicketCreate,
    TicketDetail,
    TicketOut,
    TicketPage,
    TicketUpdate,
)
from ... import audit
from ...services import realtime
from ...ratelimit import client_ip
from ...services import tickets as ticket_service
from ...services.knowledge import learn_from_operator_reply
from ...services.tickets import log_event
from ..deps import get_workspace_context, require_role

router = APIRouter()

logger = logging.getLogger("helpdesk.tickets")


async def deliver_to_telegram(
    workspace_id: int, customer_ref: str, text: str, ticket_ref: str = ""
) -> bool:
    """Отправляет ответ оператора клиенту в тот канал, откуда он писал."""
    return await realtime.deliver_operator_reply(workspace_id, customer_ref, text, ticket_ref)


def _last_customer_question(ticket: Ticket) -> str:
    """Последний вопрос клиента перед ответом оператора — будущий ключ обучения."""
    for message in reversed(ticket.messages):
        if message.sender == SenderRole.CUSTOMER.value:
            return message.content.strip()
    return ticket.subject.strip()


async def _load_detail(session: AsyncSession, workspace_id: int, ticket_id: int) -> Ticket:
    stmt = (
        select(Ticket)
        .options(selectinload(Ticket.messages), selectinload(Ticket.events))
        .where(Ticket.id == ticket_id, Ticket.workspace_id == workspace_id)
    )
    ticket = (await session.execute(stmt)).scalar_one_or_none()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Обращение не найдено")
    return ticket


@router.get("", response_model=TicketPage)
async def list_tickets(
    status_filter: Optional[TicketStatus] = Query(default=None, alias="status"),
    channel: Optional[Channel] = None,
    priority: Optional[Priority] = None,
    search: Optional[str] = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    context: tuple[Workspace, str] = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> TicketPage:
    workspace, _ = context
    total, items = await ticket_service.list_tickets(
        session,
        workspace.id,
        status=status_filter.value if status_filter else None,
        channel=channel.value if channel else None,
        priority=priority.value if priority else None,
        search=search,
        limit=limit,
        offset=offset,
    )
    return TicketPage(
        total=total,
        limit=limit,
        offset=offset,
        items=[TicketOut.model_validate(item) for item in items],
    )


@router.post("", response_model=TicketDetail, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    payload: TicketCreate,
    context: tuple[Workspace, str] = Depends(require_role("agent")),
    session: AsyncSession = Depends(get_session),
) -> Ticket:
    workspace, role = context
    ticket = await ticket_service.create_ticket(
        session,
        workspace.id,
        subject=payload.subject,
        channel=payload.channel,
        priority=payload.priority,
        customer_name=payload.customer_name,
        customer_ref=payload.customer_ref,
        assignee_id=payload.assignee_id,
    )
    if payload.message.strip():
        await ticket_service.add_message(
            session,
            ticket,
            SenderRole.CUSTOMER,
            payload.message.strip(),
            channel=payload.channel,
        )
    await ticket_service.log_event(session, ticket, "created_by", actor=role)
    await session.commit()
    return await _load_detail(session, workspace.id, ticket.id)


@router.get("/{ticket_id}", response_model=TicketDetail)
async def get_ticket(
    ticket_id: int,
    request: Request,
    context: tuple[Workspace, str] = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> TicketDetail:
    workspace, role = context
    ticket = await _load_detail(session, workspace.id, ticket_id)
    await audit.record(
        session,
        "ticket.view",
        actor=f"{role}:{context[0].slug}",
        workspace_id=workspace.id,
        ip=client_ip(request),
        meta={"ticket": ticket.public_id},
    )
    await session.commit()
    return TicketDetail.model_validate(ticket)
async def get_ticket(
    ticket_id: int,
    context: tuple[Workspace, str] = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> Ticket:
    workspace, _ = context
    return await _load_detail(session, workspace.id, ticket_id)


@router.patch("/{ticket_id}", response_model=TicketDetail)
async def update_ticket(
    ticket_id: int,
    payload: TicketUpdate,
    context: tuple[Workspace, str] = Depends(require_role("agent")),
    session: AsyncSession = Depends(get_session),
) -> Ticket:
    workspace, role = context
    ticket = await _load_detail(session, workspace.id, ticket_id)
    await ticket_service.update_ticket(
        session,
        ticket,
        status=payload.status.value if payload.status else None,
        priority=payload.priority.value if payload.priority else None,
        assignee_id=payload.assignee_id if "assignee_id" in payload.model_fields_set else None,
        subject=payload.subject,
        actor=role,
    )
    await session.commit()
    return await _load_detail(session, workspace.id, ticket_id)


@router.get("/{ticket_id}/messages", response_model=list[MessageOut])
async def list_messages(
    ticket_id: int,
    limit: int = Query(default=200, ge=1, le=500),
    context: tuple[Workspace, str] = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> list[MessageOut]:
    workspace, _ = context
    ticket = await _load_detail(session, workspace.id, ticket_id)
    return [MessageOut.model_validate(message) for message in ticket.messages[-limit:]]


@router.post("/{ticket_id}/messages", response_model=MessageOut, status_code=status.HTTP_201_CREATED)
async def add_message(
    ticket_id: int,
    payload: MessageCreate,
    context: tuple[Workspace, str] = Depends(require_role("agent")),
    session: AsyncSession = Depends(get_session),
) -> MessageOut:
    workspace, role = context
    ticket = await _load_detail(session, workspace.id, ticket_id)
    # Оператор в панели отвечает ролью agent — её и отправляем в канал.
    is_operator_reply = payload.sender == SenderRole.AGENT.value

    delivered = True
    if is_operator_reply and ticket.channel in {Channel.TELEGRAM.value, Channel.WEB.value}:
        delivered = await deliver_to_telegram(
            workspace.id, ticket.customer_ref, payload.content, ticket.public_id
        )
        if not delivered:
            logger.info(
                "ответ оператора не отправлен в %s — остаётся в панели", ticket.channel
            )

    message = await ticket_service.add_message(
        session,
        ticket,
        payload.sender,
        payload.content,
        meta={"author": role, "delivered": delivered, "read": True},
    )
    if ticket.status in {TicketStatus.OPEN.value, TicketStatus.ESCALATED.value}:
        ticket.status = TicketStatus.PENDING.value

    # Ответ оператора становится примером в базе знаний
    learned: dict[str, object] = {"learned": False}
    if is_operator_reply:
        question = _last_customer_question(ticket)
        if question:
            try:
                learned = await learn_from_operator_reply(
                    session, workspace.id, question, payload.content
                )
            except Exception as exc:  # noqa: BLE001 — обучение не должно ломать ответ
                logger.warning("не удалось обучить базу знаний: %s", exc)
                learned = {"learned": False, "reason": str(exc)[:200]}
        if learned.get("learned"):
            await log_event(
                session,
                ticket,
                EventType.KNOWLEDGE_LEARNED.value,
                actor=role,
                payload={
                    "created": bool(learned.get("created")),
                    "document_id": learned.get("document_id"),
                    "title": learned.get("title"),
                    "similarity": learned.get("similarity"),
                },
            )

    message.meta = {**(message.meta or {}), "learned": learned}
    await session.commit()
    return MessageOut.model_validate(message)
