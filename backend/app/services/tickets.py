import secrets
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..constants import (
    AGENT_SENDER,
    ASSISTANT_SENDER,
    Channel,
    EventType,
    Priority,
    SenderRole,
    TicketStatus,
)
from ..models import Message, Ticket, TicketEvent


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_public_id() -> str:
    return f"TCK-{secrets.token_hex(3).upper()}"


async def log_event(
    session: AsyncSession,
    ticket: Ticket,
    event_type: str,
    actor: str = "system",
    payload: Optional[dict[str, Any]] = None,
) -> TicketEvent:
    event = TicketEvent(ticket_id=ticket.id, type=event_type, actor=actor, payload=payload or {})
    session.add(event)
    await session.flush()
    return event


async def create_ticket(
    session: AsyncSession,
    workspace_id: int,
    subject: str,
    channel: Channel | str = Channel.WEB,
    priority: Priority | str = Priority.NORMAL,
    customer_name: str = "",
    customer_ref: str = "",
    assignee_id: Optional[int] = None,
) -> Ticket:
    ticket = Ticket(
        workspace_id=workspace_id,
        public_id=generate_public_id(),
        subject=subject.strip()[:255] or "Новое обращение",
        channel=channel.value if isinstance(channel, Channel) else str(channel),
        priority=priority.value if isinstance(priority, Priority) else str(priority),
        customer_name=customer_name.strip()[:255],
        customer_ref=customer_ref.strip()[:255],
        assignee_id=assignee_id,
    )
    session.add(ticket)
    await session.flush()
    await log_event(
        session, ticket, EventType.CREATED.value, actor="customer", payload={"channel": ticket.channel}
    )
    return ticket


async def get_ticket(session: AsyncSession, workspace_id: int, ticket_id: int) -> Optional[Ticket]:
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None or ticket.workspace_id != workspace_id:
        return None
    return ticket


async def get_ticket_by_public_id(
    session: AsyncSession, workspace_id: int, public_id: str
) -> Optional[Ticket]:
    stmt = select(Ticket).where(
        Ticket.workspace_id == workspace_id, Ticket.public_id == public_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def find_open_ticket(
    session: AsyncSession, workspace_id: int, channel: Channel | str, customer_ref: str
) -> Optional[Ticket]:
    channel_value = channel.value if isinstance(channel, Channel) else str(channel)
    stmt = (
        select(Ticket)
        .where(
            Ticket.workspace_id == workspace_id,
            Ticket.channel == channel_value,
            Ticket.customer_ref == customer_ref,
            Ticket.status.in_(
                [TicketStatus.OPEN.value, TicketStatus.PENDING.value, TicketStatus.ESCALATED.value]
            ),
        )
        .order_by(Ticket.updated_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _apply_filters(
    stmt: Select,
    workspace_id: int,
    status: Optional[str],
    channel: Optional[str],
    priority: Optional[str],
    search: Optional[str],
) -> Select:
    stmt = stmt.where(Ticket.workspace_id == workspace_id)
    if status:
        stmt = stmt.where(Ticket.status == status)
    if channel:
        stmt = stmt.where(Ticket.channel == channel)
    if priority:
        stmt = stmt.where(Ticket.priority == priority)
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Ticket.subject.ilike(pattern),
                Ticket.customer_name.ilike(pattern),
                Ticket.customer_ref.ilike(pattern),
                Ticket.public_id.ilike(pattern),
            )
        )
    return stmt


async def list_tickets(
    session: AsyncSession,
    workspace_id: int,
    status: Optional[str] = None,
    channel: Optional[str] = None,
    priority: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[int, Sequence[Ticket]]:
    base = _apply_filters(select(Ticket), workspace_id, status, channel, priority, search)
    count_stmt = _apply_filters(
        select(func.count()).select_from(Ticket), workspace_id, status, channel, priority, search
    )
    total = (await session.execute(count_stmt)).scalar_one()
    stmt = base.order_by(Ticket.updated_at.desc()).limit(limit).offset(offset)
    items = (await session.execute(stmt)).scalars().all()
    return int(total or 0), items


async def update_ticket(
    session: AsyncSession,
    ticket: Ticket,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    assignee_id: Optional[int] = None,
    subject: Optional[str] = None,
    actor: str = "agent",
) -> Ticket:
    if status and status != ticket.status:
        previous_status = ticket.status
        ticket.status = status
        if status == TicketStatus.RESOLVED.value:
            ticket.resolved_at = utcnow()
        await log_event(
            session,
            ticket,
            EventType.STATUS_CHANGED.value,
            actor=actor,
            payload={"from": previous_status, "to": status},
        )
    if priority and priority != ticket.priority:
        previous_priority = ticket.priority
        ticket.priority = priority
        await log_event(
            session,
            ticket,
            EventType.PRIORITY_CHANGED.value,
            actor=actor,
            payload={"from": previous_priority, "to": priority},
        )
    if assignee_id is not None and assignee_id != ticket.assignee_id:
        ticket.assignee_id = assignee_id or None
        await log_event(
            session, ticket, EventType.ASSIGNED.value, actor=actor, payload={"assignee_id": assignee_id}
        )
    if subject is not None:
        ticket.subject = subject.strip()[:255]
    ticket.updated_at = utcnow()
    await session.flush()
    return ticket


async def add_message(
    session: AsyncSession,
    ticket: Ticket,
    sender: SenderRole | str,
    content: str,
    channel: Optional[Channel | str] = None,
    meta: Optional[dict[str, Any]] = None,
    tokens: int = 0,
) -> Message:
    sender_value = sender.value if isinstance(sender, SenderRole) else str(sender)
    channel_value = (
        channel.value if isinstance(channel, Channel) else (str(channel) if channel else ticket.channel)
    )
    message = Message(
        ticket_id=ticket.id,
        sender=sender_value,
        channel=channel_value,
        content=content,
        meta=meta or {},
        tokens=tokens,
    )
    session.add(message)
    ticket.updated_at = utcnow()
    if sender_value in {ASSISTANT_SENDER, AGENT_SENDER} and ticket.first_response_at is None:
        ticket.first_response_at = utcnow()
    await session.flush()
    return message


async def recent_history(
    session: AsyncSession, ticket: Ticket, limit: int = 8, exclude_id: Optional[int] = None
) -> list[dict[str, str]]:
    stmt = select(Message).where(Message.ticket_id == ticket.id)
    if exclude_id is not None:
        stmt = stmt.where(Message.id != exclude_id)
    stmt = stmt.order_by(Message.created_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    history = []
    for message in reversed(rows):
        if message.sender == SenderRole.CUSTOMER.value:
            role = "user"
        elif message.sender in {ASSISTANT_SENDER, AGENT_SENDER}:
            role = "assistant"
        else:
            continue
        history.append({"role": role, "content": message.content})
    return history
