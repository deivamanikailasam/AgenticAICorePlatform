"""Shared data models. Every interface in :mod:`agent_contracts.interfaces` speaks these."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Open(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- identity and run context


class RiskTier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RunContext(Frozen):
    """Who is running what. Passed to every component call."""

    run_id: str = Field(default_factory=new_id)
    thread_id: str = Field(default_factory=new_id)
    tenant_id: str
    user_id: str
    agent_id: str
    agent_version: str = "0.0.0"
    risk_tier: RiskTier = RiskTier.LOW
    trace_id: str | None = None
    parent_run_id: str | None = None


# ---------------------------------------------------------------- messages and models


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Trust(StrEnum):
    """Where content came from. Anything not TRUSTED is data, never instructions (principle 6)."""

    TRUSTED = "trusted"  # platform-authored: system prompts, spec text
    USER = "user"  # the end user
    UNTRUSTED = "untrusted"  # tool results, retrieved documents, other agents' messages


def arguments_digest(arguments: Mapping[str, Any]) -> str:
    """SHA-256 of the arguments as canonical JSON: what an approval is bound to."""
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class ApprovalEvidence(Frozen):
    """A person's decision on one exact tool call (Step 15; routing, SLAs and edits: Step 28).

    Bound to the tool and a digest of its arguments: the platform checks the binding before
    policy sees the call, so an approval can never be reused for other arguments or another tool.
    """

    approval_id: str  # <run id>:<approval node id>
    approved: bool
    approver: str
    approver_groups: tuple[str, ...] = ()
    tool: str
    arguments_digest: str
    decided_at: datetime | None = None


class ToolCall(Frozen):
    id: str = Field(default_factory=new_id)
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    approval: ApprovalEvidence | None = None


class Message(Frozen):
    role: Role
    content: str = ""
    name: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    trust: Trust = Trust.TRUSTED


class Usage(Frozen):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_aud: float = Field(default=0.0, ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cost_aud=self.cost_aud + other.cost_aud,
        )


class ModelCapabilities(Frozen):
    tool_calling: bool = False
    structured_output: bool = False
    streaming: bool = False
    context_window: int = Field(default=8192, gt=0)


class ToolDefinition(Frozen):
    """What a model sees about a tool: name, description and argument schema."""

    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})


class ChatRequest(Frozen):
    model: str  # a gateway alias or a role name resolved by the client
    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...] = ()
    response_schema: dict[str, Any] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class ChatResponse(Frozen):
    message: Message
    model: str
    usage: Usage = Usage()
    finish_reason: Literal["stop", "tool_calls", "length", "error"] = "stop"


class ChatChunk(Frozen):
    delta: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: Literal["stop", "tool_calls", "length", "error"] | None = None
    usage: Usage | None = None


# ---------------------------------------------------------------- tools


class SideEffect(StrEnum):
    READ = "read"
    WRITE = "write"  # reversible
    IRREVERSIBLE = "irreversible"


class DataClass(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class RetryPolicy(Frozen):
    max_attempts: int = Field(default=1, ge=1, le=10)
    backoff_seconds: float = Field(default=0.5, ge=0)


class ToolSpec(Frozen):
    """The typed description of a tool (Step 6 extends the registry around it)."""

    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")  # what the model sees (OpenAI/MCP-safe)
    version: str = "1.0.0"
    ref: str | None = (
        None  # where it comes from: local://platform/calculator, mcp://erp/get_invoice …
    )
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] | None = None
    side_effect: SideEffect = SideEffect.READ
    idempotent: bool = True
    timeout_seconds: float = Field(default=30.0, gt=0)
    retry: RetryPolicy = RetryPolicy()
    data_classification: DataClass = DataClass.INTERNAL
    owner: str = "unknown"
    deprecated: bool = False
    tags: tuple[str, ...] = ()  # e.g. "external" (reaches people or systems outside the company)

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name, description=self.description, parameters=self.input_schema
        )


class ToolErrorCode(StrEnum):
    INVALID_ARGUMENTS = "invalid_arguments"
    NOT_FOUND = "not_found"
    DENIED = "denied"
    APPROVAL_REQUIRED = "approval_required"
    TIMEOUT = "timeout"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"


class ToolResult(Frozen):
    call_id: str
    tool: str
    ok: bool
    output: Any = None
    error_code: ToolErrorCode | None = None
    error: str | None = None
    artefact_uri: str | None = None  # large outputs spill to S3 (Step 6)
    duration_ms: int = 0
    trust: Trust = Trust.UNTRUSTED  # tool output is data, never instructions


# ---------------------------------------------------------------- plans and runtime


class NodeKind(StrEnum):
    FUNCTION = "function"
    LLM = "llm"
    TOOL = "tool"
    AGENT = "agent"
    SUBPLAN = "subplan"
    ROUTER = "router"
    APPROVAL = "approval"
    JOIN = "join"
    SUPERVISOR = "supervisor"  # delegates to worker agents (Step 22)


class PlanNode(Frozen):
    id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,128}$")
    kind: NodeKind
    config: dict[str, Any] = Field(default_factory=dict)


class PlanEdge(Frozen):
    source: str
    target: str
    condition: str | None = None  # None = always; otherwise a named outcome such as "on_ok"


class PlanFragment(Frozen):
    """What a PatternBuilder returns for one pattern section of a spec."""

    nodes: tuple[PlanNode, ...]
    edges: tuple[PlanEdge, ...] = ()
    entry: str
    exits: tuple[str, ...]


class ExecutablePlan(Frozen):
    """The compiler's output: a validated graph any RuntimeAdapter can run."""

    name: str
    version: str = "0.0.0"
    nodes: tuple[PlanNode, ...]
    edges: tuple[PlanEdge, ...] = ()
    entry: str
    exits: tuple[str, ...]
    metadata: dict[str, Any] = Field(default_factory=dict)

    def node(self, node_id: str) -> PlanNode:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    INTERRUPTED = "interrupted"  # waiting for a human or an external signal
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STOPPED = "stopped"  # a budget or loop limit tripped; partial result available


