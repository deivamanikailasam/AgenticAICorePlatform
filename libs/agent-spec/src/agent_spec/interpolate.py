"""``${VAR}``, ``${VAR:-default}`` and ``${env:VAR}`` substitution, and secret references.

Secret references (``${secret:<path>#<key>}``) are *never* resolved by the loader. They stay as
text and are resolved at tool-call time from OpenBao (Step 29), so a secret can never end up in a
prompt, a trace or the compiled plan. They are only allowed under ``tools[].config``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from agent_spec.errors import SpecIssue

TOKEN = re.compile(r"\$\{(?P<body>[^}]+)\}")
SECRET = re.compile(r"^secret:(?P<path>[A-Za-z0-9_./-]+)#(?P<key>[A-Za-z0-9_.-]+)$")
VAR = re.compile(r"^(?:env:)?(?P<name>[A-Z_][A-Z0-9_]*)(?::-(?P<default>.*))?$")


def _interpolate_str(text: str, env: Mapping[str, str], path: str, issues: list[SpecIssue]) -> str:
    def replace(m: re.Match[str]) -> str:
        body = m["body"]
        if SECRET.match(body):
            return m.group(0)  # kept verbatim; checked by placement rules
        var = VAR.match(body)
        if not var:
            issues.append(
                SpecIssue(
                    path,
                    f"cannot understand '${{{body}}}'; use ${{VAR}}, "
                    "${VAR:-default} or ${secret:path#key}",
                )
            )
            return m.group(0)
        if var["name"] in env:
            return env[var["name"]]
        if var["default"] is not None:
            return var["default"]
        issues.append(
            SpecIssue(path, f"environment variable {var['name']} is not set and has no default")
        )
        return m.group(0)

    return TOKEN.sub(replace, text)


def interpolate(doc: Any, env: Mapping[str, str], issues: list[SpecIssue], path: str = "") -> Any:
    if isinstance(doc, str):
        return _interpolate_str(doc, env, path, issues)
    if isinstance(doc, list):
        return [interpolate(v, env, issues, f"{path}[{i}]") for i, v in enumerate(doc)]
    if isinstance(doc, dict):
        return {
            k: interpolate(v, env, issues, f"{path}.{k}" if path else str(k))
            for k, v in doc.items()
        }
    return doc


def find_secret_refs(doc: Any, path: str = "") -> list[tuple[str, str]]:
    """Every ``(path, reference)`` in the document."""
    found: list[tuple[str, str]] = []
    if isinstance(doc, str):
        found += [(path, m.group(0)) for m in TOKEN.finditer(doc) if SECRET.match(m["body"])]
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            found += find_secret_refs(v, f"{path}[{i}]")
    elif isinstance(doc, dict):
        for k, v in doc.items():
            found += find_secret_refs(v, f"{path}.{k}" if path else str(k))
    return found


SECRET_ALLOWED = re.compile(r"^tools\[\d+\]\.config(\.|\[|$)")


def secret_placement_issues(doc: Any) -> list[SpecIssue]:
    return [
        SpecIssue(
            p,
            "secret references are only allowed under tools[].config "
            "(credentials must never reach prompts or model context)",
        )
        for p, _ in find_secret_refs(doc)
        if not SECRET_ALLOWED.match(p)
    ]
