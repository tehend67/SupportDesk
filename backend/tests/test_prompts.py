from app.ai.prompts import build_system_prompt, format_context


def test_format_context_empty():
    assert "не найдено" in format_context([])


def test_format_context_lists_hits():
    text = format_context(
        [{"document_title": "Оплата", "content": "Тариф Старт 990 рублей", "score": 0.72}]
    )
    assert "Оплата" in text
    assert "990" in text
    assert "0.72" in text


def test_build_system_prompt_includes_escalation():
    prompt = build_system_prompt("контекст", escalate=True)
    assert "специалиста" in prompt
