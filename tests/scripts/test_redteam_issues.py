"""Step 35: red-team findings tracked as GitHub issues, against a fake GitHub API."""

from __future__ import annotations

import json
from typing import Any

import httpx
from agent_evals.redteam import Report, Result

from scripts.redteam_issues import GitHub, apply, open_blocking, plan


class FakeGitHub:
    """Just enough of the issues API: list (paged), create, comment, update."""

    def __init__(self) -> None:
        self.issues: dict[int, dict[str, Any]] = {}
        self.comments: dict[int, list[str]] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        assert request.headers["Authorization"] == "Bearer t0ken"
        if method == "GET" and path.endswith("/issues"):
            state = request.url.params.get("state", "open")
            rows = [
                i
                for i in self.issues.values()
                if state == "all" or i["state"] == state
                if "redteam" in [lb["name"] for lb in i["labels"]]
            ]
            return httpx.Response(200, json=rows)
        if method == "POST" and path.endswith("/issues"):
            body = json.loads(request.content)
            number = len(self.issues) + 1
            self.issues[number] = {
                "number": number,
                "title": body["title"],
                "body": body["body"],
                "state": "open",
                "labels": [{"name": n} for n in body["labels"]],
            }
            return httpx.Response(201, json=self.issues[number])
        number = int(path.split("/issues/")[1].split("/")[0])
        if method == "POST" and path.endswith("/comments"):
            self.comments.setdefault(number, []).append(json.loads(request.content)["body"])
            return httpx.Response(201, json={})
        if method == "PATCH":
            self.issues[number].update(json.loads(request.content))
            return httpx.Response(200, json=self.issues[number])
        return httpx.Response(404)


def report(commit: str, outcomes: dict[str, str]) -> Report:
    rows = [
        Result(rid, "ASI02", f"attack {rid}", "critical", outcome, f"detail of {rid}")  # type: ignore[arg-type]
        for rid, outcome in outcomes.items()
    ]
    return Report(commit, f"2026-10-16T2{len(commit)}:00:00+00:00", "opa", rows, ("unit",))


def sync(fake: FakeGitHub, rep: Report) -> list[str]:
    gh = GitHub("owner/repo", "t0ken", transport=httpx.MockTransport(fake))
    actions = plan(rep, gh.issues())
    apply(rep, actions, gh)
    return [f"{a.kind}:{a.number or ''}" for a in actions]


def test_a_finding_opens_one_issue_and_a_repeat_comments_on_it() -> None:
    fake = FakeGitHub()
    assert sync(fake, report("c1", {"a": "finding", "b": "held"})) == ["open:"]
    issue = fake.issues[1]
    assert issue["title"] == "red team [critical] ASI02: attack a"
    assert {lb["name"] for lb in issue["labels"]} == {"redteam", "ASI02", "severity:critical"}
    assert "detail of a" in issue["body"]
    assert "blocks a release" in issue["body"]
    assert sync(fake, report("c2", {"a": "finding"})) == ["comment:1"]
    assert len(fake.issues) == 1
    assert "c2" in fake.comments[1][0]


def test_a_held_attack_closes_its_issue_and_a_regression_reopens_it() -> None:
    fake = FakeGitHub()
    sync(fake, report("c1", {"a": "finding"}))
    assert sync(fake, report("c2", {"a": "not_run"})) == []  # didn't run: nothing proven
    assert sync(fake, report("c3", {"a": "held"})) == ["close:1"]
    assert (fake.issues[1]["state"], fake.issues[1]["state_reason"]) == ("closed", "completed")
    assert sync(fake, report("c4", {"a": "finding"})) == ["reopen:1"]
    assert fake.issues[1]["state"] == "open"
    assert fake.comments[1][-1].startswith("Reopened.")


def test_open_blocking_issues_feed_the_release_gate() -> None:
    from agent_evals.redteam import gate

    fake = FakeGitHub()
    sync(fake, report("c1", {"a": "finding"}))
    gh = GitHub("owner/repo", "t0ken", transport=httpx.MockTransport(fake))
    issues = open_blocking(gh)
    clean = report("c2", {f"t{i}": "held" for i in range(1, 11)})
    for i, r in enumerate(clean.results, 1):
        r.asi = f"ASI{i:02d}"
    decision = gate(clean, issues)
    assert decision.reasons == ("open issue #1: red team [critical] ASI02: attack a",)
