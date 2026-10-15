"""``platform.yaml``: platform-wide configuration (gateway, model roles, capabilities, defaults)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from agent_contracts.models import ModelCapabilities
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agent_spec.errors import SpecError, SpecIssue
from agent_spec.interpolate import interpolate
from agent_spec.loader import _pydantic_issues

DEFAULT_PATH = Path(__file__).resolve().parents[4] / "platform.yaml"
ROLES = ("default", "planner", "executor", "router", "judge", "summariser")


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Gateway(_Cfg):
    url: str = "http://127.0.0.1:4000"
    key_env_prefix: str = "LITELLM_KEY_"  # LITELLM_KEY_PLANNER, LITELLM_KEY_DEFAULT, …
    timeout_seconds: float = Field(default=60, gt=0)
    max_retries: int = Field(default=2, ge=0, le=6)


class Models(_Cfg):
    roles: dict[str, str]
    capabilities: dict[str, ModelCapabilities] = Field(default_factory=dict)
    fallbacks: dict[str, list[str]] = Field(
        default_factory=dict
    )  # informational: the gateway applies them


class Currency(_Cfg):
    """LiteLLM accounts cost in USD; budgets and reports are in AUD."""

    usd_to_aud: float = Field(gt=0)
    source: str
    as_of: date


class McpAuth(_Cfg):
    """How the platform authenticates to a remote MCP server."""

    # on_behalf_of: every call carries the *user's* token exchanged (RFC 8693) for ``audience``;
    # tool discovery uses the client credentials below. Never falls back to a service identity.
    kind: Literal["none", "oauth_client_credentials", "bearer", "on_behalf_of"] = "none"
    issuer: str | None = None  # OAuth: the authorization server the credentials belong to
    audience: str | None = None  # on_behalf_of: the downstream service the token is valid for
    client_id: str | None = None
    client_secret: str | None = None  # a ${secret:path#key} reference, resolved at call time
    scope: str | None = None
    token: str | None = None  # bearer: a ${secret:path#key} reference

    @model_validator(mode="after")
    def _audience_for_on_behalf_of(self) -> McpAuth:
        if self.kind == "on_behalf_of" and not self.audience:
            raise ValueError("auth.kind on_behalf_of needs auth.audience")
        return self


class McpServer(_Cfg):
    """One allowlisted MCP server. Servers not listed here are refused."""

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,40}$")
    transport: Literal["http", "stdio"]
    url: str | None = None  # http
    command: str | None = None  # stdio
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    # Only these variables from the platform's own environment reach a stdio server (never the
    # whole environment: it can hold secrets). Defaults: proxy and CA settings only.
    env_passthrough: list[str] = Field(
        default_factory=lambda: [
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "NO_PROXY",
            "https_proxy",
            "http_proxy",
            "no_proxy",
            "SSL_CERT_FILE",
            "NODE_EXTRA_CA_CERTS",
            "REQUESTS_CA_BUNDLE",
        ]
    )
    protocol: Literal["auto", "legacy", "2026-07-28"] = "auto"
    trusted: bool = False  # only trusted servers' tool annotations are believed
    allowed_tools: list[str] | None = None  # None = every tool the server lists
    side_effects: dict[str, Literal["read", "write", "irreversible"]] = Field(default_factory=dict)
    auth: McpAuth = McpAuth()
    timeout_seconds: float = Field(default=30, gt=0)


class Mcp(_Cfg):
    servers: list[McpServer] = Field(default_factory=list)

    def server(self, name: str) -> McpServer | None:
        return next((s for s in self.servers if s.name == name), None)


class A2aAgent(_Cfg):
    """One allowlisted remote A2A agent (Step 24). Agents not listed here are refused, and a
    listed agent's Agent Card must carry a valid signature from one of its pinned keys."""

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,40}$")
    url: str  # base URL; the card is read from <url>/.well-known/agent-card.json
    description: str = ""
    card_keys: dict[str, str] = Field(min_length=1)  # kid -> PEM public key (pinned)
    algorithms: list[str] = Field(default_factory=lambda: ["ES256"])
    binding: Literal["JSONRPC", "HTTP+JSON", "GRPC"] = "JSONRPC"
    side_effect: Literal["read", "write", "irreversible"] = "write"  # remote agents act: not read
    auth: McpAuth = McpAuth()
    timeout_seconds: float = Field(default=120, gt=0)


class A2aPublish(_Cfg):
    """How the a2a-gateway publishes this platform's agents."""

    base_url: str = "http://127.0.0.1:8092"
    key_id: str = "agent-platform-1"
    signing_key: str = "${secret:agent-platform/a2a#signing_key}"  # ES256 private key (PEM)
    push_allowed_hosts: list[str] = Field(default_factory=list)  # push notification targets


class A2a(_Cfg):
    publish: A2aPublish = A2aPublish()
    agents: list[A2aAgent] = Field(default_factory=list)

    def agent(self, name: str) -> A2aAgent | None:
        return next((a for a in self.agents if a.name == name), None)


class PlatformConfig(_Cfg):
    platform_version: Literal[1] = 1
    gateway: Gateway = Gateway()
    models: Models
    currency: Currency
    components: dict[str, str] = Field(default_factory=dict)  # platform defaults: slot -> name
    mcp: Mcp = Mcp()
    a2a: A2a = A2a()

    def alias_for(self, role_or_alias: str, spec_models: Mapping[str, str] | None = None) -> str:
        """Spec role > platform role > the name itself (already an alias)."""
        if spec_models and role_or_alias in spec_models:
            return spec_models[role_or_alias]
        return self.models.roles.get(role_or_alias, role_or_alias)

    def capabilities(self, alias: str) -> ModelCapabilities:
        return self.models.capabilities.get(alias, ModelCapabilities())


def load_platform(
    path: str | Path | None = None, env: Mapping[str, str] | None = None
) -> PlatformConfig:
    p = Path(path) if path else Path(os.environ.get("AGENT_PLATFORM_CONFIG", str(DEFAULT_PATH)))
    issues: list[SpecIssue] = []
    raw = interpolate(yaml.safe_load(p.read_text()), os.environ if env is None else env, issues)
    if not issues:
        try:
            cfg = PlatformConfig.model_validate(raw)
        except ValidationError as err:
            issues += _pydantic_issues(err)
        else:
            missing = [r for r in ROLES if r not in cfg.models.roles]
            if not missing:
                return cfg
            issues.append(SpecIssue("models.roles", f"missing roles: {', '.join(missing)}"))
    raise SpecError(str(p), issues)
