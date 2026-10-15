"""Everything a run needs, built per run from its spec: model, tools (under policy and audit),
the event-emitting node runner and the runtime adapter.

Tools come from the spec's refs: ``local://platform/<tool>`` (built-in tools),
``mcp://rag-core/search`` (rag-core on behalf of the user) and ``mcp://<server>/<tool>`` for
servers on the platform's MCP allowlist. Anything else is refused when the run is created.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from agent_compiler.run import executors_for, functions_for
from agent_contracts.interfaces import (
    AuditSink,
    Checkpointer,
    ModelClient,
    PolicyDecisionPoint,
    ToolExecutor,
)
from agent_contracts.models import ExecutablePlan
from agent_runtime.durable import CheckpointingNodeRunner
from agent_runtime.events import EventingNodeRunner, EventingToolExecutor, StreamingModelClient
from agent_runtime.models.gateway import GatewayModelClient
from agent_runtime.nodes import RunJournal
from agent_spec import AgentSpec
from agent_spec.platform import PlatformConfig
from agent_tools.assemble import grants_for
from agent_tools.authorising import AuthorisingToolExecutor
from agent_tools.builtin import BUILTIN_TOOLS
from agent_tools.executor import PlatformToolExecutor
from agent_tools.identity import TokenExchanger
from agent_tools.mcp import McpClientPool
from agent_tools.ragcore import RagCoreSearchTool
from agent_tools.registry import InMemoryToolRegistry

ModelFactory = Callable[[AgentSpec], ModelClient]


class DurableEventingNodeRunner(EventingNodeRunner, CheckpointingNodeRunner):
    """Live events and a checkpoint after every node (events are not repeated on resume)."""


RegistryFactory = Callable[[AgentSpec], Awaitable[InMemoryToolRegistry]]


@dataclass
class Assembled:
    model: ModelClient
    tools: ToolExecutor
    runner: EventingNodeRunner


class Assembly:
    def __init__(
        self,
        platform: PlatformConfig,
        pdp: PolicyDecisionPoint,
        audit: AuditSink,
        *,
        rag_core_url: str = "http://127.0.0.1:8300",
        exchanger: TokenExchanger | None = None,
        model_factory: ModelFactory | None = None,
        registry_factory: RegistryFactory | None = None,
        checkpointer: Checkpointer | None = None,
    ) -> None:
        self.checkpointer = checkpointer
        self.platform = platform
        self.pdp = pdp
        self.audit = audit
        self.rag_core_url = rag_core_url
        self.exchanger = exchanger
        self.pool = McpClientPool(platform, audit=audit, exchanger=exchanger)
        self.model_factory = model_factory or (
            lambda spec: GatewayModelClient(self.platform, spec_models=spec.models)
        )
        self.registry_factory = registry_factory or self.registry

    async def registry(self, spec: AgentSpec) -> InMemoryToolRegistry:
        registry = InMemoryToolRegistry()
        builtin = {cls().spec.name: cls for cls in BUILTIN_TOOLS}
        servers: set[str] = set()
        for tool in spec.tools:
            scheme, _, rest = tool.ref.partition("://")
            if scheme == "local" and rest.startswith("platform/") and rest[9:] in builtin:
                await registry.register(builtin[rest[9:]]())
            elif tool.ref == "mcp://rag-core/search":
                await registry.register(RagCoreSearchTool(self.rag_core_url, self.exchanger))
            elif scheme == "mcp":
                servers.add(rest.partition("/")[0])
            else:
                raise LookupError(f"tool {tool.ref} is not available through the run API")
        for server in sorted(servers):
            await self.pool.register_all(registry, server)  # refuses servers off the allowlist
        return registry

    async def build(
        self, spec: AgentSpec, plan: ExecutablePlan, journal: RunJournal | None = None
    ) -> Assembled:
        registry = await self.registry_factory(spec)
        tools = EventingToolExecutor(
            AuthorisingToolExecutor(
                PlatformToolExecutor(registry),
                registry,
                self.pdp,
                self.audit,
                grants=grants_for(plan),
            )
        )
        model = StreamingModelClient(self.model_factory(spec))
        functions = functions_for(plan)
        executors: dict[Any, Any] = executors_for(spec, plan, functions, model=model, tools=tools)
        runner: EventingNodeRunner
        if self.checkpointer is not None:
            runner = DurableEventingNodeRunner(
                functions, executors, journal, True, checkpointer=self.checkpointer
            )
        else:
            runner = EventingNodeRunner(functions, executors)
        return Assembled(model, tools, runner)

    async def aclose(self) -> None:
        await self.pool.aclose()
