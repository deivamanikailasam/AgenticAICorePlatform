"""What each agent spec needs from Keycloak and OpenBao (Step 29), read from ``agents/*/agent.yaml``.

* ``audiences``: downstream services its tools call on behalf of the user (``mcp://rag-core/...``,
  allowlisted MCP servers and A2A agents with ``auth.kind: on_behalf_of``). Its Keycloak client
  ``agent-<name>`` may exchange tokens for these and nothing else.
* ``credentials``: the OpenBao credential sources its tools are granted (``tools[].credentials``).
  Its OpenBao policy may issue these and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class AgentNeeds:
    name: str
    audiences: tuple[str, ...] = ()
    credentials: tuple[str, ...] = field(default=())


def _on_behalf_of(entries: list[dict[str, Any]], name: str) -> str | None:
    for entry in entries:
        auth = entry.get("auth") or {}
        if entry.get("name") == name and auth.get("kind") == "on_behalf_of":
            return str(auth.get("audience"))
    return None


def agent_needs(
    agents_dir: Path = ROOT / "agents", platform: Path = ROOT / "platform.yaml"
) -> list[AgentNeeds]:
    cfg = yaml.safe_load(platform.read_text()) or {}
    servers = list((cfg.get("mcp") or {}).get("servers") or [])
    remotes = list((cfg.get("a2a") or {}).get("agents") or [])
    out = []
    for path in sorted(agents_dir.glob("*/agent.yaml")):
        spec = yaml.safe_load(path.read_text()) or {}
        audiences: set[str] = set()
        credentials: set[str] = set()
        for tool in spec.get("tools") or []:
            ref = str(tool.get("ref", ""))
            scheme, _, rest = ref.partition("://")
            target = rest.partition("/")[0]
            if ref.startswith("mcp://rag-core/"):
                audiences.add("rag-core")
            elif (scheme == "mcp" and (aud := _on_behalf_of(servers, target))) or (
                scheme == "a2a" and (aud := _on_behalf_of(remotes, target))
            ):
                audiences.add(aud)
            if tool.get("credentials"):
                credentials.add(str(tool["credentials"]))
        out.append(
            AgentNeeds(str(spec["name"]), tuple(sorted(audiences)), tuple(sorted(credentials)))
        )
    return out
