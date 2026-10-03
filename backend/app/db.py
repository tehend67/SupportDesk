from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from .config import settings


def _engine_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {"pool_pre_ping": True, "future": True}
    if settings.db_pool_disabled or settings.database_url.startswith("sqlite"):
        kwargs["poolclass"] = NullPool
    return kwargs


engine = create_async_engine(settings.database_url, **_engine_kwargs())
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


def is_postgres() -> bool:
    return engine.dialect.name == "postgresql"


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


def sync_database_url() -> str:
    return settings.database_url.replace("+asyncpg", "+psycopg").replace("+aiosqlite", "")
