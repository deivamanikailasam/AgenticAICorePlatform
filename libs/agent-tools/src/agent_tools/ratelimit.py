"""Per-tool rate limits: a fixed one-minute window per tenant, agent and tool (Step 10).

``hit`` counts one call and says whether it is within the limit. The Valkey limiter shares the
count between every replica of the gateway and the runtime; the memory limiter is for tests and a
single process.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

import redis.asyncio as redis

WINDOW_SECONDS = 60


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    count: int
    limit: int
    retry_after_seconds: int


def _window(now: float) -> tuple[int, int]:
    start = int(now) // WINDOW_SECONDS * WINDOW_SECONDS
    return start, start + WINDOW_SECONDS - int(now)


class RateLimiter(ABC):
    @abstractmethod
    async def hit(self, key: str, limit: int) -> RateDecision:
        """Count one call against ``key`` in the current minute."""


class MemoryRateLimiter(RateLimiter):
    def __init__(self, now: Callable[[], float] = time.time) -> None:
        self._counts: dict[tuple[str, int], int] = {}
        self._now = now

    async def hit(self, key: str, limit: int) -> RateDecision:
        start, left = _window(self._now())
        for k in [k for k in self._counts if k[1] < start]:
            del self._counts[k]
        count = self._counts.get((key, start), 0) + 1
        self._counts[(key, start)] = count
        return RateDecision(count <= limit, count, limit, left)


class ValkeyRateLimiter(RateLimiter):
    """``INCR`` on ``agent:rl:<key>:<window start>`` with a two-window expiry, in one pipeline."""

    def __init__(self, url: str, prefix: str = "agent:rl:") -> None:
        self._r = redis.from_url(url)
        self._prefix = prefix

    async def hit(self, key: str, limit: int) -> RateDecision:
        start, left = _window(time.time())
        name = f"{self._prefix}{key}:{start}"
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.incr(name)
            pipe.expire(name, 2 * WINDOW_SECONDS)
            count, _ = await pipe.execute()
        return RateDecision(int(count) <= limit, int(count), limit, left)

    async def aclose(self) -> None:
        await self._r.aclose()
