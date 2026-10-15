"""Step 32: the CI gate end to end, on a real git history and a mocked GitHub reviews API: a
high-risk spec change fails until the named approver approves the head commit, and the approvers
come from the base branch, so a pull request can't name its own."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest
import respx

from scripts import governance_gate

APPROVERS = (
    'default: ["@owner"]\ntiers: { high: ["@risk-officer"] }\ngovernance_paths: [/governance/]\n'
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "governance").mkdir()
    (tmp_path / "governance" / "approvers.yaml").write_text(APPROVERS)
    (tmp_path / "agents" / "pay").mkdir(parents=True)
    (tmp_path / "agents" / "pay" / "agent.yaml").write_text("name: pay\nrisk_tier: high\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "base")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / "event.json"))
    return tmp_path


def run(repo: Path, base: str, head: str, reviews: list[dict], author: str = "dev") -> int:
    (repo / "event.json").write_text(
        json.dumps(
            {
                "pull_request": {
                    "number": 7,
                    "base": {"sha": base},
                    "head": {"sha": head},
                    "user": {"login": author},
                }
            }
        )
    )
    with respx.mock:
        respx.get("https://api.github.com/repos/o/r/pulls/7/reviews?per_page=100").mock(
            return_value=httpx.Response(200, json=reviews)
        )
        return governance_gate.main()


def review(user: str, commit: str, state: str = "APPROVED") -> dict:
    return {"user": {"login": user}, "state": state, "commit_id": commit}


def test_a_high_risk_change_waits_for_the_named_approver(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    (repo / "agents" / "pay" / "agent.yaml").write_text("name: pay\nrisk_tier: high\nversion: 2\n")
    git(repo, "commit", "-qam", "change")
    head = git(repo, "rev-parse", "HEAD")
    assert run(repo, base, head, []) == 1
    out = capsys.readouterr().out
    assert "agents/pay/agent.yaml is high-risk" in out
    assert "@risk-officer" in out
    assert run(repo, base, head, [review("someone", head)]) == 1
    assert run(repo, base, head, [review("risk-officer", base)]) == 1  # an older commit
    assert run(repo, base, head, [review("risk-officer", head)]) == 0
    assert "approved by: risk-officer" in capsys.readouterr().out


def test_a_pull_request_cannot_name_its_own_approver(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    (repo / "governance" / "approvers.yaml").write_text(APPROVERS.replace("risk-officer", "dev"))
    (repo / "agents" / "pay" / "agent.yaml").write_text("name: pay\nrisk_tier: high\nversion: 2\n")
    git(repo, "commit", "-qam", "make myself the approver")
    head = git(repo, "rev-parse", "HEAD")
    # the base branch's approvers apply, and the governance file change itself needs them
    assert run(repo, base, head, [review("helper-dev", head)]) == 1
    assert run(repo, base, head, [review("risk-officer", head)]) == 0


def test_a_low_risk_change_passes_without_review(repo: Path) -> None:
    (repo / "agents" / "notes").mkdir()
    (repo / "agents" / "notes" / "agent.yaml").write_text("name: notes\nrisk_tier: low\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base2")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "agents" / "notes" / "agent.yaml").write_text(
        "name: notes\nrisk_tier: low\nversion: 2\n"
    )
    git(repo, "commit", "-qam", "change")
    assert run(repo, base, git(repo, "rev-parse", "HEAD"), []) == 0
