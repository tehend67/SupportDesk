import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..constants import OPEN_STATUSES, TELEGRAM_ACTIVE_STATUSES, TelegramStatus, WorkspaceRole
from ..models import Membership, Ticket, User, Workspace
from ..security import decrypt_secret, encrypt_secret, hash_password, is_encrypted

logger = logging.getLogger("helpdesk.workspaces")

SLUG_RE = re.compile(r"[^a-z0-9]+")
ROLES = {role.value for role in WorkspaceRole}
TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")


def make_secret(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(24)}"


def slugify(value: str) -> str:
    return (SLUG_RE.sub("-", (value or "").lower()).strip("-") or "workspace")[:60]


async def unique_slug(session: AsyncSession, value: str) -> str:
    base = slugify(value)
    slug = base
    index = 1
    while True:
        existing = (
            await session.execute(select(Workspace.id).where(Workspace.slug == slug))
        ).scalar_one_or_none()
        if existing is None:
            return slug
        index += 1
        slug = f"{base}-{index}"


async def create_workspace(
    session: AsyncSession, owner: User, name: str, slug: Optional[str] = None
) -> Workspace:
    workspace = Workspace(
        name=name.strip()[:160] or "Новая команда",
        slug=await unique_slug(session, slug or name),
        owner_id=owner.id,
        widget_key=make_secret("wk"),
        channel_key=make_secret("ck"),
    )
    session.add(workspace)
    await session.flush()
    session.add(
        Membership(workspace_id=workspace.id, user_id=owner.id, role=WorkspaceRole.OWNER.value)
    )
    await session.flush()
    return workspace


async def get_workspace(session: AsyncSession, workspace_id: int) -> Optional[Workspace]:
    return await session.get(Workspace, workspace_id)


async def get_by_channel_key(session: AsyncSession, key: str) -> Optional[Workspace]:
    if not key:
        return None
    stmt = select(Workspace).where(Workspace.channel_key == key)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_by_widget_key(session: AsyncSession, key: str) -> Optional[Workspace]:
    if not key:
        return None
    stmt = select(Workspace).where(Workspace.widget_key == key)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_membership(
    session: AsyncSession, workspace_id: int, user_id: int
) -> Optional[Membership]:
    stmt = select(Membership).where(
        Membership.workspace_id == workspace_id, Membership.user_id == user_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_for_user(
    session: AsyncSession, user: User
) -> list[dict[str, Any]]:
    stmt = (
        select(Workspace, Membership.role)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user.id)
        .order_by(Workspace.created_at)
    )
    rows = (await session.execute(stmt)).all()
    result: list[dict[str, Any]] = []
    for workspace, role in rows:
        members = (
            await session.execute(
                select(func.count())
                .select_from(Membership)
                .where(Membership.workspace_id == workspace.id)
            )
        ).scalar_one()
        open_tickets = (
            await session.execute(
                select(func.count())
                .select_from(Ticket)
                .where(Ticket.workspace_id == workspace.id, Ticket.status.in_(OPEN_STATUSES))
            )
        ).scalar_one()
        result.append(
            {
                "id": workspace.id,
                "name": workspace.name,
                "slug": workspace.slug,
                "plan": workspace.plan,
                "role": role,
                "members": int(members or 0),
                "open_tickets": int(open_tickets or 0),
                "created_at": workspace.created_at,
            }
        )
    return result


async def list_members(session: AsyncSession, workspace_id: int) -> list[dict[str, Any]]:
    stmt = (
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.workspace_id == workspace_id)
        .order_by(Membership.created_at)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "id": membership.id,
            "user_id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": membership.role,
            "created_at": membership.created_at,
        }
        for membership, user in rows
    ]


async def add_member(
    session: AsyncSession,
    workspace: Workspace,
    email: str,
    role: str,
    full_name: str = "",
    password: Optional[str] = None,
) -> tuple[Membership, User]:
    normalized = email.lower().strip()
    user = (await session.execute(select(User).where(User.email == normalized))).scalar_one_or_none()
    if user is None:
        user = User(
            email=normalized,
            full_name=full_name.strip()[:255],
            hashed_password=hash_password(password or make_secret("tmp")),
            role="agent",
        )
        session.add(user)
        await session.flush()

    membership = Membership(workspace_id=workspace.id, user_id=user.id, role=role)
    session.add(membership)
    await session.flush()
    return membership, user


async def update_member_role(
    session: AsyncSession, workspace: Workspace, member_id: int, role: str
) -> Optional[Membership]:
    membership = await session.get(Membership, member_id)
    if membership is None or membership.workspace_id != workspace.id:
        return None
    membership.role = role
    await session.flush()
    return membership


async def remove_member(
    session: AsyncSession, workspace: Workspace, member_id: int
) -> bool:
    membership = await session.get(Membership, member_id)
    if membership is None or membership.workspace_id != workspace.id:
        return False
    if membership.user_id == workspace.owner_id:
        return False
    await session.delete(membership)
    await session.flush()
    return True


async def rotate_key(session: AsyncSession, workspace: Workspace, which: str) -> Workspace:
    if which == "widget":
        workspace.widget_key = make_secret("wk")
    else:
        workspace.channel_key = make_secret("ck")
    await session.flush()
    return workspace


