from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.agent import SupportAgent
from ..config import settings
from ..constants import AGENT_SENDER, Channel, EventType, SenderRole, TicketStatus
from ..models import Message, Ticket, Workspace
from .knowledge import search_knowledge
from .tickets import (
    add_message,
    create_ticket,
    find_open_ticket,
    get_ticket_by_public_id,
    log_event,
    recent_history,
)


@dataclass
class ConversationResult:
    ticket: Ticket
    user_message: Message
    reply: Message
    hits: list[dict[str, Any]] = field(default_factory=list)
    escalated: bool = False
    ai_handled: bool = False
    model: str = ""
    # True — ассистент промолчал, тикет ведёт оператор
    muted: bool = False


async def _resolve_ticket(
    session: AsyncSession,
    workspace: Workspace,
    channel: Channel | str,
    customer_ref: str,
    customer_name: str,
    subject: str,
    ticket_ref: Optional[str],
) -> Ticket:
    channel_value = channel.value if isinstance(channel, Channel) else str(channel)
    ticket: Optional[Ticket] = None
    if ticket_ref:
        ticket = await get_ticket_by_public_id(session, workspace.id, ticket_ref)
    if ticket is None:
        ticket = await find_open_ticket(session, workspace.id, channel_value, customer_ref)
    if ticket is None:
        ticket = await create_ticket(
            session,
            workspace.id,
            subject=subject.strip() or "Обращение из канала",
            channel=channel_value,
            customer_name=customer_name,
            customer_ref=customer_ref,
        )
    return ticket


async def _operator_in_charge(session: AsyncSession, ticket: Ticket) -> bool:
    """В тикете уже отвечал человек — значит ИИ должен помолчать.

    Смотрим роли явным запросом: ленивая загрузка relationship здесь недопустима,
    она обращается к БД из синхронного контекста.
    """
    if ticket.status in {TicketStatus.RESOLVED.value, TicketStatus.CLOSED.value}:
        return False
    rows = await session.execute(
        select(Message.sender).where(Message.ticket_id == ticket.id)
    )
    return any(sender == AGENT_SENDER for (sender,) in rows)


async def handle_incoming_message(
    session: AsyncSession,
    workspace: Workspace,
    channel: Channel | str,
    content: str,
    customer_ref: str,
    customer_name: str = "",
    ticket_ref: Optional[str] = None,
    subject: str = "",
    metadata: Optional[dict[str, Any]] = None,
) -> ConversationResult:
    channel_value = channel.value if isinstance(channel, Channel) else str(channel)
    ticket = await _resolve_ticket(
        session, workspace, channel_value, customer_ref, customer_name, subject, ticket_ref
    )

    user_message = await add_message(
        session,
        ticket,
        SenderRole.CUSTOMER,
        content,
        channel=channel_value,
        meta=metadata or {},
    )

    # Тикет у живого оператора: ассистент не вмешивается, иначе клиент
    # получит второй противоречащий ответ.
    if await _operator_in_charge(session, ticket):
        await session.flush()
        return ConversationResult(
            ticket=ticket,
            user_message=user_message,
            reply=user_message,
            hits=[],
            escalated=True,
            ai_handled=False,
            model="operator-handoff",
            muted=True,
        )

    hits = await search_knowledge(session, workspace.id, content, settings.retrieval_top_k)
    history = await recent_history(
        session, ticket, settings.max_context_messages, exclude_id=user_message.id
    )

    agent = SupportAgent()
    result = await agent.respond(
        user_message=content, hits=hits, history=history, subject=ticket.subject
    )

    reply = await add_message(
        session,
        ticket,
        SenderRole.ASSISTANT,
        result.reply,
        channel=channel_value,
        tokens=result.tokens,
        meta={
            "model": result.model,
            "mock": result.mock,
            "escalated": result.escalated,
            "reason": result.reason,
            "sources": [
                {
                    "document_id": hit.get("document_id"),
                    "document_title": hit.get("document_title"),
                    "score": hit.get("score"),
                }
                for hit in hits
            ],
        },
    )

    ticket.sentiment = result.sentiment
    ticket.ai_handled = not result.escalated
    if result.escalated:
        ticket.status = TicketStatus.ESCALATED.value
        await log_event(
            session,
            ticket,
            EventType.ESCALATED.value,
            actor="ai",
            payload={"reason": result.reason, "model": result.model},
        )
    elif ticket.status == TicketStatus.OPEN.value:
        ticket.status = TicketStatus.PENDING.value
        await log_event(
            session,
            ticket,
            EventType.AI_REPLIED.value,
            actor="ai",
            payload={"model": result.model, "sources": len(hits)},
        )
    await session.flush()

    return ConversationResult(
        ticket=ticket,
        user_message=user_message,
        reply=reply,
        hits=hits,
        escalated=result.escalated,
        ai_handled=ticket.ai_handled,
        model=result.model,
    )
