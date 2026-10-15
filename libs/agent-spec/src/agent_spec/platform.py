"""``platform.yaml``: platform-wide configuration (gateway, model roles, capabilities, defaults)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from agent_contracts.models import ModelCapabilities
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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


class PlatformConfig(_Cfg):
    platform_version: Literal[1] = 1
    gateway: Gateway = Gateway()
    models: Models
    currency: Currency
    components: dict[str, str] = Field(default_factory=dict)  # platform defaults: slot -> name

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
