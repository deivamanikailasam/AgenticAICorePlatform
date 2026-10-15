from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpecIssue:
    """One problem in a spec, phrased for a human: where, what, and how to fix it."""

    path: str  # dotted location, e.g. "budgets.max_steps" or "graph.nodes[2].tool"
    message: str
    line: int | None = None
    severity: str = "error"  # error | warning

    def render(self, source: str = "agent.yaml") -> str:
        where = f"{source}:{self.line}" if self.line else source
        loc = f" {self.path}:" if self.path else ""
        return f"{where}:{loc} {self.message}"


class SpecError(Exception):
    """Raised when a spec cannot be loaded. ``issues`` lists every problem found, not just the first."""

    def __init__(self, source: str, issues: list[SpecIssue]) -> None:
        self.source = source
        self.issues = issues
        super().__init__("\n".join(i.render(source) for i in issues))
