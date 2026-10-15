"""Generate the JSON Schema and the human reference page for ``agent.yaml``."""

from __future__ import annotations

from typing import Any

from agent_spec.model import AgentSpec, Pattern


def schema() -> dict[str, Any]:
    s = AgentSpec.model_json_schema(by_alias=True)
    s["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    s["$id"] = "https://agent-platform.local/schemas/agent-spec/v1.json"
    s["title"] = "agent.yaml (spec_version 1)"
    return s


def _type_of(prop: dict[str, Any], defs: dict[str, Any]) -> str:
    if "$ref" in prop:
        return str(prop["$ref"]).rsplit("/", 1)[-1]
    if "anyOf" in prop:
        return " or ".join(_type_of(p, defs) for p in prop["anyOf"])
    if prop.get("type") == "array":
        return f"list of {_type_of(prop.get('items', {}), defs)}"
    if "enum" in prop:
        return " \\| ".join(f"`{v}`" for v in prop["enum"])
    if "const" in prop:
        return f"`{prop['const']}`"
    return str(prop.get("type", "any"))


def _table(name: str, body: dict[str, Any], defs: dict[str, Any]) -> list[str]:
    lines = [f"### `{name}`", ""]
    if body.get("description"):
        lines += [body["description"].strip(), ""]
    if "enum" in body:
        return [*lines, "One of: " + ", ".join(f"`{v}`" for v in body["enum"]), ""]
    lines += ["| Field | Type | Required | Default |", "|---|---|---|---|"]
    required = set(body.get("required", []))
    for field, prop in body.get("properties", {}).items():
        default = prop.get("default", "")
        shown = f"`{default}`" if default not in ("", None) else ""
        lines.append(
            f"| `{field}` | {_type_of(prop, defs)} | {'yes' if field in required else ''} | {shown} |"
        )
    return [*lines, ""]


def render_reference() -> str:
    s = schema()
    defs = s.get("$defs", {})
    out = [
        "# `agent.yaml` reference (spec_version 1)",
        "",
        "Generated from the Pydantic models in `libs/agent-spec` by `just spec-reference` — do not",
        "edit by hand. The machine-readable schema is `agent-spec.schema.json` next to this file.",
        "",
        "Interpolation: `${VAR}`, `${VAR:-default}` and `${env:VAR}` are replaced when the spec is",
        "loaded. `${secret:<path>#<key>}` is **never** resolved at load time and is only allowed",
        "under `tools[].config`; it is fetched from OpenBao when the tool is called.",
        "",
        "Edges may be written as `a -> b`, `a -> b: on_error`, or `{from: a, to: b, when: on_ok}`.",
        "",
        "Patterns: " + ", ".join(f"`{p.value}`" for p in Pattern),
        "",
        *_table("AgentSpec (root)", s, defs),
    ]
    for name in sorted(defs):
        out += _table(name, defs[name], defs)
    return "\n".join(out).rstrip() + "\n"
