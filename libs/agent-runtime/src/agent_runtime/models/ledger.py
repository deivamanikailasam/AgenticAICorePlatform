from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from agent_contracts.models import Usage


@dataclass
class UsageLedger:
    """Token and AUD cost per run, broken down by role and alias. In memory; Step 26 persists it."""

    _rows: dict[str, list[tuple[str, str, Usage]]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def record(self, run_id: str, role: str, alias: str, usage: Usage) -> None:
        self._rows[run_id].append((role, alias, usage))

    def total(self, run_id: str) -> Usage:
        total = Usage()
        for _, _, u in self._rows.get(run_id, []):
            total = total + u
        return total

    def by_role(self, run_id: str) -> dict[str, Usage]:
        out: dict[str, Usage] = defaultdict(Usage)
        for role, _, u in self._rows.get(run_id, []):
            out[role] = out[role] + u
        return dict(out)

    def calls(self, run_id: str) -> int:
        return len(self._rows.get(run_id, []))
