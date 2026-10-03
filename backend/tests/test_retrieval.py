from app.ai.retrieval import cosine_similarity, rank_candidates


def test_cosine_identical_vectors():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0


def test_cosine_orthogonal_vectors():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_mismatched_dimensions():
    assert cosine_similarity([1.0], [1.0, 2.0]) == 0.0


def test_rank_orders_by_score_and_limits():
    query = [1.0, 0.0]
    candidates = [(1, [0.9, 0.1]), (2, [0.0, 1.0]), (3, [1.0, 0.0])]
    ranked = rank_candidates(query, candidates, top_k=2)
    assert [candidate_id for candidate_id, _ in ranked] == [3, 1]


def test_rank_respects_min_score():
    ranked = rank_candidates([1.0, 0.0], [(1, [0.0, 1.0])], top_k=5, min_score=0.2)
    assert ranked == []
