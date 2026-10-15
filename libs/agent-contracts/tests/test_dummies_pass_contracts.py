"""Step 2 "Done when": two dummy implementations of each interface pass the contract tests.

Test classes are generated for every component registered by this package, so adding a dummy
automatically adds its contract run.
"""

from typing import Any

from agent_contracts import registry
from agent_contracts.interfaces import INTERFACES
from agent_contracts.testing import contracts as C  # noqa: N812
from agent_contracts.testing.agent_loop import AgentLoopContract

CONTRACTS: dict[str, type] = {
    "model_client": C.ModelClientContract,
    "tool": C.ToolContract,
    "tool_registry": C.ToolRegistryContract,
    "tool_executor": C.ToolExecutorContract,
    "code_sandbox": C.CodeSandboxContract,
    "agent_loop": AgentLoopContract,
    "pattern_builder": C.PatternBuilderContract,
    "runtime_adapter": C.RuntimeAdapterContract,
    "checkpointer": C.CheckpointerContract,
    "durable_executor": C.DurableExecutorContract,
    "memory_store": C.MemoryStoreContract,
    "memory_policy": C.MemoryPolicyContract,
    "context_manager": C.ContextManagerContract,
    "guardrail": C.GuardrailContract,
    "policy_decision_point": C.PolicyDecisionPointContract,
    "approval_gate": C.ApprovalGateContract,
    "budget_controller": C.BudgetControllerContract,
    "loop_detector": C.LoopDetectorContract,
    "planner": C.PlannerContract,
    "embedder": C.EmbedderContract,
    "router": C.RouterContract,
    "critic": C.CriticContract,
    "telemetry_emitter": C.TelemetryEmitterContract,
    "audit_sink": C.AuditSinkContract,
    "agent_registry": C.AgentRegistryContract,
    "evaluator": C.EvaluatorContract,
    "simulator": C.SimulatorContract,
}
SECTIONS = {"single": {"fn": "hello"}, "chain": {"steps": ["hello", "identity"]}}


def _dummy_names(slot: str) -> list[str]:
    return [
        r.name
        for r in registry.registrations()
        if r.slot == slot and r.target.startswith("agent_contracts.dummies.")
    ]


def _factory(slot: str, name: str) -> Any:
    if slot == "tool_executor":
        return lambda self, reg: registry.create(slot, name, registry=reg)
    if slot == "agent_loop":
        return lambda self, model, tools, reg: registry.create(
            slot, name, model=model, tools=tools, registry=reg
        )
    if slot == "durable_executor":
        return lambda self, workflows: registry.create(slot, name, workflows=workflows)
    return lambda self: registry.create(slot, name)


for _slot, _contract in CONTRACTS.items():
    for _name in _dummy_names(_slot):
        _attrs: dict[str, Any] = {"make": _factory(_slot, _name)}
        if _slot == "pattern_builder":
            _attrs["section"] = SECTIONS[_name]
        _cls = type(f"Test_{_slot}__{_name}", (_contract,), _attrs)
        globals()[_cls.__name__] = _cls


def test_every_slot_has_a_contract() -> None:
    assert set(CONTRACTS) == set(INTERFACES)


def test_every_slot_has_two_dummies() -> None:
    counts = {slot: len(_dummy_names(slot)) for slot in INTERFACES}
    assert all(n >= 2 for n in counts.values()), counts
