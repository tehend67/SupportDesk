from datetime import datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..constants import CLOSED_STATUSES, OPEN_STATUSES, TicketStatus
from ..db import is_postgres
from ..models import Message, Ticket
from .knowledge import knowledge_stats


async def _count(session: AsyncSession, stmt) -> int:
    return int((await session.execute(stmt)).scalar_one() or 0)


async def _group_counts(session: AsyncSession, column, workspace_id: int) -> list[dict[str, Any]]:
    stmt = (
        select(column, func.count())
        .where(Ticket.workspace_id == workspace_id)
        .group_by(column)
        .order_by(column)
    )
    rows = (await session.execute(stmt)).all()
    return [{"key": str(key), "count": int(count or 0)} for key, count in rows]


async def _avg_seconds(session: AsyncSession, workspace_id: int, end_column) -> float:
    if is_postgres():
        expr = func.avg(func.extract("epoch", end_column - Ticket.created_at))
    else:
        expr = func.avg((func.julianday(end_column) - func.julianday(Ticket.created_at)) * 86400.0)
    value = (
        await session.execute(
            select(expr).where(Ticket.workspace_id == workspace_id, end_column.is_not(None))
        )
    ).scalar_one()
    return float(value or 0.0)


def _day_key(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value[:10]
    return value.date().isoformat()


async def build_overview(session: AsyncSession, workspace_id: int) -> dict[str, Any]:
    def tickets():
        return select(func.count()).select_from(Ticket).where(Ticket.workspace_id == workspace_id)

    total = await _count(session, tickets())
    open_tickets = await _count(
        session, tickets().where(Ticket.status.in_(OPEN_STATUSES))
    )
    resolved_tickets = await _count(
        session, tickets().where(Ticket.status.in_(CLOSED_STATUSES))
    )
    escalated_tickets = await _count(
        session, tickets().where(Ticket.status == TicketStatus.ESCALATED.value)
    )
    ai_handled = await _count(session, tickets().where(Ticket.ai_handled.is_(True)))
    messages_total = await _count(
        session,
        select(func.count())
        .select_from(Message)
        .join(Ticket, Message.ticket_id == Ticket.id)
        .where(Ticket.workspace_id == workspace_id),
    )
    documents_total, chunks_total = await knowledge_stats(session, workspace_id)

    avg_first = await _avg_seconds(session, workspace_id, Ticket.first_response_at)
    avg_resolution = await _avg_seconds(session, workspace_id, Ticket.resolved_at)

    return {
        "total_tickets": total,
        "open_tickets": open_tickets,
        "resolved_tickets": resolved_tickets,
        "escalated_tickets": escalated_tickets,
        "ai_handled_share": round(ai_handled / total, 4) if total else 0.0,
        "messages_total": messages_total,
        "documents_total": documents_total,
        "chunks_total": chunks_total,
        "avg_first_response_minutes": round(avg_first / 60.0, 2),
        "avg_resolution_hours": round(avg_resolution / 3600.0, 2),
        "by_status": await _group_counts(session, Ticket.status, workspace_id),
        "by_channel": await _group_counts(session, Ticket.channel, workspace_id),
        "by_priority": await _group_counts(session, Ticket.priority, workspace_id),
    }


async def build_timeseries(
    session: AsyncSession, workspace_id: int, days: int = 14
) -> list[dict[str, Any]]:
    days = max(1, min(days, 90))
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)
    window_start = datetime.combine(start, time.min, tzinfo=timezone.utc)

    if is_postgres():
        created_expr = func.date_trunc("day", Ticket.created_at)
        resolved_expr = func.date_trunc("day", Ticket.resolved_at)
    else:
        created_expr = func.strftime("%Y-%m-%d", Ticket.created_at)
        resolved_expr = func.strftime("%Y-%m-%d", Ticket.resolved_at)

    created_rows = (
        await session.execute(
            select(created_expr.label("day"), func.count())
            .where(Ticket.workspace_id == workspace_id, Ticket.created_at >= window_start)
            .group_by("day")
        )
    ).all()
    resolved_rows = (
        await session.execute(
            select(resolved_expr.label("day"), func.count())
            .where(
                Ticket.workspace_id == workspace_id,
                Ticket.resolved_at.is_not(None),
                Ticket.resolved_at >= window_start,
            )
            .group_by("day")
        )
    ).all()

    created: dict[str, int] = {}
    for row in created_rows:
        key = _day_key(row[0])
        if key:
            created[key] = int(row[1] or 0)
    resolved: dict[str, int] = {}
    for row in resolved_rows:
        key = _day_key(row[0])
        if key:
            resolved[key] = int(row[1] or 0)

    points: list[dict[str, Any]] = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).isoformat()
        points.append({"day": day, "created": created.get(day, 0), "resolved": resolved.get(day, 0)})
    return points
