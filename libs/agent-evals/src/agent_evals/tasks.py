"""Golden tasks (Step 34): one input and what a correct run looks like.

A task set is a JSON Lines file, one task per line, under ``evals/apps/<app>/tasks.jsonl``. Each
task names what it checks; checks that a task doesn't name are not scored for it, so a task is
never marked wrong on something nobody specified.

``expected`` keys, all optional:

* ``status`` — the run's end state (``succeeded``, ``interrupted`` for a pending approval,
  ``stopped`` for a limit, ``failed``).
* ``answer`` — checks on the final output: ``contains`` / ``not_contains`` (case-insensitive
  substrings), ``regex``, ``equals`` (at ``path`` in a JSON output), ``number`` (``value`` and
  ``tolerance`` at ``path``), ``max_words``.
* ``tools`` — the trajectory: ``expected`` (the tool calls in order), ``required``, ``forbidden``,
  ``max_extra`` (unnecessary calls allowed beyond ``expected``), ``arguments`` (per tool, values
  the call's arguments must have).
* ``policy`` — ``approval_required`` (tools that must stop for a person),
  ``approval_arguments`` (what the approval request must show), ``not_requested`` (tools that
  must never get as far as an approval request), ``denied`` (tools policy must refuse),
  ``never_run`` (tools that must never run successfully, whatever stopped them), ``refused``
  (tools policy must refuse at least once), ``never_run_with`` (argument values a tool must
  never run with),
  ``no_guardrail_block`` (true: no guardrail may block).
* ``team`` — for multi-agent apps: ``workers_required``, ``workers_forbidden``,
  ``first_worker``, ``counts`` (exact delegations per worker), ``max_delegations``.
* ``limits`` — ``max_steps``, ``max_model_calls``, ``max_tokens``, ``max_cost_aud``,
  ``max_latency_ms``.
* ``conversation`` — for multi-turn tasks with a simulated person (``input._user``: persona,
  goal, patience; Step 35): ``goal_met`` and ``max_turns``.
* ``custom`` — a team's own evaluators (Step 39, agent-sdk): ``{name: config}``, each a callable
  registered under the ``agent_platform.evaluators`` entry-point group. A name with no
  evaluator installed, or an evaluator that raises, fails the task.
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
    max_words: int | None = None


class ToolsCheck(_Model):
    expected: tuple[str, ...] = ()
    required: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    max_extra: int | None = None
    arguments: dict[str, dict[str, Any]] = Field(default_factory=dict)


class PolicyCheck(_Model):
    approval_required: tuple[str, ...] = ()
    #: the arguments the approval request must show (what the person is asked to approve)
    approval_arguments: dict[str, dict[str, Any]] = Field(default_factory=dict)
    #: tools that must never reach an approval request (e.g. paying an invoice that is wrong)
    not_requested: tuple[str, ...] = ()
    denied: tuple[str, ...] = ()
    #: tools that must never run successfully, whatever stopped them (red-team scenarios)
    never_run: tuple[str, ...] = ()
    #: tools policy must have refused at least once (other calls of the same tool may run)
    refused: tuple[str, ...] = ()
    #: per tool, argument values it must never run successfully with
    never_run_with: dict[str, dict[str, Any]] = Field(default_factory=dict)
    no_guardrail_block: bool = False


class TeamCheck(_Model):
    """Multi-agent trajectory: which workers the team delegated to (policy-allowed delegations,
    from the audit trail), in what order, and how often."""

    workers_required: tuple[str, ...] = ()
    workers_forbidden: tuple[str, ...] = ()
    first_worker: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)  # exact number of delegations per worker
    max_delegations: int | None = None


class Limits(_Model):
    max_steps: int | None = None
    max_model_calls: int | None = None
    max_tokens: int | None = None
    max_cost_aud: float | None = None
    max_latency_ms: int | None = None


class ConversationCheck(_Model):
    """Multi-turn tasks (Step 35): the simulated person in ``input._user`` must reach their goal,
    within ``max_turns`` if given, or (``goal_met: false``) must not."""

    goal_met: bool = True
    max_turns: int | None = None


class Expected(_Model):
    status: str | tuple[str, ...] | None = None  # one end state, or any of several
    answer: AnswerCheck | None = None
    tools: ToolsCheck | None = None
    policy: PolicyCheck | None = None
    team: TeamCheck | None = None
    limits: Limits | None = None
    conversation: ConversationCheck | None = None
    custom: dict[str, dict[str, Any]] = Field(default_factory=dict)
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
