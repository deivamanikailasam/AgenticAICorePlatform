"""HTTP API for the sandbox runner.

``POST /v1/executions`` runs one program and returns its result. Input files may be given inline
(base64) or as ``s3://`` artefact URIs; files the program writes to ``/work/out`` come back as
artefacts in S3 (or inline when no store is configured). Callers must present a Keycloak bearer
token (the platform's service identity); the runner listens on 127.0.0.1 only.
"""

import base64
import re
from typing import Annotated, Any

from agent_contracts.interfaces import CodeSandbox
from agent_contracts.models import SandboxLimits, SandboxRequest, SandboxResult
from agent_tools.artefacts import ArtefactStore
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

ARTEFACT_URI = re.compile(r"^(s3|memory)://")


class ExecutionRequest(BaseModel):
    code: str = Field(max_length=200_000)
    files: dict[str, str] = Field(
        default_factory=dict, description="name -> base64 content or an artefact URI (s3://...)"
    )
    limits: SandboxLimits = SandboxLimits()
    run_id: str = "adhoc"
    tenant_id: str = "default"


class ExecutionResponse(BaseModel):
    result: SandboxResult
    artefacts: dict[str, str] = Field(default_factory=dict)


def build_app(
    sandbox: CodeSandbox, artefacts: ArtefactStore | None, verifier: Any | None
) -> FastAPI:
    app = FastAPI(title="sandbox-runner", version="0.1.0", docs_url=None, redoc_url=None)

    async def caller(authorization: Annotated[str | None, Header()] = None) -> str:
        if verifier is None:
            return "unauthenticated-dev"
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "bearer token required")
        token = await verifier.verify_token(authorization.removeprefix("Bearer "))
        if token is None:
            raise HTTPException(401, "invalid token")
        return str(token.client_id)

    @app.get("/healthz")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok" if sandbox.available() else "degraded",
            "engine": type(sandbox).__name__,
        }

    @app.post("/v1/executions")
    async def execute(
        req: ExecutionRequest, who: Annotated[str, Depends(caller)]
    ) -> ExecutionResponse:
        files: dict[str, str] = {}
        for name, value in req.files.items():
            if ARTEFACT_URI.match(value):
                if artefacts is None:
                    raise HTTPException(400, "artefact inputs need an artefact store")
                files[name] = base64.b64encode(await artefacts.get(value)).decode()
            else:
                files[name] = value
        result = await sandbox.run(SandboxRequest(code=req.code, files=files, limits=req.limits))
        uris: dict[str, str] = {}
        if artefacts is not None and result.files:
            for name, b64 in result.files.items():
                key = f"sandbox/{req.tenant_id}/{req.run_id}/{name}"
                uris[name] = await artefacts.put(
                    key, base64.b64decode(b64), "application/octet-stream"
                )
            result = result.model_copy(update={"files": {}})
        return ExecutionResponse(result=result, artefacts=uris)

    return app
