"""The switchable interfaces. Each one is a slot in the component registry.

Rules for implementers:

* Every method that may do I/O is ``async``.
* Implementations never trust model output, tool output or other agents' messages as
  instructions; they carry a :class:`~agent_contracts.models.Trust` label.
* An implementation is registered only after it passes the shared contract tests for its
  interface (``agent_contracts.testing``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any, ClassVar

from agent_contracts.models import (
    ApprovalDecision,
    ApprovalRecord,
    ApprovalRequest,
    AuditEvent,
    AuditRecord,
    BudgetCharge,
    BudgetLimits,
    BudgetStatus,
    ChatChunk,
    ChatRequest,
    ChatResponse,
    Checkpoint,
    ContextSection,
    Critique,
    EvalCase,
    EvalScore,
    ExecutablePlan,
    GuardrailInput,
    GuardrailResult,
    GuardrailStage,
    LoopVerdict,
    MemoryItem,
    MemoryScope,
    MemoryWriteDecision,
    Message,
    ModelCapabilities,
    Plan,
    PlanFragment,
    PolicyDecision,
    PolicyInput,
    RouteDecision,
    RunContext,
    RunEvent,
    RunResult,
    RunState,
    RunTrace,
    SimTurn,
    StepObservation,
    ToolCall,
    ToolResult,
    ToolSpec,
)


class Component(ABC):  # noqa: B024 - marker base, no abstract methods by design
    """Base of every registrable component. ``slot`` names the registry slot."""

    slot: ClassVar[str] = ""
    #: "implemented" (passes its contract), "stub" (documented adapter, ADR says when to
    #: finish it) or "dummy" (test double from agent_contracts.dummies).
    component_status: ClassVar[str] = "implemented"


# ---------------------------------------------------------------- models


class ModelClient(Component):
    """Chat, tool calling, structured output and streaming through the gateway (Step 5)."""

    slot = "model_client"

    @abstractmethod
    async def chat(self, request: ChatRequest) -> ChatResponse: ...

    @abstractmethod
    def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]: ...

    @abstractmethod
    def capabilities(self, model: str) -> ModelCapabilities: ...


# ---------------------------------------------------------------- tools


class Tool(Component):
    """One typed tool. ``invoke`` receives arguments already validated against the schema."""

    slot = "tool"

    @property
    @abstractmethod
    def spec(self) -> ToolSpec: ...

    @abstractmethod
    async def invoke(self, arguments: Mapping[str, Any], ctx: RunContext) -> Any: ...


class ToolRegistry(Component):
    """Discovery and versioning of tools (Step 6)."""

    slot = "tool_registry"

    @abstractmethod
    async def register(self, tool: Tool) -> None:
        """Add a tool. Registering the same name and version twice raises ``ValueError``."""

    @abstractmethod
    async def get(self, name: str, version: str | None = None) -> Tool:
        """Return the tool; latest non-deprecated version when ``version`` is None.

        Raises ``KeyError`` when unknown.
        """

    @abstractmethod
    async def list(self, include_deprecated: bool = False) -> Sequence[ToolSpec]: ...

    @abstractmethod
    async def deprecate(self, name: str, version: str) -> None: ...


class ToolExecutor(Component):
    """Invokes tools: validation, idempotency, timeouts, retries (local, MCP, A2A, sandbox)."""

    slot = "tool_executor"

    @abstractmethod
    async def execute(self, call: ToolCall, ctx: RunContext) -> ToolResult:
        """Never raises for tool failures: errors come back as ``ToolResult(ok=False)``."""


# ---------------------------------------------------------------- compiler and runtime


class PatternBuilder(Component):
    """Turns one pattern section of a spec into plan nodes and edges (Step 4)."""

    slot = "pattern_builder"

    @abstractmethod
    def build(self, section: Mapping[str, Any], prefix: str) -> PlanFragment:
        """Every node id in the fragment must start with ``prefix``."""


class RuntimeAdapter(Component):
    """Runs a compiled plan on an engine (Step 11)."""

    slot = "runtime_adapter"

    @abstractmethod
    async def build(self, plan: ExecutablePlan) -> str:
        """Prepare the plan; returns a handle used by the other methods."""

    @abstractmethod
    async def invoke(
        self, handle: str, inputs: Mapping[str, Any], ctx: RunContext
    ) -> RunResult: ...

    @abstractmethod
    def stream(
        self, handle: str, inputs: Mapping[str, Any], ctx: RunContext
    ) -> AsyncIterator[RunEvent]: ...

    @abstractmethod
    async def resume(
        self, handle: str, run_id: str, payload: Mapping[str, Any], ctx: RunContext
    ) -> RunResult: ...

    @abstractmethod
    async def cancel(self, run_id: str) -> None:
        """Raises ``KeyError`` for an unknown run."""

    @abstractmethod
    async def get_state(self, run_id: str) -> RunState:
        """Raises ``KeyError`` for an unknown run."""


# ---------------------------------------------------------------- state and memory


class Checkpointer(Component):
    """Saves and restores run state after every node (Step 18)."""

    slot = "checkpointer"

    @abstractmethod
    async def put(self, checkpoint: Checkpoint) -> None: ...

    @abstractmethod
    async def get(
        self, tenant_id: str, thread_id: str, checkpoint_id: str | None = None
    ) -> Checkpoint | None:
        """Latest checkpoint when ``checkpoint_id`` is None. Other tenants' threads: None."""

    @abstractmethod
    async def list(self, tenant_id: str, thread_id: str) -> Sequence[Checkpoint]:
        """Oldest first."""

    @abstractmethod
    async def delete_thread(self, tenant_id: str, thread_id: str) -> int: ...


