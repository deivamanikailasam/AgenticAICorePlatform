"""Secret and credential leak detection (Step 27).

Known credential shapes (cloud keys, private keys, tokens, JWTs, connection strings with
passwords, ``password=`` assignments) are redacted from outputs and tool results, and block a
tool call that would send one out (``tool_args``).
"""

from __future__ import annotations

import json
import re
from typing import ClassVar

from agent_contracts.interfaces import Guardrail
from agent_contracts.models import (
    GuardrailAction,
    GuardrailInput,
    GuardrailResult,
    GuardrailStage,
)

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY(?: BLOCK)?-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----"
        ),
    ),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "github_token",
        re.compile(
            r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{60,}\b"
        ),
    ),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("api_key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("connection_string", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@/]{3,}@[^\s/]+", re.I)),
    (
        "password_assignment",
        re.compile(
            r"\b(?:password|passwd|pwd|secret|api[_-]?key|token)\s*[:=]\s*['\"]?[^\s'\"]{6,}", re.I
        ),
    ),
]


class SecretLeakGuardrail(Guardrail):
    name: ClassVar[str] = "secret_leak"
    stages: ClassVar[frozenset[GuardrailStage]] = frozenset(
        {GuardrailStage.OUTPUT, GuardrailStage.TOOL_RESULT, GuardrailStage.TOOL_ARGS}
    )
    redacts: ClassVar[bool] = True

    async def check(self, payload: GuardrailInput) -> GuardrailResult:
        text = payload.text or json.dumps(payload.data, default=str)
        found = sorted({name for name, p in PATTERNS if p.search(text)})
        if not found:
            return GuardrailResult(guardrail=self.name, passed=True)
        if payload.stage is GuardrailStage.TOOL_ARGS:
            return GuardrailResult(
                guardrail=self.name,
                passed=False,
                action=GuardrailAction.BLOCK,
                findings=tuple(f"{f} in the arguments of {payload.tool}" for f in found),
            )
        redacted = payload.text
        for name, pattern in PATTERNS:
            redacted = pattern.sub(f"<{name.upper()}>", redacted)
        return GuardrailResult(
            guardrail=self.name,
            passed=False,
            action=GuardrailAction.REDACT,
            findings=tuple(found),
            redacted_text=redacted,
        )
