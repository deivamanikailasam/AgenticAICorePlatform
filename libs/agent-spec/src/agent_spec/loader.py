"""Load ``agent.yaml``: parse, migrate, interpolate, validate, and report every problem at once."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from agent_spec.errors import SpecError, SpecIssue
from agent_spec.interpolate import interpolate, secret_placement_issues
from agent_spec.migrations import migrate
from agent_spec.model import AgentSpec
from agent_spec.semantic import semantic_issues


def _line_index(text: str) -> dict[str, int]:
    """Map dotted paths (``graph.nodes[1].tool``) to 1-based line numbers."""
    index: dict[str, int] = {}
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        return index

    def walk(node: yaml.Node | None, path: str) -> None:
        if node is None:
            return
        index[path] = node.start_mark.line + 1
        if isinstance(node, yaml.MappingNode):
            for k, v in node.value:
                key = f"{path}.{k.value}" if path else str(k.value)
                index[key] = k.start_mark.line + 1
                walk(v, key)
        elif isinstance(node, yaml.SequenceNode):
            for i, v in enumerate(node.value):
                walk(v, f"{path}[{i}]")

    walk(root, "")
    return index


def _line_for(index: dict[str, int], path: str) -> int | None:
    while path:
        if path in index:
            return index[path]
        cut = max(path.rfind("."), path.rfind("["))
        path = path[:cut] if cut > 0 else ""
    return index.get("")


def _loc_to_path(loc: tuple[int | str, ...]) -> str:
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        elif "function-after" in str(part) or "function-before" in str(part):
            continue
        else:
            out += f".{part}" if out else str(part)
    return out


def _pydantic_issues(err: ValidationError) -> list[SpecIssue]:
    issues = []
    for e in err.errors(include_url=False):
        msg = str(e["msg"]).removeprefix("Value error, ")
        value = e.get("input")
        if e["type"] == "extra_forbidden":
            msg = "unknown field (check the spelling; see docs/reference/agent-yaml.md)"
        elif e["type"] == "missing":
            msg = "required field is missing"
        elif not isinstance(value, dict | list) and value is not None:
            shown = repr(value)
            msg += f" (got {shown[:60]}{'…' if len(shown) > 60 else ''})"
        issues.append(SpecIssue(_loc_to_path(tuple(e["loc"])), msg))
    return issues


def parse(
    text: str, source: str = "agent.yaml", env: Mapping[str, str] | None = None
) -> tuple[AgentSpec | None, list[SpecIssue]]:
    """Return the spec (or None) and *all* issues, errors and warnings, with line numbers."""
    index = _line_index(text)
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark is not None else None
        return None, [SpecIssue("", f"not valid YAML: {getattr(exc, 'problem', exc)}", line)]
    if not isinstance(raw, dict):
        return None, [SpecIssue("", "the file must be a YAML mapping (key: value pairs)", 1)]

    issues: list[SpecIssue] = []
    try:
        raw, _ = migrate(raw)
    except ValueError as exc:
        return None, [SpecIssue("spec_version", str(exc), _line_for(index, "spec_version"))]

    raw = interpolate(raw, os.environ if env is None else env, issues)
    issues += secret_placement_issues(raw)

    spec: AgentSpec | None = None
    try:
        spec = AgentSpec.model_validate(raw)
    except ValidationError as err:
        issues += _pydantic_issues(err)
    if spec is not None:
        issues += semantic_issues(spec)

    located = [
        i if i.line else SpecIssue(i.path, i.message, _line_for(index, i.path), i.severity)
        for i in issues
    ]
    if any(i.severity == "error" for i in located):
        spec = None
    return spec, located


def load_spec_text(
    text: str, source: str = "agent.yaml", env: Mapping[str, str] | None = None
) -> AgentSpec:
    spec, issues = parse(text, source, env)
    if spec is None:
        raise SpecError(source, [i for i in issues if i.severity == "error"])
    return spec


def load_spec(path: str | Path, env: Mapping[str, str] | None = None) -> AgentSpec:
    p = Path(path)
    return load_spec_text(p.read_text(), str(p), env)


def dump_raw(spec: AgentSpec) -> dict[str, Any]:
    """The spec as plain data (aliases applied), e.g. for cards and lineage."""
    return spec.model_dump(mode="json", by_alias=True, exclude_defaults=False)
