from pathlib import Path

import pytest
from agent_spec import SpecError, load_spec, load_spec_text
from agent_spec.cli import main as cli
from agent_spec.loader import parse
from agent_spec.model import NodeType, Pattern

ROOT = Path(__file__).resolve().parents[3]
FIX = Path(__file__).parent / "fixtures"


def test_example_specs_load() -> None:
    hello = load_spec(ROOT / "agents/hello-workflow/agent.yaml")
    assert hello.graph is not None
    assert hello.graph.nodes[0].type is NodeType.FUNCTION
    inv = load_spec(ROOT / "agents/invoice-triage/agent.yaml")
    assert inv.pattern is Pattern.HYBRID
    assert [t.name for t in inv.tools] == ["get_invoice", "post_payment", "search"]
    pay = inv.tool("post_payment")
    assert pay is not None
    assert pay.approval is not None
    assert pay.approval.approvers == "group:finance-approvers"
    assert inv.graph is not None
    assert next((e.source, e.target, e.when) for e in inv.graph.edges) == (
        "validate",
        "resolve_exceptions",
        "on_error",
    )


def test_v0_layout_is_migrated() -> None:
    spec = load_spec(FIX / "v0-draft-layout.yaml")
    assert spec.spec_version == 1
    assert spec.graph is not None
    assert [e.target for e in spec.graph.edges] == ["tick"]
    assert spec.tools[0].ref == "local://clock"


def test_future_spec_version_rejected() -> None:
    with pytest.raises(SpecError, match="newer than this platform supports"):
        load_spec_text("spec_version: 99\nname: x\n")


def test_all_schema_problems_reported_with_lines() -> None:
    _, issues = parse((FIX / "invalid/many-problems.yaml").read_text())
    text = "\n".join(i.render("f.yaml") for i in issues)
    for needle in (
        "name: String should match pattern",
        "version: String should match pattern",
        "risk_tier: Input should be 'low', 'medium' or 'high'",
        "budgets.max_steps: Input should be greater than or equal to 1",
        "colour: unknown field",
        "a function node needs: fn",
    ):
        assert needle in text, text
    assert all(i.line for i in issues), "every issue carries a line number"
    lines = {i.path: i.line for i in issues}
    assert lines["colour"] == 9
    assert lines["budgets.max_steps"] == 8


def test_semantic_problems() -> None:
    _, issues = parse((FIX / "invalid/semantic.yaml").read_text())
    msgs = "\n".join(f"{i.path}: {i.message}" for i in issues if i.severity == "error")
    for needle in (
        "budgets.max_tokens: required when the spec calls models",
        "budgets.max_cost_aud: required when the spec calls models",
        "tool 'send_money' is not declared",
        "model role 'genius' is not defined",
        "'to' node 'ghost' does not exist",
        "node 'orphan' can never run",
        "cycle b -> c -> b has no loop bound",
        "unknown guardrail 'mind_reading'",
        "no checkpointer named 'cassandra'",
        "unknown slot 'teleporter'",
        "high-risk specs must name an eval suite",
        "description: secret references are only allowed under tools[].config",
    ):
        assert needle in msgs, msgs
    assert "tools[0].config.token" not in msgs, "secrets are allowed in tool config"


def test_env_interpolation_and_missing_variables() -> None:
    base = (ROOT / "agents/hello-workflow/agent.yaml").read_text()
    text = base.replace("owner: agent-platform", "owner: ${TEAM:-fallback}").replace(
        "description: Greets", "description: ${GREETING} Greets"
    )
    spec = load_spec_text(text, env={"GREETING": "Hi."})
    assert spec.owner == "fallback"
    assert spec.description.startswith("Hi. Greets")
    with pytest.raises(SpecError, match="GREETING is not set"):
        load_spec_text(text, env={})


def test_run_value_references_are_left_for_the_executors() -> None:
    base = (ROOT / "agents/hello-workflow/agent.yaml").read_text()
    text = base.replace("description: Greets", "description: ${validate.total} ${GREETING} Greets")
    spec = load_spec_text(text, env={"GREETING": "Hi."})
    assert spec.description.startswith("${validate.total} Hi. Greets")
    with pytest.raises(SpecError, match="cannot understand"):
        load_spec_text(base.replace("description: Greets", "description: ${Bad-Name}"), env={})


def test_cycle_with_loop_bound_is_allowed() -> None:
    text = """
spec_version: 1
name: loop-ok
version: 1.0.0
owner: t
risk_tier: low
autonomy: hybrid
pattern: hybrid
budgets: {max_steps: 9, max_wall_seconds: 9, max_tokens: 100, max_cost_aud: 0.1}
graph:
  entry: draft
  nodes:
    - {id: draft, type: llm}
    - {id: review, type: router, routes: [ok, again], max_iterations: 3}
    - {id: done, type: function, fn: identity}
  edges: ["draft -> review", "review -> draft: again", "review -> done: ok"]
"""
    spec = load_spec_text(text)
    assert spec.graph is not None


def test_router_edge_must_use_known_route() -> None:
    text = """
spec_version: 1
name: router-bad
version: 1.0.0
owner: t
risk_tier: low
autonomy: hybrid
pattern: hybrid
budgets: {max_steps: 9, max_wall_seconds: 9, max_tokens: 100, max_cost_aud: 0.1}
graph:
  nodes:
    - {id: triage, type: router, routes: [billing, tech]}
    - {id: b, type: function, fn: identity}
  edges: ["triage -> b: sales"]
"""
    with pytest.raises(SpecError, match="router 'triage' has no route 'sales'"):
        load_spec_text(text)


def test_bad_yaml_is_reported_with_line() -> None:
    _, issues = parse("name: [unclosed\nversion: 1")
    assert issues[0].message.startswith("not valid YAML")
    assert issues[0].line is not None


def test_cli_validate(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli(["validate", str(ROOT / "agents")]) == 0
    assert "2 file(s), 0 failed" in capsys.readouterr().out
    assert cli(["validate", str(FIX / "invalid")]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "error:" in out


def test_compensation_steps_take_no_edges() -> None:
    base = (ROOT / "agents/hello-workflow/agent.yaml").read_text()
    text = base.replace(
        "    - { id: greet, type: function, fn: hello }",
        "    - { id: greet, type: function, fn: hello, compensation: undo }\n"
        "    - { id: undo, type: function, fn: hello }\n"
        '  edges: ["greet -> undo"]',
    )
    with pytest.raises(SpecError, match="compensation step and cannot have edges"):
        load_spec_text(text)
