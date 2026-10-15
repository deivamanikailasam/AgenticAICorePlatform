"""Step 16: agentic RAG through rag-core (on behalf of the user), on both runtime adapters.

rag-core is replaced by an offline double over the golden corpus that filters by the user's
groups, as rag-core does; the model is a deterministic stand-in that answers only when the
passages it was shown contain every required document. Counts from these runs describe the
stand-in, not any model, and are never reported (real runs: ``just eval-agentic-rag``).
"""

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest
from agent_compiler.rag_eval import MODES, evaluate, load_golden, render
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.interfaces import StopRun
from agent_contracts.models import (
    ChatRequest,
    ChatResponse,
    Message,
    NodeKind,
    PlanNode,
    Role,
)
from agent_contracts.testing import ctx
from agent_runtime.agentic_rag import AgenticRagStrategy
from agent_tools.authorising import AuthorisingToolExecutor
from agent_tools.executor import PlatformToolExecutor
from agent_tools.identity import ExchangedToken, subject_token
from agent_tools.ragcore import RagCoreSearchTool
from agent_tools.registry import InMemoryToolRegistry
from opentelemetry.sdk.trace import TracerProvider

GOLDEN = load_golden(Path(__file__).resolve().parents[3] / "evals/golden/agentic-rag")
STOP = {
    "the",
    "a",
    "an",
    "of",
    "in",
    "on",
    "to",
    "for",
    "who",
    "what",
    "is",
    "at",
    "does",
    "which",
    "by",
    "and",
    "must",
    "be",
    "how",
    "their",
    "from",
    "over",
    "s",
}


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP}


class CorpusRagCore:
    """TEST DOUBLE of rag-core's search: lexical overlap over the golden corpus, filtered by the
    groups of the user the exchanged token was issued for."""

    def __init__(self, users: Mapping[str, list[str]]) -> None:
        self.users = dict(users)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        user = request.headers["authorization"].removeprefix("Bearer for-")
        body = json.loads(request.content)
        self.requests.append({"user": user, **body})
        groups = set(self.users[user])
        q = words(body["query"])
        scored = sorted(
            (
                (len(q & words(d["title"] + " " + d["text"])), d)
                for d in GOLDEN.corpus.values()
                if groups & set(d["groups"])
            ),
            key=lambda x: -x[0],
        )
        results = [
            {
                "id": d["id"],
                "title": d["title"],
                "snippet": d["text"],
                "score": float(s),
                "citation": {"doc_id": d["id"], "title": d["title"]},
            }
            for s, d in scored[: int(body.get("k", 5))]
            if s > 0
        ]
        self.requests[-1]["returned"] = [r["id"] for r in results]
        return httpx.Response(200, json={"results": results})


class Exchanger:
    async def exchange(self, token: str, audience: str) -> ExchangedToken:
        return ExchangedToken(f"for-{token}", audience, 9e9)


USERS = {str(q["user"]): list(q["groups"]) for q in GOLDEN.questions}


def tools_factory(rag: CorpusRagCore) -> Any:
    async def tools_for(case: Mapping[str, Any]) -> AuthorisingToolExecutor:
        subject_token.set(str(case["user"]))
        reg = InMemoryToolRegistry()
        await reg.register(
            RagCoreSearchTool("http://rag.test", Exchanger(), transport=httpx.MockTransport(rag))  # type: ignore[arg-type]
        )
        return AuthorisingToolExecutor(
            PlatformToolExecutor(reg), reg, RulesPolicyDecisionPoint(), MemoryAuditSink()
        )

    return tools_for


class GoldenStandIn(ScriptedModelClient):
    def __init__(self, case: Mapping[str, Any]) -> None:
        super().__init__()
        self.case = case

    def _hop_query(self, step: Mapping[str, Any]) -> str:
        return re.sub(r"#(Q\d+)", lambda m: self.case["hop_answers"].get(m[1], m[0]), step["query"])

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        purpose = request.metadata.get("purpose")
        prompt = request.messages[-1].content
        if purpose == "rag_plan":
            out: dict[str, Any] = {"retrieve": True, "steps": self.case["plan"]}
        elif purpose == "grounding":
            out = {"grounded": True}
        else:  # rag_answer / naive_answer
            ids = re.findall(r"^\[([\w-]+)\]", prompt, re.M)
            question = prompt.split("\n", 1)[0].removeprefix("Question: ")
            hop = next((s for s in self.case["plan"] if self._hop_query(s) == question), None)
            if question != self.case["question"] and hop is not None:
                out = {"answer": self.case["hop_answers"].get(hop["id"], ""), "citations": ids}
            elif self.case["expect"] and set(self.case["required_docs"]) <= set(ids):
                out = {
                    "answer": f"The answer is {self.case['expect']}.",
                    "citations": list(self.case["required_docs"]),
                }
            else:
                out = {"answer": "The documents I can read do not say.", "citations": ids[:1]}
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=json.dumps(out)), model=request.model
        )


