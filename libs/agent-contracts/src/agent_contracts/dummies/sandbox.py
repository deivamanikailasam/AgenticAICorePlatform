from __future__ import annotations

from collections.abc import Callable

from agent_contracts.dummies._util import mark_dummies
from agent_contracts.interfaces import CodeSandbox
from agent_contracts.models import SandboxRequest, SandboxResult, SandboxStatus


class UnavailableSandbox(CodeSandbox):
    """Refuses every request: the safe default when no isolation engine is installed."""

    isolation = "none"

    def available(self) -> bool:
        return False

    async def run(self, request: SandboxRequest) -> SandboxResult:
        return SandboxResult(
            status=SandboxStatus.REFUSED,
            reasons=("no code sandbox is installed",),
            engine="unavailable",
        )


class ScriptedSandbox(CodeSandbox):
    """Returns a scripted result without executing anything (for testing tools and patterns)."""

    isolation = "none"

    def __init__(self, respond: Callable[[SandboxRequest], SandboxResult] | None = None) -> None:
        self._respond = respond or (
            lambda r: SandboxResult(
                status=SandboxStatus.OK, stdout=f"[scripted] {len(r.code)} chars", engine="scripted"
            )
        )

    def available(self) -> bool:
        return True

    async def run(self, request: SandboxRequest) -> SandboxResult:
        return self._respond(request)


mark_dummies(globals())
