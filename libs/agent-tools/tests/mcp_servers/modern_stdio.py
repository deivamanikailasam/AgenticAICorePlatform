"""A current (2026-07-28) MCP server over stdio, built with the official SDK."""

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

server = MCPServer(name="modern-math", version="1.0.0")


@server.tool(
    description="Add two numbers.",
    annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
)
def add(a: float, b: float) -> float:
    return a + b


@server.tool(description="Fail on purpose.")
def broken() -> str:
    raise ValueError("this tool always fails")


if __name__ == "__main__":
    server.run("stdio")
