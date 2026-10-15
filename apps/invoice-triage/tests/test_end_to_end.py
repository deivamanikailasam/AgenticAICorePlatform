"""Step 15 "Done when": the hybrid reference app runs end to end, on both runtime adapters.

The real ``agents/invoice-triage/agent.yaml``: a deterministic validation function, a ReAct agent
for exceptions, a payment behind a human approval. Tools are real: the demo ERP is an MCP server
over stdio (discovered and called through the platform MCP client) and search is the platform's
rag-core tool (on behalf of the user, with an exchanged token; rag-core's HTTP API is mocked
here). Every call goes through policy, rate limits and audit. Only the model is scripted: it
plays the agent's part so the test is deterministic (real-model runs: VERIFY-ON-MAC).
"""

import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from agent_compiler.compiler import compile_spec
from agent_compiler.run import adapter_for
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.models import Message, RiskTier, Role, RunContext, RunStatus, ToolCall
from agent_spec import load_spec_text
from agent_spec.platform import McpServer, load_platform
from agent_tools.assemble import grants_for
from agent_tools.authorising import AuthorisingToolExecutor
from agent_tools.executor import PlatformToolExecutor
from agent_tools.identity import ExchangedToken, subject_token
from agent_tools.mcp import McpClientPool
from agent_tools.ragcore import RagCoreSearchTool
from agent_tools.registry import InMemoryToolRegistry

ROOT = Path(__file__).resolve().parents[3]
SPEC = (ROOT / "agents/invoice-triage/agent.yaml").read_text()
ADAPTERS = ["langgraph", "agent_framework"]
APPROVER = {
    "approved": True,
    "by": "j.smith",
    "groups": ["/acme/approvers", "/acme/finance-approvers"],
}

INVOICES = {
    "INV-1001": {
        "id": "INV-1001",
        "supplier": "Acme Office Supplies Pty Ltd",
        "subtotal": 480.0,
        "gst": 48.0,
        "total": 528.0,
        "currency": "AUD",
        "po_number": "PO-7781",
    },
    "INV-1002": {
        "id": "INV-1002",
        "supplier": "Harbour Catering",
        "subtotal": 480.0,
        "gst": 50.0,
        "total": 530.0,
        "currency": "AUD",
        "po_number": "",
    },
}


class Exchanger:
    async def exchange(self, token: str, audience: str) -> ExchangedToken:
        return ExchangedToken(f"downscoped-{audience}", audience, 9e9)


def rag_core(request: httpx.Request) -> httpx.Response:
    assert request.headers["authorization"] == "Bearer downscoped-rag-core"
    return httpx.Response(
        200,
        json={
            "results": [
                {
                    "id": "policy-12",
                    "snippet": "GST is 10% of the subtotal. A purchase order approved by email "
                    "may be cited from the invoice notes.",
                    "citation": {"doc_id": "finance-policy", "section": "4.2"},
                }
            ]
        },
    )


class App:
    def __init__(self, adapter: str, data: Path) -> None:
        self.text = SPEC.replace("adapter: langgraph", f"adapter: {adapter}").replace(
            "durability: checkpoint", "durability: memory"
        )
        self.data = data
        self.audit = MemoryAuditSink()

    def payments(self) -> list[dict[str, Any]]:
        return list(json.loads(self.data.read_text())["payments"]) if self.data.exists() else []

    async def tools(self, pool: McpClientPool, plan: Any) -> AuthorisingToolExecutor:
        registry = InMemoryToolRegistry()
        await pool.register_all(registry, "erp")
        await registry.register(
            RagCoreSearchTool(
                "http://rag.test", Exchanger(), transport=httpx.MockTransport(rag_core)
            )  # type: ignore[arg-type]
        )
        return AuthorisingToolExecutor(
            PlatformToolExecutor(registry),
            registry,
            RulesPolicyDecisionPoint(),
            self.audit,
            grants=grants_for(plan),
        )


@pytest.fixture
async def pool(tmp_path: Path) -> AsyncIterator[McpClientPool]:
    erp = McpServer(
        name="erp",
        transport="stdio",
        command=sys.executable,
        args=["-m", "invoice_triage.erp_server"],
        env={"ERP_DATA": str(tmp_path / "erp.json")},
        side_effects={"get_invoice": "read", "post_payment": "irreversible"},
        allowed_tools=["get_invoice", "post_payment"],
    )
    platform = load_platform().model_copy(
        update={"mcp": load_platform().mcp.model_copy(update={"servers": [erp]})}
    )
    p = McpClientPool(platform)
    yield p
    await p.aclose()


def ctx() -> RunContext:
    return RunContext(
        tenant_id="acme", user_id="alice", agent_id="invoice-triage", risk_tier=RiskTier.MEDIUM
    )


