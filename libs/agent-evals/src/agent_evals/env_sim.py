"""Environment simulator (Step 35): mock tools with seeded faults.

An environment is a YAML file of tools. Each tool has the platform's ``ToolSpec`` fields (name,
schema, side effect) plus canned behaviour: ``responses`` matched on arguments (first match
wins; ``{arg}`` placeholders in strings are filled from the call) and a ``default``. Faults are
drawn per call from a ``random.Random`` seeded with ``<seed>:<tool>``, so the same seed and the
same calls give the same faults, every run:

    seed: 7
    tools:
      - name: get_order
        side_effect: read
        input_schema: {type: object, properties: {order_id: {type: string}}, required: [order_id]}
        output_schema: {type: object, required: [status]}
        responses:
          - when: {order_id: A-1}
            result: {status: shipped}
        default: {status: unknown, order_id: "{order_id}"}
        faults:
          error_rate: 0.2        # raises: the executor reports a failed call
          timeout_rate: 0.1      # raises TimeoutError: reported as a timeout
          malformed_rate: 0.1    # returns a result that breaks output_schema
          rate_limit_after: 5    # every call after the 5th is refused as rate limited
          outage_calls: [3]      # these call numbers (1-based) fail whatever the dice say
          latency_ms: [0, 20]    # uniform delay

Tools are real ``Tool`` components, so they go through the platform's executor (schema checks,
timeouts, circuits, policy, audit) like any other tool. ``log`` keeps every call and the fault it
got, so a test can assert what the agent faced.
"""

from __future__ import annotations

import asyncio
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from agent_contracts.interfaces import Tool
from agent_contracts.models import RunContext, SideEffect, ToolSpec
from pydantic import BaseModel, ConfigDict, Field

FAULTS = ("outage", "rate_limited", "timeout", "error", "malformed")


class SimulatedFault(RuntimeError):  # noqa: N818 - a simulated event, named for what it is
    """A fault the environment injected (not a bug in the tool)."""

    def __init__(self, kind: str, tool: str, call: int) -> None:
        super().__init__(f"simulated {kind} in {tool} (call {call})")
        self.kind = kind


class Faults(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    error_rate: float = Field(default=0.0, ge=0, le=1)
    timeout_rate: float = Field(default=0.0, ge=0, le=1)
    malformed_rate: float = Field(default=0.0, ge=0, le=1)
    rate_limit_after: int | None = Field(default=None, ge=0)
    outage_calls: tuple[int, ...] = ()
    latency_ms: tuple[int, int] = (0, 0)


class Response(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    when: dict[str, Any] = Field(default_factory=dict)
    result: Any = None


class ToolDef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str = ""
    side_effect: str = "read"
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] | None = None
    responses: tuple[Response, ...] = ()
    default: Any = None
    faults: Faults = Faults()
    example: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class CallLog:
    tool: str
    call: int
    arguments: dict[str, Any]
    fault: str | None
    result: Any = None


def _fill(value: Any, args: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        return re.sub(r"\{(\w+)\}", lambda m: str(args.get(m.group(1), m.group(0))), value)
    if isinstance(value, list):
        return [_fill(v, args) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, args) for k, v in value.items()}
    return value


class SimulatedTool(Tool):
    def __init__(self, definition: ToolDef, seed: int | str, log: list[CallLog]) -> None:
        self.definition = definition
        self._rng = random.Random(f"{seed}:{definition.name}")  # noqa: S311 - reproducible faults
        self._log = log
        self.calls = 0
        self._delay = 0.0
        self._spec = ToolSpec(
            name=definition.name,
            ref=f"sim://env/{definition.name}",
            description=definition.description,
            input_schema=definition.input_schema,
            output_schema=definition.output_schema,
            side_effect=SideEffect(definition.side_effect),
            idempotent=definition.side_effect == "read",
            owner="simulator",
        )
        self.example_arguments = dict(definition.example)

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def _fault(self) -> str | None:
        """Draw every die on every call, so one fault doesn't shift the next call's dice."""
        f = self.definition.faults
        dice = [self._rng.random() for _ in range(3)]
        delay = self._rng.randint(*f.latency_ms) if f.latency_ms[1] else 0
        self._delay = delay / 1000
        if self.calls in f.outage_calls:
            return "outage"
        if f.rate_limit_after is not None and self.calls > f.rate_limit_after:
            return "rate_limited"
        if dice[0] < f.timeout_rate:
            return "timeout"
        if dice[1] < f.error_rate:
            return "error"
        if dice[2] < f.malformed_rate:
            return "malformed"
        return None

    def _answer(self, args: Mapping[str, Any]) -> Any:
        for r in self.definition.responses:
            if all(args.get(k) == v for k, v in r.when.items()):
                return _fill(r.result, args)
        return _fill(self.definition.default, args)

    async def invoke(self, arguments: Mapping[str, Any], ctx: RunContext) -> Any:
        self.calls += 1
        fault = self._fault()
        if self._delay:
            await asyncio.sleep(self._delay)
        args = dict(arguments)
        if fault in ("outage", "rate_limited", "error"):
            self._log.append(CallLog(self.spec.name, self.calls, args, fault))
            raise SimulatedFault(fault or "error", self.spec.name, self.calls)
        if fault == "timeout":
            self._log.append(CallLog(self.spec.name, self.calls, args, fault))
            raise TimeoutError(f"simulated timeout in {self.spec.name} (call {self.calls})")
        result = {"_malformed": True} if fault == "malformed" else self._answer(args)
        self._log.append(CallLog(self.spec.name, self.calls, args, fault, result))
        return result


@dataclass
class Environment:
    """A set of simulated tools sharing one seed and one call log."""

    seed: int | str
    tools: list[SimulatedTool]
    log: list[CallLog] = field(default_factory=list)

    @staticmethod
    def from_defs(defs: Sequence[ToolDef | Mapping[str, Any]], seed: int | str = 0) -> Environment:
        log: list[CallLog] = []
        tools = [
            SimulatedTool(d if isinstance(d, ToolDef) else ToolDef.model_validate(d), seed, log)
            for d in defs
        ]
        return Environment(seed, tools, log)

    @staticmethod
    def load(path: str | Path, seed: int | str | None = None) -> Environment:
        doc = yaml.safe_load(Path(path).read_text()) or {}
        return Environment.from_defs(
            doc.get("tools") or [], seed if seed is not None else doc.get("seed", 0)
        )

    def tool(self, name: str) -> SimulatedTool:
        return next(t for t in self.tools if t.spec.name == name)

    def faults(self) -> list[tuple[str, int, str]]:
        return [(c.tool, c.call, c.fault) for c in self.log if c.fault]

    async def register(self, registry: Any) -> None:
        for t in self.tools:
            await registry.register(t)


__all__ = ["FAULTS", "CallLog", "Environment", "Faults", "SimulatedFault", "SimulatedTool"]
