"""A remote MCP server over Streamable HTTP that requires a Keycloak-issued OAuth token.

Usage: python -m ...remote_http <port> <issuer> [audience]

With an audience, only tokens issued for that audience are accepted (an on-behalf-of resource
server), and ``whoami`` reports whose rights the call carries.
"""

import sys

import uvicorn
from agent_tools.auth import KeycloakTokenVerifier
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

port, issuer = int(sys.argv[1]), sys.argv[2]
audience = sys.argv[3] if len(sys.argv) > 3 else None
server = MCPServer(
    name="remote-crm",
    version="1.0.0",
    token_verifier=KeycloakTokenVerifier(issuer, audience=audience),
    auth=AuthSettings(issuer_url=issuer, resource_server_url=f"http://127.0.0.1:{port}/mcp"),
)


@server.tool(
    description="Look up a customer by id.", annotations=ToolAnnotations(read_only_hint=True)
)
def get_customer(customer_id: str) -> dict[str, str]:
    return {"id": customer_id, "name": "Ada Lovelace", "tier": "gold"}


@server.tool(description="Email a customer.", annotations=ToolAnnotations(destructive_hint=True))
def email_customer(customer_id: str, body: str) -> str:
    return f"queued email to {customer_id}"


@server.tool(description="Who is this call for?", annotations=ToolAnnotations(read_only_hint=True))
def whoami() -> dict[str, str | None]:
    token = get_access_token()
    claims = (token.claims or {}) if token else {}
    return {
        "user": claims.get("preferred_username"),
        "subject": token.subject if token else None,
        "client_id": token.client_id if token else None,
    }


uvicorn.run(server.streamable_http_app(), host="127.0.0.1", port=port, log_level="warning")
