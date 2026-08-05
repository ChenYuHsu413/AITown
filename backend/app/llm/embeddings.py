"""Embedding providers.

Same philosophy as the LLM providers: an abstraction with a free default.
MockEmbedding is a deterministic bag-of-words random projection -- texts
sharing words get similar vectors, which is enough to exercise the whole
pgvector pipeline end-to-end at zero cost. Swap in sentence-transformers
or an API embedder later without touching callers (just update EMBED_DIM).
"""

from __future__ import annotations

import hashlib
import math
import random
from abc import ABC, abstractmethod

from ..db.models import EMBED_DIM


class EmbeddingProvider(ABC):
    dim: int = EMBED_DIM

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        raise NotImplementedError


class MockEmbedding(EmbeddingProvider):
    """Deterministic: each word maps to a fixed random unit vector; the
    text embedding is the normalized sum. No model, no API, no cost."""

    def _word_vec(self, word: str) -> list[float]:
        seed = int(hashlib.sha256(word.encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)
        v = [rng.gauss(0, 1) for _ in range(self.dim)]
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    async def embed(self, text: str) -> list[float]:
        words = [w for w in text.lower().split() if len(w) >= 2]
        if not words:
            words = ["empty"]
        acc = [0.0] * self.dim
        for w in words:
            for i, x in enumerate(self._word_vec(w)):
                acc[i] += x
        n = math.sqrt(sum(x * x for x in acc)) or 1.0
        return [x / n for x in acc]
