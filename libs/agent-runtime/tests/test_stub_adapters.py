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


async def test_google_adk_loop_stub_explains_where_adk_runs() -> None:
    from agent_contracts.testing import ctx
    from agent_contracts.testing.agent_loop import _node
    from agent_runtime.engines.stubs import GoogleAdkLoopStub

    stub = GoogleAdkLoopStub()
    assert stub.component_status == "stub"
    with pytest.raises(NotImplementedError, match="separate A2A service"):
        await stub(_node(), {"input": {}}, ctx())
