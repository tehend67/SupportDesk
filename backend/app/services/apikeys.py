from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..constants import WorkspaceRole
from ..models import ApiKey, Workspace
from ..security import constant_time_equals, hash_secret_key, mask_secret, random_secret


async def create_key(
    session: AsyncSession,
    workspace: Workspace,
    name: str,
    scope: str = "ingest",
    created_by: str = "",
) -> tuple[ApiKey, str]:
    plain = random_secret("ak")
    record = ApiKey(
        workspace_id=workspace.id,
        name=(name or "Ключ").strip()[:120],
        prefix=plain[:11],
        key_hash=hash_secret_key(plain),
        scope=scope if scope in {"ingest", "widget"} else "ingest",
        masked=mask_secret(plain),
        created_by=created_by[:255],
    )
    session.add(record)
    await session.flush()
    return record, plain


async def list_keys(session: AsyncSession, workspace_id: int) -> list[ApiKey]:
    rows = await session.execute(
        select(ApiKey).where(ApiKey.workspace_id == workspace_id).order_by(ApiKey.id.desc())
    )
    return list(rows.scalars())


async def revoke_key(session: AsyncSession, workspace: Workspace, key_id: int) -> bool:
    record = await session.get(ApiKey, key_id)
    if record is None or record.workspace_id != workspace.id or record.revoked_at:
        return False
    record.revoked_at = datetime.now(timezone.utc)
    await session.flush()
    return True


async def workspace_for_key(session: AsyncSession, plain: str) -> tuple[Optional[Workspace], Optional[ApiKey]]:
    if not plain:
        return None, None
    digest = hash_secret_key(plain)
    rows = await session.execute(select(ApiKey).where(ApiKey.key_hash == digest))
    candidates = [
        row for row in rows.scalars()
        if constant_time_equals(row.key_hash, digest)
    ]
    if not candidates:
        return None, None
    record = next((row for row in candidates if row.revoked_at is None), candidates[0])
    if record.revoked_at is not None:
        return None, None
    workspace = await session.get(Workspace, record.workspace_id)
    if workspace is None:
        return None, None
    record.last_used_at = datetime.now(timezone.utc)
    await session.flush()
    return workspace, record


def is_key_valid(record: Optional[ApiKey], expected_scope: str) -> bool:
    return record is not None and record.revoked_at is None and record.scope == expected_scope


__all__ = [
    "create_key",
    "list_keys",
    "revoke_key",
    "workspace_for_key",
    "is_key_valid",
    "WorkspaceRole",
    "settings",
]