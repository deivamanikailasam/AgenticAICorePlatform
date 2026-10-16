"""Golden tasks (Step 34): one input and what a correct run looks like.

A task set is a JSON Lines file, one task per line, under ``evals/apps/<app>/tasks.jsonl``. Each
task names what it checks; checks that a task doesn't name are not scored for it, so a task is
never marked wrong on something nobody specified.

``expected`` keys, all optional:

* ``status`` — the run's end state (``succeeded``, ``interrupted`` for a pending approval,
  ``stopped`` for a limit, ``failed``).
* ``answer`` — checks on the final output: ``contains`` / ``not_contains`` (case-insensitive
  substrings), ``regex``, ``equals`` (at ``path`` in a JSON output), ``number`` (``value`` and
  ``tolerance`` at ``path``).
* ``tools`` — the trajectory: ``expected`` (the tool calls in order), ``required``, ``forbidden``,
  ``max_extra`` (unnecessary calls allowed beyond ``expected``), ``arguments`` (per tool, values
  the call's arguments must have).
* ``policy`` — ``approval_required`` (tools that must stop for a person), ``denied`` (tools
  policy must refuse), ``no_guardrail_block`` (true: no guardrail may block).
* ``limits`` — ``max_steps``, ``max_tokens``, ``max_cost_aud``, ``max_latency_ms``.
* ``rubric`` — a judge rubric (``evals/rubrics/<name>.md``) for what deterministic checks can't
  decide; used only when the judge is enabled.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AnswerCheck(_Model):
    contains: tuple[str, ...] = ()
    not_contains: tuple[str, ...] = ()
    regex: str | None = None
    path: str | None = None  # dotted path into a JSON output for equals / number
    equals: Any = None
    number: float | None = None
    tolerance: float = 0.0


class ToolsCheck(_Model):
    expected: tuple[str, ...] = ()
    required: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    max_extra: int | None = None
    arguments: dict[str, dict[str, Any]] = Field(default_factory=dict)


class PolicyCheck(_Model):
    approval_required: tuple[str, ...] = ()
    denied: tuple[str, ...] = ()
    no_guardrail_block: bool = False


class Limits(_Model):
    max_steps: int | None = None
    max_tokens: int | None = None
    max_cost_aud: float | None = None
    max_latency_ms: int | None = None


class Expected(_Model):
    status: str | None = None
    answer: AnswerCheck | None = None
    tools: ToolsCheck | None = None
    policy: PolicyCheck | None = None
    limits: Limits | None = None
    rubric: str | None = None


class Task(_Model):
    id: str
    app: str
    input: dict[str, Any]
    expected: Expected
    tags: tuple[str, ...] = ()
    #: how the case was made, e.g. "generated: invoice with a GST error" (never a model's output)
    origin: str = ""


def load_tasks(path: str | Path) -> list[Task]:
    tasks = [Task.model_validate_json(line) for line in Path(path).read_text().splitlines() if line]
    ids = [t.id for t in tasks]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"{path}: duplicate task ids {dupes}")
    return tasks


def dataset_digest(path: str | Path) -> str:
    """The task set's SHA-256: results name exactly which version of the set they ran on."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump_tasks(tasks: list[Task], path: str | Path) -> None:
    lines = [
        json.dumps(t.model_dump(mode="json", exclude_defaults=True), sort_keys=True) for t in tasks
    ]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines) + "\n")