class RunEventType(StrEnum):
    RUN_STARTED = "run_started"
    NODE_STARTED = "node_started"
    NODE_FINISHED = "node_finished"
    TEXT_DELTA = "text_delta"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_FINISHED = "tool_call_finished"
    STATE_DELTA = "state_delta"
    APPROVAL_REQUESTED = "approval_requested"
    RUN_FINISHED = "run_finished"
    RUN_ERROR = "run_error"


class RunEvent(Frozen):
    type: RunEventType
    run_id: str
    node_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=utcnow)


class RunState(Frozen):
    run_id: str
    thread_id: str
    status: RunStatus
    values: dict[str, Any] = Field(default_factory=dict)
    next_nodes: tuple[str, ...] = ()
    error: str | None = None
    stop_reason: str | None = None


class RunResult(Frozen):
    run_id: str
    status: RunStatus
    output: Any = None
    state: RunState
    usage: Usage = Usage()


# ---------------------------------------------------------------- state and memory


class Checkpoint(Frozen):
    thread_id: str
    tenant_id: str
    checkpoint_id: str = Field(default_factory=new_id)
    parent_id: str | None = None
    run_id: str | None = None
    step: int = Field(default=0, ge=0)
    node_id: str | None = None
    state: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class MemoryKind(StrEnum):
    SEMANTIC = "semantic"  # facts
    EPISODIC = "episodic"  # past runs and outcomes
    PROCEDURAL = "procedural"  # learned instructions, versioned like prompts


class MemoryScope(Frozen):
    """Who a memory belongs to. An item is visible to a query only when each of the item's
    non-empty fields equals the query's: tenant-wide items (no user) are visible to every user of
    the tenant, a user's items only to that user, an agent's items only to that agent."""

    tenant_id: str
    user_id: str | None = None
    agent_id: str | None = None

    def sees(self, item_scope: MemoryScope) -> bool:
        return (
            item_scope.tenant_id == self.tenant_id
            and item_scope.user_id in (None, self.user_id)
            and item_scope.agent_id in (None, self.agent_id)
        )


class Provenance(Frozen):
    source: str  # "user", "tool:<name>", "agent:<id>", "document:<uri>"
    trust: Trust
    run_id: str | None = None
    validated: bool = False
    # whose personal data this came from, when that is not the item's own user (right to erasure)
    subject_user_id: str | None = None