async def set_telegram(
    session: AsyncSession, workspace: Workspace, bot_token: str, enabled: bool
) -> Workspace:
    """Cохраняет токен и сразу ставит бота в состояние «подключается».

    Вызывается из админ-панели: после сохранения воркер подхватит бота
    при следующем опросе и переведёт статус в online.
    """
    token = bot_token.strip()
    workspace.telegram_bot_token = encrypt_secret(token)
    if not enabled or not token:
        workspace.telegram_enabled = False
        workspace.telegram_status = TelegramStatus.OFF.value
        workspace.telegram_bot_username = ""
        workspace.telegram_bot_name = ""
        workspace.telegram_error = ""
        workspace.telegram_last_seen = None
        await session.flush()
        return workspace

    username, bot_name, error = await verify_bot_token(token)
    if username is None:
        workspace.telegram_enabled = False
        workspace.telegram_status = TelegramStatus.INVALID.value
        workspace.telegram_bot_username = ""
        workspace.telegram_bot_name = ""
        workspace.telegram_error = error or "Telegram не принял токен"
        await session.flush()
        return workspace

    workspace.telegram_enabled = True
    workspace.telegram_status = TelegramStatus.CONNECTING.value
    workspace.telegram_bot_username = username
    workspace.telegram_bot_name = bot_name or username
    workspace.telegram_error = ""
    await session.flush()
    return workspace


async def verify_bot_token(bot_token: str) -> tuple[Optional[str], str, str]:
    """Проверяет токен через getMe.

    Возвращает ``(username, bot_name, error)``. username=None — токен не принят
    Telegram. Сетевые ошибки не считаются ошибкой токена: токен сохраняется,
    воркер разберётся позже (статус connecting).
    """
    token = (bot_token or "").strip()
    if not token:
        return None, "", "Токен пустой"
    if not TOKEN_RE.match(token):
        return None, "", "Токен выглядит некорректно (нужен формат 123456:ABC-DEF...)"

    url = f"{settings.telegram_api_base}/bot{token}/getMe"
    try:
        async with httpx.AsyncClient(timeout=settings.telegram_verify_timeout) as client:
            response = await client.get(url)
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 — сеть недоступна, это не ошибка токена
        logger.warning("telegram getMe unavailable: %s", exc)
        return "", "", "Не удалось связаться с Telegram — проверьте позже"

    if not payload.get("ok"):
        return None, "", payload.get("description") or "Telegram отклонил токен"

    result = payload.get("result") or {}
    username = str(result.get("username") or "").strip()
    if not username:
        return None, "", "У бота нет username — задайте его в @BotFather"
    return username, str(result.get("first_name") or ""), ""


def telegram_deep_link(workspace: Workspace) -> str:
    """Ссылка вида t.me/<bot>?start=<workspace> — для вставки на сайт.

    Показывается только когда бот реально подключён или подключается.
    """
    if not workspace.telegram_bot_username:
        return ""
    if workspace.telegram_status not in TELEGRAM_ACTIVE_STATUSES:
        return ""
    return f"https://t.me/{workspace.telegram_bot_username}?start={workspace.slug}"


async def report_bot_heartbeat(
    session: AsyncSession, workspace_id: int, ok: bool, error: str = ""
) -> Optional[Workspace]:
    """Воркер сообщает, поднялся ли бот. Это двигает статус в online/error."""
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        return None
    workspace.telegram_last_seen = datetime.now(timezone.utc)
    if ok:
        workspace.telegram_status = TelegramStatus.ONLINE.value
        workspace.telegram_error = ""
    elif workspace.telegram_status != TelegramStatus.INVALID.value:
        workspace.telegram_status = TelegramStatus.ERROR.value
        workspace.telegram_error = (error or "Бот не отвечает")[:255]
    await session.flush()
    return workspace


def telegram_status_payload(workspace: Workspace) -> Any:
    """Единый вид статуса бота для API (комната и воркер отдают одинаково)."""
    from ..schemas import TelegramStatusOut

    return TelegramStatusOut(
        enabled=workspace.telegram_enabled,
        status=workspace.telegram_status,
        bot_username=workspace.telegram_bot_username or "",
        bot_name=workspace.telegram_bot_name or "",
        error=workspace.telegram_error or "",
        deep_link=telegram_deep_link(workspace),
        last_seen=workspace.telegram_last_seen,
    )


async def migrate_plain_tokens(session: AsyncSession) -> int:
    """Шифрует токены ботов, сохранённые до включения шифрования."""
    rows = (await session.execute(select(Workspace).where(Workspace.telegram_bot_token != ""))).scalars().all()
    changed = 0
    for workspace in rows:
        value = workspace.telegram_bot_token or ""
        if value and not is_encrypted(value):
            workspace.telegram_bot_token = encrypt_secret(value)
            changed += 1
    if changed:
        await session.commit()
        logger.info("зашифровано токенов бота: %s", changed)
    return changed


async def telegram_assignments(session: AsyncSession) -> Sequence[dict[str, Any]]:
    stmt = select(Workspace).where(
        Workspace.telegram_enabled.is_(True), Workspace.telegram_bot_token != ""
    )
    workspaces = (await session.execute(stmt)).scalars().all()
    return [
        {
            "workspace_id": workspace.id,
            "name": workspace.name,
            "slug": workspace.slug,
            "bot_token": decrypt_secret(workspace.telegram_bot_token),
            "bot_username": workspace.telegram_bot_username,
            "channel_key": workspace.channel_key,
        }
        for workspace in workspaces
    ]
