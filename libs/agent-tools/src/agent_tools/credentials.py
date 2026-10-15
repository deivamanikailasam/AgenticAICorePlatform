"""The credential of the tool call in progress (Step 29).

The authorising executor issues a short-lived credential for a tool whose grant names a
credential source, sets it here for the duration of that one call, and revokes it afterwards.
A tool reads it with ``require_credential()``. It never travels in arguments, results, run
state, checkpoints, prompts, events or traces, and ``Credential`` hides its values when dumped.
"""

from __future__ import annotations

import contextvars

from agent_contracts.models import Credential

current_credential: contextvars.ContextVar[Credential | None] = contextvars.ContextVar(
    "current_credential", default=None
)


class MissingCredentialError(PermissionError):
    pass


def require_credential(tool: str) -> Credential:
    cred = current_credential.get()
    if cred is None:
        raise MissingCredentialError(
            f"tool {tool} needs a call-time credential: grant it with `credentials:` in the "
            "agent's spec, and run it through the platform"
        )
    return cred
