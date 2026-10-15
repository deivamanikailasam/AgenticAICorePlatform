"""Run the reference app against Project 0 (Mac; VERIFY-ON-MAC).

    uv run python -m invoice_triage.demo INV-1002 --approver j.smith --groups /acme/approvers,/acme/finance-approvers

Real pieces: the gateway model (``planner`` role) for the exceptions agent, OPA for every tool
call, the demo ERP over MCP stdio, and rag-core search on behalf of the user (``AGENT_USER_TOKEN``,
exchanged by Keycloak for a rag-core token). The run pauses at the payment approval and prints
the proposed payment; ``--approver`` resumes it as that person (in production the approval API
takes the approver from their verified token, Step 17). Without ``--approver`` it stops at the
approval.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from agent_compiler.compiler import compile_spec
from agent_compiler.run import adapter_for
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.models import RiskTier, RunContext
from agent_runtime.models.gateway import GatewayModelClient
from agent_spec import load_spec
from agent_spec.platform import McpServer, load_platform
from agent_tools.assemble import grants_for
from agent_tools.authorising import AuthorisingToolExecutor
from agent_tools.executor import PlatformToolExecutor
from agent_tools.identity import TokenExchanger, subject_token
from agent_tools.mcp import McpClientPool
from agent_tools.policy import OpaPolicyDecisionPoint
from agent_tools.ragcore import RagCoreSearchTool
from agent_tools.registry import InMemoryToolRegistry
from agent_tools.secrets import EnvSecretResolver

from invoice_triage.erp_server import SEED


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


async def main(invoice_id: str, approver: str | None, groups: list[str]) -> int:
    spec = load_spec("agents/invoice-triage/agent.yaml")
    spec = spec.model_copy(
        update={"runtime": spec.runtime.model_copy(update={"durability": "memory"})}
    )
    plan = compile_spec(spec)
    erp = McpServer(
        name="erp",
        transport="stdio",
        command=sys.executable,
        args=["-m", "invoice_triage.erp_server"],
        side_effects={"get_invoice": "read", "post_payment": "irreversible"},
        allowed_tools=["get_invoice", "post_payment"],
    )
    base = load_platform()
    platform = base.model_copy(update={"mcp": base.mcp.model_copy(update={"servers": [erp]})})
    pool = McpClientPool(platform)
    registry = InMemoryToolRegistry()
    await pool.register_all(registry, "erp")
    issuer = f"{_env('P0_KEYCLOAK_URL', 'http://127.0.0.1:8080')}/realms/{_env('P0_KEYCLOAK_REALM', 'rag-platform')}"
    exchanger = TokenExchanger(
        f"{issuer}/protocol/openid-connect/token",
        _env("AGENT_PLATFORM_CLIENT_ID", "agent-platform"),
        _env("AGENT_PLATFORM_CLIENT_SECRET", "${secret:keycloak/agent-platform#client_secret}"),
        EnvSecretResolver(),
    )
    await registry.register(
        RagCoreSearchTool(_env("P0_RAG_CORE_URL", "http://127.0.0.1:8300"), exchanger)
    )
    audit = MemoryAuditSink()
    tools = AuthorisingToolExecutor(
        PlatformToolExecutor(registry),
        registry,
        OpaPolicyDecisionPoint(_env("P0_OPA_URL", "http://127.0.0.1:8181")),
        audit,
        grants=grants_for(plan),
    )
    model = GatewayModelClient(spec_models=spec.models)
    adapter = adapter_for(spec, plan, model=model, tools=tools)
    handle = await adapter.build(plan)
    ctx = RunContext(
        tenant_id=_env("AGENT_TENANT", "acme"),
        user_id=_env("AGENT_USER", "alice"),
        agent_id=spec.name,
        risk_tier=RiskTier(spec.risk_tier.value),
    )
    subject_token.set(os.environ.get("AGENT_USER_TOKEN"))
    try:
        result = await adapter.invoke(handle, {"invoice": SEED["invoices"][invoice_id]}, ctx)
        print(f"status: {result.status.value}  next: {list(result.state.next_nodes)}")
        print("proposed payment:", json.dumps(result.state.values.get("prepare_payment")))
        if approver and result.state.next_nodes == ("pay.approval",):
            decision: dict[str, Any] = {"approved": True, "by": approver, "groups": groups}
            result = await adapter.resume(handle, ctx.run_id, decision, ctx)
            print(f"status: {result.status.value}  error: {result.state.error}")
        cost = model.ledger.total(ctx.run_id)
        print(
            f"model cost AUD {cost.cost_aud:.4f} ({cost.input_tokens} in / {cost.output_tokens} out tokens)"
        )
        for event in await audit.read():
            p = event.event.payload
            print(f"audit: {p.get('tool')} {p.get('decision')} {p.get('approver') or ''}")
    finally:
        await pool.aclose()
    return 0 if result.status.value in ("succeeded", "interrupted") else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("invoice", choices=sorted(SEED["invoices"]))
    parser.add_argument("--approver")
    parser.add_argument("--groups", default="/acme/approvers,/acme/finance-approvers")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.invoice, args.approver, args.groups.split(","))))
