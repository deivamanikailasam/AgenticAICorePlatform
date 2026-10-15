import json

import pytest
import respx
from agent_contracts.models import RunContext
from agent_tools.identity import ExchangedToken, subject_token
from agent_tools.ragcore import RagCoreSearchTool

CTX = RunContext(tenant_id="acme", user_id="alice", agent_id="a1")


class FakeExchanger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def exchange(self, token: str, audience: str) -> ExchangedToken:
        self.calls.append((token, audience))
        return ExchangedToken("downscoped-for-" + audience, audience, 9e9)


async def test_refuses_without_a_user() -> None:
    with pytest.raises(PermissionError, match="on behalf of a user"):
        await RagCoreSearchTool("http://rag", FakeExchanger()).invoke({"query": "x"}, CTX)  # type: ignore[arg-type]


@respx.mock
async def test_calls_rag_core_with_an_exchanged_token_never_the_users() -> None:
    route = respx.post("http://rag/v1/search").respond(
        200, json={"results": [{"id": "d1", "snippet": "s", "citation": {"doc_id": "d1"}}]}
    )
    ex = FakeExchanger()
    reset = subject_token.set("alice-raw-token")
    try:
        out = await RagCoreSearchTool("http://rag", ex).invoke({"query": "refund", "k": 3}, CTX)  # type: ignore[arg-type]
    finally:
        subject_token.reset(reset)
    assert out["results"][0]["id"] == "d1"
    assert ex.calls == [("alice-raw-token", "rag-core")]
    sent = route.calls[0].request
    assert sent.headers["authorization"] == "Bearer downscoped-for-rag-core"
    assert json.loads(sent.content) == {"query": "refund", "k": 3, "architecture": "naive"}
