"""Threat model and control matrix (Step 37): load, check and render.

``governance/threat-model.yaml`` holds STRIDE threats per component; ``governance/controls.yaml``
the controls with their evidence and framework mapping. ``problems()`` lists everything that
breaks the rules: a threat without an owner, control or test; an owner role without a person; a
component that neither covers a STRIDE category nor says why it doesn't; an unknown control; a
control without evidence; evidence that doesn't exist. Test references (pytest node ids and
``redteam:<id>``) are returned by ``test_refs()`` so the caller can check they are collected.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

STRIDE = {
    "S": "Spoofing",
    "T": "Tampering",
    "R": "Repudiation",
    "I": "Information disclosure",
    "D": "Denial of service",
    "E": "Elevation of privilege",
}
FRAMEWORKS = {
    "nist_ai_rmf": "NIST AI RMF",
    "iso42001": "ISO/IEC 42001 Annex A",
    "eu_ai_act": "EU AI Act",
    "app": "Australian Privacy Principles",
    "au_gfaa": "AU Guidance for AI Adoption",
}


def person_of(owner: Any) -> str:
    """An owner role is ``"@person"`` or ``{person: "@person", responsibility: ...}``."""
    return str(owner.get("person", "")) if isinstance(owner, Mapping) else str(owner or "")


@dataclass(frozen=True)
class Model:
    owners: dict[str, Any]
    components: dict[str, dict[str, Any]]
    controls: dict[str, dict[str, Any]]

    @staticmethod
    def load(root: str | Path) -> Model:
        root = Path(root)
        threats = yaml.safe_load((root / "governance" / "threat-model.yaml").read_text()) or {}
        controls = yaml.safe_load((root / "governance" / "controls.yaml").read_text()) or {}
        return Model(
            dict(threats.get("owners") or {}),
            dict(threats.get("components") or {}),
            dict(controls.get("controls") or {}),
        )

    def threats(self) -> list[dict[str, Any]]:
        return [
            t | {"component": c}
            for c, comp in self.components.items()
            for t in comp.get("threats") or []
        ]

    def test_refs(self) -> set[str]:
        refs = {str(x) for t in self.threats() for x in t.get("tests") or []}
        for c in self.controls.values():
            for e in c.get("evidence") or []:
                if isinstance(e, Mapping):
                    refs |= {f"redteam:{e['redteam']}"} if "redteam" in e else {str(e["test"])}
        return refs

    def problems(self, root: str | Path, redteam_ids: Collection[str]) -> list[str]:
        root = Path(root)
        out: list[str] = []
        seen: set[str] = set()
        used: set[str] = set()
        for t in self.threats():
            tid = str(t.get("id"))
            where = f"{t['component']}/{tid}"
            if tid in seen:
                out.append(f"{where}: duplicate threat id")
            seen.add(tid)
            if t.get("stride") not in STRIDE:
                out.append(f"{where}: stride must be one of {', '.join(STRIDE)}")
            owner = t.get("owner")
            if not owner:
                out.append(f"{where}: no owner")
            elif not person_of(self.owners.get(str(owner))).startswith("@"):
                out.append(f"{where}: owner role {owner!r} names no person or team")
            if not t.get("controls"):
                out.append(f"{where}: no control")
            for c in t.get("controls") or []:
                used.add(str(c))
                if c not in self.controls:
                    out.append(f"{where}: unknown control {c}")
            if not t.get("tests"):
                out.append(f"{where}: no test")
            for ref in t.get("tests") or []:
                if str(ref).startswith("redteam:") and ref.split(":", 1)[1] not in redteam_ids:
                    out.append(f"{where}: unknown red-team test {ref}")
        for name, comp in self.components.items():
            covered = {t.get("stride") for t in comp.get("threats") or []}
            skipped = set((comp.get("not_applicable") or {}).keys())
            for letter in STRIDE:
                if letter not in covered and letter not in skipped:
                    out.append(f"{name}: no threat and no reason for {STRIDE[letter]} ({letter})")
                if letter in covered and letter in skipped:
                    out.append(f"{name}: {letter} has threats and is also marked not applicable")
        for cid, c in self.controls.items():
            evidence = c.get("evidence") or []
            if not evidence:
                out.append(f"control {cid}: no evidence")
            if not any(isinstance(e, Mapping) for e in evidence):
                out.append(f"control {cid}: no test among its evidence")
            for e in evidence:
                if isinstance(e, str) and not (root / e).exists():
                    out.append(f"control {cid}: evidence {e} does not exist")
                if isinstance(e, Mapping) and "redteam" in e and e["redteam"] not in redteam_ids:
                    out.append(f"control {cid}: unknown red-team test {e['redteam']}")
            unknown = set(c.get("frameworks") or {}) - set(FRAMEWORKS)
            if unknown:
                out.append(f"control {cid}: unknown framework(s) {', '.join(sorted(unknown))}")
            if cid not in used:
                out.append(f"control {cid}: answers no threat")
        return out


def _ref(ref: str) -> str:
    return f"`{ref}`"


def render_threats(model: Model) -> str:
    lines = [
        "# Threat model",
        "",
        "Generated by `just threat-model` from `governance/threat-model.yaml`; do not edit by hand.",
        "STRIDE per component, mapped to the OWASP Top 10 for Agentic Applications and to the",
        "controls in the [control matrix](control-matrix.md). Every threat has an owner, a control",
        "and an automated test; `just threat-check` (part of `just check`) enforces it.",
        "",
        f"**{len(model.threats())} threats across {len(model.components)} components.**",
        "",
        "## Owners",
        "",
        "| Role | Held by | Accountable for | Threats |",
        "|---|---|---|---|",
    ]
    for role, owner in model.owners.items():
        owned = [str(t["id"]) for t in model.threats() if t.get("owner") == role]
        what = owner.get("responsibility", "") if isinstance(owner, Mapping) else ""
        lines.append(f"| {role} | {person_of(owner)} | {what} | {', '.join(owned) or '-'} |")
    lines.append("")
    for name, comp in model.components.items():
        lines += [
            f"## {comp.get('name', name)}",
            "",
            f"*Trust boundary:* {comp.get('boundary', '')}  ",
            f"*Data:* {comp.get('data', '')}",
            "",
            "| ID | STRIDE | Threat | Owner | Controls | OWASP | Tests |",
            "|---|---|---|---|---|---|---|",
        ]
        for t in comp.get("threats") or []:
            lines.append(
                f"| {t['id']} | {STRIDE[t['stride']]} | {t['title']} | {t['owner']} | "
                f"{', '.join(t.get('controls') or [])} | {', '.join(t.get('asi') or []) or '-'} | "
                f"{'<br>'.join(_ref(x) for x in t.get('tests') or [])} |"
            )
        skipped = comp.get("not_applicable") or {}
        if skipped:
            lines.append("")
            lines.append("Not applicable:")
            lines += [f"- **{STRIDE[k]}**: {v}" for k, v in skipped.items()]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_controls(model: Model) -> str:
    lines = [
        "# Control matrix",
        "",
        "Generated by `just threat-model` from `governance/controls.yaml`; do not edit by hand.",
        "Each control links to its evidence in this repository and to the framework requirements",
        "it supports. Framework references and their sources: [compliance sources]"
        "(compliance-sources.md). This mapping supports, and does not replace, legal and",
        "compliance review.",
        "",
        "| Control | Evidence | " + " | ".join(FRAMEWORKS.values()) + " |",
        "|---|---|" + "---|" * len(FRAMEWORKS),
    ]
    for cid, c in model.controls.items():
        ev = []
        for e in c.get("evidence") or []:
            if isinstance(e, str):
                ev.append(f"[{e}](../../{e})")
            elif "redteam" in e:
                ev.append(f"red team `{e['redteam']}`")
            else:
                ev.append(f"test `{e['test'].split('::')[-1]}`")
        fw = c.get("frameworks") or {}
        cells = [", ".join(fw.get(k) or []) or "-" for k in FRAMEWORKS]
        lines.append(f"| **{cid}** {c['title']} | {'<br>'.join(ev)} | " + " | ".join(cells) + " |")
    threats_by_control: dict[str, list[str]] = {}
    for t in model.threats():
        for c in t.get("controls") or []:
            threats_by_control.setdefault(str(c), []).append(str(t["id"]))
    lines += ["", "## Threats each control answers", ""]
    lines += [
        f"- **{cid}**: {', '.join(threats_by_control.get(cid, []))}" for cid in model.controls
    ]
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["FRAMEWORKS", "STRIDE", "Model", "render_controls", "render_threats"]
