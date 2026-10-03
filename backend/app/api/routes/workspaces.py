from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ... import audit
from ...config import settings
from ...constants import WorkspaceRole
from ...db import get_session
from ...models import User, Workspace
from ...ratelimit import client_ip
from ...schemas import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyOut,
    AuditOut,
    MemberCreate,
    MemberOut,
    MemberUpdate,
    TelegramConnectRequest,
    TelegramStatusOut,
    WorkspaceCreate,
    WorkspaceDetail,
    WorkspaceOut,
)
from ...security import mask_secret
from ...services import apikeys as key_service
from ...services import workspaces as workspace_service
from ..deps import get_current_user, get_workspace_context, require_role

router = APIRouter()

ROLES = {"owner", "admin", "agent"}


def public_base(request: Request) -> str:
    """Адрес, по которому виджет будет стучаться к API.

    По умолчанию берём фактический хост запроса — он верен и локально, и за прокси.
    Если задан PUBLIC_BASE_URL, используем его: без этого виджет, вставленный на
    https-сайт клиента, попытался бы обратиться к самому себе и был бы заблокирован
    браузером как смешанное содержимое.
    """
    configured = (settings.public_base_url or "").strip().rstrip("/")
    default = "http://localhost:8000"
    if configured and configured != default:
        return configured
    return str(request.base_url).rstrip("/")


def build_snippet(request: Request, workspace: Workspace) -> dict[str, str]:
    base = public_base(request)
    widget_url = f"{base}/widget.js"
    api_base = f"{base}/api"
    snippet = (
        f'<script src="{widget_url}" data-workspace-key="{workspace.widget_key}" '
        f'data-api-base="{api_base}" data-autoload="true"></script>'
    )
    return {"widget_url": widget_url, "widget_snippet": snippet, "api_base_url": api_base}


def detail_payload(request: Request, workspace: Workspace, role: str) -> WorkspaceDetail:
    payload = WorkspaceDetail.model_validate(workspace)
    payload.role = role
    # Ключ канала агент (роль без управления) видит только маской.
    if role not in {WorkspaceRole.OWNER.value, WorkspaceRole.ADMIN.value}:
        payload.channel_key = mask_secret(workspace.channel_key)
    payload.telegram_deep_link = workspace_service.telegram_deep_link(workspace)
    for key, value in build_snippet(request, workspace).items():
        setattr(payload, key, value)
    return payload


