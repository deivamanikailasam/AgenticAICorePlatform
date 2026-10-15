"""From a compiled plan's tool list to what the authorising executor needs (Step 12).

* ``registry_name(ref)`` — the model-facing name a tool is registered under:
  ``mcp://erp/post_payment`` → ``erp__post_payment``; ``local://platform/calculator`` →
  ``calculator``; ``mcp://rag-core/search`` → ``search``; ``sandbox://python`` → ``python``.
* ``tool_names(plan)`` — spec tool name → registry name (for tool nodes).
* ``grants_for(plan)`` — registry name → ``ToolGrant`` (rate limit, call-time credential source), so policy sees exactly the
  tools the spec grants. The spec's ``approval`` is enforced by the APPROVAL node the compiler
  puts in front of the tool, so the grant does not ask again; approvals that *policy* requires
  still stop the call until Step 28 hands approval evidence to policy (fail closed).
"""

from __future__ import annotations

from typing import Any

from agent_contracts.models import ExecutablePlan

from agent_tools.authorising import ToolGrant
from agent_tools.mcp.client import model_name

#: tools the platform registers under a fixed name rather than ``<server>__<tool>``
FIXED_NAMES = {"mcp://rag-core/search": "search", "sandbox://python": "python"}


def registry_name(ref: str) -> str:
    if ref in FIXED_NAMES:
        return FIXED_NAMES[ref]
    scheme, _, rest = ref.partition("://")
    if scheme == "mcp":
        server, _, tool = rest.partition("/")
        return model_name(server, tool)
    return rest.rstrip("/").rsplit("/", 1)[-1]


def _tools(plan: ExecutablePlan) -> list[dict[str, Any]]:
    return list(plan.metadata.get("tools") or [])


def tool_names(plan: ExecutablePlan) -> dict[str, str]:
    return {str(t["name"]): registry_name(str(t["ref"])) for t in _tools(plan)}


def grants_for(plan: ExecutablePlan) -> dict[str, ToolGrant]:
    return {
        registry_name(str(t["ref"])): ToolGrant(
            rate_limit_per_minute=t.get("rate_limit_per_minute"),
            credentials=t.get("credentials"),
        )
        for t in _tools(plan)
    }
