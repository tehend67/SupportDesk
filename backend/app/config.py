from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env ищем от корня репозитория: запуск из backend/ иначе его не видит.
ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT_ENV, ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "AI Support Desk"
    environment: str = "development"
    api_prefix: str = "/api"

    secret_key: str = "dev-secret-change-me"
    algorithm: str = "HS256"
    access_token_ttl_minutes: int = 1440

    database_url: str = "sqlite+aiosqlite:///./helpdesk.db"
    db_pool_disabled: bool = False
    cors_origins: str = "*"
    auto_create_schema: bool = True

    allow_registration: bool = True
    admin_email: str = "admin@example.com"
    admin_password: str = "admin12345"
    admin_name: str = "Support Admin"
    seed_demo_data: bool = True

    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 700
    llm_timeout_seconds: float = 45.0
    # Глубина размышления для reasoning-моделей: low | medium | high.
    # low отвечает за ~0.3 сек, high — за ~12 сек.
    llm_reasoning_effort: str = "low"

    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    embedding_batch_size: int = 32

    retrieval_top_k: int = 4
    retrieval_min_score: float = 0.05
    auto_reply_min_score: float = 0.5
    max_context_messages: int = 8

    channel_api_key: str = "dev-channel-key"
    worker_api_key: str = "dev-worker-key"
    worker_api_key_header: str = "X-Worker-Key"

    telegram_api_base: str = "https://api.telegram.org"
    telegram_verify_timeout: float = 12.0
    # Воркер поднимает ботов в процессе приложения. Выключать, только если
    # боты запущены отдельно (bots/telegram/bot.py).
    telegram_worker_enabled: bool = True

    demo_workspace_name: str = "Acme Support"
    public_base_url: str = "http://localhost:8000"
    auto_reset_schema: bool = False

    session_cookie_name: str = "aisd_session"
    csrf_cookie_name: str = "aisd_csrf"
    csrf_header_name: str = "X-CSRF-Token"
    session_cookie_secure: bool = False
    session_cookie_samesite: str = "lax"

    login_rate_limit_per_minute: int = 10
    login_lockout_minutes: int = 15
    ingest_rate_limit_per_minute: int = 60
    api_rate_limit_per_minute: int = 600

    audit_enabled: bool = True
    secrets_master_key: str = ""

    # X-Forwarded-For пишет обратный прокси. Без прокси заголовок подделывает
    # клиент и обходит лимиты, поэтому по умолчанию не доверяем.
    trust_proxy_headers: bool = False

    @property
    def cors_origin_list(self) -> List[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
