import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...constants import UserRole
from ...db import get_session
from ...models import User
from ...ratelimit import client_ip, limiter
from ...schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut
from ...security import hash_password, password_problems, verify_password
from ... import audit
from ...services import session as session_service
from ..deps import get_current_user

router = APIRouter()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def issue_token(user: User) -> str:
    return session_service.issue(user.id, user.role, user.email)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    ident = f"{client_ip(request)}|{payload.email.lower().strip()}"
    blocked = await session_service.locked_until(session, ident)
    if blocked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Слишком много попыток. Повторите через {blocked // 60 + 1} мин.",
            headers={"Retry-After": str(blocked)},
        )

    ok, remaining, retry_after = await limiter.check(
        "login", ident, settings.login_rate_limit_per_minute
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много попыток входа. Подождите минуту.",
            headers={"Retry-After": str(retry_after or 60)},
        )

    result = await session.execute(select(User).where(User.email == payload.email.lower().strip()))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(payload.password, user.hashed_password):
        failures = await session_service.register_failure(session, ident)
        await session.commit()
        await audit.record(
            session, "auth.login_failed", actor=payload.email, ip=client_ip(request)
        )
        await session.commit()
        logger = __import__("logging").getLogger("helpdesk.auth")
        logger.warning("неудачный вход: %s (попыток: %s)", payload.email, failures)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный email или пароль"
        )

    await session_service.register_success(session, ident)
    await limiter.success("login", ident)
    await audit.record(
        session,
        "auth.login",
        actor=user.email,
        workspace_id=await _first_workspace_id(session, user.id),
        ip=client_ip(request),
        meta={"user_id": user.id},
    )
    await session.commit()

    token = issue_token(user)
    session_service.set_session_cookies(response, token)
    return TokenResponse(
        access_token=token, expires_in=settings.access_token_ttl_minutes * 60
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    if not settings.allow_registration:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Регистрация отключена администратором"
        )

    problems = password_problems(payload.password)
    if problems:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Пароль: " + ", ".join(problems),
        )

    ident = f"{client_ip(request)}|{payload.email.lower().strip()}"
    ok, _, _ = await limiter.check("register", ident, 5, window=3600.0)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много регистраций с этого адреса",
            headers={"Retry-After": "3600"},
        )

    email = payload.email.lower().strip()
    if not EMAIL_RE.match(email):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Некорректный email"
        )

    existing = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Пользователь с таким email уже существует"
        )

    full_name = payload.full_name.strip()[:255]
    user = User(
        email=email,
        full_name=full_name,
        hashed_password=hash_password(payload.password),
        role=UserRole.AGENT.value,
    )
    session.add(user)
    await session.flush()

    from ...services.workspaces import create_workspace

    team_name = f"Команда {full_name.split(' ')[0]}" if full_name else "Моя команда"
    workspace = await create_workspace(session, user, team_name)
    await audit.record(
        session,
        "auth.register",
        actor=email,
        workspace_id=workspace.id,
        ip=client_ip(request),
    )
    await session.commit()

    token = issue_token(user)
    session_service.set_session_cookies(response, token)
    return TokenResponse(access_token=token, expires_in=settings.access_token_ttl_minutes * 60)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, session: AsyncSession = Depends(get_session)) -> None:
    session_service.clear_session_cookies(response)
    await session.commit()


async def _first_workspace_id(session: AsyncSession, user_id: int) -> Optional[int]:
    from ...models import Membership

    row = await session.execute(
        select(Membership.workspace_id).where(Membership.user_id == user_id).limit(1)
    )
    return row.scalar_one_or_none()


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user