"""Pytest plugin for the red-team suite (Step 35): every test that stands for an attack in the
catalogue becomes a result in the red-team report.

Scenario attacks are ``test_attack[<attack id>]``; linked tests are matched on their node id.
A passing test means the control held; a failing or erroring one (setup included: a policy
engine that isn't ready fails every attack) is a finding; a skipped one is ``not_run`` with the
skip reason. Scenario tests attach what the platform recorded with
``record_property("evidence", {...})``.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from agent_evals.redteam import Attack, Catalogue, LinkedTest, Report, Result

_PARAM = re.compile(r"::test_attack\[(?P<id>[^\]]+)\]$")


def _node(nodeid: str) -> str:
    return nodeid.replace("\\", "/")


class RedTeamPlugin:
    def __init__(
        self,
        catalogue: Catalogue,
        commit: str,
        date: str,
        policy_engine: str,
        tiers: tuple[str, ...] = (),
    ) -> None:
        self.catalogue = catalogue
        self.by_id = catalogue.by_id()
        self.by_node = {_node(t.node): t for t in catalogue.tests}
        self.commit, self.date, self.policy_engine, self.tiers = commit, date, policy_engine, tiers
        self.results: dict[str, Result] = {}
        self.report: Report | None = None

    def _entry(self, nodeid: str) -> LinkedTest | Attack | None:
        m = _PARAM.search(nodeid)
        if m:
            return self.by_id.get(m.group("id"))
        node = _node(nodeid)
        # a linked node without parameters stands for every parametrization of that test
        return self.by_node.get(node) or self.by_node.get(node.split("[", 1)[0])

    @staticmethod
    def _text(report: pytest.TestReport) -> str:
        crash = getattr(report.longrepr, "reprcrash", None)
        if crash is not None:
            return str(crash.message)
        if isinstance(report.longrepr, tuple):
            return str(report.longrepr[-1])
        return str(report.longrepr or "")[-500:]

    @pytest.hookimpl
    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        entry = self._entry(report.nodeid)
        if entry is None:
            return
        evidence: dict[str, Any] = {k: v for k, v in report.user_properties if k == "evidence"}
        evidence = evidence.get("evidence", {}) if evidence else {}
        if report.failed:
            outcome, detail = "finding", self._text(report)
        elif report.skipped:
            reason = self._text(report).removeprefix("Skipped: ")
            outcome, detail = "not_run", f"skipped: {reason}"
        elif report.when == "call":
            outcome, detail = "held", "control held"
        else:
            return
        known = self.results.get(entry.id)
        if known is not None and known.outcome == "finding":
            return  # a later pass (teardown, another parametrization) never hides a failure
        self.results[entry.id] = Result(
            id=entry.id,
            asi=entry.asi,
            title=entry.title,
            severity=entry.severity,
            outcome=outcome,  # type: ignore[arg-type]
            detail=detail,
            evidence=evidence or (known.evidence if known else {}),
        )

    @pytest.hookimpl
    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        for entry in self.catalogue.entries():
            if entry.id not in self.results:
                why = (
                    f"tier {entry.tier} not in this run"
                    if self.tiers and entry.tier not in self.tiers
                    else "not collected"
                )
                self.results[entry.id] = Result(
                    entry.id, entry.asi, entry.title, entry.severity, "not_run", why
                )
        self.report = Report(
            self.commit,
            self.date,
            self.policy_engine,
            sorted(self.results.values(), key=lambda r: (r.asi, r.id)),
            self.tiers,
        )


__all__ = ["RedTeamPlugin"]
