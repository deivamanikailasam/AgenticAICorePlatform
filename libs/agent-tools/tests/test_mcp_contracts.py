"""Step 33: the MCP tool contract against real MCP servers over stdio (no network): the
invoice-triage ERP demo and the two local test servers (modern and legacy protocol)."""

import sys
import tempfile
from pathlib import Path
from typing import Any, ClassVar

import pytest
from agent_spec.platform import McpServer
from agent_tools.mcp.contract import McpToolContract

ROOT = Path(__file__).resolve().parents[3]
SERVERS = Path(__file__).parent / "mcp_servers"


class TestErpDemoServer(McpToolContract):
    server = McpServer(
        name="erp",
        transport="stdio",
        command=sys.executable,
        args=[str(ROOT / "apps" / "invoice-triage" / "src" / "invoice_triage" / "erp_server.py")],
        env={"ERP_DATA": str(Path(tempfile.mkdtemp(prefix="erp-contract-")) / "erp.json")},
        side_effects={"get_invoice": "read", "post_payment": "irreversible"},
        allowed_tools=["get_invoice", "post_payment"],
    )
    examples: ClassVar[dict[str, dict[str, Any]]] = {
        "get_invoice": {"invoice_id": "INV-1001"},
        "post_payment": {"invoice_id": "INV-1001", "amount_aud": 528.0, "currency": "AUD"},
    }


class TestModernServer(McpToolContract):
    server = McpServer(
        name="math",
        transport="stdio",
        command=sys.executable,
        args=[str(SERVERS / "modern_stdio.py")],
        trusted=True,
        allowed_tools=["add"],
    )
    examples: ClassVar[dict[str, dict[str, Any]]] = {"add": {"a": 2, "b": 3}}


class TestLegacyServer(McpToolContract):
    server = McpServer(
        name="legacy",
        transport="stdio",
        command=sys.executable,
        args=[str(SERVERS / "legacy_stdio.py")],
        allowed_tools=["echo"],
    )
    examples: ClassVar[dict[str, dict[str, Any]]] = {"echo": {"text": "hi"}}


def _filesystem() -> McpServer:
    """platform.yaml's filesystem server, rooted at a fresh directory with one file in it."""
    from agent_spec.platform import load_platform

    root = Path(tempfile.mkdtemp(prefix="fs-contract-"))
    (root / "hello.txt").write_text("hello from the contract\n")
    server = next(s for s in load_platform().mcp.servers if s.name == "filesystem")
    return server.model_copy(update={"args": [*server.args[:-1], str(root)]})


FILESYSTEM = _filesystem()


@pytest.mark.integration
class TestFilesystemServer(McpToolContract):
    """The filesystem server platform.yaml allows (npx downloads it: integration only)."""

    server = FILESYSTEM
    _root = FILESYSTEM.args[-1]
    examples: ClassVar[dict[str, dict[str, Any]]] = {
        "read_text_file": {"path": f"{_root}/hello.txt"},
        "list_directory": {"path": _root},
        "search_files": {"path": _root, "pattern": "hello"},
        "get_file_info": {"path": f"{_root}/hello.txt"},
    }
