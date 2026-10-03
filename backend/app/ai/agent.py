import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from ..config import settings
from ..constants import Sentiment
from .llm import LLMClient
from .prompts import build_system_prompt, format_context

logger = logging.getLogger("helpdesk.agent")

NEGATIVE_MARKERS = (
    "не работает",
    "сломал",
    "ошибка",
    "проблем",
    "ужас",
    "отвратит",
    "верните",
    "не могу",
    "жалоб",
    "недовол",
    "плохо",
    "broken",
    "error",
    "terrible",
    "refund",
    "angry",
    "useless",
    "scam",
)

POSITIVE_MARKERS = ("спасибо", "благодар", "отлично", "супер", "thanks", "great", "awesome", "perfect")

HUMAN_MARKERS = (
    "оператор",
    "человек",
    "специалист",
    "менеджер",
    "живой",
    "позовите",
    "human",
    "agent",
    "manager",
    "representative",
)

FALLBACK_REPLY = (
    "Спасибо за обращение! Я зафиксировал ваш вопрос и уже подключаю специалиста поддержки."
)


def detect_sentiment(text: str) -> str:
    lowered = (text or "").lower()
    if any(marker in lowered for marker in NEGATIVE_MARKERS):
        return Sentiment.NEGATIVE.value
    if any(marker in lowered for marker in POSITIVE_MARKERS):
        return Sentiment.POSITIVE.value
    return Sentiment.NEUTRAL.value


def needs_human(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in HUMAN_MARKERS)


@dataclass
class AgentResult:
    reply: str
    escalated: bool
    sentiment: str
    model: str
    tokens: int = 0
    mock: bool = False
    reason: str = ""
    sources: List[Dict] = field(default_factory=list)


class SupportAgent:
    def __init__(self, llm: Optional[LLMClient] = None) -> None:
        self.llm = llm or LLMClient()

    async def respond(
        self,
        user_message: str,
        hits: Optional[Sequence[Dict]] = None,
        history: Optional[Sequence[Dict[str, str]]] = None,
        subject: str = "",
    ) -> AgentResult:
        hits = list(hits or [])
        history = list(history or [])
        sentiment = detect_sentiment(user_message)
        escalated = needs_human(user_message) or sentiment == Sentiment.NEGATIVE.value
        reason = "customer_requested_human" if needs_human(user_message) else ""
        if not reason and sentiment == Sentiment.NEGATIVE.value:
            reason = "negative_sentiment"

        context = format_context(hits)
        system_prompt = build_system_prompt(context, escalated)
        if subject:
            system_prompt = f"{system_prompt}\nТема обращения: {subject}"

        messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for item in history[-settings.max_context_messages :]:
            role = item.get("role", "user")
            content = item.get("content", "")
            if content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_message})

        try:
            result = await self.llm.chat(messages)
        except Exception as exc:  # noqa: BLE001 — перегруженный провайдер не должен ронять приём обращений
            logger.warning("ИИ недоступен (%s) — перекапываем оператору", exc)
            return AgentResult(
                reply=(
                    "Сейчас я не успеваю отвечать мгновенно, но ваш вопрос уже у оператора поддержки — "
                    "он ответит в ближайшее время."
                ),
                escalated=True,
                sentiment=sentiment,
                model="unavailable",
                tokens=0,
                mock=True,
                reason="ИИ временно недоступен",
                sources=[dict(hit) for hit in hits],
            )
        reply = (result.text or "").strip() or FALLBACK_REPLY
        if result.mock and escalated:
            reply = (
                "Приношу извинения за неудобства. Я передаю ваш вопрос специалисту поддержки, "
                f"он подключится в ближайшее время. {reply}"
            )
        return AgentResult(
            reply=reply,
            escalated=escalated,
            sentiment=sentiment,
            model=result.model,
            tokens=result.tokens,
            mock=result.mock,
            reason=reason,
            sources=[dict(hit) for hit in hits],
        )
