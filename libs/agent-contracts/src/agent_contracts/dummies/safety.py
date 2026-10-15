from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from agent_contracts.dummies._util import mark_dummies
from agent_contracts.interfaces import (
    ApprovalGate,
    BudgetController,
    Guardrail,
    LoopDetector,
    PolicyDecisionPoint,
)
from agent_contracts.models import (
    ApprovalDecision,
    ApprovalRecord,
    ApprovalRequest,
    ApprovalStatus,
    BudgetCharge,
    BudgetLimits,
    BudgetStatus,
    GuardrailAction,
    GuardrailInput,
    GuardrailResult,
    GuardrailStage,
    LoopVerdict,
    PolicyDecision,
    PolicyEffect,
    PolicyInput,
    SideEffect,
    StepObservation,
)

INJECTION = re.compile(
    r"ignore (all |any )?(previous|prior|above) (instructions|rules)|disregard (the|your) "
    r"(system|previous) (prompt|instructions)|you are now (?:in )?developer mode|"
    r"reveal (your|the) system prompt",
    re.IGNORECASE,
)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


class KeywordInjectionGuardrail(Guardrail):
    """Blocks text containing well-known injection phrases. A weak baseline, not a defence."""

    name = "keyword_injection"
    stages = frozenset({GuardrailStage.INPUT, GuardrailStage.TOOL_RESULT, GuardrailStage.RETRIEVED})

    async def check(self, payload: GuardrailInput) -> GuardrailResult:
        hits = [m.group(0) for m in INJECTION.finditer(payload.text)]
        if hits:
            return GuardrailResult(
                guardrail=self.name,
                passed=False,
                action=GuardrailAction.BLOCK,
                findings=tuple(f"injection phrase: {h}" for h in hits),
                score=1.0,
            )
        return GuardrailResult(guardrail=self.name, passed=True, score=0.0)


class RegexPiiGuardrail(Guardrail):
    """Redacts e-mail addresses. Presidio replaces this in Step 27."""

    name = "regex_pii"
    stages = frozenset({GuardrailStage.INPUT, GuardrailStage.OUTPUT, GuardrailStage.TOOL_RESULT})

    async def check(self, payload: GuardrailInput) -> GuardrailResult:
        found = EMAIL.findall(payload.text)
        if not found:
            return GuardrailResult(guardrail=self.name, passed=True)
        return GuardrailResult(
            guardrail=self.name,
            passed=True,
            action=GuardrailAction.REDACT,
            findings=tuple("email" for _ in found),
            redacted_text=EMAIL.sub("[EMAIL]", payload.text),
        )


class RulesPolicyDecisionPoint(PolicyDecisionPoint):
    """In-process rules: same tenant only; irreversible needs approval, satisfied by a bound
    approval from someone else in an ``approvers`` group (as the OPA policy). OPA: Step 10."""

    async def decide(self, request: PolicyInput) -> PolicyDecision:
        if request.resource_tenant_id and request.resource_tenant_id != request.ctx.tenant_id:
            return PolicyDecision(effect=PolicyEffect.DENY, reasons=("cross-tenant access",))
        ev = request.approval or {}
        if (
            request.side_effect is SideEffect.IRREVERSIBLE
            and ev.get("approved") is True
            and ev.get("bound") is True
            and ev.get("approver") not in ("", None, request.ctx.user_id)
            and any(str(g).endswith("/approvers") for g in ev.get("approver_groups", ()))
        ):
            return PolicyDecision(
                effect=PolicyEffect.ALLOW,
                reasons=(f"approved by {ev['approver']} ({ev.get('approval_id')})",),
            )
        if request.side_effect is SideEffect.IRREVERSIBLE:
            return PolicyDecision(
                effect=PolicyEffect.REQUIRE_APPROVAL,
                reasons=("irreversible action",),
                approvers="group:approvers",
            )
        return PolicyDecision(effect=PolicyEffect.ALLOW, reasons=("same tenant",))


class DenyAllPolicyDecisionPoint(PolicyDecisionPoint):
    """Denies everything: the safe default when no policy is configured."""

    async def decide(self, request: PolicyInput) -> PolicyDecision:
        return PolicyDecision(effect=PolicyEffect.DENY, reasons=("no policy configured",))


class InMemoryApprovalGate(ApprovalGate):
    """Approval requests in a dictionary."""

    default_ttl: timedelta | None = None

    def __init__(self) -> None:
        self._records: dict[str, ApprovalRecord] = {}

    def _fresh(self, record: ApprovalRecord) -> ApprovalRecord:
        exp = record.request.expires_at
        if record.status is ApprovalStatus.PENDING and exp and exp <= datetime.now(UTC):
            record = record.model_copy(update={"status": ApprovalStatus.EXPIRED})
            self._records[record.request.id] = record
        return record

    async def request(self, request: ApprovalRequest) -> ApprovalRecord:
        if request.expires_at is None and self.default_ttl is not None:
            request = request.model_copy(
                update={"expires_at": datetime.now(UTC) + self.default_ttl}
            )
        record = ApprovalRecord(request=request, status=ApprovalStatus.PENDING)
        self._records[request.id] = record
        return record

    async def decide(self, approval_id: str, decision: ApprovalDecision) -> ApprovalRecord:
        record = await self.get(approval_id)
        if record.status is not ApprovalStatus.PENDING:
            raise ValueError(f"approval {approval_id} is {record.status.value}")
        record = record.model_copy(
            update={
                "status": decision.status,
                "decision": decision,
                "decided_at": datetime.now(UTC),
            }
        )
        self._records[approval_id] = record
        return record

    async def get(self, approval_id: str) -> ApprovalRecord:
        return self._fresh(self._records[approval_id])

    async def pending(self, approvers: str | None = None) -> Sequence[ApprovalRecord]:
        recs = [self._fresh(r) for r in list(self._records.values())]
        return [
            r
            for r in recs
            if r.status is ApprovalStatus.PENDING and approvers in (None, r.request.approvers)
        ]


