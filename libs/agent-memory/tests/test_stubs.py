"""Zep and Letta are documented stubs: they refuse every call and point to ADR 0031."""

from typing import Any

import pytest
from agent_contracts.models import MemoryScope
from agent_contracts.registry import registry as default_registry
from agent_memory.stubs import LettaMemoryStore, ZepMemoryStore


@pytest.mark.parametrize("name", ["zep", "letta"])
def test_registered_as_stubs(name: str) -> None:
    store = default_registry.create("memory_store", name)
    assert store.component_status == "stub"


@pytest.mark.parametrize("stub", [ZepMemoryStore, LettaMemoryStore])
async def test_every_call_refuses_with_a_pointer_to_the_adr(stub: Any) -> None:
    store = stub()
    with pytest.raises(NotImplementedError, match=r"documented stub \(ADR 0031\)"):
        await store.search(MemoryScope(tenant_id="t1"), "anything")
    with pytest.raises(NotImplementedError, match="ADR 0031"):
        await store.erase_subject("t1", "u1")