class MemoryItem(Frozen):
    id: str = Field(default_factory=new_id)
    kind: MemoryKind = MemoryKind.SEMANTIC
    scope: MemoryScope
    content: str
    provenance: Provenance
    confidence: float = Field(default=1.0, ge=0, le=1)
    expires_at: datetime | None = None
    # procedural memory is versioned like prompts: items sharing a key are versions, and search
    # returns only the newest; the store assigns the version
    key: str | None = None
    version: int = Field(default=1, ge=1)
    score: float | None = None  # filled in by search
    created_at: datetime = Field(default_factory=utcnow)


class MemoryWriteDecision(Frozen):
    allowed: bool
    item: MemoryItem | None = None  # possibly redacted
    reasons: tuple[str, ...] = ()


class ContextSection(Frozen):
    name: str  # system | tools | memory | history | retrieved | user
    messages: tuple[Message, ...]
    priority: int = 0  # higher survives trimming longer
    required: bool = False


# ---------------------------------------------------------------- safety


class GuardrailStage(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    TOOL_ARGS = "tool_args"
    TOOL_RESULT = "tool_result"
    RETRIEVED = "retrieved"


class GuardrailAction(StrEnum):
    ALLOW = "allow"
    BLOCK = "block"
    REDACT = "redact"


class GuardrailInput(Frozen):
    stage: GuardrailStage
    text: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    tool: str | None = None
    trust: Trust = Trust.USER


class GuardrailResult(Frozen):
    guardrail: str
    passed: bool
    action: GuardrailAction = GuardrailAction.ALLOW
    findings: tuple[str, ...] = ()
    redacted_text: str | None = None
    score: float | None = None


class PolicyEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class PolicyInput(Frozen):
    """Everything OPA needs to decide one tool call or data access (Step 10)."""

    action: str = "tool.call"
    ctx: RunContext
    tool: str | None = None
    side_effect: SideEffect | None = None
    data_classification: DataClass | None = None
    resource_tenant_id: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    # who is asking, as established by the caller's verified token: authenticated, groups, roles
    subject: dict[str, Any] = Field(default_factory=dict)
    tool_ref: str | None = None
    tool_tags: tuple[str, ...] = ()
    # the tools the calling agent's spec grants (None = not called by an agent, e.g. a person via the gateway)
    allowed_tools: tuple[str, ...] | None = None
    # a person's decision on this call: approved, approver, approver_groups, approval_id and
    # bound (the platform verified it is for this tool and these exact arguments)
    approval: dict[str, Any] | None = None


class PolicyDecision(Frozen):
    effect: PolicyEffect
    reasons: tuple[str, ...] = ()
    approvers: str | None = None  # group that may approve when effect is REQUIRE_APPROVAL
    policy_version: str | None = None

    @property
    def allowed(self) -> bool:
        return self.effect is PolicyEffect.ALLOW


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    EXPIRED = "expired"


class ApprovalRequest(Frozen):
    id: str = Field(default_factory=new_id)
    ctx: RunContext
    tool: str
    arguments: dict[str, Any]
    reason: str = ""
    risk: RiskTier = RiskTier.HIGH
    approvers: str = "group:approvers"
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ApprovalDecision(Frozen):
    status: Literal[ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.ESCALATED]
    decided_by: str
    edited_arguments: dict[str, Any] | None = None
    comment: str = ""


class ApprovalRecord(Frozen):
    request: ApprovalRequest
    status: ApprovalStatus
    decision: ApprovalDecision | None = None
    decided_at: datetime | None = None

    @property
    def effective_arguments(self) -> dict[str, Any]:
        if self.decision and self.decision.edited_arguments is not None:
            return self.decision.edited_arguments
        return self.request.arguments


class BudgetLimits(Frozen):
    max_steps: int | None = Field(default=None, ge=1)
    max_tokens: int | None = Field(default=None, ge=1)
    max_cost_aud: float | None = Field(default=None, gt=0)
    max_wall_seconds: float | None = Field(default=None, gt=0)
    max_tool_calls: int | None = Field(default=None, ge=1)
    max_delegation_depth: int | None = Field(default=None, ge=0)


class BudgetCharge(Frozen):
    steps: int = Field(default=0, ge=0)
    usage: Usage = Usage()
    tool_calls: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0.0, ge=0)