@router.get("", response_model=list[WorkspaceOut])
async def list_workspaces(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[WorkspaceOut]:
    items = await workspace_service.list_for_user(session, user)
    return [WorkspaceOut.model_validate(item) for item in items]


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> WorkspaceOut:
    workspace = await workspace_service.create_workspace(session, user, payload.name, payload.slug)
    await session.commit()
    return WorkspaceOut.model_validate(
        {
            "id": workspace.id,
            "name": workspace.name,
            "slug": workspace.slug,
            "plan": workspace.plan,
            "role": WorkspaceRole.OWNER.value,
            "members": 1,
            "open_tickets": 0,
            "created_at": workspace.created_at,
        }
    )


@router.get("/current", response_model=WorkspaceDetail)
async def current_workspace(
    request: Request,
    context: tuple[Workspace, str] = Depends(get_workspace_context),
) -> WorkspaceDetail:
    workspace, role = context
    return detail_payload(request, workspace, role)


@router.patch("/current", response_model=WorkspaceDetail)
async def rename_workspace(
    payload: WorkspaceCreate,
    request: Request,
    context: tuple[Workspace, str] = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> WorkspaceDetail:
    workspace, role = context
    workspace.name = payload.name.strip()[:160]
    await session.commit()
    return detail_payload(request, workspace, role)


@router.get("/current/members", response_model=list[MemberOut])
async def list_members(
    context: tuple[Workspace, str] = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> list[MemberOut]:
    workspace, _ = context
    members = await workspace_service.list_members(session, workspace.id)
    return [MemberOut.model_validate(member) for member in members]


@router.post("/current/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(
    payload: MemberCreate,
    context: tuple[Workspace, str] = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> MemberOut:
    workspace, _ = context
    if payload.role not in ROLES:
        raise HTTPException(status_code=422, detail="Неизвестная роль")
    if payload.role == WorkspaceRole.OWNER.value:
        raise HTTPException(status_code=422, detail="Владельца назначить нельзя")

    existing = await workspace_service.list_members(session, workspace.id)
    email = payload.email.lower().strip()
    if any(member["email"] == email for member in existing):
        raise HTTPException(status_code=409, detail="Этот пользователь уже в команде")

    membership, user = await workspace_service.add_member(
        session,
        workspace,
        email,
        payload.role,
        full_name=payload.full_name,
        password=payload.password,
    )
    await session.commit()
    return MemberOut.model_validate(
        {
            "id": membership.id,
            "user_id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": membership.role,
            "created_at": membership.created_at,
        }
    )


@router.patch("/current/members/{member_id}", response_model=MemberOut)
async def change_member_role(
    member_id: int,
    payload: MemberUpdate,
    context: tuple[Workspace, str] = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> MemberOut:
    workspace, _ = context
    if payload.role not in ROLES:
        raise HTTPException(status_code=422, detail="Неизвестная роль")
    membership = await workspace_service.update_member_role(session, workspace, member_id, payload.role)
    if membership is None:
        raise HTTPException(status_code=404, detail="Участник не найден")
    await session.commit()
    members = await workspace_service.list_members(session, workspace.id)
    target = next(member for member in members if member["id"] == member_id)
    return MemberOut.model_validate(target)


@router.delete("/current/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    member_id: int,
    context: tuple[Workspace, str] = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> None:
    workspace, _ = context
    if not await workspace_service.remove_member(session, workspace, member_id):
        raise HTTPException(status_code=400, detail="Нельзя удалить владельца команды")
    await session.commit()
    return None


@router.post("/current/keys/rotate", response_model=WorkspaceDetail)
async def rotate_keys(
    request: Request,
    target: str = "widget",
    context: tuple[Workspace, str] = Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
) -> WorkspaceDetail:
    workspace, role = context
    if target not in {"widget", "channel"}:
        raise HTTPException(status_code=422, detail="target должен быть widget или channel")
    await workspace_service.rotate_key(session, workspace, target)
    await session.commit()
    return detail_payload(request, workspace, role)


@router.get("/current/telegram/status", response_model=TelegramStatusOut)
async def telegram_status(
    context: tuple[Workspace, str] = Depends(get_workspace_context),
) -> TelegramStatusOut:
    """Живой статус бота — фронтенд опрашивает его, пока идёт connecting."""
    workspace, _ = context
    return workspace_service.telegram_status_payload(workspace)


@router.get("/current/keys", response_model=list[ApiKeyOut])
async def list_api_keys(
    context: tuple[Workspace, str] = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> list[ApiKeyOut]:
    workspace, _ = context
    records = await key_service.list_keys(session, workspace.id)
    return [ApiKeyOut.model_validate(_key_payload(record)) for record in records]


def _key_payload(record) -> dict:
    return {
        "id": record.id,
        "name": record.name,
        "prefix": record.prefix,
        "masked": record.masked,
        "scope": record.scope,
        "created_by": record.created_by,
        "revoked": record.revoked_at is not None,
        "last_used_at": record.last_used_at,
        "created_at": record.created_at,
    }


@router.post("/current/keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    payload: ApiKeyCreate,
    request: Request,
    context: tuple[Workspace, str] = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> ApiKeyCreated:
    workspace, role = context
    record, plain = await key_service.create_key(
        session, workspace, payload.name, payload.scope, created_by=f"{role}"
    )
    await audit.record(
        session,
        "key.created",
        actor=role,
        workspace_id=workspace.id,
        ip=client_ip(request),
        meta={"name": record.name, "scope": record.scope},
    )
    await session.commit()
    return ApiKeyCreated(**_key_payload(record), key=plain)


@router.delete("/current/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: int,
    request: Request,
    context: tuple[Workspace, str] = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> None:
    workspace, role = context
    if not await key_service.revoke_key(session, workspace, key_id):
        raise HTTPException(status_code=404, detail="Ключ не найден")
    await audit.record(
        session,
        "key.revoked",
        actor=role,
        workspace_id=workspace.id,
        ip=client_ip(request),
        meta={"key_id": key_id},
    )
    await session.commit()
    return None


@router.get("/current/audit", response_model=list[AuditOut])
async def list_audit(
    limit: int = Query(default=100, ge=1, le=500),
    context: tuple[Workspace, str] = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> list[AuditOut]:
    workspace, _ = context
    entries = await audit.recent(session, workspace.id, limit)
    return [
        AuditOut.model_validate(
            {
                "id": entry.id,
                "action": entry.action,
                "actor": entry.actor,
                "ip": entry.ip,
                "meta": entry.meta or {},
                "created_at": entry.created_at,
            }
        )
        for entry in entries
    ]


@router.put("/current/telegram", response_model=WorkspaceDetail)
async def connect_telegram(
    payload: TelegramConnectRequest,
    request: Request,
    context: tuple[Workspace, str] = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> WorkspaceDetail:
    workspace, role = context
    await workspace_service.set_telegram(session, workspace, payload.bot_token, payload.enabled)
    await session.commit()
    return detail_payload(request, workspace, role)
