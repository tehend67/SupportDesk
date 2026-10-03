import asyncio
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import auth_headers

from app import seed
from app.config import settings
from app.main import app, init_db


def client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module", autouse=True)
def prepare_database():
    async def setup():
        await init_db()
        await seed.run_seed()

    run(setup())


async def login(http: AsyncClient) -> str:
    response = await http.post(
        "/api/auth/login",
        json={"email": settings.admin_email, "password": settings.admin_password},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_health_reports_database_up():
    async def scenario():
        async with client() as http:
            response = await http.get("/api/health")
            assert response.status_code == 200
            assert response.json()["database"] == "up"

    run(scenario())


def test_root_serves_dashboard_or_meta():
    async def scenario():
        async with client() as http:
            response = await http.get("/healthz")
            assert response.status_code == 200
            assert response.json()["app"] == settings.app_name

    run(scenario())


def test_ingest_creates_ticket_and_ai_reply():
    async def scenario():
        async with client() as http:
            response = await http.post(
                "/api/ingest/message",
                headers={"X-Channel-Key": settings.channel_api_key},
                json={
                    "channel": "telegram",
                    "content": "Сколько стоит тариф Старт и как оплатить?",
                    "customer_ref": "integration-1",
                    "customer_name": "Тестовый Клиент",
                },
            )
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["public_id"].startswith("TCK-")
            assert data["reply"]
            assert data["status"] in {"pending", "escalated", "open"}
            assert data["message_id"] is not None
            assert data["sources"]
            return data["ticket_id"]

    run(scenario())


def test_ingest_reuses_open_ticket_per_customer():
    async def scenario():
        async with client() as http:
            headers = {"X-Channel-Key": settings.channel_api_key}
            first = await http.post(
                "/api/ingest/message",
                headers=headers,
                json={"channel": "web", "content": "Привет", "customer_ref": "same-guest"},
            )
            second = await http.post(
                "/api/ingest/message",
                headers=headers,
                json={"channel": "web", "content": "Ещё вопрос", "customer_ref": "same-guest"},
            )
            assert first.status_code == 200 and second.status_code == 200
            assert first.json()["public_id"] == second.json()["public_id"]

    run(scenario())


def test_agent_escalates_negative_message():
    async def scenario():
        async with client() as http:
            response = await http.post(
                "/api/ingest/message",
                headers={"X-Channel-Key": settings.channel_api_key},
                json={
                    "channel": "telegram",
                    "content": "Ничего не работает, это ужас, позовите человека!",
                    "customer_ref": "angry-1",
                },
            )
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["escalated"] is True
            assert data["status"] == "escalated"
            assert data["ai_handled"] is False

    run(scenario())


def test_ingest_requires_channel_key():
    async def scenario():
        async with client() as http:
            response = await http.post(
                "/api/ingest/message",
                headers={"X-Channel-Key": "wrong"},
                json={"channel": "web", "content": "hi", "customer_ref": "bad"},
            )
            assert response.status_code == 401

    run(scenario())


def test_auth_and_ticket_lifecycle():
    async def scenario():
        async with client() as http:
            unauthorized = await http.get("/api/tickets")
            assert unauthorized.status_code == 401

            token = await login(http)
            headers = auth_headers(http, token)

            me = await http.get("/api/auth/me", headers=headers)
            assert me.status_code == 200
            assert me.json()["role"] == "admin"

            listing = await http.get("/api/tickets", headers=headers)
            assert listing.status_code == 200
            payload = listing.json()
            assert payload["total"] >= 1

            ticket_id = payload["items"][0]["id"]
            detail = await http.get(f"/api/tickets/{ticket_id}", headers=headers)
            assert detail.status_code == 200
            assert detail.json()["messages"]

            reply = await http.post(
                f"/api/tickets/{ticket_id}/messages",
                headers=headers,
                json={"content": "Оператор на связи.", "sender": "agent"},
            )
            assert reply.status_code == 201
            assert reply.json()["sender"] == "agent"

            patched = await http.patch(
                f"/api/tickets/{ticket_id}", headers=headers, json={"status": "resolved"}
            )
            assert patched.status_code == 200
            assert patched.json()["status"] == "resolved"
            assert patched.json()["resolved_at"] is not None

    run(scenario())


def test_registration_creates_account_and_signs_in():
    async def scenario():
        email = f"agent-{uuid4().hex[:8]}@example.com"
        async with client() as http:
            created = await http.post(
                "/api/auth/register",
                json={"email": email, "password": "TestPass-2024-Secure", "full_name": "Новый Агент"},
            )
            assert created.status_code == 201, created.text
            token = created.json()["access_token"]

            me = await http.get("/api/auth/me", headers=auth_headers(http, token))
            assert me.status_code == 200
            assert me.json()["email"] == email
            assert me.json()["role"] == "agent"
            assert me.json()["full_name"] == "Новый Агент"

            duplicate = await http.post(
                "/api/auth/register", json={"email": email, "password": "TestPass-2024-Secure"}
            )
            assert duplicate.status_code == 409

            weak = await http.post(
                "/api/auth/register", json={"email": "weak@example.com", "password": "short"}
            )
            assert weak.status_code == 422

            malformed = await http.post(
                "/api/auth/register", json={"email": "not-an-email", "password": "TestPass-2024-Secure"}
            )
            assert malformed.status_code == 422

            login = await http.post(
                "/api/auth/login", json={"email": email, "password": "TestPass-2024-Secure"}
            )
            assert login.status_code == 200

    run(scenario())


def test_knowledge_search_returns_relevant_document():
    async def scenario():
        async with client() as http:
            token = await login(http)
            headers = auth_headers(http, token)

            search = await http.post(
                "/api/knowledge/search",
                headers=headers,
                json={"query": "как вернуть товар и получить деньги обратно"},
            )
            assert search.status_code == 200
            hits = search.json()["hits"]
            assert hits
            assert any("возврат" in hit["content"].lower() for hit in hits)

    run(scenario())


def test_knowledge_document_upload_and_delete():
    async def scenario():
        async with client() as http:
            token = await login(http)
            headers = auth_headers(http, token)

            created = await http.post(
                "/api/knowledge/documents",
                headers=headers,
                json={
                    "title": "Интеграционный документ",
                    "content": "Промокод HABR10 даёт скидку 10 процентов на первый заказ.",
                    "source": "test",
                },
            )
            assert created.status_code == 201, created.text
            document = created.json()
            assert document["status"] == "ready"
            assert document["chunk_count"] >= 1

            removed = await http.delete(
                f"/api/knowledge/documents/{document['id']}", headers=headers
            )
            assert removed.status_code == 204

    run(scenario())


def test_analytics_overview_and_timeseries():
    async def scenario():
        async with client() as http:
            token = await login(http)
            headers = auth_headers(http, token)

            overview = await http.get("/api/analytics/overview", headers=headers)
            assert overview.status_code == 200
            body = overview.json()
            assert body["total_tickets"] >= 1
            assert body["chunks_total"] >= 1
            assert body["messages_total"] >= 1
            assert body["avg_first_response_minutes"] >= 0
            assert body["by_channel"]

            timeseries = await http.get("/api/analytics/timeseries?days=7", headers=headers)
            assert timeseries.status_code == 200
            points = timeseries.json()
            assert len(points) == 7
            assert sum(point["created"] for point in points) >= 1

    run(scenario())


def test_websocket_chat_replies():
    from fastapi.testclient import TestClient

    from app.db import SessionLocal
    from app.models import Workspace
    from sqlalchemy import select

    async def widget_key() -> str:
        async with SessionLocal() as session:
            workspace = (
                await session.execute(select(Workspace).order_by(Workspace.id).limit(1))
            ).scalar_one()
            return workspace.widget_key

    key = asyncio.run(widget_key())

    with TestClient(app) as test_client:
        with test_client.websocket_connect(f"/api/ws/chat?key={key}") as websocket:
            websocket.send_json({"content": "Здравствуйте, как отменить подписку?", "customer_name": "WS"})
            payload = websocket.receive_json()
            assert payload["type"] == "reply"
            assert payload["ticket_ref"].startswith("TCK-")
            assert payload["reply"]

    with TestClient(app) as test_client:
        with test_client.websocket_connect("/api/ws/chat?key=wk_подделка") as websocket:
            websocket.send_json({"content": "Привет"})
            assert websocket.receive_json()["type"] == "error"
