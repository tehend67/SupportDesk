import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

from .config import settings


@dataclass
class _Bucket:
    count: int = 0
    window_start: float = field(default_factory=time.monotonic)
    blocked_until: float = 0.0
    failures: int = 0


class RateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    async def check(self, scope: str, ident: str, limit: int, window: float = 60.0) -> tuple[bool, int, int]:
        now = time.monotonic()
        key = f"{scope}:{ident}"
        async with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(window_start=now)
                self._buckets[key] = bucket
                self._evict_locked(now)
            if bucket.blocked_until > now:
                return False, 0, int(bucket.blocked_until - now)
            if now - bucket.window_start >= window:
                bucket.window_start = now
                bucket.count = 0
            bucket.count += 1
            if bucket.count > limit:
                retry_after = int(window - (now - bucket.window_start)) or 1
                return False, 0, retry_after
            return True, limit - bucket.count, 0

    async def fail(self, scope: str, ident: str, lockout: float) -> int:
        now = time.monotonic()
        key = f"{scope}:{ident}"
        async with self._lock:
            bucket = self._buckets.setdefault(key, _Bucket(window_start=now))
            bucket.failures += 1
            bucket.blocked_until = now + lockout
            return bucket.failures

    async def success(self, scope: str, ident: str) -> None:
        async with self._lock:
            bucket = self._buckets.get(f"{scope}:{ident}")
            if bucket is not None:
                bucket.failures = 0
                bucket.blocked_until = 0.0

    def _evict_locked(self, now: float) -> None:
        if len(self._buckets) < 5000:
            return
        stale = [k for k, b in self._buckets.items() if now - b.window_start > 900]
        for key in stale[:2000]:
            self._buckets.pop(key, None)


limiter = RateLimiter()


def client_ip(request) -> str:
    """Адрес клиента для лимитов и аудита.

    Заголовкам X-Forwarded-For / X-Real-IP верим только при
    TRUST_PROXY_HEADERS=true: без прокси их подставляет сам клиент и так
    обходит лимит, просто меняя адрес в заголовке.
    """
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
        real_ip = request.headers.get("x-real-ip", "")
        if real_ip:
            return real_ip.strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


__all__ = ["limiter", "client_ip", "RateLimiter"]