import base64

import httpx
import pytest
from agent_contracts.dummies.sandbox import ScriptedSandbox
from agent_contracts.models import RunContext, SandboxRequest, SandboxResult, SandboxStatus
from agent_tools.artefacts import MemoryArtefactStore
from agent_tools.sandbox_tool import SandboxPythonTool
from fastapi.testclient import TestClient
from sandbox_runner.app import build_app


def respond(req: SandboxRequest) -> SandboxResult:
    data = base64.b64decode(req.files.get("in.txt", "")) if req.files else b""
    return SandboxResult(
        status=SandboxStatus.OK,
        stdout="ran",
        files={"out.txt": base64.b64encode(data.upper()).decode()},
        engine="scripted",
    )


class Verifier:
    async def verify_token(self, token: str):  # type: ignore[no-untyped-def]
        from mcp.server.auth.provider import AccessToken

        return (
            AccessToken(token=token, client_id="agent-platform", scopes=[])
            if token == "good"
            else None
        )


def test_requires_a_valid_token() -> None:
    app = build_app(ScriptedSandbox(), None, Verifier())
    with TestClient(app) as client:
        assert client.post("/v1/executions", json={"code": "print(1)"}).status_code == 401
        assert (
            client.post(
                "/v1/executions", json={"code": "print(1)"}, headers={"Authorization": "Bearer bad"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/v1/executions",
                json={"code": "print(1)"},
                headers={"Authorization": "Bearer good"},
            ).status_code
            == 200
        )


async def test_files_come_from_and_go_to_the_artefact_store() -> None:
    store = MemoryArtefactStore()
    uri = await store.put("inputs/in.txt", b"abc")
    app = build_app(ScriptedSandbox(respond), store, None)
    with TestClient(app) as client:
        doc = client.post(
            "/v1/executions",
            json={"code": "x", "files": {"in.txt": uri}, "run_id": "r1", "tenant_id": "acme"},
        ).json()
    assert doc["artefacts"] == {"out.txt": "memory://sandbox/acme/r1/out.txt"}
    assert await store.get(doc["artefacts"]["out.txt"]) == b"ABC"
    assert doc["result"]["files"] == {}


async def test_sandbox_tool_calls_the_runner() -> None:
    app = build_app(ScriptedSandbox(), None, None)
    tool = SandboxPythonTool("http://runner", transport=httpx.ASGITransport(app=app))
    out = await tool.invoke(
        {"code": "print(1)"}, RunContext(tenant_id="t", user_id="u", agent_id="a")
    )
    assert out["status"] == "ok"
    assert out["stdout"].startswith("[scripted]")


@pytest.mark.sandbox
async def test_end_to_end_through_gvisor() -> None:
    from sandbox_runner.engines import GvisorSandbox

    if not GvisorSandbox().available():
        pytest.skip("runsc not installed")
    app = build_app(GvisorSandbox(), None, None)
    tool = SandboxPythonTool("http://runner", transport=httpx.ASGITransport(app=app))
    out = await tool.invoke(
        {"code": "print(6 * 7)"}, RunContext(tenant_id="t", user_id="u", agent_id="a")
    )
    assert out["status"] == "ok"
    assert out["stdout"].strip() == "42"