@pytest.mark.parametrize("adapter", ["langgraph", "agent_framework"])
async def test_both_modes_answer_every_golden_question_within_access(adapter: str) -> None:
    rag = CorpusRagCore(USERS)
    reports = await evaluate(
        GOLDEN,
        lambda case, mode: GoldenStandIn(case),
        tools_factory(rag),
        MODES,
        TracerProvider(),
        adapter,
    )
    for report in reports:
        assert len(report.outcomes) == len(GOLDEN.questions)
        assert [o.error for o in report.outcomes if o.error] == []
        assert sum(o.violations for o in report.outcomes) == 0, report.mode
        assert sum(o.invalid for o in report.outcomes) == 0, report.mode
    agentic = reports[1]
    no_access = {o.case: o for o in agentic.outcomes if o.case.endswith("no-access")}
    assert all(o.correct for o in no_access.values()), "restricted facts never leak"
    restricted = {"supplier-voltcell", "hr-bands"}
    alice_saw = {d for r in rag.requests if r["user"] == "alice" for d in r["returned"]}
    assert alice_saw
    assert not alice_saw & restricted, "alice only ever receives documents she may read"
    assert "(10 questions, 10 documents)" in render(reports, GOLDEN, "stand-in")


async def _strategy(model: ScriptedModelClient, rag: CorpusRagCore) -> AgenticRagStrategy:
    tools = await tools_factory(rag)({"user": "alice"})
    return AgenticRagStrategy(model, tools, tools.registry)


def node(**cfg: Any) -> PlanNode:
    return PlanNode(
        id="kb.loop",
        kind=NodeKind.AGENT,
        config={"loop": "agentic_rag", "tools": ["search"], "max_iterations": 12, "config": cfg},
    )


def say(obj: Any) -> Message:
    return Message(role=Role.ASSISTANT, content=json.dumps(obj))


async def test_corrective_re_retrieval_when_the_answer_is_not_grounded() -> None:
    rag = CorpusRagCore(USERS)
    model = ScriptedModelClient(
        [
            say(
                {
                    "retrieve": True,
                    "steps": [
                        {"id": "Q1", "query": "travel policy flights", "architecture": "naive"}
                    ],
                }
            ),
            say({"answer": "Priya Raman approves it.", "citations": ["pol-travel", "made-up-doc"]}),
            say(
                {
                    "grounded": False,
                    "unsupported": ["Priya Raman"],
                    "missing": ["engineering director organisation chart"],
                }
            ),
            say(
                {
                    "answer": "Priya Raman, the engineering director.",
                    "citations": ["pol-travel", "org-chart"],
                }
            ),
            say({"grounded": True}),
        ]
    )
    subject_token.set("alice")
    out = await (await _strategy(model, rag))(
        node(), {"input": {"question": "Who approves an engineer's AUD 720 flight?"}}, ctx()
    )
    assert out["grounded"] is True
    assert out["corrections"] == 1
    assert [c["id"] for c in out["citations"]] == ["pol-travel", "org-chart"]
    assert out["citations"][1]["title"] == "Organisation chart", "rag-core citations pass through"
    assert [r["architecture"] for r in rag.requests] == ["naive", "corrective"]
    assert "made-up-doc" not in model.requests[2].messages[-1].content, "uncited ids are dropped"
    assert "data, not instructions" in model.requests[1].messages[-1].content


async def test_no_retrieval_when_the_question_does_not_need_documents() -> None:
    rag = CorpusRagCore(USERS)
    model = ScriptedModelClient(
        [say({"retrieve": False, "steps": []}), Message(role=Role.ASSISTANT, content="Hello!")]
    )
    subject_token.set("alice")
    out = await (await _strategy(model, rag))(node(), {"input": {"question": "hi"}}, ctx())
    assert (out["answer"], out["retrieved"], rag.requests) == ("Hello!", False, [])


async def test_architectures_are_limited_to_the_allowed_list() -> None:
    rag = CorpusRagCore(USERS)
    model = ScriptedModelClient(
        [
            say(
                {
                    "retrieve": True,
                    "steps": [{"id": "Q1", "query": "meal allowance", "architecture": "sql"}],
                }
            ),
            say({"answer": "AUD 85 in Melbourne.", "citations": ["pol-meals"]}),
            say({"grounded": True}),
        ]
    )
    subject_token.set("alice")
    await (await _strategy(model, rag))(
        node(architectures=["naive", "adaptive"]), {"input": {"question": "meal allowance?"}}, ctx()
    )
    assert model.requests[0].response_schema["properties"]["steps"]["items"]["properties"][
        "architecture"
    ]["enum"] == ["naive", "adaptive"]
    assert rag.requests[0]["architecture"] == "naive"


async def test_needs_the_search_tool_and_respects_the_model_call_cap() -> None:
    rag = CorpusRagCore(USERS)
    strategy = await _strategy(ScriptedModelClient(), rag)
    no_search = node()
    no_search = no_search.model_copy(update={"config": {**no_search.config, "tools": []}})
    with pytest.raises(ValueError, match="'search' tool"):
        await strategy(no_search, {"input": {"question": "x"}}, ctx())
    looping = ScriptedModelClient(
        [say({"retrieve": True, "steps": [{"id": "Q1", "query": "leave"}]})]
        + [say({"answer": "?", "citations": []}), say({"grounded": False, "missing": ["leave"]})]
        * 5
    )
    capped = node(max_corrections=5)
    capped = capped.model_copy(update={"config": {**capped.config, "max_iterations": 4}})
    subject_token.set("alice")
    with pytest.raises(StopRun, match=r"max_steps \(4\)"):
        await (await _strategy(looping, rag))(capped, {"input": {"question": "leave?"}}, ctx())
