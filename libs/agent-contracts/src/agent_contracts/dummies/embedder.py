from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence

from agent_contracts.dummies._util import mark_dummies
from agent_contracts.interfaces import Embedder
from agent_contracts.models import RunContext


class HashingEmbedder(Embedder):
    """Feature hashing of words and character trigrams into ``dimensions`` buckets, L2-normalised.

    Deterministic and offline: a lexical stand-in for an embedding model in tests. It knows no
    synonyms, so it measures plumbing, never embedding quality.
    """

    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = dimensions

    def _features(self, text: str) -> list[str]:
        lowered = text.lower()
        tokens = [w for w in "".join(c if c.isalnum() else " " for c in lowered).split() if w]
        grams = [f"#{w[i : i + 3]}" for w in tokens for i in range(max(1, len(w) - 2))]
        return tokens + grams

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        for feature in self._features(text):
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            vec[bucket] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec

    async def embed(self, texts: Sequence[str], ctx: RunContext) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class WordHashingEmbedder(HashingEmbedder):
    """Words only (no character trigrams): coarser, so spelling variants do not match."""

    def _features(self, text: str) -> list[str]:
        return [f for f in super()._features(text) if not f.startswith("#")]


mark_dummies(globals())
