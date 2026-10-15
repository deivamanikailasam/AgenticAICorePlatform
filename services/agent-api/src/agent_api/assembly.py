"""Everything a run needs, built per run from its spec: model, tools (under policy and audit),
the event-emitting node runner and the runtime adapter.

Tools come from the spec's refs: ``local://platform/<tool>`` (built-in tools),
``mcp://rag-core/search`` (rag-core on behalf of the user), ``mcp://<server>/<tool>`` for
servers on the platform's MCP allowlist and ``a2a://<agent>`` for remote A2A agents on the
platform's A2A allowlist (Step 24: their signed Agent Card is checked before the first call).
Anything else is refused when the run is created.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from agent_compiler.run import Admit, executors_for, functions_for
from agent_contracts.interfaces import (
    AuditSink,
    Checkpointer,
    CredentialBroker,
    Guardrail,
    ModelClient,
    PolicyDecisionPoint,
    ToolExecutor,
)
from agent_contracts.models import ExecutablePlan, NodeKind, SideEffect
from agent_contracts.registry import registry as component_registry
from agent_guardrails.guarded import GuardedModelClient, GuardedToolExecutor, GuardrailRuntime
from agent_guardrails.pipeline import GuardrailPipeline
from agent_memory.factory import memory_for
from agent_memory.service import MemoryService
from agent_memory.tools import RememberTool
from agent_runtime.budget import BudgetedModelClient, BudgetedToolExecutor, RunBudgets
from agent_runtime.checkpoints import PostgresCheckpointer
from agent_runtime.circuit import (
    CircuitBreaker,
    CircuitBreakingModelClient,
    CircuitBreakingToolExecutor,
)
from agent_runtime.durable import CheckpointingNodeRunner
from agent_runtime.events import EventingNodeRunner, EventingToolExecutor, StreamingModelClient
from agent_runtime.models.embeddings import GatewayEmbedder
from agent_runtime.models.gateway import GatewayModelClient
from agent_runtime.models.ledger import UsageLedger
from agent_runtime.nodes import RunJournal
from agent_spec import AgentSpec
from agent_spec.platform import PlatformConfig
from agent_telemetry.spans import TracingModelClient, TracingToolExecutor
from agent_tools.a2a import A2aRemoteAgentTool
from agent_tools.assemble import grants_for, tool_names, tool_refs
from agent_tools.authorising import AuthorisingToolExecutor, delegation_authoriser
from agent_tools.builtin import BUILTIN_TOOLS
from agent_tools.executor import PlatformToolExecutor
from agent_tools.identity import Exchanger
from agent_tools.mcp import McpClientPool
from agent_tools.ragcore import RagCoreSearchTool
from agent_tools.registry import InMemoryToolRegistry
from agent_tools.secrets import DEV_STAGES, SecretResolver, default_resolver
from agent_tools.sql_read import SqlReadTool

ModelFactory = Callable[[AgentSpec], ModelClient]
MemoryFactory = Callable[[AgentSpec], Awaitable[MemoryService | None]]
REMEMBER = "local://platform/remember"
SQL_READ = "local://platform/sql_read"
#: always on, whatever the spec lists (Step 27; turning them off needs an ADR)
BASELINE_GUARDRAILS = ("prompt_injection", "secret_leak")


async def require_approvals(plan: ExecutablePlan, registry: InMemoryToolRegistry) -> None:
    """An irreversible tool always waits for a person (Step 28): a plan in which one could run
    without an approval step is refused before the run starts. Policy also requires approval for
    irreversible calls at execution time; this makes the wait part of the plan."""
    specs = {t.name: t for t in await registry.list()}
    names = tool_names(plan)

    def irreversible(spec_name: str) -> bool:
        tool = specs.get(names.get(spec_name, spec_name))
        return tool is not None and tool.side_effect is SideEffect.IRREVERSIBLE

    for node in plan.nodes:
        cfg = node.config
        if (
            node.kind is NodeKind.TOOL
            and irreversible(str(cfg.get("tool")))
            and not cfg.get("approval_node")
        ):
            raise LookupError(
                f"tool {cfg.get('tool')} is irreversible: the spec must require approval for it "
                "(tools[].approval) before it can run"
            )
        if node.kind is NodeKind.AGENT:
            gated = set(cfg.get("approval_tools", ()))
            for t in cfg.get("tools", ()):
                if irreversible(str(t)) and str(t) not in gated:
                    raise LookupError(
                        f"agent node {node.id} may call {t}, which is irreversible: the spec "
                        "must require approval for it (tools[].approval)"
                    )


class DurableEventingNodeRunner(EventingNodeRunner, CheckpointingNodeRunner):
    """Live events and a checkpoint after every node (events are not repeated on resume)."""


RegistryFactory = Callable[[AgentSpec], Awaitable[InMemoryToolRegistry]]


async def load_gateway_keys(
    platform: PlatformConfig, secrets: SecretResolver, stage: str
) -> dict[str, str] | None:
    """The LiteLLM key of every platform role from ``${secret:litellm/<role>#api_key}``
    (``just provision-openbao`` stores them). In dev and CI, ``None`` when OpenBao has none, so
    the ``LITELLM_KEY_*`` variables apply; in staging and prod the keys come only from here."""
    keys: dict[str, str] = {}
    for role in sorted({*platform.models.roles, "default"}):
        try:
            keys[role] = await secrets.resolve(f"${{secret:litellm/{role}#api_key}}")
        except KeyError:
            continue
    if not keys and stage in DEV_STAGES:
        return None
    return keys


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
        exchanger: Exchanger | None = None,
        model_factory: ModelFactory | None = None,
        registry_factory: RegistryFactory | None = None,
        checkpointer: Checkpointer | None = None,
        memory_factory: MemoryFactory | None = None,
        secrets: SecretResolver | None = None,
        a2a_transport: httpx.AsyncBaseTransport | None = None,
        admit: Admit | None = None,
        budgets: RunBudgets | None = None,
        ledger: UsageLedger | None = None,
        broker: CredentialBroker | None = None,
        gateway_keys: Mapping[str, str] | None = None,
    ) -> None:
        # Step 29: short-lived credentials for tools whose grant names a source (OpenBao)
        self.broker = broker
        # every model call's tokens and cost (Postgres in deployments: cost dashboards and
        # reconciliation with the gateway, Step 26)
        self.ledger = ledger or UsageLedger()
        # Step 26: budgets and loop detection for every run and child run built here
        self.budgets = budgets if budgets is not None else RunBudgets()
        # circuit breakers shared by every run of this process (Step 26)
        self.tool_circuits = CircuitBreaker(failures=5, cooldown=30)
        self.model_circuits = CircuitBreaker(failures=3, cooldown=30)
        # Step 25: which agents may run and be delegated to here (the registry gate)
        self.admit = admit
        self.secrets = secrets or default_resolver()
        self.a2a_transport = a2a_transport
        self.checkpointer = checkpointer
        self.memory_factory = memory_factory or self.memory
        self._memories: dict[str, MemoryService | None] = {}
        self.platform = platform
        self.pdp = pdp
        self.audit = audit
        self.rag_core_url = rag_core_url
        self.exchanger = exchanger
        self.pool = McpClientPool(platform, audit=audit, exchanger=exchanger)
        # Step 29: LiteLLM role keys from OpenBao (None: the LITELLM_KEY_* env vars, dev only)
        self.gateway_keys = dict(gateway_keys) if gateway_keys is not None else None
        self.model_factory = model_factory or (
            lambda spec: GatewayModelClient(
                self.platform, spec_models=spec.models, keys=self.gateway_keys, ledger=self.ledger
            )
        )
        self.registry_factory = registry_factory or self.registry

    def guardrails_for(self, spec: AgentSpec) -> GuardrailRuntime:
        """The spec's guardrails (Step 27), plus the platform baseline that is always on:
        injection detection on untrusted content and secret-leak checks."""
        refs = [(g.name, dict(g.config)) for g in spec.guardrails]
        names = {n for n, _ in refs}
        refs += [(n, {}) for n in BASELINE_GUARDRAILS if n not in names]
        guards = []
        for name, config in refs:
            if name == "prompt_injection.llm_judge" or "llm_judge" in config.get("detectors", ()):
                config["model"] = self.model_factory(spec)
            guard = component_registry.create("guardrail", name, **config)
            assert isinstance(guard, Guardrail)
            guards.append(guard)
        return GuardrailRuntime(GuardrailPipeline(guards, self.audit), self.budgets.context)

    async def registry(self, spec: AgentSpec) -> InMemoryToolRegistry:
        registry = InMemoryToolRegistry()
        builtin = {cls().spec.name: cls for cls in BUILTIN_TOOLS}
        servers: set[str] = set()
        for tool in spec.tools:
            scheme, _, rest = tool.ref.partition("://")
            if tool.ref == REMEMBER:
                continue  # needs the run's memory service: registered in build()
            if scheme == "local" and rest.startswith("platform/") and rest[9:] in builtin:
                await registry.register(builtin[rest[9:]]())
            elif tool.ref == SQL_READ:
                cfg = tool.config
                await registry.register(
                    SqlReadTool(
                        queries=cfg.get("queries"),
                        database=str(cfg.get("database", "agent")),
                        host=cfg.get("host"),
                    )
                )
            elif tool.ref == "mcp://rag-core/search":
                await registry.register(RagCoreSearchTool(self.rag_core_url, self.exchanger))
            elif scheme == "mcp":
                servers.add(rest.partition("/")[0])
            elif scheme == "a2a":
                remote = self.platform.a2a.agent(rest.partition("/")[0])
                if remote is None:
                    raise LookupError(
                        f"A2A agent {rest} is not on the platform allowlist (a2a.agents)"
                    )
                await registry.register(
                    A2aRemoteAgentTool(
                        remote, self.secrets, self.exchanger, transport=self.a2a_transport
                    )
                )
            else:
                raise LookupError(f"tool {tool.ref} is not available through the run API")
        for server in sorted(servers):
            # refuses servers off the allowlist, and tools whose descriptions carry an injection
            await self.pool.register_all(
                registry, server, screen=self.guardrails_for(spec).screen_tool
            )
        return registry

    async def build(
        self,
        spec: AgentSpec,
        plan: ExecutablePlan,
        journal: RunJournal | None = None,
        spec_path: Path | None = None,
    ) -> Assembled:
        memory = await self.memory_factory(spec)
        registry = await self.registry_factory(spec)
        if any(t.ref == REMEMBER for t in spec.tools):
            if memory is None:
                raise LookupError(f"{REMEMBER} needs memory.long_term in the spec")
            await registry.register(RememberTool(memory))
        await require_approvals(plan, registry)
        guardrails = self.guardrails_for(spec)
        # Step 30: execute_tool spans around everything, refused calls included
        tools = TracingToolExecutor(
            BudgetedToolExecutor(
                EventingToolExecutor(
                    GuardedToolExecutor(
                        CircuitBreakingToolExecutor(
                            AuthorisingToolExecutor(
                                PlatformToolExecutor(registry),
                                registry,
                                self.pdp,
                                self.audit,
                                grants=grants_for(plan),
                                broker=self.broker,
                            ),
                            self.tool_circuits,
                        ),
                        guardrails,
                    )
                ),
                self.budgets,
            ),
            agent=spec.name,
            refs=tool_refs(plan),
        )
        model = StreamingModelClient(
            BudgetedModelClient(
                GuardedModelClient(
                    CircuitBreakingModelClient(
                        # Step 30: a chat span per call as the gateway sees it (retries included)
                        TracingModelClient(self.model_factory(spec), agent=spec.name),
                        self.model_circuits,
                    ),
                    guardrails,
                ),
                self.budgets,
            )
        )
        functions = functions_for(plan)
        executors: dict[Any, Any] = executors_for(
            spec,
            plan,
            functions,
            model=model,
            tools=tools,
            memory=memory,
            spec_path=spec_path,
            workers=self.worker,
            authorise=delegation_authoriser(self.pdp, self.audit),
            admit=self.admit,
            budgets=self.budgets,
        )
        runner: EventingNodeRunner
        if self.checkpointer is not None:
            runner = DurableEventingNodeRunner(
                functions, executors, journal, True, checkpointer=self.checkpointer
            )
        else:
            runner = EventingNodeRunner(functions, executors)
        return Assembled(model, tools, runner)

    async def worker(
        self, spec: AgentSpec, plan: ExecutablePlan
    ) -> tuple[ModelClient, ToolExecutor]:
        """A team worker (Step 22) is assembled like any agent: its own model roles, and only
        its own spec's tools under policy with its own grants."""
        parts = await self.build(spec, plan)
        return parts.model, parts.tools

    async def memory(self, spec: AgentSpec) -> MemoryService | None:
        """The spec's long-term memory (Step 20), built once per agent and cached."""
        config = spec.memory.long_term
        if config is None:
            return None
        if spec.name not in self._memories:
            embedder = GatewayEmbedder(
                GatewayModelClient(self.platform, spec_models=spec.models, ledger=self.ledger)
            )
            cipher = (
                self.checkpointer.cipher
                if isinstance(self.checkpointer, PostgresCheckpointer)
                else None
            )
            self._memories[spec.name] = await memory_for(
                config, embedder=embedder, cipher=cipher, audit=self.audit
            )
        return self._memories[spec.name]

    async def aclose(self) -> None:
        await self.pool.aclose()
