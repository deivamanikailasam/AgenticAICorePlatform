"""Idempotency stores: the second call with the same key returns the first call's result.

Keys are scoped by tenant and tool. ``claim`` reserves a key atomically so two concurrent calls
cannot both execute a side effect.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

import redis.asyncio as redis
from agent_contracts.models import ToolResult

PENDING = "__pending__"


def _text(raw: bytes | str | None) -> str | None:
    return raw.decode() if isinstance(raw, bytes) else raw


class IdempotencyStore(ABC):
    @abstractmethod
    async def claim(self, key: str, ttl_seconds: int) -> bool:
        """Reserve ``key``. False if it already exists (pending or done)."""

    @abstractmethod
    async def get(self, key: str) -> ToolResult | None:
        """The stored result; None if absent or still pending."""

    @abstractmethod
    async def pending(self, key: str) -> bool: ...

    @abstractmethod
    async def put(self, key: str, result: ToolResult, ttl_seconds: int) -> None: ...

    @abstractmethod
    async def release(self, key: str) -> None:
        """Drop a claim so the call may be retried (used after a safe, retryable failure)."""


class MemoryIdempotencyStore(IdempotencyStore):
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, str]] = {}

    def _live(self, key: str) -> str | None:
        item = self._data.get(key)
        if item is None or item[0] < time.monotonic():
            self._data.pop(key, None)
            return None
        return item[1]

    async def claim(self, key: str, ttl_seconds: int) -> bool:
        if self._live(key) is not None:
            return False
        self._data[key] = (time.monotonic() + ttl_seconds, PENDING)
        return True

    async def get(self, key: str) -> ToolResult | None:
        value = self._live(key)
        return None if value in (None, PENDING) else ToolResult.model_validate_json(str(value))

    async def pending(self, key: str) -> bool:
        return self._live(key) == PENDING

    async def put(self, key: str, result: ToolResult, ttl_seconds: int) -> None:
        self._data[key] = (time.monotonic() + ttl_seconds, result.model_dump_json())

    async def release(self, key: str) -> None:
        self._data.pop(key, None)


class ValkeyIdempotencyStore(IdempotencyStore):
    """Keys ``idem:<tenant>:<tool>:<key>`` in Project 0's Valkey, with a TTL."""

    def __init__(self, url: str, prefix: str = "agent:idem:") -> None:
        self._r = redis.from_url(url)
        self._prefix = prefix

    async def claim(self, key: str, ttl_seconds: int) -> bool:
        return bool(await self._r.set(self._prefix + key, PENDING, nx=True, ex=ttl_seconds))

    async def get(self, key: str) -> ToolResult | None:
        raw = _text(await self._r.get(self._prefix + key))
        if raw is None or raw == PENDING:
            return None
        return ToolResult.model_validate_json(raw)

    async def pending(self, key: str) -> bool:
        return _text(await self._r.get(self._prefix + key)) == PENDING

    async def put(self, key: str, result: ToolResult, ttl_seconds: int) -> None:
        await self._r.set(self._prefix + key, result.model_dump_json(), ex=ttl_seconds)

    async def release(self, key: str) -> None:
        await self._r.delete(self._prefix + key)

    async def aclose(self) -> None:
        await self._r.aclose()
