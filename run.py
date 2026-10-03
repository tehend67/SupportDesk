#!/usr/bin/env python
"""Запуск AI Support Desk без Docker: uvicorn поднимает API и отдаёт веб-панель.

По умолчанию используется SQLite, поэтому ничего дополнительно ставить не нужно.
Чтобы подключиться к PostgreSQL, задайте DATABASE_URL в .env или в окружении.
"""
import argparse
import asyncio
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("PYTHONPATH", str(BACKEND))

PORT_SCAN_LIMIT = 50


def describe_database(url: str) -> str:
    scheme = url.split("://", 1)[0]
    if scheme.startswith("sqlite"):
        path = url.split("///", 1)[-1] if "///" in url else url
        return f"SQLite ({path or ':memory:'})"
    if scheme.startswith("postgresql"):
        return "PostgreSQL"
    return scheme


def probe(host: str, port: int) -> tuple[bool, str]:
    # SO_REUSEADDR намеренно не ставим: на Windows он даёт bind на занятый порт,
    # и проверка перестаёт замечать занятые порты.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return True, ""
        except OSError as exc:
            return False, f"{exc.errno}: {exc.strerror}"


def pick_port(host: str, preferred: int) -> tuple[int, str]:
    writable_host = host if host not in {"0.0.0.0", "::"} else "127.0.0.1"
    ok, reason = probe(writable_host, preferred)
    if ok:
        return preferred, ""
    for candidate in range(preferred + 1, preferred + 1 + PORT_SCAN_LIMIT):
        if probe(writable_host, candidate)[0]:
            return candidate, f"порт {preferred} недоступен ({reason})"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((writable_host, 0))
        return int(sock.getsockname()[1]), f"порт {preferred} и соседние недоступны ({reason})"


def missing_dependency(exc: ModuleNotFoundError) -> None:
    name = exc.name or "неизвестный модуль"
    print(f"\n[!] Не хватает зависимости: {name}", flush=True)
    print("    Установите зависимости проекта:", flush=True)
    print("      py -3.12 -m pip install -r backend/requirements.txt", flush=True)
    print("    Либо, если нужен только режим SQLite (без PostgreSQL и pgvector):", flush=True)
    print(
        '      py -3.12 -m pip install fastapi uvicorn "sqlalchemy[asyncio]" pydantic '
        "pydantic-settings PyJWT bcrypt httpx aiosqlite python-multipart",
        flush=True,
    )
    sys.exit(1)


async def preflight() -> None:
    from sqlalchemy import text

    from app.db import engine

    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    await engine.dispose()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI Support Desk — локальный запуск")
    parser.add_argument("--host", default=None, help="адрес (по умолчанию 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="порт (по умолчанию 8000)")
    parser.add_argument("--no-reload", action="store_true", help="отключить автоперезагрузку")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    try:
        import uvicorn

        from app.ai.llm import get_llm_client
        from app.config import settings
    except ModuleNotFoundError as exc:
        missing_dependency(exc)
        return

    args = parse_args(argv)
    host = (args.host or os.getenv("HOST", "") or "127.0.0.1").strip()
    env_port = os.getenv("PORT", "").strip()
    preferred = (
        args.port
        if args.port
        else (int(env_port) if env_port.isdigit() and int(env_port) > 0 else 8000)
    )
    reload_enabled = not args.no_reload and os.getenv("RELOAD", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    if settings.database_url.startswith("sqlite"):
        db_path = settings.database_url.split("///", 1)[-1]
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    try:
        asyncio.run(preflight())
    except ModuleNotFoundError as exc:
        missing_dependency(exc)
        return
    except Exception as exc:
        print(f"\n[!] Не удалось подключиться к базе данных: {type(exc).__name__}: {exc}")
        if settings.database_url.startswith("postgresql"):
            print("[!] Проверьте DATABASE_URL и что PostgreSQL с расширением pgvector запущен,")
            print("    либо не задавайте DATABASE_URL — тогда приложение поднимется на SQLite.")
        else:
            print("[!] Проверьте значение DATABASE_URL.")
        sys.exit(1)

    port, port_problem = pick_port(host, preferred)

    llm = get_llm_client()
    mode = f"{llm.model} (LLM_API_KEY задан)" if llm.configured else "офлайн (без ключей)"

    bot_line = (
        "  Telegram-боты: встроенный воркер включён — вставьте токен в панели"
        if settings.telegram_worker_enabled
        else "  Telegram-боты: встроенный воркер выключен (TELEGRAM_WORKER_ENABLED=false)"
    )

    lines = [
        "=" * 62,
        f"  {settings.app_name}",
        "=" * 62,
        f"  база данных  : {describe_database(settings.database_url)}",
        f"  LLM-режим    : {mode}",
        f"  панель       : http://{host}:{port}/",
        f"  API + Swagger: http://{host}:{port}/docs",
        f"  вход         : {settings.admin_email} / {settings.admin_password}",
        f"  {bot_line}",
        "  остановить   : Ctrl+C",
        "=" * 62,
    ]
    print("\n".join(lines), flush=True)

    if port_problem:
        print(f"[i] {port_problem}, выбран порт {port}.", flush=True)
        if os.name == "nt":
            print(
                "[i] Windows часто держит порты в резерве (Hyper-V/WSL/антивирус).\n"
                "    Посмотреть диапазоны: netsh interface ipv4 show excludedportrange protocol=tcp\n"
                "    Задать порт вручную:  python run.py --port 8080",
                flush=True,
            )

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload_enabled,
        reload_dirs=[str(BACKEND)] if reload_enabled else None,
    )


if __name__ == "__main__":
    main()
