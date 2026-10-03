"""Тесты встроенного воркера Telegram-ботов.

Проверяем то, из-за чего бот раньше молчал: воркер обязан подниматься
вместе с приложением и писать статус прямо в БД.
"""
import asyncio
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app import seed
from app.bots import telegram_worker
from app.config import settings
from app.db import SessionLocal
from app.main import app, init_db, start_telegram_worker
from app.services import workspaces as workspace_service


@pytest.fixture(scope="module", autouse=True)
def prepare_database():
    async def setup():
        await init_db()
        await seed.run_seed()

    asyncio.run(setup())


def test_aiogram_is_installed_for_builtin_worker():
    """Без aiogram встроенный воркер не сможет поднять ботов."""
    assert telegram_worker.aiogram_available(), (
        "Установите aiogram из backend/requirements.txt, иначе Telegram-канал не заработает"
    )


def test_aiogram_check_does_not_raise_when_missing(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "aiogram" or name.startswith("aiogram."):
            raise ModuleNotFoundError("No module named 'aiogram'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert telegram_worker.aiogram_available() is False


def test_start_returns_none_without_aiogram(monkeypatch):
    """Если aiogram нет, приложение всё равно стартует — просто без ботов."""
    monkeypatch.setattr(telegram_worker, "aiogram_available", lambda: False)
    assert start_telegram_worker() is None


def test_worker_writes_heartbeat_directly_to_database():
    """Heartbeat идёт в БД напрямую, без HTTP-запроса к самому себе."""

    async def scenario():
        from app.models import Workspace
        from app.models import User

        async with SessionLocal() as session:
            user = User(email=f"worker-{uuid4().hex[:8]}@example.com", hashed_password="x")
            session.add(user)
            await session.flush()
            workspace = await workspace_service.create_workspace(session, user, "Worker Room")
            await session.commit()
            workspace_id = workspace.id

        async with SessionLocal() as session:
            updated = await workspace_service.report_bot_heartbeat(session, workspace_id, True)
            assert updated is not None
            await session.commit()

        async with SessionLocal() as session:
            refreshed = await session.get(Workspace, workspace_id)
            assert refreshed.telegram_status == "online"
            assert refreshed.telegram_last_seen is not None

    asyncio.run(scenario())


def test_assignments_include_slug_and_username():
    """Вореру нужен slug для deep link и username для логов."""

    async def scenario():
        from app.models import User

        async with SessionLocal() as session:
            user = User(email=f"assign-{uuid4().hex[:8]}@example.com", hashed_password="x")
            session.add(user)
            await session.flush()
            workspace = await workspace_service.create_workspace(
                session, user, "Assign Room"
            )
            workspace.telegram_enabled = True
            workspace.telegram_bot_token = "123456:AAHq_demo_token_value_0123456789"
            workspace.telegram_bot_username = "assign_room_bot"
            await session.commit()
            workspace_id = workspace.id

        async with SessionLocal() as session:
            rows = list(await workspace_service.telegram_assignments(session))
            row = next(r for r in rows if r["workspace_id"] == workspace_id)
            assert row["slug"].startswith("assign-room")
            assert row["bot_username"] == "assign_room_bot"
            assert row["bot_token"]

    asyncio.run(scenario())


def test_ingest_path_used_by_builtin_client_exists():
    """Клиент воркера зовёт тот же конвейер, что и web-виджет."""
    from app.config import Settings
    from app.services.conversation import handle_incoming_message

    assert callable(handle_incoming_message)
    assert hasattr(telegram_worker._LocalChannelClient, "send")
    # В тестах воркер выключен (TELEGRAM_WORKER_ENABLED=false), но по умолчанию
    # во внешнем окружении он должен подниматься вместе с приложением.
    assert Settings.model_fields["telegram_worker_enabled"].default is True
    assert settings.telegram_worker_enabled is False