class DurableExecutor(Component):
    """Long-running execution with signals and exactly-once side effects (Step 19)."""

    slot = "durable_executor"

    @abstractmethod
    async def start(
        self, workflow: str, inputs: Mapping[str, Any], ctx: RunContext, idempotency_key: str
    ) -> str:
        """Same ``idempotency_key`` returns the same run id and does not start twice."""

    @abstractmethod
    async def signal(self, run_id: str, name: str, payload: Mapping[str, Any]) -> None: ...

    @abstractmethod
    async def status(self, run_id: str) -> RunState: ...

    @abstractmethod
    async def result(self, run_id: str, wait_seconds: float = 30.0) -> RunResult: ...


class MemoryStore(Component):
    """Long-term memory, scoped per tenant, user or agent (Step 20)."""

    slot = "memory_store"

    @abstractmethod
    async def put(self, item: MemoryItem) -> str: ...

    @abstractmethod
    async def get(self, scope: MemoryScope, item_id: str) -> MemoryItem | None: ...

    @abstractmethod
    async def search(self, scope: MemoryScope, query: str, k: int = 5) -> Sequence[MemoryItem]:
        """Never returns items outside ``scope.tenant_id``; best match first."""

    @abstractmethod
    async def delete(self, scope: MemoryScope, item_id: str) -> bool: ...

    @abstractmethod
    async def erase_subject(self, tenant_id: str, user_id: str) -> int:
        """Right to erasure: remove every item about this user; returns the count."""


class MemoryPolicy(Component):
    """Decides what may be written to memory and in what form (Step 20)."""

    slot = "memory_policy"

    @abstractmethod
    async def evaluate_write(self, item: MemoryItem) -> MemoryWriteDecision: ...


class ContextManager(Component):
    """Decides what goes into the context window (Step 21)."""

    slot = "context_manager"

    @abstractmethod
    def build(self, sections: Sequence[ContextSection], budget_tokens: int) -> list[Message]:
        """Result fits ``budget_tokens``, keeps required sections, delimits untrusted content."""

    @abstractmethod
    def count_tokens(self, messages: Sequence[Message]) -> int: ...


# ---------------------------------------------------------------- safety


class Guardrail(Component):
    """A check before or after a model or tool call (Step 27)."""

    slot = "guardrail"
    name: ClassVar[str] = ""
    stages: ClassVar[frozenset[GuardrailStage]] = frozenset()

    @abstractmethod
    async def check(self, payload: GuardrailInput) -> GuardrailResult: ...


class PolicyDecisionPoint(Component):
    """Authorisation for every tool call and data access (Step 10)."""

    slot = "policy_decision_point"

    @abstractmethod
    async def decide(self, request: PolicyInput) -> PolicyDecision: ...


