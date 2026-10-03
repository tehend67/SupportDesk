import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from . import __version__
from .api.router import api_router
from .config import settings
from .db import engine, is_postgres
from .middleware import SecurityHeadersMiddleware
from .models import Base
from .security import assert_production_ready, production

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("helpdesk")

# httpx печатает полный URL, а в адресе Telegram-метода сидит токен бота.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def start_telegram_worker() -> asyncio.Task | None:
    """Поднимает встроенный супервизор Telegram-ботов, если установлен aiogram."""
    from .bots.telegram_worker import aiogram_available, supervisor

    if not aiogram_available():
        logger.warning(
            "aiogram не установлен — Telegram-боты не поднимутся. "
            "Установите его: py -3.12 -m pip install aiogram"
        )
        return None
    task = asyncio.create_task(supervisor(), name="telegram-worker")
    logger.info("встроенный воркер Telegram-ботов запущен")
    return task


async def has_tables() -> bool:
    """Есть ли вообще таблицы. Пустая база — это первый запуск, а не расхождение."""
    try:
        async with engine.connect() as conn:
            if is_postgres():
                count = await conn.execute(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                )
            else:
                count = await conn.execute(
                    text("SELECT count(*) FROM sqlite_master WHERE type = 'table'")
                )
            return int(count.scalar() or 0) > 0
    except Exception:
        return False


async def schema_outdated() -> bool:
    """Схема старше модели: появились новые таблицы или колонки."""
    checks = (
        "SELECT workspace_id FROM tickets LIMIT 1",
        "SELECT telegram_status FROM workspaces LIMIT 1",
        "SELECT key_hash FROM api_keys LIMIT 1",
        "SELECT blocked_until FROM login_attempts LIMIT 1",
        "SELECT action FROM audit_entries LIMIT 1",
    )
    try:
        async with engine.connect() as conn:
            for statement in checks:
                await conn.execute(text(statement))
        return False
    except Exception:
        return True


async def init_db() -> None:
    fresh = not await has_tables()
    outdated = False if fresh else await schema_outdated()
    if outdated:
        if settings.auto_reset_schema:
            logger.warning(
                "Схема не соответствует модели — пересоздаю таблицы "
                "(данные будут потеряны)"
            )
        else:
            logger.warning(
                "Схема не соответствует модели: не хватает новых таблиц или колонок. "
                "Чтобы пересоздать базу, поставьте AUTO_RESET_SCHEMA=true один раз "
                "или примените миграции: alembic upgrade head"
            )
    async with engine.begin() as conn:
        if outdated and settings.auto_reset_schema:
            await conn.run_sync(Base.metadata.drop_all)
        if is_postgres():
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        if fresh:
            logger.info("создаю схему с нуля: %d таблиц", len(Base.metadata.tables))


@asynccontextmanager
async def lifespan(_: FastAPI):
    problems = assert_production_ready()
    for problem in problems:
        logger.error("БЕЗОПАСНОСТЬ: %s", problem)
    if problems:
        logger.error(
            "Прод не запустится без исправлений: %s. Подробности — README, раздел «Безопасность».",
            "; ".join(problems),
        )

    if settings.auto_create_schema:
        await init_db()
    if settings.seed_demo_data:
        try:
            from .seed import run_seed

            await run_seed()
        except Exception:
            logger.exception("seeding failed")

    try:
        from .db import SessionLocal
        from .services.workspaces import migrate_plain_tokens

        async with SessionLocal() as session:
            await migrate_plain_tokens(session)
    except Exception:
        logger.exception("не удалось зашифровать токены ботов")

    worker: asyncio.Task | None = None
    if settings.telegram_worker_enabled:
        worker = start_telegram_worker()

    yield
    if worker is not None:
        worker.cancel()
        try:
            await worker
        except (asyncio.CancelledError, Exception):
            pass
    await engine.dispose()


# На проде схема API не отдаётся.
_OPEN_API = None if production() else "/openapi.json"
app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description="Мультитенантный ИИ-ассистент поддержки: API, PostgreSQL + pgvector, Telegram, Web.",
    lifespan=lifespan,
    docs_url=None if production() else "/docs",
    redoc_url=None if production() else "/redoc",
    openapi_url=_OPEN_API,
)

app.add_middleware(
    SecurityHeadersMiddleware,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    # При CORS_ORIGINS="*" запрос с cookie браузер всё равно не пустит,
    # а виджет с чужого домена работает без cookie. Явный список на проде
    # включает credentials.
    allow_credentials=settings.cors_origins.strip() != "*",
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=600,
)

app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/healthz", tags=["meta"])
async def root() -> dict[str, str]:
    return {
        "app": settings.app_name,
        "version": __version__,
        "docs": "/docs",
        "api": settings.api_prefix,
    }


FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