class TtlApprovalGate(InMemoryApprovalGate):
    """Like ``memory`` but every request without an expiry gets a default one (SLA timer)."""

    default_ttl = timedelta(hours=24)


_LIMITS = (
    ("max_steps", "steps"),
    ("max_tokens", "tokens"),
    ("max_cost_aud", "cost_aud"),
    ("max_tool_calls", "tool_calls"),
    ("max_wall_seconds", "elapsed_seconds"),
)


class _Account:
    def __init__(self, limits: BudgetLimits, parent: str | None) -> None:
        self.limits = limits
        self.parent = parent
        self.used = dict.fromkeys(
            ("steps", "tokens", "cost_aud", "tool_calls", "elapsed_seconds"), 0.0
        )
        self.tripped: str | None = None


class InMemoryBudgetController(BudgetController):
    """Counters per run; every charge also counts against all ancestors."""

    def __init__(self) -> None:
        self._acc: dict[str, _Account] = {}

    async def open(
        self, run_id: str, limits: BudgetLimits, parent_run_id: str | None = None
    ) -> BudgetStatus:
        if parent_run_id is not None and parent_run_id not in self._acc:
            raise KeyError(parent_run_id)
        self._acc[run_id] = _Account(limits, parent_run_id)
        return await self.status(run_id)

    def _apply(self, run_id: str, charge: BudgetCharge) -> None:
        acc = self._acc[run_id]
        acc.used["steps"] += charge.steps
        acc.used["tokens"] += charge.usage.total_tokens
        acc.used["cost_aud"] += charge.usage.cost_aud
        acc.used["tool_calls"] += charge.tool_calls
        acc.used["elapsed_seconds"] += charge.elapsed_seconds
        for limit, counter in _LIMITS:
            cap = getattr(acc.limits, limit)
            if acc.tripped is None and cap is not None and acc.used[counter] > cap:
                acc.tripped = limit
        if acc.parent:
            self._apply(acc.parent, charge)

    def _tripped(self, run_id: str) -> str | None:
        acc = self._acc[run_id]
        return acc.tripped or (self._tripped(acc.parent) if acc.parent else None)

    async def charge(self, run_id: str, charge: BudgetCharge) -> BudgetStatus:
        self._apply(run_id, charge)
        return await self.status(run_id)

    async def status(self, run_id: str) -> BudgetStatus:
        acc = self._acc[run_id]
        tripped = self._tripped(run_id)
        return BudgetStatus(
            run_id=run_id,
            ok=tripped is None,
            tripped=tripped,
            steps=int(acc.used["steps"]),
            tokens=int(acc.used["tokens"]),
            cost_aud=acc.used["cost_aud"],
            tool_calls=int(acc.used["tool_calls"]),
            elapsed_seconds=acc.used["elapsed_seconds"],
        )


class LedgerBudgetController(InMemoryBudgetController):
    """Keeps every charge in a ledger (for audit) on top of the in-memory counters."""

    def __init__(self) -> None:
        super().__init__()
        self.ledger: list[tuple[float, str, BudgetCharge]] = []

    async def charge(self, run_id: str, charge: BudgetCharge) -> BudgetStatus:
        self.ledger.append((time.time(), run_id, charge))
        return await super().charge(run_id, charge)


class RepeatLoopDetector(LoopDetector):
    """Flags the same step signature seen ``threshold`` times within the last ``window`` steps."""

    def __init__(self, threshold: int = 3, window: int = 10) -> None:
        self.threshold, self.window = threshold, window
        self._seen: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=self.window))

    def observe(self, run_id: str, step: StepObservation) -> LoopVerdict:
        seen = self._seen[run_id]
        seen.append(step.signature)
        if seen.count(step.signature) >= self.threshold:
            return LoopVerdict(loop=True, reason=f"repeated step {step.signature!r}")
        return LoopVerdict(loop=False)

    def reset(self, run_id: str) -> None:
        self._seen.pop(run_id, None)


class NoProgressLoopDetector(RepeatLoopDetector):
    """Also flags ``patience`` consecutive steps that made no progress."""

    def __init__(self, threshold: int = 3, window: int = 10, patience: int = 4) -> None:
        super().__init__(threshold, window)
        self.patience = patience
        self._stalled: dict[str, int] = defaultdict(int)

    def observe(self, run_id: str, step: StepObservation) -> LoopVerdict:
        self._stalled[run_id] = 0 if step.progress else self._stalled[run_id] + 1
        if self._stalled[run_id] >= self.patience:
            return LoopVerdict(loop=True, reason=f"{self._stalled[run_id]} steps without progress")
        return super().observe(run_id, step)

    def reset(self, run_id: str) -> None:
        super().reset(run_id)
        self._stalled.pop(run_id, None)


mark_dummies(globals())
