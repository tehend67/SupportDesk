import asyncio

from app.ai.agent import SupportAgent, detect_sentiment, needs_human
from app.ai.llm import LLMClient


def offline_agent() -> SupportAgent:
    return SupportAgent(LLMClient(api_key=""))


def test_detect_sentiment():
    assert detect_sentiment("Это ужас, ничего не работает") == "negative"
    assert detect_sentiment("Спасибо, всё отлично") == "positive"
    assert detect_sentiment("Подскажите сроки доставки") == "neutral"


def test_needs_human_detection():
    assert needs_human("Позовите оператора")
    assert needs_human("I want a human agent")
    assert not needs_human("Как оплатить подписку?")


def test_agent_escalates_on_negative_sentiment():
    result = asyncio.run(offline_agent().respond("Ничего не работает, это ужас"))
    assert result.escalated is True
    assert result.sentiment == "negative"
    assert result.reply


def test_agent_escalates_on_human_request():
    result = asyncio.run(offline_agent().respond("Соедините с человеком"))
    assert result.escalated is True
    assert result.reason == "customer_requested_human"


def test_agent_answers_from_context_when_available():
    hits = [
        {
            "document_id": 1,
            "document_title": "Доставка",
            "content": "Доставка по России занимает от 2 до 5 рабочих дней.",
            "score": 0.8,
        }
    ]
    result = asyncio.run(offline_agent().respond("Сколько идёт доставка?", hits=hits))
    assert result.escalated is False
    assert "доставка" in result.reply.lower() or "доставк" in result.reply.lower()
    assert result.sources
