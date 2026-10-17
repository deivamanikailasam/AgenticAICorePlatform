"""An agent project: a repository made from the template (Step 39), or the platform itself.

Layout (``agentctl`` finds the root by walking up to the first directory with ``agents/``)::

    agents/<agent>/agent.yaml    the specs (dev: what main's head runs)
    evals/<agent>/tasks.jsonl    golden tasks
    platform.yaml                the platform config the project runs against (gateway, models)
    deploy/                      promotion files and overlays (agent_governance.release_repo)
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_api.assembly import Assembly
from agent_api.eval_runner import Provider, gateway_provider
from agent_api.runs import SPEC_PATHS
from agent_compiler.compiler import compile_spec
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.interfaces import ModelClient
from agent_contracts.models import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    ModelCapabilities,
    NodeKind,
)
from agent_evals.tasks import Task
from agent_governance.release_repo import ReleaseRepo
from agent_spec import AgentSpec, load_spec_text
from agent_spec.platform import PlatformConfig, load_platform
from agent_tools.registry import InMemoryToolRegistry

PROVIDERS = ("none", "stand-in", "gateway")
MODEL_NODES = {NodeKind.LLM, NodeKind.ROUTER, NodeKind.AGENT}


class ProjectError(Exception):
    pass


@dataclass(frozen=True)
class Project:
    root: Path

    @classmethod
    def find(cls, start: Path | None = None) -> Project:
        here = (start or Path.cwd()).resolve()
        for d in (here, *here.parents):
            if (d / "agents").is_dir():
                return cls(d)
        raise ProjectError(f"no agents/ directory in {here} or above: not an agent project")

    def configure(self) -> None:
        """Use the project's ``platform.yaml`` unless ``AGENT_PLATFORM_CONFIG`` names another."""
        config = self.root / "platform.yaml"
        if "AGENT_PLATFORM_CONFIG" not in os.environ and config.is_file():
            os.environ["AGENT_PLATFORM_CONFIG"] = str(config)

    @property
    def name(self) -> str:
        """The project's name (pyproject.toml), which its environments are named after."""
        pyproject = self.root / "pyproject.toml"
        if pyproject.is_file():
            name = tomllib.loads(pyproject.read_text()).get("project", {}).get("name")
            if name:
                return str(name)
        return self.root.name

    def spec_path(self, agent: str) -> Path:
        path = self.root / "agents" / agent / "agent.yaml"
        if not path.is_file():
            known = ", ".join(self.agents()) or "none"
            raise ProjectError(f"no agent {agent!r} (agents/{agent}/agent.yaml); known: {known}")
        return path

    def agents(self) -> list[str]:
        base = self.root / "agents"
        return sorted(d.name for d in base.iterdir() if (d / "agent.yaml").is_file())

    def tasks(self, agent: str) -> Path:
        return self.root / "evals" / agent / "tasks.jsonl"

    def releases(self) -> ReleaseRepo:
        return ReleaseRepo(
            self.root,
            promote_command="agentctl promote",
            config_command="agentctl deploy-config",
        )


class ProjectHarness:
    """An agent of the project, evaluated or run in process through the platform's run API with
    the same assembly as production: tools by ref (the platform's own, the team's installed
    ones, MCP servers on the allowlist), the in-process policy rules, guardrails, budgets and an
    audit trail. Durable checkpoints are kept in memory."""

    def __init__(self, project: Project, agent: str) -> None:
        self.project = project
        self.agent = agent
        self.path = project.spec_path(agent)

    def spec(self) -> AgentSpec:
        text = self.path.read_text().replace("durability: checkpoint", "durability: memory")
        spec = load_spec_text(text, str(self.path))
        SPEC_PATHS[spec.name] = self.path.resolve()  # workers resolve next to the spec
        return spec

    def platform(self, base: PlatformConfig) -> PlatformConfig:
        return base

    async def registry(self, spec: AgentSpec) -> InMemoryToolRegistry:
        assembly = Assembly(load_platform(), RulesPolicyDecisionPoint(), MemoryAuditSink())
        return await assembly.registry(spec)

    async def prepare(self, tasks: Sequence[Task]) -> None:
        return None

    async def close(self) -> None:
        return None


class _NoModel(ModelClient):
    """The model of a run with ``--provider none``: an agent that calls one is told how to give
    it one, rather than getting an invented answer."""

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities()

    async def chat(self, request: ChatRequest) -> ChatResponse:
        raise ProjectError(
            f"this run has no model ({request.model}): use --provider stand-in or gateway"
        )

    def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        raise ProjectError(
            f"this run has no model ({request.model}): use --provider stand-in or gateway"
        )


def needs_model(spec: AgentSpec) -> bool:
    return any(n.kind in MODEL_NODES for n in compile_spec(spec).nodes)


def provider(kind: str, spec: AgentSpec) -> Provider:
    """``none`` (default for agents without model nodes), ``stand-in`` or ``gateway``."""
    if kind == "auto":
        kind = "gateway" if needs_model(spec) else "none"
    if kind == "none":
        return Provider("none", lambda s: _NoModel(), {})
    if kind not in PROVIDERS:
        raise ProjectError(f"unknown provider {kind!r}; choose from {', '.join(PROVIDERS)}")
    return gateway_provider(kind, spec)
