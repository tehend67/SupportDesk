"""Встроенный супервизор Telegram-ботов.

Работает в том же процессе, что и API: читает подключённые комнаты прямо из БД
и пишет heartbeat без HTTP-запросов к самому себе. Благодаря этому после вставки
токена в панели бот поднимается автоматически — запускать отдельный процесс не нужно.

Если пакет ``aiogram`` не установлен, воркер молча отключается: приложение
продолжает работать, просто Telegram-канал остаётся в статусе connecting.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..config import settings
from ..db import SessionLocal
from ..services import workspaces as workspace_service

logger = logging.getLogger("helpdesk.telegram")

REFRESH_SECONDS = 20
HEARTBEAT_SECONDS = 30

# Живые боты по комнатам: сюда доставляем ответы оператора.
_active_bots: dict[int, Any] = {}


def register_bot(workspace_id: int, bot: Any) -> None:
    _active_bots[workspace_id] = bot


def unregister_bot(workspace_id: int) -> None:
    _active_bots.pop(workspace_id, None)


def telegram_user_id(customer_ref: str) -> int | None:
    """Достаёт telegram user id из customer_ref вида ``tg-<workspace_id>-<user_id>``."""
    parts = (customer_ref or "").split("-")
    if len(parts) != 3 or parts[0] != "tg":
        return None
    try:
        return int(parts[2])
    except ValueError:
        return None


async def deliver_to_customer(workspace_id: int, customer_ref: str, text: str) -> bool:
    """Отправляет ответ оператора тому же клиенту в Telegram.

    Возвращает False, если бот не запущен или отправить не удалось:
    ответ при этом остаётся в панели.
    """
    bot = _active_bots.get(workspace_id)
    if bot is None:
        logger.info("нет живого бота для комнаты %s — ответ остался в панели", workspace_id)
        return False
    user_id = telegram_user_id(customer_ref)
    if user_id is None:
        return False
    try:
        await bot.send_message(user_id, text)
        logger.info("ответ оператора отправлен клиенту %s", user_id)
        return True
    except Exception as exc:  # noqa: BLE001 — доставка не должна ломать ответ оператора
        logger.warning("не удалось отправить ответ клиенту %s: %s", user_id, exc)
        return False

HELP_TEXT = (
    "Я ИИ-ассистент поддержки. Напишите свой вопрос — отвечу по базе знаний, "
    "а если понадобится, подключу оператора."
)


def aiogram_available() -> bool:
    try:
        import aiogram  # noqa: F401
    except Exception:
        return False
    return True


async def _report(workspace_id: int, ok: bool, error: str = "") -> None:
    """Пишем heartbeat прямо в БД, минуя HTTP."""
    try:
        async with SessionLocal() as session:
            await workspace_service.report_bot_heartbeat(session, workspace_id, ok, error)
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — статус не должен ронять воркер
        logger.debug("heartbeat не записан для %s: %s", workspace_id, exc)


def build_dispatcher(assignment: dict[str, Any], client: Any) -> Any:
    """Собирает обработчики сообщений бота."""
    from aiogram import Dispatcher
    from aiogram.filters import Command, CommandStart
    from aiogram.types import Message

    dp = Dispatcher()
    tickets: dict[int, str] = {}

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
                customer_ref=f"tg-{assignment['workspace_id']}-{user_id}",
                customer_name=message.from_user.full_name,
                ticket_ref=tickets.get(user_id),
                subject=message.text[:120],
            )
        except Exception:
            logger.exception("ws %s: не удалось принять сообщение", assignment.get("slug"))
            await message.answer("Сервис временно недоступен, попробуйте позже.")
            return
        tickets[user_id] = reply.ticket_ref
        if reply.muted:
            # Тикет ведёт оператор — ассистент молчит
            return
        prefix = "" if reply.ai_handled else "⚠️ Подключаю оператора.\n\n"
        await message.answer(f"{prefix}{reply.reply}\n\nОбращение: {reply.ticket_ref}")

    return dp


async def run_assignment(assignment: dict[str, Any], stop: asyncio.Event) -> None:
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode

    bot = Bot(
        token=assignment["bot_token"],
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    client = _LocalChannelClient(assignment["channel_key"])
    dp = build_dispatcher(assignment, client)
    polling = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
    beater = asyncio.create_task(_keepalive(assignment, stop))

    await asyncio.sleep(1.5)
    if polling.done():
        error = "Не удалось запустить бота"
        try:
            error = str(polling.exception() or error)
        except asyncio.CancelledError:
            pass
        logger.error("ws %s: бот не стартовал: %s", assignment.get("slug"), error)
        await _report(assignment["workspace_id"], False, error)
        unregister_bot(assignment["workspace_id"])
        beater.cancel()
        await bot.session.close()
        return

    logger.info("ws %s: бот @%s на связи", assignment.get("slug"), assignment.get("bot_username", "?"))
    register_bot(assignment["workspace_id"], bot)
    await _report(assignment["workspace_id"], True)
    try:
        await stop.wait()
    finally:
        unregister_bot(assignment["workspace_id"])
        polling.cancel()
        beater.cancel()
        for task in (polling, beater):
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        await bot.session.close()
        logger.info("ws %s: бот остановлен", assignment.get("slug"))


async def _keepalive(assignment: dict[str, Any], stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_SECONDS)
        except asyncio.TimeoutError:
            await _report(assignment["workspace_id"], True)


class _LocalChannelClient:
    """Отправляет сообщения в тот же AI-конвейер, что и web-виджет, без HTTP."""

    def __init__(self, channel_key: str) -> None:
        self.channel_key = channel_key

    async def send(
        self,
        content: str,
        customer_ref: str,
        customer_name: str = "",
        ticket_ref: str | None = None,
        subject: str = "",
    ) -> Any:
        from ..services.conversation import handle_incoming_message

        async with SessionLocal() as session:
            workspace = await self._workspace(session)
            if workspace is None:
                raise RuntimeError("комната не найдена по ключу канала")
            result = await handle_incoming_message(
                session,
                workspace,
                channel="telegram",
                content=content,
                customer_ref=customer_ref,
                customer_name=customer_name,
                ticket_ref=ticket_ref or None,
                subject=subject or content[:120],
                metadata={},
            )
            await session.commit()
            return _LocalReply(
                ticket_ref=result.ticket.public_id,
                reply=result.reply.content,
                escalated=result.escalated,
                ai_handled=result.ai_handled,
                muted=result.muted,
            )

    async def _workspace(self, session: Any) -> Any:
        from ..services.workspaces import get_by_channel_key

        return await get_by_channel_key(session, self.channel_key)


class _LocalReply:
    """Ответ ИИ в том же виде, что ждёт обработчик бота."""

    def __init__(
        self,
        ticket_ref: str,
        reply: str,
        escalated: bool,
        ai_handled: bool,
        muted: bool = False,
    ) -> None:
        self.ticket_ref = ticket_ref
        self.reply = reply
        self.escalated = escalated
        self.ai_handled = ai_handled
        self.muted = muted


async def supervisor() -> None:
    """Поднимает по одному боту на каждую подключённую комнату."""
    running: dict[int, tuple[asyncio.Task, asyncio.Event, str]] = {}

    while True:
        try:
            async with SessionLocal() as session:
                assignments = list(await workspace_service.telegram_assignments(session))
        except Exception as exc:  # noqa: BLE001
            logger.warning("не удалось получить список комнат с ботами: %s", exc)
            await asyncio.sleep(REFRESH_SECONDS)
            continue

        seen = set()
        for assignment in assignments:
            seen.add(assignment["workspace_id"])
            current = running.get(assignment["workspace_id"])
            if current and current[2] == assignment["bot_token"]:
                continue
            if current:
                current[1].set()
            stop = asyncio.Event()
            task = asyncio.create_task(run_assignment(assignment, stop))
            running[assignment["workspace_id"]] = (task, stop, assignment["bot_token"])

        for workspace_id in list(running):
            if workspace_id not in seen:
                _, stop, _ = running.pop(workspace_id)
                stop.set()
                logger.info("ws %s: отключён в панели", workspace_id)
            elif running[workspace_id][0].done():
                running.pop(workspace_id)
                logger.info("ws %s: бот завершился, будет перезапуск", workspace_id)

        await asyncio.sleep(REFRESH_SECONDS)