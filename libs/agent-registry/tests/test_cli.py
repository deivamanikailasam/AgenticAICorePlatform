"""``just agents``: lists every agent with its status, and changes status."""

from pathlib import Path

import pytest
from agent_registry.__main__ import main

ROOT = Path(__file__).resolve().parents[3]


def test_lists_all_agents_with_status_and_approves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("AGENT_DB_DSN", raising=False)
    monkeypatch.setenv("AGENT_REGISTRY_FILE", str(tmp_path / "agents.db"))
    agents = str(ROOT / "agents")
    assert main(["--agents-dir", agents]) == 0
    out = capsys.readouterr().out
    assert "hello-workflow" in out
    assert "draft" in out
    assert "registry: local file" in out
    assert main(["--approve", "hello-workflow", "0.1.0", "--by", "carol", "--reason", "ok"]) == 0
    assert "hello-workflow@0.1.0: approved (by carol)" in capsys.readouterr().out
    assert main(["--agents-dir", agents]) == 0
    line = next(x for x in capsys.readouterr().out.splitlines() if x.startswith("hello-workflow "))
    assert "approved" in line
    with pytest.raises(ValueError, match="by"):
        main(["--retire", "hello-workflow", "0.1.0"])


def test_staging_refuses_a_local_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_DB_DSN", raising=False)
    monkeypatch.setenv("AGENT_ENV", "staging")
    with pytest.raises(SystemExit, match="shared registry"):
        main([])
