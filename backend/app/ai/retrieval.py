import math
from typing import Iterable, List, Sequence, Tuple


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for left, right in zip(a, b):
        dot += left * right
        norm_a += left * left
        norm_b += right * right
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def rank_candidates(
    query_embedding: Sequence[float],
    candidates: Iterable[Tuple[int, Sequence[float]]],
    top_k: int = 5,
    min_score: float = 0.0,
) -> List[Tuple[int, float]]:
    scored = [
        (candidate_id, cosine_similarity(query_embedding, embedding))
        for candidate_id, embedding in candidates
    ]
    filtered = [(candidate_id, score) for candidate_id, score in scored if score >= min_score]
    filtered.sort(key=lambda pair: pair[1], reverse=True)
    return filtered[: max(1, top_k)]