async def start(app: App, pool: McpClientPool, invoice: str, model: ScriptedModelClient) -> Any:
    spec = load_spec_text(app.text)
    plan = compile_spec(spec)
    adapter = adapter_for(spec, plan, model=model, tools=await app.tools(pool, plan))
    handle = await adapter.build(plan)
    c = ctx()
    subject_token.set("alice-access-token")
    first = await adapter.invoke(handle, {"invoice": INVOICES[invoice]}, c)
    return adapter, handle, c, first


def agent_model() -> ScriptedModelClient:
    """The exceptions agent's part: look the invoice up, read the policy, answer in schema."""
    return ScriptedModelClient(
        [
            Message(
                role=Role.ASSISTANT,
                tool_calls=(
                    ToolCall(name="erp__get_invoice", arguments={"invoice_id": "INV-1002"}),
                ),
            ),
            Message(
                role=Role.ASSISTANT,
                tool_calls=(
                    ToolCall(name="search", arguments={"query": "GST and email PO policy"}),
                ),
            ),
            Message(
                role=Role.ASSISTANT,
                content=json.dumps(
                    {
                        "invoice_id": "INV-1002",
                        "amount": 528,
                        "currency": "AUD",
                        "resolution": "GST corrected to 48.00 (10% of 480.00); PO-7790 from the "
                        "invoice notes (finance-policy 4.2).",
                    }
                ),
            ),
        ]
    )


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_valid_invoice_waits_for_approval_then_pays_once(
    adapter: str, pool: McpClientPool, tmp_path: Path
) -> None:
    app = App(adapter, tmp_path / "erp.json")
    model = ScriptedModelClient()  # the valid path never asks a model
    flow, handle, c, first = await start(app, pool, "INV-1001", model)
    assert first.status is RunStatus.INTERRUPTED, first.state.error
    assert first.state.next_nodes == ("pay.approval",)
    assert app.payments() == []

    done = await flow.resume(handle, c.run_id, APPROVER, c)
    assert done.status is RunStatus.SUCCEEDED, done.state.error
    assert [(p["invoice_id"], p["amount_aud"]) for p in app.payments()] == [("INV-1001", 528.0)]
    assert model.requests == []
    paid = [
        e.event.payload
        for e in await app.audit.read()
        if e.event.payload.get("tool") == "erp__post_payment"
    ]
    assert paid[-1]["approver"] == "j.smith"
    assert paid[-1]["decision"] == "allow"


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_invalid_invoice_is_resolved_by_the_agent_then_paid_after_approval(
    adapter: str, pool: McpClientPool, tmp_path: Path
) -> None:
    app = App(adapter, tmp_path / "erp.json")
    model = agent_model()
    flow, handle, c, first = await start(app, pool, "INV-1002", model)
    assert first.status is RunStatus.INTERRUPTED, first.state.error
    assert first.state.next_nodes == ("pay.approval",)
    error = first.state.values["validate"]["error"]
    assert "GST 50.00 is not 10% of 480.00" in error
    assert "no purchase order number" in error
    assert error in model.requests[0].messages[-1].content, "the agent is told why"
    tool_messages = [m for m in model.requests[-1].messages if m.role is Role.TOOL]
    assert [m.name for m in tool_messages] == ["erp__get_invoice", "search"]
    assert "finance-policy" in tool_messages[1].content, "citations pass through"
    assert first.state.values["prepare_payment"] == {
        "invoice_id": "INV-1002",
        "amount_aud": 528.0,
        "currency": "AUD",
    }

    done = await flow.resume(handle, c.run_id, APPROVER, c)
    assert done.status is RunStatus.SUCCEEDED, done.state.error
    assert [(p["invoice_id"], p["amount_aud"]) for p in app.payments()] == [("INV-1002", 528.0)]


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_rejected_payment_is_never_made(
    adapter: str, pool: McpClientPool, tmp_path: Path
) -> None:
    app = App(adapter, tmp_path / "erp.json")
    flow, handle, c, _ = await start(app, pool, "INV-1001", ScriptedModelClient())
    done = await flow.resume(handle, c.run_id, {**APPROVER, "approved": False}, c)
    assert done.status is not RunStatus.FAILED, done.state.error
    assert app.payments() == []


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_requester_cannot_approve_their_own_payment(
    adapter: str, pool: McpClientPool, tmp_path: Path
) -> None:
    app = App(adapter, tmp_path / "erp.json")
    flow, handle, c, _ = await start(app, pool, "INV-1001", ScriptedModelClient())
    done = await flow.resume(handle, c.run_id, {**APPROVER, "by": "alice"}, c)
    assert done.status is RunStatus.FAILED
    assert "approval_required" in (done.state.error or "")
    assert app.payments() == []
