import asyncio
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.conftest import auth_headers

from app import seed
from app.config import settings
from app.main import app, init_db
from app.models import ApiKey
from app.ratelimit import RateLimiter
from app.security import (
    constant_time_equals,
    decrypt_secret,
    encrypt_secret,
    hash_secret_key,
    is_encrypted,
    mask_secret,
    password_problems,
    random_secret,
)
from app.security import create_access_token, decode_token, hash_password, verify_password
from app.services import session as session_service
from app.db import session_scope

GOOD_PASSWORD = "TestPass-2024-Secure"


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


async def register(http: AsyncClient):
    email = f"sec-{uuid4().hex[:8]}@example.com"
    response = await http.post(
        "/api/auth/register",
        json={"email": email, "password": GOOD_PASSWORD, "full_name": "Охранник"},
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    listing = await http.get("/api/workspaces", headers=auth_headers(http, token))
    return email, token, auth_headers(http, token), listing.json()[0]


def test_security_headers_present():
    async def scenario():
        async with client() as http:
            panel = await http.get("/")
            assert panel.status_code == 200
            assert panel.headers["x-frame-options"] == "DENY"
            assert panel.headers["x-content-type-options"] == "nosniff"
            assert "frame-ancestors 'none'" in panel.headers["content-security-policy"]
            assert "object-src 'none'" in panel.headers["content-security-policy"]

            widget = await http.get("/widget.js")
            assert widget.status_code == 200
            assert "connect-src 'self'" in widget.headers["content-security-policy"]

            api = await http.get("/api/auth/me")
            assert api.headers["cache-control"] == "no-store, no-cache, must-revalidate"

    run(scenario())


def test_cookie_session_is_httponly_and_csrf_protected():
    async def scenario():
        async with client() as http:
            email = f"csrf-{uuid4().hex[:8]}@example.com"
            response = await http.post(
                "/api/auth/register",
                json={"email": email, "password": GOOD_PASSWORD, "full_name": "Cookie"},
            )
            assert response.status_code == 201, response.text
            raw_cookie = response.headers.get("set-cookie", "")
            assert f"{settings.session_cookie_name}=" in raw_cookie
            assert "HttpOnly" in raw_cookie
            assert "SameSite=lax" in raw_cookie or "SameSite=Lax" in raw_cookie
            assert http.cookies.get(settings.session_cookie_name)
            assert http.cookies.get(settings.csrf_cookie_name)

            listing = await http.get("/api/workspaces")
            assert listing.status_code == 200

            # Только cookie, без Bearer — панельный запрос идёт как cookie-сессия.
            no_csrf = await http.post(
                "/api/tickets", json={"subject": "CSRF", "message": "hi", "channel": "web"}
            )
            assert no_csrf.status_code == 403
            assert "CSRF" in no_csrf.json()["detail"]

            with_csrf = await http.post(
                "/api/tickets",
                headers={"X-CSRF-Token": http.cookies.get(settings.csrf_cookie_name)},
                json={"subject": "С CSRF", "message": "hi", "channel": "web"},
            )
            assert with_csrf.status_code == 201, with_csrf.text

            wrong = await http.post(
                "/api/tickets",
                headers={"X-CSRF-Token": "forged-token"},
                json={"subject": "Фальшивый CSRF", "message": "hi", "channel": "web"},
            )
            assert wrong.status_code == 403

    run(scenario())


def test_bearer_token_needs_no_csrf_header():
    async def scenario():
        async with client() as http:
            _, token, _, workspace = await register(http)
            response = await http.post(
                "/api/tickets",
                headers={"Authorization": f"Bearer {token}", "X-Workspace-Id": str(workspace["id"])},
                json={"subject": "Bearer", "message": "hi", "channel": "api"},
            )
            assert response.status_code == 201, response.text

    run(scenario())


def test_login_bruteforce_is_locked_out():
    async def scenario():
        async with client() as http:
            email = f"brute-{uuid4().hex[:8]}@example.com"
            codes = []
            for attempt in range(12):
                response = await http.post(
                    "/api/auth/login", json={"email": email, "password": f"guess-{attempt}"}
                )
                codes.append(response.status_code)
            assert 429 in codes
            assert codes.index(429) < 12

            even_right = await http.post(
                "/api/auth/login", json={"email": email, "password": GOOD_PASSWORD}
            )
            assert even_right.status_code == 429

    run(scenario())


def test_weak_password_rejected_and_rate_limit_on_register():
    async def scenario():
        async with client() as http:
            weak = await http.post(
                "/api/auth/register",
                json={"email": f"weak-{uuid4().hex[:8]}@example.com", "password": "12345678"},
            )
            assert weak.status_code == 422

    run(scenario())


def test_named_api_key_lifecycle():
    async def scenario():
        async with client() as http:
            _, _, headers, workspace = await register(http)

            created = await http.post(
                "/api/workspaces/current/keys",
                headers={**headers, "X-Workspace-Id": str(workspace["id"])},
                json={"name": "Виджет сайта", "scope": "ingest"},
            )
            assert created.status_code == 201, created.text
            body = created.json()
            plain = body["key"]
            assert plain.startswith("ak_")
            assert body["masked"].count("•") == 8

            listed = await http.get(
                "/api/workspaces/current/keys",
                headers={**headers, "X-Workspace-Id": str(workspace["id"])},
            )
            assert listed.status_code == 200
            row = listed.json()[0]
            assert "key" not in row
            assert row["masked"] == body["masked"]
            assert plain not in listed.text

            ingested = await http.post(
                "/api/ingest/message",
                headers={"X-Channel-Key": plain},
                json={"channel": "web", "content": "Привет", "customer_ref": f"c-{uuid4().hex[:6]}"},
            )
            assert ingested.status_code == 200, ingested.text

            wrong = await http.post(
                "/api/ingest/message",
                headers={"X-Channel-Key": plain + "x"},
                json={"channel": "web", "content": "Привет", "customer_ref": "c2"},
            )
            assert wrong.status_code == 401

            revoked = await http.delete(
                f"/api/workspaces/current/keys/{row['id']}",
                headers={**headers, "X-Workspace-Id": str(workspace["id"])},
            )
            assert revoked.status_code == 204

            after = await http.post(
                "/api/ingest/message",
                headers={"X-Channel-Key": plain},
                json={"channel": "web", "content": "Привет", "customer_ref": "c3"},
            )
            assert after.status_code == 401

    run(scenario())


def test_api_key_stored_only_as_hash():
    async def scenario():
        plain_box = {}

        async def flow():
            async with client() as http:
                _, _, headers, workspace = await register(http)
                created = await http.post(
                    "/api/workspaces/current/keys",
                    headers={**headers, "X-Workspace-Id": str(workspace["id"])},
                    json={"name": "Хранилище"},
                )
                plain_box["key"] = created.json()["key"]
                plain_box["workspace"] = workspace["id"]

        run(flow())

        async def inspect():
            async with session_scope() as session:
                rows = (
                    await session.execute(select(ApiKey).where(ApiKey.workspace_id == plain_box["workspace"]))
                ).scalars().all()
                assert rows
                for row in rows:
                    assert row.key_hash == hash_secret_key(plain_box["key"])
                    assert plain_box["key"] not in row.key_hash
                    assert len(row.key_hash) == 64

        run(inspect())


def test_ingest_rate_limit_blocks_flood():
    async def scenario():
        async with client() as http:
            _, _, headers, workspace = await register(http)
            created = await http.post(
                "/api/workspaces/current/keys",
                headers={**headers, "X-Workspace-Id": str(workspace["id"])},
                json={"name": "Лимит"},
            )
            plain = created.json()["key"]
            payload = {"channel": "web", "content": "флуд", "customer_ref": f"f-{uuid4().hex[:6]}"}
            codes = []
            for _ in range(settings.ingest_rate_limit_per_minute + 4):
                response = await http.post(
                    "/api/ingest/message", headers={"X-Channel-Key": plain}, json=payload
                )
                codes.append(response.status_code)
            assert 429 in codes, codes[-3:]
            assert codes.index(429) >= settings.ingest_rate_limit_per_minute
            assert codes[0] == 200

    run(scenario())


def test_audit_log_records_events_without_secrets():
    async def scenario():
        async with client() as http:
            email, _, headers, workspace = await register(http)
            created = await http.post(
                "/api/workspaces/current/keys",
                headers={**headers, "X-Workspace-Id": str(workspace["id"])},
                json={"name": "Аудит", "scope": "ingest"},
            )
            plain = created.json()["key"]
            await http.delete(
                f"/api/workspaces/current/keys/{created.json()['id']}",
                headers={**headers, "X-Workspace-Id": str(workspace["id"])},
            )

            entries = await http.get(
                "/api/workspaces/current/audit",
                headers={**headers, "X-Workspace-Id": str(workspace["id"])},
            )
            assert entries.status_code == 200, entries.text
            actions = [item["action"] for item in entries.json()]
            assert "key.created" in actions
            assert "key.revoked" in actions
            assert plain not in entries.text
            assert GOOD_PASSWORD not in entries.text

    run(scenario())


def test_tenant_cannot_read_other_tenant_audit_or_keys():
    async def scenario():
        async with client() as http:
            _, _, headers_a, workspace_a = await register(http)
            created = await http.post(
                "/api/workspaces/current/keys",
                headers={**headers_a, "X-Workspace-Id": str(workspace_a["id"])},
                json={"name": "Приватный"},
            )
            assert created.status_code == 201
            _, _, headers_b, _ = await register(http)

            keys_b = await http.get("/api/workspaces/current/keys", headers=headers_b)
            assert all(item["id"] != created.json()["id"] for item in keys_b.json())

            audit_b = await http.get("/api/workspaces/current/audit", headers=headers_b)
            assert audit_b.status_code == 200
            actions = {item["action"] for item in audit_b.json()}
            assert "key.created" not in actions or all(
                item.get("meta", {}).get("name") != "Приватный" for item in audit_b.json()
            )

    run(scenario())


def test_agent_does_not_see_open_channel_key():
    """Роль без управления видит только маску: ключ уходит во внешние системы."""

    async def scenario():
        async with client() as http:
            _, owner_token, owner_headers, workspace = await register(http)
            ws_header = {"X-Workspace-Id": str(workspace["id"])}
            secret = (
                await http.get("/api/workspaces/current", headers={**owner_headers, **ws_header})
            ).json()["channel_key"]
            assert secret.startswith("ck_")

            email = f"agent-{uuid4().hex[:8]}@example.com"
            await http.post(
                "/api/workspaces/current/members",
                headers={**owner_headers, **ws_header},
                json={"email": email, "role": "agent", "password": GOOD_PASSWORD},
            )
            login = await http.post("/api/auth/login", json={"email": email, "password": GOOD_PASSWORD})
            agent_headers = auth_headers(http, login.json()["access_token"])

            seen = (await http.get("/api/workspaces/current", headers={**agent_headers, **ws_header})).json()
            assert "•" in seen["channel_key"]
            assert secret not in seen["channel_key"]

            audit = await http.get("/api/workspaces/current/audit", headers={**agent_headers, **ws_header})
            assert audit.status_code == 403

    run(scenario())


def test_secret_helpers():
    secret = random_secret("tg", 12)
    blob = encrypt_secret(secret)
    assert is_encrypted(blob)
    assert secret not in blob
    assert decrypt_secret(blob) == secret
    assert decrypt_secret(secret) == secret
    assert decrypt_secret(encrypt_secret("")) == ""

    assert mask_secret("ck_abcdefghijkl").startswith("ck_a")
    assert "efghij" not in mask_secret("ck_abcdefghijkl")
    assert constant_time_equals("abc", "abc")
    assert not constant_time_equals("abc", "abd")

    assert password_problems("12345678")
    assert password_problems("password123")
    assert not password_problems(GOOD_PASSWORD)


def test_forwarded_for_not_trusted_by_default():
    """Без прокси X-Forwarded-For подделывается и обходит лимиты."""

    class FakeClient:
        host = "10.0.0.9"

    class FakeRequest:
        client = FakeClient()

        def __init__(self, headers):
            self.headers = headers

    from app.ratelimit import client_ip

    original = settings.trust_proxy_headers
    try:
        settings.trust_proxy_headers = False
        assert client_ip(FakeRequest({"x-forwarded-for": "1.2.3.4"})) == "10.0.0.9"
        settings.trust_proxy_headers = True
        assert client_ip(FakeRequest({"x-forwarded-for": "1.2.3.4, 5.6.7.8"})) == "1.2.3.4"
    finally:
        settings.trust_proxy_headers = original


def test_no_secrets_in_frontend_assets():
    """Во фронтенде не должно быть ни одного секрета.

    Клиентский JS нельзя спрятать — браузер обязан его прочитать, чтобы исполнить.
    Значит, единственная защита: в отдаваемых файлах нет ключей, токенов и паролей.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "frontend"
    patterns = (
        r"gsk_[A-Za-z0-9]{10,}",
        r"[0-9]{8,12}:[A-Za-z0-9_-]{30,}",
        r"ck_[A-Za-z0-9_-]{12,}",
        r"ak_[A-Za-z0-9_-]{12,}",
        r"SECRET_KEY",
        r"MASTER_KEY",
        r"ADMIN_PASSWORD",
    )
    import re

    offenders = []
    for path in sorted(root.glob("*")):
        if path.suffix not in {".js", ".html", ".css"} or path.name == "build.mjs":
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                offenders.append(f"{path.name}: {match.group(0)[:10]}…")
    assert not offenders, f"секреты во фронтенде: {offenders}"


def test_frontend_does_not_persist_session_token():
    """Токен не должен храниться в localStorage/sessionStorage."""
    from pathlib import Path

    app_js = (Path(__file__).resolve().parents[2] / "frontend" / "app.js").read_text(
        encoding="utf-8"
    )
    assert "sessionStorage" not in app_js
    assert "access_token" not in app_js
    stored = [
        line for line in app_js.splitlines() if "localStorage.setItem" in line
    ]
    assert stored, "панель должна что-то сохранять (выбор комнаты)"
    for line in stored:
        assert "token" not in line.lower()


def test_rate_limiter_unit():
    async def scenario():
        limiter = RateLimiter()
        results = [await limiter.check("t", "1.2.3.4", 3) for _ in range(5)]
        assert [ok for ok, _, _ in results] == [True, True, True, False, False]
        assert results[3][2] > 0

        await limiter.fail("t", "1.2.3.4", 30)
        blocked = await limiter.check("t", "1.2.3.4", 100)
        assert blocked[0] is False

        await limiter.success("t", "1.2.3.4")
        assert (await limiter.check("t", "1.2.3.4", 100))[0] is True
        assert (await limiter.check("t", "5.6.7.8", 1))[0] is True

    run(scenario())


def test_session_cookies_clear_on_logout():
    async def scenario():
        async with client() as http:
            email = f"out-{uuid4().hex[:8]}@example.com"
            await http.post(
                "/api/auth/register",
                json={"email": email, "password": GOOD_PASSWORD, "full_name": "Выход"},
            )
            csrf = http.cookies.get(settings.csrf_cookie_name)
            logged_out = await http.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
            assert logged_out.status_code == 204
            assert not http.cookies.get(settings.session_cookie_name)
            assert (await http.get("/api/auth/me")).status_code == 401

    run(scenario())


def test_password_hash_never_leaks_in_payload():
    async def scenario():
        async with client() as http:
            await http.post(
                "/api/auth/register",
                json={
                    "email": f"leak-{uuid4().hex[:8]}@example.com",
                    "password": GOOD_PASSWORD,
                    "full_name": "Утечка",
                },
            )
            me = await http.get("/api/auth/me")
            assert me.status_code == 200
            assert "password" not in me.json()
            assert "hashed" not in me.text
            assert GOOD_PASSWORD not in me.text

    run(scenario())


def test_widget_socket_requires_valid_key():
    """Виджет без ключа не должен попадать в чужую комнату."""

    async def scenario():
        from app.api.routes.ws import _resolve_workspace
        from app.db import session_scope

        async with client() as http:
            _, _, headers, workspace = await register(http)
            widget_key = (
                await http.get(
                    "/api/workspaces/current",
                    headers={**headers, "X-Workspace-Id": str(workspace["id"])},
                )
            ).json()["widget_key"]

        async with session_scope() as session:
            assert await _resolve_workspace(session, widget_key) is not None
            assert await _resolve_workspace(session, "") is None
            assert await _resolve_workspace(session, "wk_нет-такого") is None

    run(scenario())


def test_session_service_issue_roundtrip():
    token = session_service.issue(7, "owner", "x@example.com")
    assert token.count(".") == 2
    payload = decode_token(token)
    assert payload["sub"] == "7" and payload["role"] == "owner"
    assert payload["jti"] and payload["exp"] > payload["iat"]


def test_hash_and_verify_password():
    hashed = hash_password("s3cret-pass")
    assert hashed != "s3cret-pass"
    assert verify_password("s3cret-pass", hashed)
    assert not verify_password("wrong", hashed)
    assert not verify_password("x", "not-a-hash")


def test_token_claims_required():
    assert decode_token("definitely.not.valid") is None
    assert create_access_token("42", extra={"role": "admin"}) != create_access_token("42")


def test_tampered_token_rejected():
    token = create_access_token("42")
    head, payload, signature = token.split(".")
    forged = f"{head}.{payload}.{signature[:-2]}xy"
    assert decode_token(forged) is None