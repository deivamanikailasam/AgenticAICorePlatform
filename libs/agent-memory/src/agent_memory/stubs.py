"""Memory stores the catalogue lists as documented stubs (ADR 0031). They refuse clearly.

Checked 2026-10-15 against each project's own README:

* **Zep**: "Zep Community Edition is no longer supported"; the supported product is Zep Cloud,
  which needs an account and API key (and sends memory to a third party).
* **Letta**: the repository's V1 API server, which held archival memory, is "retired"; active
  projects are pointed at Letta Code. There is no supported self-hosted memory API to build on.

Finish either when the owner supplies a cloud account and accepts the data-residency trade-off,
or when a supported self-hosted server returns.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, NoReturn

from agent_contracts.interfaces import MemoryStore
from agent_contracts.models import MemoryItem, MemoryScope


class _Stub(MemoryStore):
    component_status = "stub"
    NAME = ""

    def __init__(self, **_: Any) -> None:
        pass

    def _refuse(self) -> NoReturn:
        raise NotImplementedError(
            f"the {self.NAME} memory store is a documented stub (ADR 0031); use pgvector, "
            "qdrant or mem0"
        )

    async def put(self, item: MemoryItem) -> str:
        self._refuse()

    async def get(self, scope: MemoryScope, item_id: str) -> MemoryItem | None:
        self._refuse()

    async def search(self, scope: MemoryScope, query: str, k: int = 5) -> Sequence[MemoryItem]:
        self._refuse()

    async def delete(self, scope: MemoryScope, item_id: str) -> bool:
        self._refuse()

    async def erase_subject(self, tenant_id: str, user_id: str) -> int:
        self._refuse()

    async def items(self, scope: MemoryScope, limit: int = 100) -> Sequence[MemoryItem]:
        self._refuse()

    async def flush_run(self, tenant_id: str, run_id: str) -> int:
        self._refuse()


class ZepMemoryStore(_Stub):
    """Zep. STUB: Community Edition is unsupported; Zep Cloud needs an account and API key."""

    NAME = "zep"


class LettaMemoryStore(_Stub):
    """Letta. STUB: the self-hosted V1 API server (archival memory) is retired."""

    NAME = "letta"
