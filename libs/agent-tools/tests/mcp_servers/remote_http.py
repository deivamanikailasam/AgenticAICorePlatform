"""A remote MCP server over Streamable HTTP that requires a Keycloak-issued OAuth token.

Usage: python -m ...remote_http <port> <issuer>
"""

import sys

import uvicorn
from agent_tools.auth import KeycloakTokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

port, issuer = int(sys.argv[1]), sys.argv[2]
server = MCPServer(
    name="remote-crm",
    version="1.0.0",
    token_verifier=KeycloakTokenVerifier(issuer),
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


uvicorn.run(server.streamable_http_app(), host="127.0.0.1", port=port, log_level="warning")
