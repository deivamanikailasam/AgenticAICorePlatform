"""agentctl on a small agent project (Step 39): an SDK tool granted by ref, run and evaluated
through the in-process run API, then promoted."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from agent_contracts.registry import registry
from agent_sdk import RunContext, SideEffect, Tool, tool
from agent_sdk.cli import main

SPEC = """\
spec_version: 1
name: greeter
version: 0.1.0
owner: team-a
risk_tier: low
autonomy: workflow
pattern: workflow
runtime: { adapter: langgraph, durability: memory }
budgets: { max_steps: 5, max_wall_seconds: 20 }
tools:
  - ref: local://team-a/shout
graph:
  nodes:
    - { id: greet, type: function, fn: hello }
    - { id: shout, type: tool, tool: shout, config: { args: { text: $greet.greeting } } }
  edges:
    - { from: greet, to: shout }
"""

TASKS = [
    {
        "app": "greeter",
        "id": "greeter-000",
        "input": {"name": "Ada"},
        "expected": {
            "status": "succeeded",
            "answer": {"contains": ["HELLO, ADA"]},
            "tools": {"expected": ["shout"]},
        },
    }
]


@tool(
    name="shout",
    ref="local://team-a/shout",
    description="Upper-cases text.",
    input_schema={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    },
)
async def shout(arguments: Mapping[str, Any], ctx: RunContext) -> str:
    return str(arguments["text"]).upper()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    (tmp_path / "agents" / "greeter").mkdir(parents=True)
    (tmp_path / "agents" / "greeter" / "agent.yaml").write_text(SPEC)
    (tmp_path / "evals" / "greeter").mkdir(parents=True)
    (tmp_path / "evals" / "greeter" / "tasks.jsonl").write_text(
        "".join(json.dumps(t) + "\n" for t in TASKS)
    )
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "promotion.yaml").write_text("canary_weight: 10\n")
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    monkeypatch.chdir(tmp_path / "agents")  # agentctl finds the root above
    registry.add("tool", "team-a.shout", shout)
    yield tmp_path
    del registry._classes[("tool", "team-a.shout")]


def test_the_decorator_makes_a_tool() -> None:
    assert issubclass(shout, Tool)
    t = shout()
    assert t.spec.ref == "local://team-a/shout"
    assert t.spec.side_effect is SideEffect.READ
    with pytest.raises(ValueError, match="local://"):
        tool(name="x", ref="mcp://x/y", description="", input_schema={})


def test_validate_run_eval_promote(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate"]) == 0
    assert main(["run", "greeter", "--input", '{"name": "Ada"}']) == 0
    out = capsys.readouterr().out
    run = json.loads(out[out.index("{") :])
    assert run["status"] == "succeeded"
    assert "HELLO, ADA" in json.dumps(run["output"])

    assert main(["eval", "greeter"]) == 0
    out = capsys.readouterr().out
    assert "greeter: 1/1 tasks succeeded (100.0%)" in out
    assert "provider none" in out
    assert list((project / "evals" / "results" / "greeter").glob("*-none.json"))

    assert main(["promote", "greeter", "--to", "staging"]) == 0
    assert (project / "deploy" / "specs" / "greeter" / "0.1.0" / "agent.yaml").is_file()
    assert "deploy(staging): greeter serve 0.1.0" in capsys.readouterr().out
    assert main(["deploy-config", "--check"]) == 0


def test_a_failing_task_fails_the_eval(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = dict(TASKS[0], expected={"answer": {"contains": ["GOODBYE"]}})
    (project / "evals" / "greeter" / "tasks.jsonl").write_text(json.dumps(bad) + "\n")
    assert main(["eval", "greeter"]) == 1
    assert "FAILED greeter-000" in capsys.readouterr().out


def test_an_ungranted_or_unknown_agent_is_a_usage_error(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["run", "nobody"]) == 2
    assert "known: greeter" in capsys.readouterr().err
    assert main(["promote", "greeter", "--to", "prod"]) == 2
    assert "promote it there first" in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("kustomize") is None, reason="needs kustomize")
def test_deploy_check_applies_the_platform_rules(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for env in ("dev", "staging", "prod"):
        overlay = project / "deploy" / "overlays" / env
        overlay.mkdir(parents=True)
        (overlay / "kustomization.yaml").write_text("resources: [app.yaml]\n")
        (overlay / "app.yaml").write_text(
            "apiVersion: v1\nkind: Service\nmetadata: {name: open}\n"
            "spec: {type: NodePort, ports: [{port: 80}]}\n"
        )
    assert main(["deploy-check"]) == 1
    err = capsys.readouterr().err
    assert "dev:" in err
    assert "NodePort" in err
