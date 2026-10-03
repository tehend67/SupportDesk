from app.ai.chunking import chunk_text, estimate_tokens, normalize, split_sentences


def test_normalize_collapses_whitespace():
    assert normalize("  Привет\n\n  мир \t!  ") == "Привет мир !"


def test_short_text_is_single_chunk():
    chunks = chunk_text("Короткий текст про доставку.")
    assert len(chunks) == 1


def test_empty_text_has_no_chunks():
    assert chunk_text("   ") == []


def test_long_text_is_split_without_exceeding_limit():
    text = " ".join(f"Предложение номер {index} о доставке и оплате." for index in range(200))
    chunks = chunk_text(text, max_chars=300, overlap=40)
    assert len(chunks) > 1
    assert all(len(chunk) <= 300 for chunk in chunks)


def test_split_sentences_handles_russian_punctuation():
    sentences = split_sentences("Первое предложение. Второе предложение! Третье?")
    assert len(sentences) == 3


def test_estimate_tokens_positive():
    assert estimate_tokens("некоторый текст") > 0
    assert estimate_tokens("") == 0
