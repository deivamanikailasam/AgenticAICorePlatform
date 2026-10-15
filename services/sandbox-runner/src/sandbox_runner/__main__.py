"""Run the sandbox runner on the host: ``just sandbox-runner``.

It drives Docker with the gVisor runtime, so it runs as a host process rather than in a
container (giving a container the Docker socket would hand it root on the host).
"""

from __future__ import annotations

import os

import uvicorn
from agent_contracts.registry import registry
from agent_tools.artefacts import S3ArtefactStore
from agent_tools.auth import KeycloakTokenVerifier

from sandbox_runner.app import build_app


def main() -> None:
    engine = registry.create("code_sandbox", os.environ.get("SANDBOX_ENGINE", "gvisor"))
    store = None
    if os.environ.get("P0_S3_ACCESS_KEY"):
        store = S3ArtefactStore(
            os.environ.get("P0_S3_ENDPOINT", "http://127.0.0.1:8333"),
            os.environ.get("AGENT_ARTEFACT_BUCKET", "agent-artefacts"),
            os.environ["P0_S3_ACCESS_KEY"],
            os.environ["P0_S3_SECRET_KEY"],
        )
    issuer = f"{os.environ.get('P0_KEYCLOAK_URL', 'http://127.0.0.1:8080')}/realms/{os.environ.get('P0_KEYCLOAK_REALM', 'rag-platform')}"
    verifier = (
        None
        if os.environ.get("SANDBOX_AUTH", "keycloak") == "off"
        else KeycloakTokenVerifier(issuer)
    )
    app = build_app(engine, store, verifier)  # type: ignore[arg-type]
    uvicorn.run(
        app, host="127.0.0.1", port=int(os.environ.get("SANDBOX_PORT", "8095")), log_level="info"
    )


if __name__ == "__main__":
    main()
