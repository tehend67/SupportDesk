import base64
import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

from .config import settings

logger = logging.getLogger("helpdesk.security")

_SECRET_CACHE: dict[str, Fernet] = {}
UNSAFE_SECRETS = {"", "dev-secret-change-me", "change-me-to-a-long-random-string"}
WEAK_PASSWORDS = {
    "password", "12345678", "qwerty123", "admin123", "password123",
    "supersecret1", "letmein1", "iloveyou", "qwertyuiop",
}


def production() -> bool:
    return settings.environment.strip().lower() == "production"


def _derive_key(material: str) -> bytes:
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def secret_cipher() -> Fernet:
    material = settings.secrets_master_key.strip() or settings.secret_key
    cached = _SECRET_CACHE.get(material)
    if cached is None:
        cached = Fernet(_derive_key(material))
        _SECRET_CACHE[material] = cached
    return cached


def encrypt_secret(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    return secret_cipher().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if not value.startswith("gAAAA"):
        return value
    try:
        return secret_cipher().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.error("не удалось расшифровать секрет — изменился SECRET_KEY?")
        return ""


def is_encrypted(value: str) -> bool:
    return bool(value) and value.startswith("gAAAA")


def mask_secret(value: str, keep: int = 4) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "•" * len(value)
    return f"{value[:keep]}{'•' * 8}{value[-keep:]}"


def random_secret(prefix: str = "sk", nbytes: int = 24) -> str:
    return f"{prefix}_{secrets.token_urlsafe(nbytes)}"


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest((left or "").encode("utf-8"), (right or "").encode("utf-8"))


def hash_secret_key(value: str) -> str:
    return hashlib.sha256(_derive_key_prefix(value).encode("utf-8")).hexdigest()


def _derive_key_prefix(value: str) -> str:
    return f"{settings.secret_key}:{value}"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def password_problems(password: str) -> list[str]:
    problems = []
    if len(password or "") < 10:
        problems.append("минимум 10 символов")
    if (password or "").lower() in WEAK_PASSWORDS:
        problems.append("слишком простой пароль")
    if password and password.isdigit():
        problems.append("пароль не должен состоять только из цифр")
    return problems


def create_access_token(subject: str, extra: Optional[dict[str, Any]] = None) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "jti": secrets.token_urlsafe(12),
        "exp": int((now + timedelta(minutes=settings.access_token_ttl_minutes)).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> Optional[dict[str, Any]]:
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
            options={"require": ["exp", "sub", "iat"]},
        )
    except jwt.PyJWTError:
        return None


def assert_production_ready() -> list[str]:
    problems = []
    if production():
        if settings.secret_key in UNSAFE_SECRETS:
            problems.append("SECRET_KEY остался значением по умолчанию")
        if settings.admin_password in UNSAFE_SECRETS or settings.admin_password == "admin12345":
            problems.append("ADMIN_PASSWORD остался значением по умолчанию")
        if settings.cors_origins.strip() == "*":
            problems.append("CORS_ORIGINS='*' — укажите домены явно")
        if not settings.database_url.startswith("postgresql"):
            problems.append("для продакшена используйте PostgreSQL, а не SQLite")
        if settings.seed_demo_data:
            problems.append("SEED_DEMO_DATA=true отключите на продакшене")
        if not settings.session_cookie_secure:
            problems.append("SESSION_COOKIE_SECURE должен быть true на HTTPS")
    return problems


__all__ = [
    "production",
    "encrypt_secret",
    "decrypt_secret",
    "is_encrypted",
    "mask_secret",
    "random_secret",
    "hash_secret_key",
    "constant_time_equals",
    "hash_password",
    "verify_password",
    "password_problems",
    "create_access_token",
    "decode_token",
    "assert_production_ready",
    "UNSAFE_SECRETS",
]