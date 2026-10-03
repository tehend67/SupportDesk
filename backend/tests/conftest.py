import os
import tempfile
from pathlib import Path

_test_db = os.getenv("TEST_DATABASE_URL")
if not _test_db:
    _test_db = f"sqlite+aiosqlite:///{(Path(tempfile.gettempdir()) / 'helpdesk_test.db').as_posix()}"

if _test_db.startswith("sqlite"):
    _path = Path(_test_db.split("///", 1)[-1])
    if _path.exists():
        _path.unlink()

os.environ["DATABASE_URL"] = _test_db
os.environ["DB_POOL_DISABLED"] = "true"

# Тесты не должны ходить в Groq: без ключа приложение работает в офлайн-режиме
# и проверки становятся быстрыми и детерминированными. Переменные выставляем
# ДО импорта настроек: .env репозитория иначе снова подставит боевой ключ.
os.environ["LLM_API_KEY"] = ""
os.environ["TELEGRAM_WORKER_ENABLED"] = "false"
os.environ.setdefault("SEED_DEMO_DATA", "true")
os.environ.setdefault("AUTO_CREATE_SCHEMA", "true")

from app.config import settings  # noqa: E402 — импорт после настройки окружения

assert not settings.llm_api_key, "тесты не должны видеть боевой LLM_API_KEY"

CSRF_COOKIE = "aisd_csrf"


def auth_headers(http, token: str) -> dict:
    """Заголовки как у браузера: Bearer + CSRF-токен из cookie.

    Без CSRF-заголовка панельные запросы блокируются — это проверка защиты.
    """
    csrf = http.cookies.get(CSRF_COOKIE, "")
    return {"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf}


def worker_headers() -> dict:
    """Заголовки воркера: без сессионной cookie, поэтому CSRF не применяется."""
    return {"X-Worker-Key": settings.worker_api_key}
