"""Run the worker: ``agent-worker`` (or ``python -m agent_worker``).

Configuration is the run API's (``AGENT_API_*``, ``P0_*``) plus ``TEMPORAL_ADDRESS`` and
``TEMPORAL_NAMESPACE``. With ``P0_S3_ENDPOINT`` set, values over 256 KiB go to Project 0's S3
(``AGENT_ARTEFACT_BUCKET``; keys from the secret store) and travel by reference.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from agent_api.assembly import Assembly
from agent_api.registry import registry_gate
from agent_api.runs import RunManager
from agent_api.settings import Settings
from agent_contracts.dummies.observability import JsonlAuditSink
from agent_runtime.temporal.runtime import TemporalRuntime
from agent_spec.platform import load_platform
from agent_tools.artefacts import S3ArtefactStore
from agent_tools.identity import TokenExchanger
from agent_tools.pdp_factory import build_pdp
from agent_tools.secrets import EnvSecretResolver

from agent_worker.worker import serve


async def artefacts() -> Any:
    endpoint = os.environ.get("P0_S3_ENDPOINT")
    if not endpoint:
        return None  # values stay inline (Temporal's 2 MB payload limit applies)
    secrets = EnvSecretResolver()
    return S3ArtefactStore(
        endpoint,
        os.environ.get("AGENT_ARTEFACT_BUCKET", "agent-artefacts"),
        await secrets.resolve("${secret:project0/s3#access_key}"),
        await secrets.resolve("${secret:project0/s3#secret_key}"),
    )


async def run() -> None:
    settings = Settings()
    exchanger = TokenExchanger(
        settings.token_url, settings.client_id, settings.client_secret, EnvSecretResolver()
    )
    audit = JsonlAuditSink(
        os.environ.get("AGENT_WORKER_AUDIT_FILE", "/tmp/agent-worker-audit.jsonl")  # noqa: S108
    )
    # Step 25: teams run by the worker delegate only to approved agents in staging and prod
    gate = await registry_gate(settings.environment, audit, who="agent-worker")
    assembly = Assembly(
        load_platform(),
        build_pdp(settings.pdp, opa_url=settings.opa_url, cerbos_url=settings.cerbos_url),
        audit,
        rag_core_url=settings.rag_core_url,
        exchanger=exchanger,
        admit=gate.refusal,
    )
    runtime = TemporalRuntime(artefacts=await artefacts())
    handles = await serve(runtime, assembly, RunManager.load_agents(settings.agents_dir))
    print(
        f"agent-worker: serving {handles or 'no temporal agents'} on {runtime.address}", flush=True
    )
    async with runtime.worker():
        await asyncio.Event().wait()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
