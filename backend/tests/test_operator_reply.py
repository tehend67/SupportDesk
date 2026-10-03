"""Тесты: ответ оператора, доставка в Telegram и обучение базы знаний."""
import asyncio
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.conftest import auth_headers, worker_headers

from app import seed
from app.bots import telegram_worker
from app.main import app, init_db
from app.services.knowledge import OPERATOR_SOURCE, learn_from_operator_reply


@pytest.fixture(scope="module", autouse=True)
def prepare_database():
    async def setup():
        await init_db()
        await seed.run_seed()

    asyncio.run(setup())


def client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def make_workspace(http: AsyncClient):
    email = f"op-{uuid4().hex[:8]}@example.com"
    response = await http.post(
        "/api/auth/register",
        json={"email": email, "password": "TestPass-2024-Secure", "full_name": "Оператор"},
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    headers = auth_headers(http, token)
    listing = await http.get("/api/workspaces", headers=headers)
    workspace = listing.json()[0]
    return headers | {"X-Workspace-Id": str(workspace["id"])}, workspace


def test_telegram_user_id_parsing():
    assert telegram_worker.telegram_user_id("tg-3-777123") == 777123
    assert telegram_worker.telegram_user_id("web-1") is None
    assert telegram_worker.telegram_user_id("") is None
    assert telegram_worker.telegram_user_id("tg-3-abc") is None


def test_operator_reply_reaches_customer_in_telegram():
    """Главный регресс: ответ оператора должен уйти клиенту, а не остаться в панели."""
    sent: list[tuple[int, str]] = []

    class FakeBot:
        async def send_message(self, chat_id: int, text: str, **kwargs):
            sent.append((chat_id, text))
            return {"message_id": 1}

    async def scenario():
        async with client() as http:
            headers, workspace = await make_workspace(http)
            await http.post(
                "/api/channels/telegram/heartbeat",
                headers=worker_headers(),
                json={"workspace_id": workspace["id"], "ok": True},
            )

            # Клиент пишет через Telegram-канал комнаты
            current = await http.get("/api/workspaces/current", headers=headers)
            channel_key = current.json()["channel_key"]
            ingest = await http.post(
                "/api/ingest/message",
                headers={"X-Workspace-Key": channel_key},
                json={
                    "channel": "telegram",
                    "content": "Что делать, если посылка не приходит?",
                    "customer_ref": f"tg-{workspace['id']}-424242",
                    "customer_name": "Клиент",
                },
            )
            assert ingest.status_code == 200, ingest.text
            ticket_id = ingest.json()["ticket_id"]

            telegram_worker.register_bot(workspace["id"], FakeBot())
            try:
                reply = await http.post(
                    f"/api/tickets/{ticket_id}/messages",
                    headers=headers,
                    json={"sender": "agent", "content": "Проверим трек-номер и вернём деньги."},
                )
            finally:
                telegram_worker.unregister_bot(workspace["id"])

            assert reply.status_code == 201, reply.text
            assert sent == [(424242, "Проверим трек-номер и вернём деньги.")]
            assert reply.json()["meta"]["delivered"] is True

    asyncio.run(scenario())


def test_operator_reply_marked_not_delivered_without_bot():
    """Без живого бота ответ сохраняется, но честно помечается недоставленным."""

    async def scenario():
        async with client() as http:
            headers, workspace = await make_workspace(http)
            current = await http.get("/api/workspaces/current", headers=headers)
            channel_key = current.json()["channel_key"]
            ingest = await http.post(
                "/api/ingest/message",
                headers={"X-Workspace-Key": channel_key},
                json={
                    "channel": "telegram",
                    "content": "Когда придёт заказ?",
                    "customer_ref": f"tg-{workspace['id']}-555",
                },
            )
            ticket_id = ingest.json()["ticket_id"]
            reply = await http.post(
                f"/api/tickets/{ticket_id}/messages",
                headers=headers,
                json={"sender": "agent", "content": "Завтра."},
            )
            assert reply.status_code == 201
            assert reply.json()["meta"]["delivered"] is False

    asyncio.run(scenario())


def test_ai_stays_silent_after_operator_takes_ticket():
    """После живого ответа ИИ не должен отвечать тому же клиенту."""

    async def scenario():
        async with client() as http:
            headers, workspace = await make_workspace(http)
            current = await http.get("/api/workspaces/current", headers=headers)
            channel_key = current.json()["channel_key"]
            payload = {
                "channel": "telegram",
                "content": "Посылка не пришла",
                "customer_ref": f"tg-{workspace['id']}-999",
            }
            first = await http.post("/api/ingest/message", headers={"X-Workspace-Key": channel_key}, json=payload)
            ticket_id = first.json()["ticket_id"]
            await http.post(
                f"/api/tickets/{ticket_id}/messages",
                headers=headers,
                json={"sender": "agent", "content": "Мы уже взяли в работу."},
            )
            second = await http.post("/api/ingest/message", headers={"X-Workspace-Key": channel_key}, json=payload)
            assert second.status_code == 200
            detail = await http.get(f"/api/tickets/{ticket_id}", headers=headers)
            texts = [m["content"] for m in detail.json()["messages"] if m["sender"] == "assistant"]
            assert len(texts) == 1, "ИИ не должен отвечать повторно, когда тикет у оператора"

    asyncio.run(scenario())


def test_knowledge_learns_from_operator_answers():
    """Ответ оператора попадает в базу знаний и потом находится поиском."""
    from app.db import SessionLocal
    from app.services.knowledge import search_knowledge

    async def scenario():
        async with client() as http:
            headers, workspace = await make_workspace(http)
            current = await http.get("/api/workspaces/current", headers=headers)
            channel_key = current.json()["channel_key"]
            ingest = await http.post(
                "/api/ingest/message",
                headers={"X-Workspace-Key": channel_key},
                json={
                    "channel": "telegram",
                    "content": "Как оформить возврат посылки?",
                    "customer_ref": f"tg-{workspace['id']}-1010",
                },
            )
            ticket_id = ingest.json()["ticket_id"]
            await http.post(
                f"/api/tickets/{ticket_id}/messages",
                headers=headers,
                json={
                    "sender": "agent",
                    "content": "Возврат оформляется в личном кабинете в разделе «Мои заказы».",
                },
            )

            docs = await http.get("/api/knowledge/documents", headers=headers)
            learned = [d for d in docs.json() if d["source"].startswith(OPERATOR_SOURCE)]
            assert learned, "ответ оператора должен создать документ в базе знаний"

            # Бейдж «в базу знаний» рисуется из meta сообщения — он должен
            # сохраниться в БД, а не потеряться по дороге.
            detail = await http.get(f"/api/tickets/{ticket_id}", headers=headers)
            reply = [m for m in detail.json()["messages"] if m["sender"] == "agent"][-1]
            assert reply["meta"]["learned"]["learned"] is True
            assert reply["meta"]["learned"]["document_id"] == learned[0]["id"]

            async with SessionLocal() as session:
                hits = await search_knowledge(session, workspace["id"], "как оформить возврат", top_k=3)
                assert any("личном кабинете" in hit["content"] for hit in hits), hits

    asyncio.run(scenario())


def test_repeated_topic_updates_existing_document():
    """Повторный ответ на ту же тему не плодит дубли, а обновляет документ."""

    async def scenario():
        async with client() as http:
            headers, workspace = await make_workspace(http)
            current = await http.get("/api/workspaces/current", headers=headers)
            channel_key = current.json()["channel_key"]

            for index, customer_ref in enumerate(("111", "222")):
                ingest = await http.post(
                    "/api/ingest/message",
                    headers={"X-Workspace-Key": channel_key},
                    json={
                        "channel": "telegram",
                        "content": "Сколько стоит возврат посылки?",
                        "customer_ref": f"tg-{workspace['id']}-{customer_ref}",
                    },
                )
                ticket_id = ingest.json()["ticket_id"]
                await http.post(
                    f"/api/tickets/{ticket_id}/messages",
                    headers=headers,
                    json={
                        "sender": "agent",
                        "content": f"Возврат бесплатный, вернём деньги за {index + 1} дня.",
                    },
                )

            docs = await http.get("/api/knowledge/documents", headers=headers)
            learned = [d for d in docs.json() if d["source"].startswith(OPERATOR_SOURCE)]
            assert len(learned) == 1, f"ожидался один документ по теме, пришло {len(learned)}"

            # Повторный ответ обязан не только не создать дубль, но и обновить содержимое:
            # именно на этом пути раньше всплывала ленивая загрузка relationship.
            assert learned[0]["status"] == "ready"
            from app.db import SessionLocal
            from app.models import KnowledgeChunk

            async with SessionLocal() as session:
                chunks = (
                    await session.execute(
                        select(KnowledgeChunk).where(
                            KnowledgeChunk.document_id == learned[0]["id"]
                        )
                    )
                ).scalars().all()
            text = " ".join(c.content for c in chunks)
            assert "2 дня" in text and "1 дня" not in text, text

    asyncio.run(scenario())


def test_learn_ignores_empty_pairs():
    from app.db import SessionLocal

    async def scenario():
        async with SessionLocal() as session:
            assert (await learn_from_operator_reply(session, 1, "", "ответ"))["learned"] is False
            assert (await learn_from_operator_reply(session, 1, "вопрос", "  "))["learned"] is False

    asyncio.run(scenario())

def test_operator_reply_reaches_open_web_widget():
    """Ответ оператора должен прийти в открытую вкладку виджета по WebSocket."""

    class FakeSocket:
        def __init__(self):
            self.sent = []

        async def send_json(self, payload):
            self.sent.append(payload)

        async def receive_text(self):
            await asyncio.sleep(3600)

        async def close(self):
            pass

    async def scenario():
        socket = FakeSocket()
        from app.services import realtime

        realtime.register(1, "web-abc123", socket)
        try:
            delivered = await realtime.deliver_operator_reply(
                1, "web-abc123", "Вернём деньги за 3 дня.", "TCK-TEST01"
            )
            assert delivered is True
            assert socket.sent[-1]["type"] == "operator_reply"
            assert socket.sent[-1]["reply"] == "Вернём деньги за 3 дня."
        finally:
            realtime.unregister(1, "web-abc123", socket)

        # Клиента нет на сайте — это не ошибка, просто сообщение некуда слать
        assert await realtime.deliver_operator_reply(1, "web-closed", "тест", "TCK-1") is False

    asyncio.run(scenario())
