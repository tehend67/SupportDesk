from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..constants import WorkspaceRole, role_at_least
from ..db import get_session
from ..models import User, Workspace
from ..security import constant_time_equals, decode_token
from ..services import apikeys as key_service
from ..services import workspaces as workspace_service

bearer_scheme = HTTPBearer(auto_error=False)


def _read_token(request: Request, credentials: Optional[HTTPAuthorizationCredentials]) -> Optional[str]:
    if credentials is not None and credentials.credentials:
        return credentials.credentials
    return request.cookies.get(settings.session_cookie_name) or None


def _csrf_ok(request: Request, header: Optional[str]) -> bool:
    cookie = request.cookies.get(settings.csrf_cookie_name)
    if not cookie or not header:
        return False
    return constant_time_equals(cookie, header)


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    token = _read_token(request, credentials)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется вход",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Сессия истекла")
    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Некорректная сессия")
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Пользователь не найден")
    return user


async def require_csrf(
    request: Request,
    header: Optional[str] = Header(default=None, alias=settings.csrf_header_name),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> None:
    """Защита от подделки запросов.

    Нужна только для cookie-сессий: если клиент сам прислал заголовок
    Authorization, подделать запрос из чужого сайта он не может — такой
    запрос CSRF-атакой не является.
    """
    if credentials is not None and credentials.credentials:
        return
    if request.url.path.startswith((f"{settings.api_prefix}/channels", f"{settings.api_prefix}/ingest")):
        return
    # Ключ в заголовке — не cookie-сессия: подделать такой запрос с чужого
    # сайта нельзя, браузер требует preflight.
    if any(
        request.headers.get(name)
        for name in (settings.worker_api_key_header, "X-Workspace-Key", "X-Channel-Key")
    ):
        return
    if request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return
    if not request.cookies.get(settings.session_cookie_name):
        return
    if not _csrf_ok(request, header):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Недействительный CSRF-токен"
        )


async def get_workspace_context(
    x_workspace_id: Optional[int] = Header(default=None, alias="X-Workspace-Id"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> tuple[Workspace, str]:
    target = x_workspace_id
    if target is None:
        memberships = await workspace_service.list_for_user(session, user)
        if not memberships:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="У вас нет ни одной команды")
        target = memberships[0]["id"]

    workspace = await workspace_service.get_workspace(session, int(target))
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Команда не найдена")
    membership = await workspace_service.get_membership(session, workspace.id, user.id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к команде")
    return workspace, membership.role


def require_role(minimum: str):
    async def dependency(
        context: tuple[Workspace, str] = Depends(get_workspace_context),
    ) -> tuple[Workspace, str]:
        workspace, role = context
        if not role_at_least(role, minimum):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Требуется роль {minimum} или выше",
            )
        return workspace, role

    return dependency


async def resolve_channel_workspace(
    request: Request,
    x_workspace_key: Optional[str] = Header(default=None),
    x_channel_key: Optional[str] = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> Workspace:
    key = (x_workspace_key or x_channel_key or "").strip()
    if not key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Нужен ключ команды")

    workspace, record = await key_service.workspace_for_key(session, key)
    if workspace is not None and key_service.is_key_valid(record, "ingest"):
        await session.commit()
        return workspace

    legacy = await workspace_service.get_by_channel_key(session, key)
    if legacy is None:
        legacy = await workspace_service.get_by_widget_key(session, key)
    if legacy is None and constant_time_equals(key, settings.channel_api_key):
        legacy = (
            await session.execute(select(Workspace).order_by(Workspace.id).limit(1))
        ).scalar_one_or_none()
    if legacy is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный ключ команды")
    await session.commit()
    return legacy


async def require_worker_key(
    x_worker_key: Optional[str] = Header(default=None, alias="X-Worker-Key")
) -> str:
    if not x_worker_key or not constant_time_equals(x_worker_key, settings.worker_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный ключ воркера")
    return x_worker_key


__all__ = [
    "bearer_scheme",
    "get_current_user",
    "get_workspace_context",
    "require_role",
    "require_csrf",
    "resolve_channel_workspace",
    "require_worker_key",
    "WorkspaceRole",
]