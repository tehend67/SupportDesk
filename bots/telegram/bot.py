import asyncio
import logging
import os
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from client import Assignment, HelpdeskClient, fetch_assignments, send_heartbeat

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("telegram-worker")

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")
WORKER_KEY = os.getenv("WORKER_API_KEY", "dev-worker-key")
REFRESH_SECONDS = int(os.getenv("ASSIGNMENT_REFRESH_SECONDS", "20"))
HEARTBEAT_SECONDS = int(os.getenv("HEARTBEAT_SECONDS", "30"))

HELP_TEXT = (
    "Я ИИ-ассистент поддержки. Напишите свой вопрос — отвечу по базе знаний, "
    "а если понадобится, подключу оператора."
)


def build_dispatcher(assignment: Assignment, tickets: dict[int, str]) -> Dispatcher:
    client = HelpdeskClient(BACKEND_URL, assignment.channel_key)
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def on_start(message: Message) -> None:
        await message.answer(f"Здравствуйте, {message.from_user.full_name}! {HELP_TEXT}")

    @dp.message(Command("help"))
    async def on_help(message: Message) -> None:
        await message.answer(HELP_TEXT)

    @dp.message(Command("reset"))
    async def on_reset(message: Message) -> None:
        tickets.pop(message.from_user.id, None)
        await message.answer("Начал новое обращение. Чем могу помочь?")

    @dp.message()
    async def on_message(message: Message) -> None:
        if not message.text:
            await message.answer("Пожалуйста, отправьте текстовое сообщение.")
            return
        user_id = message.from_user.id
        try:
            reply = await client.send(
                content=message.text,
                customer_ref=f"tg-{assignment.workspace_id}-{user_id}",
                customer_name=message.from_user.full_name,
                ticket_ref=tickets.get(user_id),
                subject=message.text[:120],
            )
        except Exception:
            logger.exception("workspace %s: ingest failed", assignment.name)
            await message.answer("Сервис временно недоступен, попробуйте позже.")
            return
        tickets[user_id] = reply.ticket_ref
        prefix = "" if reply.ai_handled else "⚠️ Подключаю оператора.\n\n"
        await message.answer(f"{prefix}{reply.reply}\n\nОбращение: {reply.ticket_ref}")

    return dp


async def heartbeat(assignment: Assignment, ok: bool, error: str = "") -> None:
    try:
        await send_heartbeat(BACKEND_URL, WORKER_KEY, assignment.workspace_id, ok, error)
    except Exception as exc:  # noqa: BLE001 — статус не должен ронять воркер
        logger.debug("heartbeat failed for %s: %s", assignment.name, exc)


async def keepalive(assignment: Assignment, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_SECONDS)
        except asyncio.TimeoutError:
            await heartbeat(assignment, True)


async def run_assignment(assignment: Assignment, stop: asyncio.Event) -> None:
    bot = Bot(token=assignment.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    tickets: dict[int, str] = {}
    dp = build_dispatcher(assignment, tickets)
    polling = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
    beater = asyncio.create_task(keepalive(assignment, stop))
    # Даём поллингу секунду подняться: невалидный токен падает сразу
    await asyncio.sleep(1.5)
    if polling.done():
        error = "Не удалось запустить бота"
        with suppress(asyncio.CancelledError):
            error = str(polling.exception() or error)
        logger.error("workspace %s: bot failed to start: %s", assignment.name, error)
        await heartbeat(assignment, False, error)
        beater.cancel()
        await bot.session.close()
        return
    logger.info("workspace %s: bot started", assignment.name)
    await heartbeat(assignment, True)
    try:
        await stop.wait()
    finally:
        polling.cancel()
        beater.cancel()
        for task in (polling, beater):
            with suppress(asyncio.CancelledError):
                await task
        await bot.session.close()
        logger.info("workspace %s: bot stopped", assignment.name)


async def supervisor() -> None:
    running: dict[int, tuple[asyncio.Task, asyncio.Event, str]] = {}

    while True:
        try:
            assignments = await fetch_assignments(BACKEND_URL, WORKER_KEY)
        except Exception as exc:
            logger.warning("cannot load telegram assignments: %s", exc)
            await asyncio.sleep(REFRESH_SECONDS)
            continue

        seen = set()
        for assignment in assignments:
            seen.add(assignment.workspace_id)
            current = running.get(assignment.workspace_id)
            if current and current[2] == assignment.bot_token:
                continue
            if current:
                current[1].set()
            stop = asyncio.Event()
            task = asyncio.create_task(run_assignment(assignment, stop))
            running[assignment.workspace_id] = (task, stop, assignment.bot_token)

        for workspace_id in list(running):
            if workspace_id not in seen:
                task, stop, _ = running.pop(workspace_id)
                stop.set()
                logger.info("workspace %s: disconnected", workspace_id)
            elif running[workspace_id][0].done():
                # Бот упал: убираем из реестра, следующий цикл поднимет заново
                running.pop(workspace_id)
                logger.info("workspace %s: bot task finished, will retry", workspace_id)

        await asyncio.sleep(REFRESH_SECONDS)


async def main() -> None:
    logger.info("telegram worker started, backend=%s", BACKEND_URL)
    logger.warning(
        "Этот процесс дублирует встроенный воркер приложения. "
        "Если боты поднимаются вместе с run.py (TELEGRAM_WORKER_ENABLED=true), "
        "запускать bot.py не нужно — иначе Telegram вернёт конфликт опроса апдейтов."
    )
    await supervisor()


if __name__ == "__main__":
    asyncio.run(main())
