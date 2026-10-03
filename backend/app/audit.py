import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .models import AuditEntry

logger = logging.getLogger("helpdesk.audit")


async def record(
    session: AsyncSession,
    action: str,
    actor: str = "system",
    workspace_id: Optional[int] = None,
    ip: str = "",
    meta: Optional[dict[str, Any]] = None,
) -> None:
    if not settings.audit_enabled:
        return
    try:
        session.add(
            AuditEntry(
                workspace_id=workspace_id,
                action=action[:64],
                actor=str(actor)[:64],
                ip=(ip or "")[:64],
                meta=_safe_meta(meta),
            )
        )
        await session.flush()
    except Exception as exc:  # noqa: BLE001 — аудит не должен ломать операцию
        logger.warning("не удалось записать аудит %s: %s", action, exc)


SENSITIVE = {"password", "bot_token", "token", "secret", "key", "authorization", "widget_key", "channel_key"}


def _safe_meta(meta: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not meta:
        return {}
    clean: dict[str, Any] = {}
    for key, value in meta.items():
        if any(word in key.lower() for word in SENSITIVE):
            clean[key] = "***"
        elif isinstance(value, (int, float, bool)) or value is None:
            clean[key] = value
        else:
            clean[key] = str(value)[:160]
    return clean


async def recent(
    session: AsyncSession, workspace_id: int, limit: int = 100
) -> list[AuditEntry]:
    rows = await session.execute(
        select(AuditEntry)
        .where(AuditEntry.workspace_id == workspace_id)
        .order_by(AuditEntry.id.desc())
        .limit(limit)
    )
    return list(rows.scalars())


__all__ = ["record", "recent"]