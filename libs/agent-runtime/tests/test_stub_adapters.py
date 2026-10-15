import pytest
from agent_contracts.testing.contracts import hello_plan
from agent_runtime.adapters.stubs import CrewAIAdapter


async def test_crewai_stub_refuses_clearly() -> None:
    rt = CrewAIAdapter()
    assert rt.component_status == "stub"
    with pytest.raises(NotImplementedError, match="documented stub"):
        await rt.build(hello_plan())
    with pytest.raises(KeyError):
        await rt.get_state("nope")
