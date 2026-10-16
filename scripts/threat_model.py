"""Threat model and control matrix (Step 37).

threat_model write   # docs/security/threat-model.md and control-matrix.md from governance/
threat_model check   # every rule holds, every test exists, the generated pages are current
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from agent_evals.redteam import Catalogue
from agent_governance.threats import Model, render_controls, render_threats

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "security"
PAGES = {"threat-model.md": render_threats, "control-matrix.md": render_controls}
PYTEST = ["-o", "addopts=", "-q", "--import-mode=importlib", "-p", "no:cacheprovider"]


def collected(nodes: list[str]) -> set[str]:
    found: list[str] = []

    class Collect:
        @pytest.hookimpl
        def pytest_collection_finish(self, session: pytest.Session) -> None:
            found.extend(item.nodeid for item in session.items)

    pytest.main([*PYTEST, "--collect-only", "--rootdir", str(ROOT), *nodes], plugins=[Collect()])
    return set(found) | {n.split("[", 1)[0] for n in found}


def main(argv: list[str]) -> int:
    model = Model.load(ROOT)
    if argv[:1] == ["write"]:
        OUT.mkdir(parents=True, exist_ok=True)
        for name, render in PAGES.items():
            (OUT / name).write_text(render(model))
            print(f"threat-model: wrote docs/security/{name}")
        return 0
    redteam = Catalogue.load(ROOT / "evals" / "redteam")
    problems = model.problems(ROOT, set(redteam.by_id()))
    nodes = sorted(r for r in model.test_refs() if not r.startswith("redteam:"))
    have = collected(nodes)
    problems += [f"test not found: {n}" for n in nodes if n not in have]
    for name, render in PAGES.items():
        page = OUT / name
        if not page.exists() or page.read_text() != render(model):
            problems.append(f"docs/security/{name} is stale: run just threat-model")
    print(
        f"threat-check: {len(model.threats())} threats, {len(model.components)} components, "
        f"{len(model.controls)} controls, {len(nodes)} tests, {len(problems)} problem(s)"
    )
    for p in problems:
        print(f"threat-check: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
