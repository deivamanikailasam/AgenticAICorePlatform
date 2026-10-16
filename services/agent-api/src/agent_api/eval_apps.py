"""Eval harnesses for the reference apps that need no app-specific services (Step 34):
hello-workflow and the five team specs.

Tools are the platform's own: calculator, word count, clock, and the rag-core search tool. The
search tool talks to rag-core's HTTP API as in production; here that API is answered from the
documents the tasks carry (``input._corpus``), so research tasks have known sources and known
figures. Workers are resolved from the spec's directory, as the run API does.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
from agent_evals.tasks import Task
from agent_spec import AgentSpec, load_spec_text
from agent_spec.platform import PlatformConfig
from agent_tools.builtin import CalculatorTool, ClockTool, WordCountTool
from agent_tools.identity import ExchangedToken
from agent_tools.ragcore import RagCoreSearchTool
from agent_tools.registry import InMemoryToolRegistry

from agent_api.runs import SPEC_PATHS

ROOT = Path(__file__).resolve().parents[4]


class _Exchanger:
    async def exchange(self, subject_token: str, audience: str) -> ExchangedToken:
        return ExchangedToken(f"eval-{audience}", audience, 9e9)

    async def aclose(self) -> None:
        return None


class SpecHarness:
    """A reference app evaluated from its spec alone."""

    def __init__(self, agent: str) -> None:
        self.agent = agent
        self.path = ROOT / "agents" / agent / "agent.yaml"
        self.corpus: list[dict[str, Any]] = []

    def spec(self) -> AgentSpec:
        text = self.path.read_text().replace("durability: checkpoint", "durability: memory")
        spec = load_spec_text(text, str(self.path))
        SPEC_PATHS[spec.name] = self.path.resolve()  # workers resolve next to the spec
        return spec

    def platform(self, base: PlatformConfig) -> PlatformConfig:
        return base

    async def prepare(self, tasks: Sequence[Task]) -> None:
        self.corpus = [doc for t in tasks for doc in t.input.get("_corpus") or []]

    def _search(self, request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content or b"{}").get("query", "")
        words = {w for w in re.findall(r"[a-z0-9]+", str(query).lower()) if len(w) > 2}
        scored = sorted(
            (
                (
                    len(
                        words
                        & set(re.findall(r"[a-z0-9]+", (d["title"] + " " + d["text"]).lower()))
                    ),
                    d,
                )
                for d in self.corpus
            ),
            key=lambda x: -x[0],
        )
        hits = [d for n, d in scored if n > 0][:3]
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": d["id"],
                        "snippet": d["text"],
                        "citation": {"doc_id": d["id"], "title": d["title"]},
                    }
                    for d in hits
                ]
            },
        )

    async def registry(self, spec: AgentSpec) -> InMemoryToolRegistry:
        reg = InMemoryToolRegistry()
        for tool in (CalculatorTool(), WordCountTool(), ClockTool()):
            await reg.register(tool)
        await reg.register(
            RagCoreSearchTool(
                "http://rag-core.eval", _Exchanger(), transport=httpx.MockTransport(self._search)
            )
        )
        return reg

    async def close(self) -> None:
        return None


def hello_workflow() -> SpecHarness:
    return SpecHarness("hello-workflow")


def support_handoff() -> SpecHarness:
    return SpecHarness("support-handoff")


def research_report() -> SpecHarness:
    return SpecHarness("research-report")


def review_chat() -> SpecHarness:
    return SpecHarness("review-chat")


def batch_summary() -> SpecHarness:
    return SpecHarness("batch-summary")


def policy_debate() -> SpecHarness:
    return SpecHarness("policy-debate")
