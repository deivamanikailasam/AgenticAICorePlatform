"""Model-backed Planner and Critic (Step 14): ``planner.llm`` and ``critic.llm``.

Both ask the platform model client for structured output, so a plan or critique is always a
validated object, never free text to be parsed. The planner may only name tools it was given;
the critic fails empty output without asking a model.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from agent_contracts.interfaces import Critic, ModelClient, Planner
from agent_contracts.models import (
    ChatRequest,
    Critique,
    Message,
    Plan,
    PlanStep,
    Role,
    RunContext,
)

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "description": {"type": "string"},
                    "tool": {"type": ["string", "null"]},
                    "arguments": {"type": "object"},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "description"],
            },
        }
    },
    "required": ["steps"],
}
CRITIQUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "passed": {"type": "boolean"},
        "feedback": {"type": "string"},
    },
    "required": ["score", "passed", "feedback"],
}

PLANNER_SYSTEM = (
    "You plan before acting. Break the goal into the fewest steps. Each step may use one of the "
    "listed tools (or none). Give tool arguments when you know them; to use an earlier step's "
    "result write #E<n> (step n's whole result) or #E<n>.<field>. Number step ids E1, E2, ..."
)
CRITIC_SYSTEM = (
    "You review an answer against its task. Score from 0 to 1 how well it solves the task, say "
    "whether it passes, and give short, specific feedback for improving it."
)


class LlmPlanner(Planner):
    def __init__(self, model: ModelClient, role: str = "planner") -> None:
        self.model = model
        self.role = role

    async def plan(self, goal: str, tools: Sequence[str], ctx: RunContext) -> Plan:
        response = await self.model.chat(
            ChatRequest(
                model=self.role,
                messages=(
                    Message(role=Role.SYSTEM, content=PLANNER_SYSTEM),
                    Message(
                        role=Role.USER,
                        content=f"Goal: {goal}\nTools: {', '.join(tools) or 'none'}",
                    ),
                ),
                response_schema=PLAN_SCHEMA,
                metadata={"run_id": ctx.run_id, "purpose": "plan"},
            )
        )
        data = json.loads(response.message.content)
        allowed = set(tools)
        steps = []
        for raw in data["steps"]:
            tool = raw.get("tool") or None
            if tool is not None and tool not in allowed:
                raise ValueError(f"the plan uses tool '{tool}', which is not available")
            steps.append(
                PlanStep(
                    id=str(raw["id"]),
                    description=str(raw["description"]),
                    tool=tool,
                    arguments=dict(raw.get("arguments") or {}),
                    depends_on=tuple(str(d) for d in raw.get("depends_on", ())),
                )
            )
        ids = {s.id for s in steps}
        for s in steps:
            if not set(s.depends_on) <= ids:
                raise ValueError(f"step {s.id} depends on an unknown step")
        return Plan(goal=goal, steps=tuple(steps))


class LlmCritic(Critic):
    def __init__(self, model: ModelClient, role: str = "judge") -> None:
        self.model = model
        self.role = role

    async def critique(self, task: str, output: str, ctx: RunContext) -> Critique:
        if not output.strip():
            return Critique(score=0.0, passed=False, feedback="the answer is empty")
        response = await self.model.chat(
            ChatRequest(
                model=self.role,
                messages=(
                    Message(role=Role.SYSTEM, content=CRITIC_SYSTEM),
                    Message(role=Role.USER, content=f"Task: {task}\n\nAnswer: {output}"),
                ),
                response_schema=CRITIQUE_SCHEMA,
                metadata={"run_id": ctx.run_id, "purpose": "critique"},
            )
        )
        data = json.loads(response.message.content)
        return Critique(
            score=min(max(float(data["score"]), 0.0), 1.0),
            passed=bool(data["passed"]),
            feedback=str(data.get("feedback", "")),
        )
