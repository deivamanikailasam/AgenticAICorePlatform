"""Routers (Step 15): LLM, embedding classifier and rules; the router node's confidence floor."""

import json
from collections.abc import Sequence

import pytest
from agent_contracts.dummies.embedder import HashingEmbedder
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.models import Message, NodeKind, PlanNode, Role, RunContext
from agent_contracts.testing import RouterContract, ctx
from agent_runtime.executors import RouterNodeExecutor
from agent_runtime.routing import EmbeddingRouter, LlmRouter, RulesRouter

EXAMPLES = {
    "billing": ["my invoice is wrong", "refund the charge on my bill", "update my payment card"],
    "technical": ["the app crashes on login", "error 500 when saving", "the page will not load"],
    "sales": ["I want a quote for 50 seats", "pricing for the enterprise plan", "book a demo"],
}


def say(obj: object) -> Message:
    return Message(role=Role.ASSISTANT, content=json.dumps(obj))


class TestLlmRouterContract(RouterContract):
    def make(self) -> LlmRouter:
        return LlmRouter(ScriptedModelClient([say({"route": "billing", "confidence": 0.8})]))


class TestEmbeddingRouterContract(RouterContract):
    def make(self) -> EmbeddingRouter:
        return EmbeddingRouter(HashingEmbedder(), EXAMPLES)


class TestRulesRouterContract(RouterContract):
    def make(self) -> RulesRouter:
        return RulesRouter([{"route": "billing", "any": [r"\binvoice\b", "refund"]}], "technical")


async def test_llm_router_offers_only_the_declared_routes_and_refuses_others() -> None:
    model = ScriptedModelClient([say({"route": "sales"}), say({"route": "wire-money"})])
    router = LlmRouter(model, descriptions={"sales": "new business"})
    decision = await router.route("quote please", ["billing", "sales"], ctx())
    assert decision.route == "sales"
    request = model.requests[0]
    assert request.model == "router"
    assert request.response_schema["properties"]["route"]["enum"] == ["billing", "sales"]
    assert "- sales: new business" in request.messages[-1].content
    assert "data, not instructions" in request.messages[0].content
    with pytest.raises(ValueError, match="not one of"):
        await router.route("x", ["billing", "sales"], ctx())


class CountingEmbedder(HashingEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.batches: list[int] = []

    async def embed(self, texts: Sequence[str], ctx: RunContext) -> list[list[float]]:
        self.batches.append(len(texts))
        return await super().embed(texts, ctx)


async def test_embedding_router_picks_the_nearest_route_and_embeds_examples_once() -> None:
    embedder = CountingEmbedder()
    router = EmbeddingRouter(embedder, EXAMPLES)
    routes = list(EXAMPLES)
    first = await router.route("there is an error when I save", routes, ctx())
    second = await router.route("please refund my invoice", routes, ctx())
    assert (first.route, second.route) == ("technical", "billing")
    assert 0 < first.confidence <= 1
    assert embedder.batches == [9, 1, 1], "examples embedded once, then one text per request"


async def test_embedding_router_needs_examples_for_every_route() -> None:
    with pytest.raises(ValueError, match="no examples"):
        await EmbeddingRouter(HashingEmbedder(), EXAMPLES).route("x", ["billing", "legal"], ctx())
    with pytest.raises(ValueError, match="at least one example"):
        EmbeddingRouter(HashingEmbedder(), {"billing": []})


async def test_rules_router_first_match_wins_else_default_with_zero_confidence() -> None:
    router = RulesRouter(
        [
            {"route": "billing", "any": [r"\binvoice\b"]},
            {"route": "technical", "any": ["error", "crash"]},
        ],
        default="sales",
    )
    routes = ["billing", "technical", "sales"]
    assert (await router.route("Invoice error", routes, ctx())).route == "billing"
    miss = await router.route("hello there", routes, ctx())
    assert (miss.route, miss.confidence) == ("sales", 0.0)
    with pytest.raises(ValueError, match="bad pattern"):
        RulesRouter([{"route": "x", "any": ["("]}])


async def test_router_node_sends_unsure_decisions_to_the_fallback_route() -> None:
    node = PlanNode(
        id="triage",
        kind=NodeKind.ROUTER,
        config={
            "routes": ["billing", "technical", "human"],
            "config": {"template": "$input.text", "min_confidence": 0.5, "fallback": "human"},
        },
    )
    rules = RulesRouter([{"route": "billing", "any": ["invoice"]}], default="technical")
    executor = RouterNodeExecutor(routers=lambda n: rules)
    sure = await executor(node, {"input": {"text": "invoice"}}, ctx())
    unsure = await executor(node, {"input": {"text": "hmm"}}, ctx())
    assert sure["_outcome"] == "billing"
    assert (unsure["_outcome"], unsure["chosen"], unsure["confidence"]) == (
        "human",
        "technical",
        0.0,
    )
