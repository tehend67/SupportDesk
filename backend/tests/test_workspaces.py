import asyncio
from contextlib import contextmanager
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import auth_headers, worker_headers

from app import seed
from app.config import settings
from app.main import app, init_db
from app.services import workspaces as workspace_service

GOOD_TOKEN = "123456789:test-demo-token"


@contextmanager
def fake_getme(username, name: str = "", error: str = ""):
    """Подменяет сетевую проверку токена, чтобы тесты не ходили в Telegram."""

    async def fake_verify(_: str):
        if username is None:
            return None, "", error
        return username, name, ""

    original = workspace_service.verify_bot_token
    workspace_service.verify_bot_token = fake_verify
    try:
        yield
    finally:
        workspace_service.verify_bot_token = original


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


async def register(http: AsyncClient, full_name: str = "Тестовый Владелец"):
    email = f"owner-{uuid4().hex[:8]}@example.com"
    response = await http.post(
        "/api/auth/register",
        json={"email": email, "password": "TestPass-2024-Secure", "full_name": full_name},
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    headers = auth_headers(http, token)
    listing = await http.get("/api/workspaces", headers=headers)
    assert listing.status_code == 200, listing.text
    workspaces = listing.json()
    assert len(workspaces) == 1
    return email, headers, workspaces[0]


def test_register_creates_its_own_workspace():
    async def scenario():
        async with client() as http:
            _, headers, workspace = await register(http)
            assert workspace["role"] == "owner"
            current = await http.get("/api/workspaces/current", headers=headers)
            assert current.status_code == 200
            body = current.json()
            assert body["widget_key"].startswith("wk_")
            assert body["channel_key"].startswith("ck_")
            assert "widget_snippet" in body and body["widget_snippet"]
            assert body["api_base_url"].endswith("/api")

    run(scenario())


def test_workspaces_are_isolated_between_tenants():
    async def scenario():
        async with client() as http:
            _, headers_a, workspace_a = await register(http)
            _, headers_b, workspace_b = await register(http)

            created = await http.post(
                "/api/tickets",
                headers={**headers_a, "X-Workspace-Id": str(workspace_a["id"])},
                json={"subject": "Секрет компании A", "message": "Привет", "channel": "api"},
            )
            assert created.status_code == 201, created.text

            list_a = await http.get(
                "/api/tickets", headers={**headers_a, "X-Workspace-Id": str(workspace_a["id"])}
            )
            assert list_a.json()["total"] >= 1

            list_b = await http.get(
                "/api/tickets", headers={**headers_b, "X-Workspace-Id": str(workspace_b["id"])}
            )
            assert list_b.json()["total"] == 0

            forbidden = await http.get(
                "/api/tickets", headers={**headers_b, "X-Workspace-Id": str(workspace_a["id"])}
            )
            assert forbidden.status_code == 403

    run(scenario())


def test_channel_key_routes_ingest_to_own_workspace():
    async def scenario():
        async with client() as http:
            _, headers, workspace = await register(http)
            current = await http.get("/api/workspaces/current", headers=headers)
            channel_key = current.json()["channel_key"]

            ingested = await http.post(
                "/api/ingest/message",
                headers={"X-Workspace-Key": channel_key},
                json={
                    "channel": "telegram",
                    "content": "Сколько стоит тариф Старт?",
                    "customer_ref": f"tg-{uuid4().hex[:6]}",
                },
            )
            assert ingested.status_code == 200, ingested.text
            public_id = ingested.json()["public_id"]

            listing = await http.get(
                "/api/tickets", headers={**headers, "X-Workspace-Id": str(workspace["id"])}
            )
            assert any(item["public_id"] == public_id for item in listing.json()["items"])

            bad = await http.post(
                "/api/ingest/message",
                headers={"X-Workspace-Key": "ck_not-a-real-key"},
                json={"channel": "api", "content": "hi", "customer_ref": "x"},
            )
            assert bad.status_code == 401

    run(scenario())


def test_owner_can_manage_team_and_agents_cannot():
    async def scenario():
        async with client() as http:
            _, headers_owner, workspace = await register(http)
            ws_header = {"X-Workspace-Id": str(workspace["id"])}

            teammate_email = f"agent-{uuid4().hex[:8]}@example.com"
            added = await http.post(
                "/api/workspaces/current/members",
                headers={**headers_owner, **ws_header},
                json={
                    "email": teammate_email,
                    "role": "agent",
                    "full_name": "Новый Агент",
                    "password": "TestPass-2024-Secure",
                },
            )
            assert added.status_code == 201, added.text
            member = added.json()
            assert member["role"] == "agent"

            members = await http.get(
                "/api/workspaces/current/members", headers={**headers_owner, **ws_header}
            )
            assert len(members.json()) == 2

            # New agent signs in and cannot escalate privileges.
            login = await http.post(
                "/api/auth/login", json={"email": teammate_email, "password": "TestPass-2024-Secure"}
            )
            assert login.status_code == 200
            agent_headers = {
                **auth_headers(http, login.json()["access_token"]),
                **ws_header,
            }
            denied = await http.post(
                "/api/workspaces/current/members",
                headers=agent_headers,
                json={"email": "x@example.com", "role": "admin"},
            )
            assert denied.status_code == 403

            promoted = await http.patch(
                f"/api/workspaces/current/members/{member['id']}",
                headers={**headers_owner, **ws_header},
                json={"role": "admin"},
            )
            assert promoted.status_code == 200
            assert promoted.json()["role"] == "admin"

            removed = await http.delete(
                f"/api/workspaces/current/members/{member['id']}",
                headers={**headers_owner, **ws_header},
            )
            assert removed.status_code == 204

    run(scenario())


def test_owner_key_rotation_and_widget_isolation():
    async def scenario():
        async with client() as http:
            _, headers, workspace = await register(http)
            ws_header = {"X-Workspace-Id": str(workspace["id"])}

            before = await http.get("/api/workspaces/current", headers={**headers, **ws_header})
            old_channel = before.json()["channel_key"]
            old_widget = before.json()["widget_key"]

            rotated = await http.post(
                "/api/workspaces/current/keys/rotate?target=channel",
                headers={**headers, **ws_header},
            )
            assert rotated.status_code == 200, rotated.text
            body = rotated.json()
            assert body["channel_key"] != old_channel
            assert body["widget_key"] == old_widget
            assert f'data-workspace-key="{body["widget_key"]}"' in body["widget_snippet"]

    run(scenario())


def test_telegram_assignment_worker_endpoint():
    async def scenario():
        async with client() as http:
            _, headers, workspace = await register(http)
            ws_header = {"X-Workspace-Id": str(workspace["id"])}

            unauthorized = await http.get("/api/channels/telegram/assignments")
            assert unauthorized.status_code == 401

            connected = await http.put(
                "/api/workspaces/current/telegram",
                headers={**headers, **ws_header},
                json={"bot_token": "123456:ABC-DEF", "enabled": True},
            )
            assert connected.status_code == 200, connected.text
            assert connected.json()["telegram_enabled"] is True

            authorized = await http.get(
                "/api/channels/telegram/assignments",
                headers=worker_headers(),
            )
            assert authorized.status_code == 200
            tokens = [row["bot_token"] for row in authorized.json()]
            assert "123456:ABC-DEF" in tokens

    with fake_getme("demo_support_bot", "Demo Support"):
        run(scenario())


def test_telegram_auto_activation_flow():
    """Вставка токена сразу поднимает комнату: connecting -> online, плюс ссылка для сайта."""

    async def scenario():
        async with client() as http:
            _, headers, workspace = await register(http)
            ws_header = {"X-Workspace-Id": str(workspace["id"])}

            off = await http.get("/api/workspaces/current/telegram/status", headers={**headers, **ws_header})
            assert off.status_code == 200
            assert off.json()["status"] == "off"

            connected = await http.put(
                "/api/workspaces/current/telegram",
                headers={**headers, **ws_header},
                json={"bot_token": GOOD_TOKEN, "enabled": True},
            )
            body = connected.json()
            assert body["telegram_status"] == "connecting"
            assert body["telegram_bot_username"] == "demo_support_bot"
            assert body["telegram_bot_name"] == "Demo Support"
            assert body["telegram_deep_link"] == f"https://t.me/demo_support_bot?start={workspace['slug']}"

            assignments = await http.get(
                "/api/channels/telegram/assignments",
                headers=worker_headers(),
            )
            rows = [row for row in assignments.json() if row["workspace_id"] == workspace["id"]]
            assert rows and rows[0]["bot_username"] == "demo_support_bot"
            assert rows[0]["slug"] == workspace["slug"]

            beat = await http.post(
                "/api/channels/telegram/heartbeat",
                headers=worker_headers(),
                json={"workspace_id": workspace["id"], "ok": True},
            )
            assert beat.status_code == 200, beat.text
            assert beat.json()["status"] == "online"
            assert beat.json()["last_seen"] is not None

            still_online = await http.get(
                "/api/workspaces/current/telegram/status", headers={**headers, **ws_header}
            )
            assert still_online.json()["status"] == "online"

    with fake_getme("demo_support_bot", "Demo Support"):
        run(scenario())


def test_invalid_token_is_not_activated():
    async def scenario():
        async with client() as http:
            _, headers, workspace = await register(http)
            ws_header = {"X-Workspace-Id": str(workspace["id"])}

            response = await http.put(
                "/api/workspaces/current/telegram",
                headers={**headers, **ws_header},
                json={"bot_token": GOOD_TOKEN, "enabled": True},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["telegram_enabled"] is False
            assert body["telegram_status"] == "invalid"
            assert "401" in body["telegram_error"]

            assignments = await http.get(
                "/api/channels/telegram/assignments",
                headers=worker_headers(),
            )
            assert all(row["workspace_id"] != workspace["id"] for row in assignments.json())

    with fake_getme(None, error="401: Unauthorized"):
        run(scenario())


def test_malformed_token_rejected_without_network():
    """Формат проверяется локально — даже без сети токен-мусор не пройдёт."""

    async def scenario():
        async with client() as http:
            _, headers, workspace = await register(http)
            response = await http.put(
                "/api/workspaces/current/telegram",
                headers={**headers, "X-Workspace-Id": str(workspace["id"])},
                json={"bot_token": "не-токен", "enabled": True},
            )
            assert response.status_code == 200
            assert response.json()["telegram_status"] == "invalid"
            assert "некорректно" in response.json()["telegram_error"]

    run(scenario())


def test_telegram_disable_resets_status():
    async def scenario():
        async with client() as http:
            _, headers, workspace = await register(http)
            ws_header = {**headers, "X-Workspace-Id": str(workspace["id"])}

            await http.put(
                "/api/workspaces/current/telegram",
                headers=ws_header,
                json={"bot_token": GOOD_TOKEN, "enabled": True},
            )
            await http.post(
                "/api/channels/telegram/heartbeat",
                headers=worker_headers(),
                json={"workspace_id": workspace["id"], "ok": True},
            )
            off = await http.put(
                "/api/workspaces/current/telegram",
                headers=ws_header,
                json={"bot_token": "", "enabled": False},
            )
            assert off.json()["telegram_status"] == "off"
            assert off.json()["telegram_deep_link"] == ""

    with fake_getme("demo_support_bot", "Demo Support"):
        run(scenario())


def test_heartbeat_requires_worker_key_and_reports_failures():
    async def scenario():
        async with client() as http:
            _, headers, workspace = await register(http)
            ws_header = {**headers, "X-Workspace-Id": str(workspace["id"])}

            unauth = await http.post(
                "/api/channels/telegram/heartbeat",
                json={"workspace_id": workspace["id"], "ok": True},
            )
            assert unauth.status_code == 401

            missing = await http.post(
                "/api/channels/telegram/heartbeat",
                headers=worker_headers(),
                json={"workspace_id": 999999, "ok": True},
            )
            assert missing.status_code == 404

            await http.put(
                "/api/workspaces/current/telegram",
                headers=ws_header,
                json={"bot_token": GOOD_TOKEN, "enabled": True},
            )
            failed = await http.post(
                "/api/channels/telegram/heartbeat",
                headers=worker_headers(),
                json={"workspace_id": workspace["id"], "ok": False, "error": "Unauthorized: bot token invalid"},
            )
            assert failed.json()["status"] == "error"
            assert "invalid" in failed.json()["error"]

    with fake_getme("demo_support_bot", "Demo Support"):
        run(scenario())


def test_knowledge_is_scoped_per_workspace():
    async def scenario():
        async with client() as http:
            _, headers_a, workspace_a = await register(http)
            _, headers_b, workspace_b = await register(http)

            created = await http.post(
                "/api/knowledge/documents",
                headers={**headers_a, "X-Workspace-Id": str(workspace_a["id"])},
                json={
                    "title": "Только для компании A",
                    "content": "Промокод COMPANYA даёт скидку 25 процентов.",
                    "source": "test",
                },
            )
            assert created.status_code == 201, created.text

            docs_a = await http.get(
                "/api/knowledge/documents",
                headers={**headers_a, "X-Workspace-Id": str(workspace_a["id"])},
            )
            docs_b = await http.get(
                "/api/knowledge/documents",
                headers={**headers_b, "X-Workspace-Id": str(workspace_b["id"])},
            )
            assert any(doc["title"] == "Только для компании A" for doc in docs_a.json())
            assert all(doc["title"] != "Только для компании A" for doc in docs_b.json())

    run(scenario())
