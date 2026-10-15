from __future__ import annotations

from collections.abc import Sequence

from agent_contracts.dummies._util import mark_dummies, words
from agent_contracts.interfaces import Critic, Planner, Router
from agent_contracts.models import Critique, Plan, PlanStep, RouteDecision, RunContext


class SingleStepPlanner(Planner):
    """A plan with exactly one step: the goal itself."""

    async def plan(self, goal: str, tools: Sequence[str], ctx: RunContext) -> Plan:
        return Plan(goal=goal, steps=(PlanStep(id="s1", description=goal),))


class PerToolPlanner(Planner):
    """One step per available tool, chained in order; one step if there are no tools."""

    async def plan(self, goal: str, tools: Sequence[str], ctx: RunContext) -> Plan:
        if not tools:
            return Plan(goal=goal, steps=(PlanStep(id="s1", description=goal),))
        steps = tuple(
            PlanStep(
                id=f"s{i + 1}",
                description=f"use {t} for: {goal}",
                tool=t,
                depends_on=(f"s{i}",) if i else (),
            )
            for i, t in enumerate(tools)
        )
        return Plan(goal=goal, steps=steps)


class KeywordRouter(Router):
    """Picks the route whose name appears in the text; otherwise the first, with low confidence."""

    async def route(self, text: str, routes: Sequence[str], ctx: RunContext) -> RouteDecision:
        if not routes:
            raise ValueError("no routes")
        tokens = words(text)
        for r in routes:
            if words(r) & tokens:
                return RouteDecision(route=r, confidence=0.9, reason="keyword match")
        return RouteDecision(route=routes[0], confidence=0.1, reason="default route")


class FirstRouter(Router):
    """Always the first route (a fixed path, useful as a baseline)."""

    async def route(self, text: str, routes: Sequence[str], ctx: RunContext) -> RouteDecision:
        if not routes:
            raise ValueError("no routes")
        return RouteDecision(route=routes[0], confidence=0.5, reason="fixed")


class LengthCritic(Critic):
    """Passes outputs of at least ``min_words`` words."""

    def __init__(self, min_words: int = 3) -> None:
        self.min_words = min_words

    async def critique(self, task: str, output: str, ctx: RunContext) -> Critique:
        n = len(output.split())
        score = min(1.0, n / self.min_words)
        return Critique(score=score, passed=n >= self.min_words, feedback=f"{n} words")


class OverlapCritic(Critic):
    """Scores the share of task words that the output mentions."""

    def __init__(self, threshold: float = 0.3) -> None:
        self.threshold = threshold

    async def critique(self, task: str, output: str, ctx: RunContext) -> Critique:
        t = words(task)
        score = len(t & words(output)) / len(t) if t else 0.0
        return Critique(score=score, passed=score >= self.threshold and bool(output.strip()))


mark_dummies(globals())
