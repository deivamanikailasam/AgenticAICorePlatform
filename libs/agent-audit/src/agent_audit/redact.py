"""Tool arguments in the audit trail: hashed always, recorded only redacted (Step 31).

The digest (SHA-256 of the canonical JSON) proves which exact arguments a call ran with, and is
the same digest approvals bind to (``arguments_digest``), so an approved call and its audit record
can be matched. The recorded copy keeps the shape and harmless values for investigators, with
sensitive values replaced:

* values under sensitive keys (password, secret, token, api key, authorization, credential,
  card, IBAN, account and tax numbers ...);
* strings that look like credentials anywhere (the secret-leak guardrail's patterns);
* long free text, which may hold personal data, is cut to a prefix.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from agent_contracts.models import arguments_digest
from agent_guardrails.secrets import PATTERNS

REDACTED = "[redacted]"
SENSITIVE_KEY = re.compile(
    r"pass(word|wd)?|secret|token|api[_-]?key|authori[sz]ation|credential|private[_-]?key|"
    r"card|cvv|iban|bsb|account[_-]?(number|no)|tfn|ssn|tax[_-]?(file|id)",
    re.IGNORECASE,
)
MAX_TEXT = 200


def _value(value: Any) -> Any:
    if isinstance(value, str):
        for name, pattern in PATTERNS:
            value = pattern.sub(f"<{name.upper()}>", value)
        return value if len(value) <= MAX_TEXT else value[:MAX_TEXT] + "…[truncated]"
    if isinstance(value, Mapping):
        return redact_arguments(value)
    if isinstance(value, list | tuple):
        return [_value(v) for v in value]
    return value


def redact_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(k): (REDACTED if SENSITIVE_KEY.search(str(k)) else _value(v))
        for k, v in arguments.items()
    }


def audited_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """What a ``tool.call`` record carries about its arguments."""
    return {
        "arguments_digest": arguments_digest(dict(arguments)),
        "arguments": redact_arguments(arguments),
    }
