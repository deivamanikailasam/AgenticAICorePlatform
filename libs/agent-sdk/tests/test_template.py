"""The agent-project template (Step 39): what a new project gets, rendered with copier."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
copier = pytest.importorskip("copier")


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    dest = tmp_path_factory.mktemp("render") / "claims-desk"
    copier.run_copy(
        str(ROOT),
        str(dest),
        data={"project_name": "Claims Desk"},
        defaults=True,
        vcs_ref="HEAD",
        quiet=True,
    )
    return dest


def test_the_template_data_is_current() -> None:
    from scripts.template_data import rendered

    stale = [p for p, text in rendered().items() if not p.exists() or p.read_text() != text]
    assert stale == []


def test_a_project_pins_the_platform_its_environments_run(project: Path) -> None:
    pyproject = tomllib.loads((project / "pyproject.toml").read_text())
    source = pyproject["tool"]["uv"]["sources"]["agent-sdk"]
    data = yaml.safe_load((ROOT / "templates/agent-project/platform.yml").read_text())
    assert source["rev"] == data["platform_ref"]["default"]
    dev = (ROOT / "deploy/overlays/dev/kustomization.yaml").read_text()
    assert f"# images from commit {source['rev']}" in dev
    # the third-party versions the platform tested, not whatever is newest
    constraints = pyproject["tool"]["uv"]["constraint-dependencies"]
    assert "copier==9.18.2" in constraints


def test_a_project_runs_under_its_own_names_with_the_platform_s_rules(project: Path) -> None:
    for env in ("dev", "staging", "prod"):
        overlay = (project / f"deploy/overlays/{env}/kustomization.yaml").read_text()
        props = (project / f"deploy/overlays/{env}/env.properties").read_text()
        assert f"namespace: claims-desk-{env}" in overlay
        # the platform's own environments are never named (its ConfigMap name is the base's)
        assert not any(f"agent-platform-{e}" in overlay + props for e in ("dev", "staging", "prod"))
        assert f"AGENT_BAO_K8S_ROLE=claims-desk-{env}" in props
        assert "@sha256:" in overlay or "digest:" in overlay
    agent = yaml.safe_load((project / "agents/claims-desk-triage/agent.yaml").read_text())
    assert agent["tools"] == [{"ref": "local://claims-desk/lookup_policy"}]
    ci = (project / ".github/workflows/ci.yml").read_text()
    assert "${{ github.ref }}" in ci  # GitHub's expressions pass through the template untouched
    rendered = [p for p in project.rglob("*") if p.is_file() and ".git" not in p.parts]
    assert not [p for p in rendered if "{{ " in p.read_text().replace("${{ ", "")]
