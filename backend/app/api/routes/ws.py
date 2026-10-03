import json
import logging
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from ...config import settings
from ...constants import Channel
from ...db import SessionLocal
from ...models import Workspace
from ...ratelimit import limiter
from ...security import constant_time_equals
from ...services import apikeys as key_service
from ...services import workspaces as workspace_service
from ...services.conversation import handle_incoming_message
from ...services import realtime

logger = logging.getLogger("helpdesk.ws")
router = APIRouter()

MAX_MESSAGE_CHARS = 4000
RATE_LIMIT_PER_MINUTE = 20


async def _resolve_workspace(session, key: str) -> Workspace | None:
    """Комната по ключу виджета, канала или именованному ключу."""
    if not key:
        return None
    workspace, record = await key_service.workspace_for_key(session, key)
    if workspace is not None and key_service.is_key_valid(record, "widget"):
        await session.commit()
        return workspace
    workspace = await workspace_service.get_by_widget_key(session, key)
    if workspace is not None:
        return workspace
    workspace = await workspace_service.get_by_channel_key(session, key)
    if workspace is not None:
        return workspace
    if constant_time_equals(key, settings.channel_api_key):
        return (await session.execute(select(Workspace).order_by(Workspace.id).limit(1))).scalar_one_or_none()
    return None


@router.websocket("/chat")
async def chat_socket(websocket: WebSocket) -> None:
    key = websocket.query_params.get("key", "")
    state: dict[str, str] = {}
    peer = websocket.client.host if websocket.client else "unknown"
    ok_ip, _, retry_ip = await limiter.check(f"ws-ip:{key[:12]}", peer, RATE_LIMIT_PER_MINUTE * 3)
    if not ok_ip:
        await websocket.close(code=4429, reason=f"Слишком много запросов, пауза {retry_ip} сек")
        return
    ok_conn, _, retry_conn = await limiter.check(f"ws-conn:{key[:12]}", f"{peer}:{id(websocket)}", RATE_LIMIT_PER_MINUTE)
    if not ok_conn:
        await websocket.close(code=4429, reason=f"Слишком много запросов, пауза {retry_conn} сек")
        return
    await websocket.accept()
    try:
        while True:
            raw = await websocket.receive_text()
            ok_msg, _, retry_msg = await limiter.check(f"ws-msg:{key[:12]}", peer, RATE_LIMIT_PER_MINUTE * 3)
            if not ok_msg:
                await websocket.send_json(
                    {"type": "error", "detail": f"Слишком много сообщений, пауза {retry_msg} сек"}
                )
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "detail": "Некорректный JSON"})
                continue

            content = (payload.get("content") or "").strip()
            if not content:
                await websocket.send_json({"type": "error", "detail": "Пустое сообщение"})
                continue
            if len(content) > MAX_MESSAGE_CHARS:
                await websocket.send_json(
                    {"type": "error", "detail": f"Сообщение длиннее {MAX_MESSAGE_CHARS} символов"}
                )
                continue

            customer_ref = payload.get("customer_ref") or state.get("customer_ref")
            if not customer_ref:
                customer_ref = f"web-{uuid4().hex[:8]}"
                state["customer_ref"] = customer_ref
            ticket_ref = payload.get("ticket_ref") or state.get("ticket_ref")
            workspace_key = payload.get("workspace_key") or key

            try:
                async with SessionLocal() as session:
                    workspace = await _resolve_workspace(session, workspace_key)
                    if workspace is None:
                        await websocket.send_json(
                            {"type": "error", "detail": "Неверный ключ виджета"}
                        )
                        await session.rollback()
                        continue
                    result = await handle_incoming_message(
                        session,
                        workspace,
                        channel=Channel.WEB,
                        content=content,
                        customer_ref=customer_ref,
                        customer_name=payload.get("customer_name", "Веб-гость"),
                        ticket_ref=ticket_ref,
                        subject=payload.get("subject", ""),
                        metadata={"source": "web-widget", "user_agent": str(payload.get("user_agent", ""))},
                    )
                    await session.commit()
                    state["ticket_ref"] = result.ticket.public_id
                    # сокет запоминаем, чтобы ответ оператора дошёл до этой вкладки
                    state["workspace_id"] = workspace.id
                    realtime.register(workspace.id, customer_ref, websocket)
                    await websocket.send_json(
                        {
                            "type": "reply",
                            "customer_ref": customer_ref,
                            "ticket_ref": result.ticket.public_id,
                            "ticket_id": result.ticket.id,
                            "workspace": workspace.slug,
                            "status": result.ticket.status,
                            "reply": result.reply.content,
                            "escalated": result.escalated,
                            "ai_handled": result.ai_handled,
                            "sources": result.hits,
                        }
                    )
            except WebSocketDisconnect:
                raise
            except Exception as exc:
                logger.exception("ws handling failed")
                await websocket.send_json({"type": "error", "detail": str(exc)[:200]})
    except WebSocketDisconnect:
        return
    finally:
        workspace_id = state.get("workspace_id")
        customer_ref = state.get("customer_ref", "")
        if workspace_id and customer_ref:
            realtime.unregister(workspace_id, customer_ref, websocket)
