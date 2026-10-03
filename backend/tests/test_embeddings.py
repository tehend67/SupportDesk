import math

from app.ai.embeddings import hash_embedding, tokenize
from app.ai.retrieval import cosine_similarity


def test_tokenize_lowercases_and_splits():
    assert tokenize("Доставка 2 дня!") == ["доставка", "2", "дня"]


def test_embedding_is_deterministic_and_normalized():
    first = hash_embedding("оплата подписки", dim=64)
    second = hash_embedding("оплата подписки", dim=64)
    assert first == second
    norm = math.sqrt(sum(value * value for value in first))
    assert abs(norm - 1.0) < 1e-6


def test_empty_text_gives_zero_vector():
    vector = hash_embedding("", dim=32)
    assert len(vector) == 32
    assert all(value == 0.0 for value in vector)


def test_similar_texts_score_higher_than_unrelated():
    base = hash_embedding("сколько стоит доставка и сроки", dim=256)
    related = hash_embedding("стоимость доставки и сроки", dim=256)
    unrelated = hash_embedding("настройка SSO и двухфакторная аутентификация", dim=256)
    assert cosine_similarity(base, related) > cosine_similarity(base, unrelated)
