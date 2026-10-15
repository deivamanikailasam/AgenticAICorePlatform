"""Value checks on tool arguments and outputs; topic and safety filters (Step 27)."""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

import jsonschema
from agent_contracts.interfaces import Guardrail
from agent_contracts.models import (
    GuardrailAction,
    GuardrailInput,
    GuardrailResult,
    GuardrailStage,
)


def _block(name: str, findings: Sequence[str]) -> GuardrailResult:
    return GuardrailResult(
        guardrail=name, passed=False, action=GuardrailAction.BLOCK, findings=tuple(findings)
    )


def _ok(name: str) -> GuardrailResult:
    return GuardrailResult(guardrail=name, passed=True)


class ToolArgsGuardrail(Guardrail):
    """Tool arguments against the tool's JSON Schema and per-argument limits from the spec::

    guardrails:
      - name: tool_args_schema
        config:
          limits:
            erp__post_payment: { amount_aud: { max: 5000 }, currency: { enum: [AUD] } }
            send_email: { to: { pattern: "@example\\\\.com$" } }
    """

    name: ClassVar[str] = "tool_args_schema"
    stages: ClassVar[frozenset[GuardrailStage]] = frozenset({GuardrailStage.TOOL_ARGS})

    def __init__(self, limits: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None) -> None:
        self.limits = {t: dict(v) for t, v in (limits or {}).items()}

    async def check(self, payload: GuardrailInput) -> GuardrailResult:
        args = dict(payload.data.get("arguments") or {})
        problems: list[str] = []
        schema = payload.data.get("schema")
        if schema:
            try:
                jsonschema.validate(args, schema)
            except jsonschema.ValidationError as exc:
                problems.append(f"schema: {exc.message}")
        for arg, rule in self.limits.get(payload.tool or "", {}).items():
            if arg not in args:
                continue
            value = args[arg]
            if "max" in rule and isinstance(value, int | float) and value > rule["max"]:
                problems.append(f"{arg}={value} is above the limit {rule['max']}")
            if "min" in rule and isinstance(value, int | float) and value < rule["min"]:
                problems.append(f"{arg}={value} is below the limit {rule['min']}")
            if "enum" in rule and value not in rule["enum"]:
                problems.append(f"{arg}={value!r} is not one of {rule['enum']}")
            if "pattern" in rule and not re.search(str(rule["pattern"]), str(value)):
                problems.append(f"{arg}={value!r} does not match {rule['pattern']!r}")
            if "max_length" in rule and len(str(value)) > int(rule["max_length"]):
                problems.append(f"{arg} is longer than {rule['max_length']} characters")
        return _block(self.name, problems) if problems else _ok(self.name)


class OutputGuardrail(Guardrail):
    """The answer against a JSON Schema (``schema``, or the spec's output schema passed in
    ``data``) and output policy: ``forbidden`` patterns (e.g. promises the business does not
    make) and ``max_length``."""

    name: ClassVar[str] = "output_schema"
    stages: ClassVar[frozenset[GuardrailStage]] = frozenset({GuardrailStage.OUTPUT})

    def __init__(
        self,
        schema: Mapping[str, Any] | None = None,
        forbidden: Sequence[str] = (),
        max_length: int | None = None,
    ) -> None:
        self.schema = dict(schema) if schema else None
        self.forbidden = [re.compile(p, re.I) for p in forbidden]
        self.max_length = max_length

    async def check(self, payload: GuardrailInput) -> GuardrailResult:
        problems: list[str] = []
        schema = payload.data.get("schema") or self.schema
        if schema and "output" in payload.data:
            output = payload.data["output"]
            if isinstance(output, str):
                with contextlib.suppress(json.JSONDecodeError):
                    output = json.loads(output)
            try:
                jsonschema.validate(output, schema)
            except jsonschema.ValidationError as exc:
                problems.append(f"output schema: {exc.message}")
        for pattern in self.forbidden:
            if m := pattern.search(payload.text):
                problems.append(f"forbidden by output policy: {m.group(0)!r}")
        if self.max_length is not None and len(payload.text) > self.max_length:
            problems.append(f"answer longer than {self.max_length} characters")
        return _block(self.name, problems) if problems else _ok(self.name)


class TopicFilterGuardrail(Guardrail):
    """Keeps an agent to its job: ``blocked`` topics (name -> phrases or regexes) are refused in
    the user's input and in answers."""

    name: ClassVar[str] = "topic_filter"
    stages: ClassVar[frozenset[GuardrailStage]] = frozenset(
        {GuardrailStage.INPUT, GuardrailStage.OUTPUT}
    )

    def __init__(self, blocked: Mapping[str, Sequence[str]] | None = None) -> None:
        self.blocked = {
            topic: [
                re.compile(rf"\b{p}\b" if p.replace(" ", "").isalnum() else p, re.I)
                for p in phrases
            ]
            for topic, phrases in (blocked or {}).items()
        }

    async def check(self, payload: GuardrailInput) -> GuardrailResult:
        hits = [
            f"off-topic ({topic}): {m.group(0)!r}"
            for topic, patterns in self.blocked.items()
            for p in patterns
            if (m := p.search(payload.text))
        ]
        return _block(self.name, hits) if hits else _ok(self.name)


SAFETY: dict[str, re.Pattern[str]] = {
    "weapons": re.compile(
        r"\b(how to|steps to|instructions? (for|to)|guide to)\b[^.\n]{0,40}\b(make|build|assemble|"
        r"synthesi[sz]e)\b[^.\n]{0,30}\b(bomb|explosive|pipe bomb|nerve agent|bioweapon|ghost gun)\b",
        re.I,
    ),
    "self_harm": re.compile(
        r"\b(how (to|can i)|best way to|methods? (to|of))\b[^.\n]{0,30}\b(kill myself|end my life|"
        r"commit suicide|self[- ]harm)\b",
        re.I,
    ),
    "malware": re.compile(
        r"\b(write|create|build|generate)\b[^.\n]{0,30}\b(ransomware|keylogger|credential stealer|"
        r"botnet|malware)\b",
        re.I,
    ),
    "violent_threat": re.compile(r"\bI (will|am going to) (kill|hurt|shoot|stab)\b", re.I),
}


class SafetyFilterGuardrail(Guardrail):
    """Rules for clearly harmful requests and answers (weapons, self-harm methods, malware,
    violent threats). A self-harm hit also adds a support message for the caller to show."""

    name: ClassVar[str] = "safety_filter"
    stages: ClassVar[frozenset[GuardrailStage]] = frozenset(
        {GuardrailStage.INPUT, GuardrailStage.OUTPUT}
    )

    def __init__(self, categories: Sequence[str] | None = None) -> None:
        self.categories = {c: SAFETY[c] for c in (categories or SAFETY)}

    async def check(self, payload: GuardrailInput) -> GuardrailResult:
        hits = [name for name, p in self.categories.items() if p.search(payload.text)]
        if not hits:
            return _ok(self.name)
        findings = [f"unsafe: {h}" for h in hits]
        if "self_harm" in hits:
            findings.append("support: Lifeline 13 11 14 (Australia)")
        return _block(self.name, findings)
