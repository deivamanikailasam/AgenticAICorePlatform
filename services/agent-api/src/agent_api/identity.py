"""Who the platform's runs act as, and where their secrets come from (Step 29).

With OpenBao configured (always in staging and prod, where ``default_resolver`` refuses to start
without it):

* secrets come from OpenBao, read with the runtime's AppRole (or, in Kubernetes, its
  service-account token);
* each agent acts as its own Keycloak client ``agent-<name>``: downstream tokens are exchanged
  in two hops, user → platform → agent → service (``DelegatingExchanger``);
* tools get short-lived credentials from OpenBao, which the agent obtains by logging in with its
  own service-account token, so OpenBao's policy for that agent bounds what it can get.

Without OpenBao (dev only): environment secrets, the platform's single-hop exchange, and no
credential broker (tools that need credentials fail closed).
``AGENT_IDENTITY_MODE=platform|per-agent`` overrides the identity choice.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from agent_contracts.interfaces import CredentialBroker
from agent_tools.identity import AgentIdentities, DelegatingExchanger, Exchanger, TokenExchanger
from agent_tools.openbao import OpenBaoCredentialBroker
from agent_tools.secrets import SecretResolver, default_resolver

from agent_api.settings import Settings


@dataclass
class Identity:
    secrets: SecretResolver
    exchanger: Exchanger
    broker: CredentialBroker | None
    identities: AgentIdentities | None


def identity_from_env(settings: Settings) -> Identity:
    secrets = default_resolver()
    platform = TokenExchanger(
        settings.token_url, settings.client_id, settings.client_secret, secrets
    )
    bao_url = os.environ.get("P0_OPENBAO_URL")
    bao_ready = bool(
        bao_url and (os.environ.get("AGENT_BAO_ROLE_ID") or os.environ.get("AGENT_BAO_K8S_ROLE"))
    )
    mode = os.environ.get("AGENT_IDENTITY_MODE") or ("per-agent" if bao_ready else "platform")
    if mode not in ("per-agent", "platform"):
        raise SystemExit(f"AGENT_IDENTITY_MODE={mode}: expected per-agent or platform")
    if mode == "platform":
        return Identity(secrets, platform, None, None)
    identities = AgentIdentities(settings.token_url, secrets)
    broker = OpenBaoCredentialBroker(bao_url, agent_auth=identities.bao_auth) if bao_url else None
    return Identity(secrets, DelegatingExchanger(platform, identities), broker, identities)
