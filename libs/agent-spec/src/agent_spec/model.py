"""Pydantic models for ``agent.yaml``. The JSON Schema in docs/reference is generated from these."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any, Literal

from agent_contracts.models import RiskTier
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CURRENT_SPEC_VERSION = 1

NAME = r"^[a-z][a-z0-9-]{1,62}$"
NODE_ID = r"^[a-z][a-z0-9_]{0,62}$"
SEMVER = r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$"
TOOL_REF = r"^(mcp|local|a2a|sandbox)://[a-z0-9][a-z0-9._-]*(/[a-zA-Z0-9][a-zA-Z0-9._-]*)?$"
PRINCIPAL = r"^(group|user|role):[a-zA-Z0-9._@-]+$"


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Autonomy(StrEnum):
    WORKFLOW = "workflow"
    AGENT = "agent"
    MULTI_AGENT = "multi_agent"
    HYBRID = "hybrid"


class Pattern(StrEnum):
    """Every value of ``pattern:``; each maps to a PatternBuilder (Steps 4, 12-16, 22-23)."""

    WORKFLOW = "workflow"
    STATE_MACHINE = "state_machine"
    PROMPT_CHAIN = "prompt_chain"
    ROUTER = "router"
    REACT = "react"
    PLAN_EXECUTE = "plan_execute"
    REWOO = "rewoo"
    REFLECTION = "reflection"
    EVALUATOR_OPTIMISER = "evaluator_optimiser"
    AGENTIC_RAG = "agentic_rag"
    SUPERVISOR = "supervisor"
    HIERARCHICAL = "hierarchical"
    HANDOFF = "handoff"
    GROUP_CHAT = "group_chat"
    MAP_REDUCE = "map_reduce"
    DEBATE = "debate"
    HYBRID = "hybrid"


class Durability(StrEnum):
    MEMORY = "memory"  # unit tests only
    CHECKPOINT = "checkpoint"  # Postgres checkpointer (default)
    TEMPORAL = "temporal"  # long or multi-day runs


class Runtime(_Spec):
    adapter: str = "langgraph"
    durability: Durability = Durability.CHECKPOINT


class Budgets(_Spec):
    """Limits for one run. Safe by default: steps and wall-clock time are always required."""

    max_steps: int = Field(ge=1, le=10_000)
    max_wall_seconds: float = Field(gt=0, le=60 * 60 * 24 * 30)
    max_tokens: int | None = Field(default=None, ge=1)
    max_cost_aud: float | None = Field(default=None, gt=0)
    max_tool_calls: int | None = Field(default=None, ge=1)
    max_delegation_depth: int = Field(default=2, ge=0, le=10)
    max_fanout: int = Field(default=8, ge=1, le=1000)


class Approval(_Spec):
    required: bool = True
    approvers: Annotated[str, Field(pattern=PRINCIPAL)] = "group:approvers"
    two_person: bool = False
    sla_minutes: int | None = Field(default=None, ge=1)


class ToolRef(_Spec):
    ref: Annotated[str, Field(pattern=TOOL_REF)]
    alias: Annotated[str | None, Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")] = None
    approval: Approval | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1)
    config: dict[str, Any] = Field(default_factory=dict)

    @property
    def name(self) -> str:
        """The name nodes use for this tool: the alias, or the last segment of the ref."""
        return self.alias or self.ref.rstrip("/").rsplit("/", 1)[-1].replace("-", "_").replace(
            ".", "_"
        )


class LongTermMemory(_Spec):
    store: str = "pgvector"
    scope: Literal["user", "tenant", "agent"] = "user"
    policy: str = "validating"
    ttl_days: int | None = Field(default=None, ge=1)


class Memory(_Spec):
    short_term: Literal["checkpoint", "none"] = "checkpoint"
    long_term: LongTermMemory | None = None


class ContextSettings(_Spec):
    """How each model call's context is built (Step 21)."""

    strategy: Literal["rolling_summary", "trim", "offload_s3"] = "rolling_summary"
    summariser: Literal["llm", "extractive"] = "llm"
    # default: three quarters of the model's context window (the rest: tools and the answer)
    window_tokens: int | None = Field(default=None, ge=256)
    compact_tool_results_over: int = Field(default=1500, ge=100)
    # just-in-time tool loading: above this many tools, show the most relevant few and let the
    # model load others by search
    jit_tools_over: int = Field(default=20, ge=1)
    jit_top_k: int = Field(default=8, ge=1)


class GuardrailRef(_Spec):
    name: str
    config: dict[str, Any] = Field(default_factory=dict)


class NodeType(StrEnum):
    FUNCTION = "function"
    LLM = "llm"
    TOOL = "tool"
    AGENT = "agent"
    ROUTER = "router"
    APPROVAL = "approval"
    SUBGRAPH = "subgraph"
    JOIN = "join"


