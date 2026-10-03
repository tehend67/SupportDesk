import hashlib
import math
import re
from typing import List

from ..config import settings

_TOKEN_RE = re.compile(r"[0-9a-z\u0430-\u044f\u0451]+", re.IGNORECASE)


def tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _features(text: str) -> List[str]:
    tokens = tokenize(text)
    features = list(tokens)
    features.extend(f"{left}_{right}" for left, right in zip(tokens, tokens[1:]))
    return features


def hash_embedding(text: str, dim: int | None = None) -> List[float]:
    dimension = int(dim or settings.embedding_dim)
    vector = [0.0] * dimension
    features = _features(text)
    if not features:
        return vector
    for feature in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % dimension
        sign = 1.0 if digest[4] & 1 else -1.0
        weight = 1.0 + 0.5 * min(len(feature), 12) / 12
        vector[index] += sign * weight
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]
