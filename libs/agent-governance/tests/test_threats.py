"""Step 37: the threat model's rules are enforced: every threat has an owner, a control and a
test; every STRIDE category is covered or explained; every control has evidence that exists."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from agent_governance.threats import Model, render_controls, render_threats

ROOT = Path(__file__).resolve().parents[3]


def model(tmp_path: Path, threats: dict[str, Any], controls: dict[str, Any]) -> Model:
    (tmp_path / "governance").mkdir()
    (tmp_path / "governance" / "threat-model.yaml").write_text(yaml.safe_dump(threats))
    (tmp_path / "governance" / "controls.yaml").write_text(yaml.safe_dump(controls))
    (tmp_path / "evidence.md").write_text("x")
    return Model.load(tmp_path)


GOOD_CONTROL = {
    "title": "c",
    "evidence": ["evidence.md", {"test": "t.py::test_x"}],
    "frameworks": {"app": ["APP 11"]},
}


def component(**extra: Any) -> dict[str, Any]:
    return {
        "name": "api",
        "threats": [
            {
                "id": "T1",
                "stride": "S",
                "title": "x",
                "owner": "security",
                "controls": ["C1"],
                "tests": ["t.py::test_x"],
            }
        ],
        "not_applicable": {k: "reason" for k in "TRIDE"},
    } | extra


def test_a_complete_model_has_no_problems(tmp_path: Path) -> None:
    m = model(
        tmp_path,
        {"owners": {"security": "@sam"}, "components": {"api": component()}},
        {"controls": {"C1": GOOD_CONTROL}},
    )
    assert m.problems(tmp_path, set()) == []
    assert m.test_refs() == {"t.py::test_x"}


def test_an_owner_role_can_name_a_person_and_a_responsibility(tmp_path: Path) -> None:
    role = {"person": "@sam", "responsibility": "identity and policy"}
    m = model(
        tmp_path,
        {"owners": {"security": role}, "components": {"api": component()}},
        {"controls": {"C1": GOOD_CONTROL}},
    )
    assert m.problems(tmp_path, set()) == []
    assert "| security | @sam | identity and policy | T1 |" in render_threats(m)
    nobody = {"owners": {"security": {"responsibility": "x"}}, "components": {"api": component()}}
    (other := tmp_path / "b").mkdir()
    m = model(other, nobody, {"controls": {"C1": GOOD_CONTROL}})
    assert "api/T1: owner role 'security' names no person or team" in m.problems(other, set())


def test_every_rule_is_enforced(tmp_path: Path) -> None:
    bad_threat = {
        "id": "T1",
        "stride": "X",
        "title": "x",
        "owner": "nobody",
        "controls": ["C9"],
        "tests": ["redteam:missing"],
    }
    comp = component(
        threats=[bad_threat, {"id": "T1", "stride": "S", "title": "y"}],
        not_applicable={"T": "r", "S": "r"},
    )
    controls = {"C1": {"title": "c", "evidence": ["nowhere.md"], "frameworks": {"made_up": ["x"]}}}
    m = model(
        tmp_path,
        {"owners": {"security": "sam"}, "components": {"api": comp}},
        {"controls": controls},
    )
    problems = set(m.problems(tmp_path, {"known"}))
    assert {
        "api/T1: stride must be one of S, T, R, I, D, E",
        "api/T1: owner role 'nobody' names no person or team",
        "api/T1: unknown control C9",
        "api/T1: unknown red-team test redteam:missing",
        "api/T1: duplicate threat id",
        "api/T1: no owner",
        "api/T1: no control",
        "api/T1: no test",
        "api: no threat and no reason for Repudiation (R)",
        "api: S has threats and is also marked not applicable",
        "control C1: no test among its evidence",
        "control C1: evidence nowhere.md does not exist",
        "control C1: unknown framework(s) made_up",
        "control C1: answers no threat",
    } <= problems


def test_the_repository_model_is_complete_and_renders() -> None:
    from agent_evals.redteam import Catalogue

    m = Model.load(ROOT)
    assert m.problems(ROOT, set(Catalogue.load(ROOT / "evals" / "redteam").by_id())) == []
    asi = {a for t in m.threats() for a in t.get("asi") or []}
    assert asi == {f"ASI{i:02d}" for i in range(1, 11)}  # the OWASP mapping covers all ten
    page = render_threats(m)
    assert "| T07 | Tampering |" in page
    assert "| **C19** Data lifecycle" in render_controls(m)
