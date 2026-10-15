"""Circuit breakers on failing tools and models (Step 26).

A breaker per key (tool name, model role) counts consecutive failures. After ``failures`` in a
row it opens: calls are refused at once, without reaching the broken dependency, for
``cooldown`` seconds. Then one trial call is let through (half-open); success closes the
circuit, failure opens it again. Breakers are shared by every run in the process, so one run
discovering an outage spares the others.

Only failures that say something about the dependency count: timeouts, errors and rate limits.
A policy denial, bad arguments or a missing approval are about the call, not the tool.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from agent_contracts.interfaces import ModelClient, StopRun, ToolExecutor
from agent_contracts.models import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    ModelCapabilities,
    RunContext,
    ToolCall,
    ToolErrorCode,
    ToolResult,
)

COUNTED = {ToolErrorCode.TIMEOUT, ToolErrorCode.FAILED, ToolErrorCode.RATE_LIMITED}


@dataclass
class _State:
    failures: int = 0
    opened_at: float | None = None
    trial: bool = False


class CircuitBreaker:
    def __init__(
        self,
        failures: int = 5,
        cooldown: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.threshold, self.cooldown, self.clock = failures, cooldown, clock
        self._s: dict[str, _State] = {}

    def state(self, key: str) -> str:
        s = self._s.get(key)
        if s is None or s.opened_at is None:
            return "closed"
        return "half_open" if self.clock() - s.opened_at >= self.cooldown else "open"

    def allow(self, key: str) -> bool:
        """May a call go through now? In half-open state only one trial at a time."""
        state = self.state(key)
        if state == "closed":
            return True
        if state == "half_open" and not self._s[key].trial:
            self._s[key].trial = True
            return True
        return False

    def success(self, key: str) -> None:
        self._s[key] = _State()

    def failure(self, key: str) -> None:
        s = self._s.setdefault(key, _State())
        s.failures += 1
        s.trial = False
        if s.opened_at is not None or s.failures >= self.threshold:
            s.opened_at = self.clock()

    def retry_in(self, key: str) -> float:
        s = self._s.get(key)
        if s is None or s.opened_at is None:
            return 0.0
        return max(0.0, self.cooldown - (self.clock() - s.opened_at))


class CircuitBreakingToolExecutor(ToolExecutor):
    def __init__(self, inner: ToolExecutor, breaker: CircuitBreaker) -> None:
        self.inner = inner
        self.breaker = breaker
        self.registry = getattr(inner, "registry", None)

    async def execute(self, call: ToolCall, ctx: RunContext) -> ToolResult:
        if not self.breaker.allow(call.name):
            return ToolResult(
                call_id=call.id,
                tool=call.name,
                ok=False,
                error_code=ToolErrorCode.UNAVAILABLE,
                error=f"tool {call.name} is unavailable after repeated failures "
                f"(circuit open; retry in {self.breaker.retry_in(call.name):.0f} s)",
            )
        result = await self.inner.execute(call, ctx)
        if result.ok:
            self.breaker.success(call.name)
        elif result.error_code in COUNTED:
            self.breaker.failure(call.name)
        return result


class CircuitBreakingModelClient(ModelClient):
    """A model (role or alias) that keeps failing stops the run with the reason rather than
    hammering the gateway; the run keeps its partial state."""

    def __init__(self, inner: ModelClient, breaker: CircuitBreaker) -> None:
        self.inner = inner
        self.breaker = breaker

    def capabilities(self, model: str) -> ModelCapabilities:
        return self.inner.capabilities(model)

    def _refuse(self, model: str) -> None:
        if not self.breaker.allow(f"model:{model}"):
            raise StopRun(
                f"circuit open: model {model} failed repeatedly "
                f"(retry in {self.breaker.retry_in(f'model:{model}'):.0f} s)"
            )

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self._refuse(request.model)
        try:
            response = await self.inner.chat(request)
        except StopRun:
            raise
        except Exception:
            self.breaker.failure(f"model:{request.model}")
            raise
        self.breaker.success(f"model:{request.model}")
        return response

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        self._refuse(request.model)
        try:
            async for chunk in self.inner.stream(request):
                yield chunk
        except Exception:
            self.breaker.failure(f"model:{request.model}")
            raise
        self.breaker.success(f"model:{request.model}")
