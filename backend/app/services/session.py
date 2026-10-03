import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import LoginAttempt
from ..security import create_access_token


def set_session_cookies(response: Response, token: str) -> str:
    csrf = secrets.token_urlsafe(32)
    max_age = settings.access_token_ttl_minutes * 60
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=max_age,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf,
        max_age=max_age,
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )
    return csrf


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")


def cookie_flags() -> dict:
    return {
        "httponly": True,
        "secure": settings.session_cookie_secure,
        "samesite": settings.session_cookie_samesite,
    }


async def register_failure(session: AsyncSession, ident: str) -> int:
    row = await _attempt_row(session, ident)
    row.failures += 1
    row.blocked_until = datetime.now(timezone.utc) + timedelta(
        minutes=settings.login_lockout_minutes
    )
    await session.flush()
    return row.failures


async def register_success(session: AsyncSession, ident: str) -> None:
    row = await session.execute(
        select(LoginAttempt).where(LoginAttempt.ident == ident)
    )
    existing = row.scalar_one_or_none()
    if existing is None:
        session.add(LoginAttempt(ident=ident, failures=0))
    else:
        existing.failures = 0
        existing.blocked_until = None
    await session.flush()


async def locked_until(session: AsyncSession, ident: str) -> Optional[int]:
    row = await session.execute(
        select(LoginAttempt).where(LoginAttempt.ident == ident)
    )
    entry = row.scalar_one_or_none()
    if entry is None or entry.blocked_until is None:
        return None
    entry.blocked_until = entry.blocked_until.replace(tzinfo=timezone.utc)
    remaining = (entry.blocked_until - datetime.now(timezone.utc)).total_seconds()
    return int(remaining) if remaining > 0 else None


async def _attempt_row(session: AsyncSession, ident: str) -> LoginAttempt:
    row = await session.execute(select(LoginAttempt).where(LoginAttempt.ident == ident))
    entry = row.scalar_one_or_none()
    if entry is None:
        entry = LoginAttempt(ident=ident, failures=0)
        session.add(entry)
        await session.flush()
    return entry


def issue(user_id: int, role: str, email: str) -> str:
    return create_access_token(str(user_id), extra={"role": role, "email": email})


__all__ = [
    "set_session_cookies",
    "clear_session_cookies",
    "cookie_flags",
    "register_failure",
    "register_success",
    "locked_until",
    "issue",
]