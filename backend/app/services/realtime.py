"""Рассылка событий клиентам по открытым WebSocket-соединениям.

Виджет на сайте держит постоянное соединение, поэтому ответ оператора можно
доставить клиенту сразу, без перезагрузки страницы.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("helpdesk.realtime")

# (workspace_id, customer_ref) -> набор открытых сокетов
_connections: dict[tuple[int, str], set[WebSocket]] = {}


def register(workspace_id: int, customer_ref: str, websocket: WebSocket) -> None:
    if not customer_ref:
        return
    _connections.setdefault((workspace_id, customer_ref), set()).add(websocket)


def unregister(workspace_id: int, customer_ref: str, websocket: WebSocket) -> None:
    sockets = _connections.get((workspace_id, customer_ref))
    if not sockets:
        return
    sockets.discard(websocket)
    if not sockets:
        _connections.pop((workspace_id, customer_ref), None)


async def send_to_customer(workspace_id: int, customer_ref: str, payload: dict[str, Any]) -> bool:
    """Отправляет событие клиенту. False — если его сейчас нет на сайте."""
    sockets = list(_connections.get((workspace_id, customer_ref), ()))
    if not sockets:
        return False
    delivered = False
    for socket in sockets:
        try:
            await socket.send_json(payload)
            delivered = True
        except Exception as exc:  # noqa: BLE001 — клиент мог просто закрыть вкладку
            logger.debug("не удалось отправить событие клиенту: %s", exc)
    return delivered


async def deliver_operator_reply(workspace_id: int, customer_ref: str, text: str, ticket_ref: str) -> bool:
    """Ответ оператора уходит клиенту в тот канал, откуда он писал."""
    channel = "telegram" if customer_ref.startswith("tg-") else "web"
    if channel == "telegram":
        # В Telegram доставляет воркер бота: клиент мог быть офлайн,
        # но ответ уже отправлен через Telegram API.
        from ..bots.telegram_worker import deliver_to_customer

        return await deliver_to_customer(workspace_id, customer_ref, text)
    return await send_to_customer(
        workspace_id,
        customer_ref,
        {"type": "operator_reply", "reply": text, "ticket_ref": ticket_ref},
    )


def connection_count() -> int:
    """Сколько сейчас клиентов на сайте — пригодится для метрик."""
    return sum(len(sockets) for sockets in _connections.values())


__all__ = [
    "register",
    "unregister",
    "send_to_customer",
    "deliver_operator_reply",
    "connection_count",
]