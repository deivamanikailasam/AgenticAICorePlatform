"""ReleaseRepo on a repository other than the platform's (Step 39): an agent project made from
the template promotes its own agents with the same rules."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from agent_governance.release_repo import PromotionError, ReleaseRepo


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        check=True,
        capture_output=True,
    )


def _agent(root: Path, version: str) -> None:
    path = root / "agents" / "greeter" / "agent.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"name: greeter\nversion: {version}\n")


@pytest.fixture
def repo(tmp_path: Path) -> ReleaseRepo:
    _git(tmp_path, "init", "-q")
    _agent(tmp_path, "0.1.0")
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "promotion.yaml").write_text("canary_weight: 10\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return ReleaseRepo(tmp_path, promote_command="agentctl promote")


def _commit(repo: ReleaseRepo, message: str) -> None:
    assert repo.check("HEAD", None) == []
    _git(repo.root, "add", "-A")
    _git(repo.root, "commit", "-qm", message)


def test_a_project_walks_dev_staging_canary_prod(repo: ReleaseRepo) -> None:
    assert repo.promote("greeter", "staging").what == "serve 0.1.0 (new snapshot)"
    repo.write_config()
    _commit(repo, "staging")
    assert repo.promote("greeter", "prod").what == "first release of 0.1.0"
    _commit(repo, "prod")

    _agent(repo.root, "0.2.0")
    _commit(repo, "dev 0.2.0")
    repo.promote("greeter", "staging")
    _commit(repo, "staging 0.2.0")
    step = repo.promote("greeter", "prod", shadow=False)
    assert step.what == "0.2.0 as a 10% canary candidate"
    _commit(repo, "canary")
    with pytest.raises(PromotionError, match="not 'pass'"):
        repo.promote("greeter", "prod", full=True, verdict={"online_eval": {"decision": "hold"}})
    repo.promote("greeter", "prod", full=True, verdict={"online_eval": {"decision": "pass"}})
    _commit(repo, "full")
    prod = yaml.safe_load((repo.root / "deploy/releases/prod.yaml").read_text())
    assert prod["agents"]["greeter"] == {"stable": "0.2.0"}
    assert "`agentctl promote`" in (repo.root / "deploy/releases/prod.yaml").read_text()
    released = repo.released("prod")
    kustomization = yaml.safe_load(
        released[repo.root / "deploy/overlays/prod/released/kustomization.yaml"]
    )
    assert kustomization["configMapGenerator"][0]["files"] == [
        "greeter--0.2.0--agent.yaml=../../../specs/greeter/0.2.0/agent.yaml"
    ]


def test_a_project_cannot_skip_staging_or_edit_a_snapshot(repo: ReleaseRepo) -> None:
    with pytest.raises(PromotionError, match="promote it there first"):
        repo.promote("greeter", "prod")
    repo.promote("greeter", "staging")
    _commit(repo, "staging")
    (repo.root / "deploy/specs/greeter/0.1.0/agent.yaml").write_text("name: greeter\nversion: x\n")
    assert any("a released version is immutable" in p for p in repo.check("HEAD", None))


def _rev(root: Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def test_a_push_of_many_commits_is_judged_step_by_step(repo: ReleaseRepo) -> None:
    """A push carrying a whole release (staging, prod, then the next version to staging) breaks
    no rule step by step, though its two ends alone look like prod skipped staging's version.
    A bad step inside it is still caught, and named by its commit."""
    base = _rev(repo.root)
    repo.promote("greeter", "staging")
    _commit(repo, "staging")
    repo.promote("greeter", "prod")
    _commit(repo, "prod")
    _agent(repo.root, "0.2.0")
    _commit(repo, "dev 0.2.0")
    repo.promote("greeter", "staging")
    _commit(repo, "staging 0.2.0")
    assert repo.check(base, "HEAD"), "as one jump, the first prod release isn't staging's"
    assert repo.check_each(base) == []

    prod = repo.root / "deploy/releases/prod.yaml"
    prod.write_text(prod.read_text().replace("stable: 0.1.0", "stable: 0.2.0"))
    _git(repo.root, "commit", "-qam", "prod 0.2.0 without a canary")
    found = repo.check_each(base)
    assert len(found) == 1
    assert found[0].startswith(_rev(repo.root)[:7] + ": ")
    assert "canary" in found[0]
