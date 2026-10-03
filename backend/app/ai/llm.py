import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from ..config import settings
from .embeddings import hash_embedding

CONTEXT_MARKER = "КОНТЕКСТ ИЗ БАЗЫ ЗНАНИЙ:"
logger = logging.getLogger("helpdesk.llm")


@dataclass
class LLMResult:
    text: str
    model: str
    tokens: int = 0
    mock: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)


def _extract_context(messages: List[Dict[str, str]]) -> str:
    for message in messages:
        if message.get("role") != "system":
            continue
        content = message.get("content") or ""
        if CONTEXT_MARKER in content:
            context = content.split(CONTEXT_MARKER, 1)[1].strip()
            return context
    return ""


GROQ_HOST = "api.groq.com"


def is_reasoning_model(model: str) -> bool:
    """Reasoning-модели (gpt-oss) тратят токены на размышление перед ответом.

    Если лимит маленький, весь бюджет уходит в reasoning и ``content`` приходит пустым.
    """
    return "gpt-oss" in (model or "").lower()


class LLMClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        embedding_model: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.model = model or settings.llm_model
        self.embedding_model = embedding_model or settings.embedding_model
        self.timeout = timeout or settings.llm_timeout_seconds
        # Groq обслуживает только чат: эмбеддинги он не отдаёт.
        self._embeddings_local = GROQ_HOST in self.base_url

    @property
    def configured(self) -> bool:
        return bool(self.api_key.strip())

    @property
    def mode(self) -> str:
        return "hosted" if self.configured else "offline-hash"

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        if not self.configured or self._embeddings_local:
            return [hash_embedding(text) for text in texts]
        payload = {"model": self.embedding_model, "input": texts}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/embeddings", headers=self._headers(), json=payload
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:  # noqa: BLE001
            # Groq не отдаёт эмбеддинги: один раз переключаемся на локальный вектор,
            # чтобы поиск по базе знаний работал без внешних сервисов.
            self._embeddings_local = True
            logger.warning(
                "эмбеддинги недоступны (%s: %s) — считаем локально, база знаний остаётся рабочей",
                self.base_url,
                exc,
            )
            return [hash_embedding(text) for text in texts]
        ordered = sorted(data.get("data", []), key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in ordered]

    async def embed_one(self, text: str) -> List[float]:
        vectors = await self.embed([text])
        return vectors[0] if vectors else hash_embedding(text)

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResult:
        if not self.configured:
            return self._mock_chat(messages)
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": settings.llm_temperature if temperature is None else temperature,
            "max_tokens": settings.llm_max_tokens if max_tokens is None else max_tokens,
        }
        # Глубину размышления понимают только reasoning-модели: остальные серверы
        # отвергают неизвестный параметр, поэтому шлём его для gpt-oss.
        if is_reasoning_model(self.model):
            effort = (settings.llm_reasoning_effort or "").strip()
            if effort:
                payload["reasoning_effort"] = effort
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions", headers=self._headers(), json=payload
            )
            response.raise_for_status()
            data = response.json()
        choices = data.get("choices") or []
        text = (choices[0].get("message", {}).get("content") if choices else "") or ""
        usage = data.get("usage") or {}
        # У reasoning-моделей весь лимит может уйти в размышление, и content пуст —
        # повторяем запрос с удвоенным бюджетом.
        if not text.strip() and is_reasoning_model(self.model):
            logger.warning("reasoning-модель вернула пустой ответ — повторяю с увеличенным лимитом")
            bigger = (settings.llm_max_tokens if max_tokens is None else max_tokens) * 2
            payload["max_tokens"] = bigger
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions", headers=self._headers(), json=payload
                )
                response.raise_for_status()
                data = response.json()
            choices = data.get("choices") or []
            text = (choices[0].get("message", {}).get("content") if choices else "") or ""
            usage = data.get("usage") or usage
        return LLMResult(
            text=text.strip(),
            model=data.get("model", self.model),
            tokens=int(usage.get("total_tokens", 0) or 0),
            mock=False,
            raw=data,
        )

    def _mock_chat(self, messages: List[Dict[str, str]]) -> LLMResult:
        context = _extract_context(messages)
        has_context = bool(context) and "не найдено" not in context.lower()
        if has_context:
            excerpt = " ".join(context.splitlines()[0:2]).strip()[:420]
            reply = f"Спасибо за обращение! По нашей документации: {excerpt}"
        else:
            reply = (
                "Спасибо, что написали. Я зафиксировал ваш вопрос и передаю его специалисту "
                "поддержки — он ответит в ближайшее время."
            )
        return LLMResult(text=reply, model="offline-mock", tokens=len(reply) // 4, mock=True, raw={})


_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
