from __future__ import annotations

from collections.abc import Iterable, Sequence

from agent_contracts.interfaces import Evaluator, Simulator
from agent_contracts.models import EvalCase, EvalScore, Message, Role, RunTrace, SimTurn


class ExactMatchEvaluator(Evaluator):
    """1.0 when the output equals ``expected['output']`` (after trimming strings)."""

    async def evaluate(self, case: EvalCase, trace: RunTrace) -> EvalScore:
        expected = case.expected.get("output")
        got = trace.output
        if isinstance(expected, str) and isinstance(got, str):
            expected, got = expected.strip(), got.strip()
        value = 1.0 if got == expected else 0.0
        return EvalScore(
            evaluator="exact_match", metric="correctness", value=value, passed=value == 1.0
        )


class ToolRecallEvaluator(Evaluator):
    """Share of ``expected['tools']`` that the trajectory actually called."""

    async def evaluate(self, case: EvalCase, trace: RunTrace) -> EvalScore:
        wanted = set(case.expected.get("tools", []))
        called = {s.name for s in trace.trajectory if s.kind == "tool"}
        value = len(wanted & called) / len(wanted) if wanted else 1.0
        return EvalScore(
            evaluator="tool_recall",
            metric="trajectory.tool_recall",
            value=value,
            passed=value >= 1.0,
            details={"missing": sorted(wanted - called)},
        )


class ScriptedUserSimulator(Simulator):
    """Plays a fixed list of user turns, then says it is done."""

    def __init__(self, turns: Iterable[str] = ("hello", "thanks")) -> None:
        self._turns = list(turns)

    async def next_turn(self, history: Sequence[Message]) -> SimTurn:
        asked = sum(1 for m in history if m.role is Role.USER)
        if asked >= min(len(self._turns), self.max_turns):
            return SimTurn(done=True, reason="script finished")
        return SimTurn(message=Message(role=Role.USER, content=self._turns[asked]))


class PatientUserSimulator(Simulator):
    """Repeats one goal until an assistant reply contains ``success_word`` or patience runs out."""

    def __init__(
        self, goal: str = "book a meeting", success_word: str = "done", patience: int = 3
    ) -> None:
        self.goal, self.success_word, self.patience = (
            goal,
            success_word,
            min(patience, self.max_turns),
        )

    async def next_turn(self, history: Sequence[Message]) -> SimTurn:
        if any(
            m.role is Role.ASSISTANT and self.success_word in m.content.lower() for m in history
        ):
            return SimTurn(done=True, reason="goal reached")
        if sum(1 for m in history if m.role is Role.USER) >= self.patience:
            return SimTurn(done=True, reason="patience exhausted")
        return SimTurn(message=Message(role=Role.USER, content=self.goal))