class Node(_Spec):
    id: Annotated[str, Field(pattern=NODE_ID)]
    type: NodeType
    fn: str | None = None  # function: dotted import path or a registered function name
    tool: str | None = None  # tool: a tool name from `tools`
    tools: tuple[str, ...] = ()  # agent: the tools this node may use (least privilege)
    pattern: Pattern | None = None  # agent: its inner pattern (react, plan_execute, ...)
    model: str | None = None  # llm/agent: a model role or alias
    prompt: str | None = None  # prompt registry ref, e.g. prompt://invoice-triage/resolve@3
    routes: tuple[str, ...] = ()  # router: the outcomes it may choose
    spec: str | None = None  # subgraph: path or registry name of another agent.yaml
    approval: Approval | None = None  # approval nodes
    max_iterations: int | None = Field(default=None, ge=1, le=1000)  # loop bound when on a cycle
    timeout_seconds: float | None = Field(default=None, gt=0)
    compensation: str | None = None  # node id run if a later step fails (saga, Step 12)
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _required_fields(self) -> Node:
        need = {
            NodeType.FUNCTION: ("fn",),
            NodeType.TOOL: ("tool",),
            NodeType.AGENT: ("pattern",),
            NodeType.SUBGRAPH: ("spec",),
        }.get(self.type, ())
        missing = [f for f in need if getattr(self, f) in (None, "")]
        if missing:
            raise ValueError(f"a {self.type.value} node needs: {', '.join(missing)}")
        if self.type is NodeType.ROUTER and len(self.routes) < 2:
            raise ValueError("a router node needs at least two routes")
        return self


EDGE_TEXT = re.compile(
    r"^\s*(?P<src>[a-z][a-z0-9_]*)\s*->\s*(?P<dst>[a-z][a-z0-9_]*)\s*(?::\s*(?P<when>[a-z][a-z0-9_]*))?\s*$"
)


class Edge(_Spec):
    source: str = Field(alias="from")
    target: str = Field(alias="to")
    when: str | None = None  # an outcome: on_ok, on_error, a router route, approved, rejected …

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @classmethod
    def parse(cls, value: Any) -> Any:
        if isinstance(value, str):
            m = EDGE_TEXT.match(value)
            if not m:
                raise ValueError(f"edge {value!r} must look like 'a -> b' or 'a -> b: on_error'")
            return {"from": m["src"], "to": m["dst"], "when": m["when"]}
        return value


class Graph(_Spec):
    entry: str | None = None
    nodes: tuple[Node, ...] = Field(min_length=1)
    edges: tuple[Edge, ...] = ()

    @field_validator("edges", mode="before")
    @classmethod
    def _parse_edges(cls, value: Any) -> Any:
        return [Edge.parse(v) for v in value] if isinstance(value, list | tuple) else value


class SubAgent(_Spec):
    """A worker in a multi-agent spec: another agent.yaml with its own tools and budget."""

    name: Annotated[str, Field(pattern=NAME)]
    spec: str
    role: str = ""
    # shared state (Step 22): the fields this worker is given, and the fields it may set
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()


class SupervisorSettings(_Spec):
    """How a supervisor delegates (Step 22, ``pattern: supervisor`` or ``hierarchical``)."""

    dispatcher: Literal["llm", "rules"] = "llm"
    model: str = "planner"
    max_rounds: int = Field(default=8, ge=1, le=50)
    max_depth: int = Field(default=3, ge=1, le=10)  # supervisors nested inside workers
    max_failures: int = Field(default=2, ge=1)  # per worker, then its circuit opens
    # the typed shared state: field -> JSON Schema; "task" (the input) is always present
    state: dict[str, dict[str, Any]] = Field(default_factory=dict)
    output: str | None = None  # the state field returned as the answer


class Evals(_Spec):
    suite: str
    gates: str = "default"


class Interop(_Spec):
    a2a_publish: bool = False
    mcp_publish: bool = False


class AgentSpec(_Spec):
    """The root of ``agent.yaml``."""

    spec_version: Literal[1] = 1
    name: Annotated[str, Field(pattern=NAME)]
    version: Annotated[str, Field(pattern=SEMVER)]
    owner: str = Field(min_length=1)
    description: str = ""
    risk_tier: RiskTier
    autonomy: Autonomy
    pattern: Pattern
    models: dict[str, str] = Field(default_factory=lambda: {"default": "local-qwen-fast"})
    runtime: Runtime = Runtime()
    budgets: Budgets
    tools: tuple[ToolRef, ...] = ()
    memory: Memory = Memory()
    context: ContextSettings = ContextSettings()
    guardrails: tuple[GuardrailRef, ...] = ()
    graph: Graph | None = None
    agents: tuple[SubAgent, ...] = ()
    supervisor: SupervisorSettings = SupervisorSettings()
    components: dict[str, str] = Field(default_factory=dict)  # slot -> registered name
    evals: Evals | None = None
    interop: Interop = Interop()
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("guardrails", mode="before")
    @classmethod
    def _guardrail_shorthand(cls, value: Any) -> Any:
        if isinstance(value, list | tuple):
            return [{"name": v} if isinstance(v, str) else v for v in value]
        return value

    @field_validator("models")
    @classmethod
    def _default_model(cls, value: dict[str, str]) -> dict[str, str]:
        if "default" not in value:
            raise ValueError("models must define a 'default' role")
        return value

    def tool(self, name: str) -> ToolRef | None:
        return next((t for t in self.tools if t.name == name), None)
