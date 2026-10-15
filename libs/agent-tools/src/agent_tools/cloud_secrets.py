"""Cloud alternatives to OpenBao and the AppRole bootstrap, as documented stubs (ADR 0040).

The spec's production path is "Keycloak + OpenBao → cloud IAM and secret managers". Each cloud
has its own short-lived credentials, and they map onto the same two contracts:

| Cloud | Static secrets (``SecretResolver``) | Short-lived tool credentials (``CredentialBroker``) |
|---|---|---|
| AWS | Secrets Manager | STS ``AssumeRole`` session credentials; RDS IAM auth tokens (15 min) |
| GCP | Secret Manager | Service-account access tokens (``generateAccessToken``, ≤ 1 h, settable lower); Cloud SQL IAM auth |
| Azure | Key Vault | Entra ID tokens for a managed identity; Azure Database Entra auth |

Workload identity replaces the AppRole secret id in Kubernetes: SPIFFE/SPIRE issues each pod a
JWT-SVID, which logs in to OpenBao through the same JWT auth the agents use
(``SpiffeJwtAuth``).

None of these can be built and verified here: there is no cloud account and no Kubernetes
cluster in this build. They refuse clearly, and are finished in Step 37 (production) against
a real account or cluster.
"""

from __future__ import annotations

from typing import Any, NoReturn

import httpx
from agent_contracts.interfaces import CredentialBroker
from agent_contracts.models import Credential, RunContext

from agent_tools.openbao import OpenBaoAuth
from agent_tools.secrets import SecretResolver

ADR = "ADR 0040"


def _refuse(what: str) -> NoReturn:
    raise NotImplementedError(
        f"{what} is a documented stub ({ADR}); use OpenBao (credential_broker.openbao) until "
        "it is finished against a real account in Step 37"
    )


class _StubBroker(CredentialBroker):
    component_status = "stub"
    NAME = ""

    def __init__(self, **_: Any) -> None:
        pass

    async def issue(self, source: str, ctx: RunContext) -> Credential:
        _refuse(self.NAME)

    async def revoke(self, credential: Credential) -> None:
        _refuse(self.NAME)

    async def valid(self, credential: Credential) -> bool:
        _refuse(self.NAME)


class AwsCredentialBroker(_StubBroker):
    """AWS. STUB: STS ``AssumeRole`` (session policy per call, ``DurationSeconds`` ≥ 900) or RDS
    IAM auth tokens."""

    NAME = "the AWS credential broker"


class GcpCredentialBroker(_StubBroker):
    """GCP. STUB: short-lived service-account tokens via the IAM Credentials API."""

    NAME = "the GCP credential broker"


class AzureCredentialBroker(_StubBroker):
    """Azure. STUB: Entra ID tokens for a user-assigned managed identity."""

    NAME = "the Azure credential broker"


class _StubResolver(SecretResolver):
    NAME = ""

    async def resolve(self, reference: str) -> str:
        _refuse(self.NAME)


class AwsSecretsManagerResolver(_StubResolver):
    NAME = "the AWS Secrets Manager resolver"


class GcpSecretManagerResolver(_StubResolver):
    NAME = "the GCP Secret Manager resolver"


class AzureKeyVaultResolver(_StubResolver):
    NAME = "the Azure Key Vault resolver"


class SpiffeJwtAuth(OpenBaoAuth):
    """Kubernetes workload identity. STUB: fetch a JWT-SVID for audience ``openbao`` from the
    SPIRE agent's Workload API socket and log in through OpenBao's JWT auth (role per workload)."""

    def __init__(self, role: str, socket: str = "unix:///run/spire/sockets/agent.sock") -> None:
        self.role, self.socket = role, socket

    async def login(self, http: httpx.AsyncClient, url: str) -> tuple[str, float]:
        _refuse("SPIFFE/SPIRE workload identity")
