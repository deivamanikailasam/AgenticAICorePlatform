"""Red-team suite (Step 35): ``just redteam`` runs it, ``just redteam-check`` checks the register,
``just release-gate`` decides whether a release may go ahead.

    redteam run   [--tier unit --tier integration ...] [--report PATH]
    redteam check
    redteam gate  REPORT [REPORT ...]

``run`` executes the catalogue's linked tests and the scenario attacks of the chosen tiers as one
pytest session, writes the report (every attack's outcome, coverage per ASI category, findings)
and fails on any high or critical finding or on a category with no executed test. ``check``
fails if a category has no automated test or a linked test no longer exists. ``gate`` merges
reports (e.g. the integration and sandbox tiers) and applies the release rule.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pytest
from agent_evals.redteam import ALL_ASI, Catalogue, Report, gate, merge
from agent_evals.redteam_pytest import RedTeamPlugin
from agent_evals.results import git_commit, now

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / "evals" / "redteam"
SCENARIOS = "services/agent-api/tests/test_redteam_scenarios.py"
DEFAULT_REPORT = ROOT / "evals" / "results" / "redteam" / "report.json"
PYTEST = [
    "-o",
    "addopts=",
    "-q",
    "--strict-markers",
    "--import-mode=importlib",
    "-p",
    "no:cacheprovider",
]


def nodes(catalogue: Catalogue, tiers: tuple[str, ...]) -> list[str]:
    linked = [t.node for t in catalogue.tests if t.tier in tiers]
    scenarios = [f"{SCENARIOS}::test_attack[{a.id}]" for a in catalogue.attacks if a.tier in tiers]
    return linked + scenarios


def run(tiers: tuple[str, ...], report_path: Path) -> int:
    catalogue = Catalogue.load(CATALOGUE)
    selected = nodes(catalogue, tiers)
    engine = "opa" if any(a.tier in tiers for a in catalogue.attacks) else "n/a (no scenarios)"
    plugin = RedTeamPlugin(catalogue, git_commit(ROOT), now(), engine, tiers)
    pytest.main([*PYTEST, "--rootdir", str(ROOT), *selected], plugins=[plugin])
    report = plugin.report
    if report is None:
        print("redteam: pytest did not finish a session", file=sys.stderr)
        return 2
    report.save(report_path)
    print(report.summary())
    print(f"redteam: report written to {report_path.relative_to(ROOT)}")
    # a run of some tiers must execute every category those tiers have tests for
    decision = gate(report, require_all_categories=False)
    reasons = list(decision.reasons)
    expected = {asi for asi, ids in catalogue.coverage(tiers).items() if ids}
    reasons += [f"no executed test for {asi}" for asi in report.untested() if asi in expected]
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write("```\n" + report.summary() + "\n```\n")
    for reason in reasons:
        print(f"redteam: BLOCKING: {reason}", file=sys.stderr)
    return 1 if reasons else 0


def check() -> int:
    catalogue = Catalogue.load(CATALOGUE)
    problems = [f"{asi} has no automated test" for asi in catalogue.gaps()]
    unit_gaps = catalogue.gaps(("unit", "integration"))
    problems += [f"{asi} has no test that runs without a sandbox runtime" for asi in unit_gaps]
    linked = [t.node for t in catalogue.tests]
    collected: list[str] = []

    class Collect:
        @pytest.hookimpl
        def pytest_collection_finish(self, session: pytest.Session) -> None:
            collected.extend(item.nodeid for item in session.items)

    pytest.main(
        [*PYTEST, "--collect-only", "--rootdir", str(ROOT), *linked, SCENARIOS],
        plugins=[Collect()],
    )
    have = set(collected) | {n.split("[", 1)[0] for n in collected}
    problems += [f"linked test not found: {n}" for n in linked if n not in have]
    missing = [a.id for a in catalogue.attacks if f"{SCENARIOS}::test_attack[{a.id}]" not in have]
    problems += [f"scenario not collected: {i}" for i in missing]
    for asi, ids in catalogue.coverage().items():
        print(f"redteam-check: {asi} {catalogue.categories[asi].name}: {len(ids)} test(s)")  # type: ignore[index]
    total = len(catalogue.entries())
    print(
        f"redteam-check: {total} tests ({len(catalogue.attacks)} scenarios, "
        f"{len(catalogue.tests)} linked) across {len(ALL_ASI)} categories, "
        f"{len(problems)} problem(s)"
    )
    for p in problems:
        print(f"redteam-check: {p}", file=sys.stderr)
    return 1 if problems else 0


def gate_reports(paths: list[Path]) -> int:
    reports = [Report.load(p) for p in paths if p.exists()]
    missing = [str(p) for p in paths if not p.exists()]
    report = merge(reports) if reports else None
    decision = gate(report)
    if missing:
        print(f"release-gate: missing report(s): {', '.join(missing)}", file=sys.stderr)
    if report is not None:
        print(report.summary())
    if decision.release and not missing:
        print("release-gate: PASS (no open high or critical red-team finding)")
        return 0
    for r in decision.reasons:
        print(f"release-gate: BLOCKED: {r}", file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="redteam")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--tier", action="append", choices=["unit", "integration", "sandbox"])
    r.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    sub.add_parser("check")
    g = sub.add_parser("gate")
    g.add_argument("reports", nargs="+", type=Path)
    args = parser.parse_args(argv)
    if args.cmd == "run":
        return run(tuple(args.tier or ("unit", "integration")), args.report.resolve())
    if args.cmd == "check":
        return check()
    return gate_reports(args.reports)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