class ApprovalGate(Component):
    """Human approval before risky actions (Step 28)."""

    slot = "approval_gate"

    @abstractmethod
    async def request(self, request: ApprovalRequest) -> ApprovalRecord: ...

    @abstractmethod
    async def decide(self, approval_id: str, decision: ApprovalDecision) -> ApprovalRecord:
        """Raises ``ValueError`` if the request is no longer pending (decided or expired)."""

    @abstractmethod
    async def get(self, approval_id: str) -> ApprovalRecord:
        """Expired requests are reported as EXPIRED. Raises ``KeyError`` when unknown."""

    @abstractmethod
    async def pending(self, approvers: str | None = None) -> Sequence[ApprovalRecord]: ...


class BudgetController(Component):
    """Per-run and per-agent limits that roll up to the parent (Step 26)."""

    slot = "budget_controller"

    @abstractmethod
    async def open(
        self, run_id: str, limits: BudgetLimits, parent_run_id: str | None = None
    ) -> BudgetStatus:
        """Open a budget. A child's spending also counts against every ancestor, and a child
        is stopped as soon as any ancestor trips, so a run total can never exceed its parent."""

    @abstractmethod
    async def charge(self, run_id: str, charge: BudgetCharge) -> BudgetStatus:
        """Charges roll up to every ancestor; ``ok=False`` names the first limit tripped."""

    @abstractmethod
    async def status(self, run_id: str) -> BudgetStatus: ...


class LoopDetector(Component):
    """Detects runaway agents: repeated calls, no-progress cycles, oscillation (Step 26)."""

    slot = "loop_detector"

    @abstractmethod
    def observe(self, run_id: str, step: StepObservation) -> LoopVerdict: ...

    @abstractmethod
    def reset(self, run_id: str) -> None: ...


# ---------------------------------------------------------------- reasoning roles


class Planner(Component):
    slot = "planner"

    @abstractmethod
    async def plan(self, goal: str, tools: Sequence[str], ctx: RunContext) -> Plan: ...


class Router(Component):
    slot = "router"

    @abstractmethod
    async def route(self, text: str, routes: Sequence[str], ctx: RunContext) -> RouteDecision:
        """``decision.route`` is always one of ``routes``."""


class Critic(Component):
    slot = "critic"

    @abstractmethod
    async def critique(self, task: str, output: str, ctx: RunContext) -> Critique: ...


# ---------------------------------------------------------------- telemetry and audit


class TelemetryEmitter(Component):
    """GenAI spans and metrics (Step 30)."""

    slot = "telemetry_emitter"

    @abstractmethod
    def span(
        self, name: str, attributes: Mapping[str, Any] | None = None
    ) -> AbstractContextManager[Any]: ...

    @abstractmethod
    def metric(
        self, name: str, value: float, attributes: Mapping[str, Any] | None = None
    ) -> None: ...


class AuditSink(Component):
    """Hash-chained, append-only audit events (Step 31)."""

    slot = "audit_sink"

    @abstractmethod
    async def append(self, event: AuditEvent) -> AuditRecord: ...

    @abstractmethod
    async def read(self, run_id: str | None = None) -> Sequence[AuditRecord]: ...

    @abstractmethod
    async def verify(self) -> bool:
        """True when every record's hash and back-link are intact."""


# ---------------------------------------------------------------- evaluation


class Evaluator(Component):
    slot = "evaluator"

    @abstractmethod
    async def evaluate(self, case: EvalCase, trace: RunTrace) -> EvalScore: ...


class Simulator(Component):
    """Synthetic users or environments for multi-turn evaluation (Step 35)."""

    slot = "simulator"
    max_turns: ClassVar[int] = 10

    @abstractmethod
    async def next_turn(self, history: Sequence[Message]) -> SimTurn: ...


INTERFACES: dict[str, type[Component]] = {
    cls.slot: cls
    for cls in (
        ModelClient,
        Tool,
        ToolRegistry,
        ToolExecutor,
        PatternBuilder,
        RuntimeAdapter,
        Checkpointer,
        DurableExecutor,
        MemoryStore,
        MemoryPolicy,
        ContextManager,
        Guardrail,
        PolicyDecisionPoint,
        ApprovalGate,
        BudgetController,
        LoopDetector,
        Planner,
        Router,
        Critic,
        TelemetryEmitter,
        AuditSink,
        Evaluator,
        Simulator,
    )
}