class BudgetStatus(Frozen):
    run_id: str
    ok: bool
    tripped: str | None = None  # name of the limit that tripped, e.g. "max_steps"
    steps: int = 0
    tokens: int = 0
    cost_aud: float = 0.0
    tool_calls: int = 0
    elapsed_seconds: float = 0.0


class StepObservation(Frozen):
    kind: Literal["tool_call", "model_call", "plan", "state"]
    signature: str  # a stable fingerprint, e.g. tool name + canonical arguments
    progress: bool = True  # did this step change the task state?


class LoopVerdict(Frozen):
    loop: bool
    reason: str | None = None


# ---------------------------------------------------------------- reasoning roles


class PlanStep(Frozen):
    id: str
    description: str
    tool: str | None = None
    depends_on: tuple[str, ...] = ()
    # ReWOO (Step 14): tool arguments may refer to earlier steps' results as "#E<n>" or "#E<n>.field"
    arguments: dict[str, Any] = Field(default_factory=dict)


class Plan(Frozen):
    goal: str
    steps: tuple[PlanStep, ...] = Field(min_length=1)


class RouteDecision(Frozen):
    route: str
    confidence: float = Field(ge=0, le=1)
    reason: str = ""


class Critique(Frozen):
    score: float = Field(ge=0, le=1)
    passed: bool
    feedback: str = ""


# ---------------------------------------------------------------- telemetry, audit, evals


class AuditEvent(Frozen):
    type: str  # run.started, tool.call, policy.decision, approval.decision, memory.write …
    ctx: RunContext
    payload: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=utcnow)


class AuditRecord(Frozen):
    seq: int = Field(ge=0)
    event: AuditEvent
    prev_hash: str
    hash: str


class EvalCase(Frozen):
    id: str
    input: dict[str, Any]
    expected: dict[str, Any] = Field(default_factory=dict)
    tags: tuple[str, ...] = ()


class TrajectoryStep(Frozen):
    kind: Literal["model", "tool", "agent", "approval", "guardrail"]
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True


class RunTrace(Frozen):
    """What an evaluator sees about one run."""

    case_id: str
    output: Any = None
    trajectory: tuple[TrajectoryStep, ...] = ()
    usage: Usage = Usage()
    latency_ms: int = 0
    status: RunStatus = RunStatus.SUCCEEDED


class EvalScore(Frozen):
    evaluator: str
    metric: str
    value: float = Field(ge=0, le=1)
    passed: bool
    details: dict[str, Any] = Field(default_factory=dict)


class SimTurn(Frozen):
    message: Message | None = None
    done: bool = False
    reason: str = ""


# ---------------------------------------------------------------- code sandbox (Step 9)


class SandboxLimits(Frozen):
    wall_seconds: float = Field(default=10.0, gt=0, le=600)
    memory_mb: int = Field(default=256, ge=16, le=4096)
    cpus: float = Field(default=0.5, gt=0, le=4)
    pids: int = Field(default=64, ge=4, le=1024)
    output_bytes: int = Field(default=64 * 1024, ge=1024, le=16 * 1024 * 1024)
    disk_mb: int = Field(default=64, ge=1, le=1024)


class SandboxStatus(StrEnum):
    OK = "ok"  # exit code 0
    ERROR = "error"  # non-zero exit code
    TIMEOUT = "timeout"  # wall-clock limit reached; process killed
    OOM = "oom"  # memory limit reached; process killed
    KILLED = "killed"  # killed for another resource limit (e.g. pids)
    REFUSED = "refused"  # the sandbox would not run this request


class SandboxRequest(Frozen):
    language: Literal["python"] = "python"
    code: str = Field(max_length=200_000)
    files: dict[str, str] = Field(default_factory=dict)  # name -> base64 content (inputs)
    limits: SandboxLimits = SandboxLimits()
    network: Literal["none"] = "none"  # model-written code never gets a network


class SandboxResult(Frozen):
    status: SandboxStatus
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    duration_ms: int = 0
    files: dict[str, str] = Field(
        default_factory=dict
    )  # name -> base64 content (outputs in /work/out)
    reasons: tuple[str, ...] = ()
    engine: str = ""
