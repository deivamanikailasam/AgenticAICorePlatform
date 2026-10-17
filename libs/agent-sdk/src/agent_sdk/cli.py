"""``agentctl``: the command line of an agent project (Step 39).

    agentctl validate [PATH ...] [--strict]        check agent.yaml files (default: agents/)
    agentctl run AGENT --input JSON                one run, in process through the run API
    agentctl run AGENT --input JSON --api URL      one run on a deployed platform (AGENT_API_TOKEN)
    agentctl eval AGENT [--provider P]             the golden tasks in evals/AGENT/tasks.jsonl
    agentctl promote AGENT --to staging|prod ...   one promotion step, as a reviewable change
    agentctl deploy-config [--check [--base REF]]  regenerate (or check) the deployment config
    agentctl deploy-check                          render each overlay; the platform's deploy rules

``--provider``: ``none`` (no model calls; the default for agents without model nodes),
``stand-in`` (the stand-in gateway's canned answers: mechanics only) or ``gateway`` (real models
behind Project 0's LiteLLM). Exit codes: 0 success, 1 a failed run, eval or check, 2 a usage or
spec error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml
from agent_api.eval_runner import APPROVER, evaluate, session, summary
from agent_evals.results import save
from agent_governance.deploy_policy import ENVIRONMENTS
from agent_governance.deploy_policy import problems as deploy_problems
from agent_governance.release_repo import PromotionError
from agent_spec import SpecError
from agent_spec.cli import validate as validate_specs

from agent_sdk.project import PROVIDERS, Project, ProjectError, ProjectHarness, provider

TERMINAL = {"succeeded", "failed", "cancelled", "stopped", "interrupted"}


def _output(events: list[dict[str, Any]]) -> Any:
    return next(
        (
            e.get("result", {}).get("output")
            for e in reversed(events)
            if e.get("type") == "RUN_FINISHED"
        ),
        None,
    )


def _error(events: list[dict[str, Any]]) -> str | None:
    return next((str(e.get("message")) for e in events if e.get("type") == "RUN_ERROR"), None)


def _report(summary_: dict[str, Any], events: list[dict[str, Any]]) -> int:
    doc = {
        "run_id": summary_.get("run_id"),
        "status": summary_.get("status"),
        "output": _output(events),
        "error": _error(events),
        "interrupts": summary_.get("interrupts") or [],
    }
    print(json.dumps(doc, indent=2, default=str))
    return 0 if doc["status"] in ("succeeded", "interrupted") else 1


async def _run_local(
    project: Project, agent: str, inputs: dict[str, Any], kind: str, approve: bool
) -> int:
    harness = ProjectHarness(project, agent)
    async with session(harness, provider(kind, harness.spec())) as s:
        run, resp = await s.start(inputs)
        if run is None:
            print(f"agentctl: the run API refused the run: HTTP {resp.status_code} {resp.text}")
            return 1
        if approve and run.status == "interrupted":
            decided = await s.client.post(
                f"/v1/runs/{run.run_id}/approvals",
                json={"decision": "approve"},
                headers={"Authorization": f"Bearer {APPROVER}"},
            )
            if decided.status_code == 202:
                await s.manager.wait_idle(run)
        return _report(run.summary(), list(run.events))


async def _run_remote(api: str, agent: str, inputs: dict[str, Any], wait_seconds: float) -> int:
    token = os.environ.get("AGENT_API_TOKEN")
    if not token:
        raise ProjectError("--api needs a bearer token in AGENT_API_TOKEN")
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=api.rstrip("/"), headers=headers, timeout=30) as c:
        resp = await c.post(f"/v1/agents/{agent}/runs", json={"input": inputs})
        if resp.status_code not in (200, 201):
            print(f"agentctl: the run API refused the run: HTTP {resp.status_code} {resp.text}")
            return 1
        state = resp.json()
        deadline = time.monotonic() + wait_seconds
        while state.get("status") not in TERMINAL and time.monotonic() < deadline:
            await asyncio.sleep(1)
            state = (await c.get(f"/v1/runs/{state['run_id']}")).json()
        events: list[dict[str, Any]] = []
        if state.get("status") in TERMINAL - {"interrupted"}:
            async with c.stream("GET", f"/v1/runs/{state['run_id']}/events") as stream:
                async for line in stream.aiter_lines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))
        return _report(state, events)


async def _eval(project: Project, agent: str, tasks: Path, kind: str, min_success: float) -> int:
    harness = ProjectHarness(project, agent)
    report = await evaluate(harness, tasks, provider(kind, harness.spec()), root=project.root)
    path = save(report, project.root / "evals" / "results")
    print(summary(report))
    print(f"agentctl: report {path.relative_to(project.root)}")
    rate = report.aggregates()["success_rate"]
    if rate < min_success:
        for r in report.results:
            if not r.success:
                failed = [f"{s.metric}: {s.detail}" for s in r.scores if not s.passed]
                print(f"  FAILED {r.task}: {'; '.join(failed) or r.error or r.status}")
        return 1
    return 0


def _promote(project: Project, args: argparse.Namespace) -> int:
    repo = project.releases()
    verdict = repo.online_verdict(args.agent, args.api, args.verdict_file) if args.full else None
    step = repo.promote(
        args.agent, args.to, shadow=args.shadow, full=args.full, abort=args.abort, verdict=verdict
    )
    repo.write_config()
    found = repo.check("HEAD", None)
    for line in found:
        print(f"agentctl: {line}", file=sys.stderr)
    if found:
        return 1
    print(f"agentctl: {step.message(args.agent)}")
    if args.commit:
        branch = repo.commit(args.agent, step)
        print(f"agentctl: on branch {branch}; push it and open a pull request")
    return 0


def _deploy_check(project: Project) -> int:
    """``kustomize build`` each overlay (remote bases need network) and apply the platform's
    deployment rules (digests, Pod Security, no secrets, network policy, replicas ...)."""
    if shutil.which("kustomize") is None:
        raise ProjectError("deploy-check needs kustomize on PATH")
    found: list[str] = []
    for env in ENVIRONMENTS:
        overlay = project.root / "deploy" / "overlays" / env
        proc = subprocess.run(  # noqa: S603
            # as the platform's deploy-check and Argo CD build it (deploy/argocd/install)
            ["kustomize", "build", "--load-restrictor", "LoadRestrictionsNone", str(overlay)],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            found.append(f"{env}: kustomize build failed: {proc.stderr.strip()}")
            continue
        objects = [o for o in yaml.safe_load_all(proc.stdout) if o]
        problems = deploy_problems(env, objects)
        found += [f"{env}: {p}" for p in problems]
        print(f"agentctl: {env}: {len(objects)} objects, {len(problems)} problem(s)")
    for line in found:
        print(f"agentctl: {line}", file=sys.stderr)
    return 1 if found else 0


def _deploy_config(project: Project, check: bool, base: str) -> int:
    repo = project.releases()
    if not check:
        for path in repo.write_config():
            print(f"agentctl: wrote {path.relative_to(project.root)}")
        return 0
    total, stale = repo.stale_config()
    for path in stale:
        print(f"agentctl: {path.relative_to(project.root)} is stale: run agentctl deploy-config")
    problems = repo.check(base, None)
    for line in problems:
        print(f"agentctl: {line}", file=sys.stderr)
    print(f"agentctl: {total} generated files, {len(stale)} stale, {len(problems)} problem(s)")
    return 1 if stale or problems else 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentctl", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="check agent.yaml files (default: agents/)")
    v.add_argument("paths", nargs="*")
    v.add_argument("--strict", action="store_true", help="treat warnings as errors")
    r = sub.add_parser("run", help="run an agent once")
    r.add_argument("agent")
    r.add_argument("--input", default="{}", help="the run's input, a JSON object")
    r.add_argument("--provider", default="auto", choices=["auto", *PROVIDERS])
    r.add_argument("--approve", action="store_true", help="local: approve a pending approval")
    r.add_argument("--api", help="a deployed run API instead of an in-process one")
    r.add_argument("--timeout", type=float, default=300.0, help="--api: seconds to wait")
    e = sub.add_parser("eval", help="run an agent's golden tasks")
    e.add_argument("agent")
    e.add_argument("--tasks", help="default: evals/AGENT/tasks.jsonl")
    e.add_argument("--provider", default="auto", choices=["auto", *PROVIDERS])
    e.add_argument("--min-success", type=float, default=1.0, help="fail below this rate")
    pr = sub.add_parser("promote", help="one promotion step (staging, prod canary, full, abort)")
    pr.add_argument("agent")
    pr.add_argument("--to", choices=["staging", "prod"])
    pr.add_argument("--shadow", action="store_true", help="prod candidate in shadow mode")
    pr.add_argument("--full", action="store_true", help="the prod canary becomes stable")
    pr.add_argument("--abort", action="store_true", help="remove prod's candidate")
    pr.add_argument("--api", help="prod run API, for the online-eval verdict (with --full)")
    pr.add_argument("--verdict-file", help="a saved GET /v1/admin/releases response")
    pr.add_argument("--commit", action="store_true", help="commit on a promote/ branch")
    d = sub.add_parser("deploy-config", help="regenerate the deployment config")
    d.add_argument("--check", action="store_true", help="fail if stale or a rule is broken")
    d.add_argument("--base", default="HEAD", help="--check: promotion rules from REF (CI: base)")
    sub.add_parser("deploy-check", help="render each overlay and apply the deployment rules")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        project = Project.find()
        project.configure()
        if args.cmd == "validate":
            paths = args.paths or [str(project.root / "agents")]
            return validate_specs(paths, args.strict)
        if args.cmd == "run":
            inputs = json.loads(args.input)
            if args.api:
                return asyncio.run(_run_remote(args.api, args.agent, inputs, args.timeout))
            return asyncio.run(_run_local(project, args.agent, inputs, args.provider, args.approve))
        if args.cmd == "eval":
            tasks = Path(args.tasks) if args.tasks else project.tasks(args.agent)
            if not tasks.is_file():
                raise ProjectError(f"no golden tasks at {tasks}")
            return asyncio.run(_eval(project, args.agent, tasks, args.provider, args.min_success))
        if args.cmd == "promote":
            if not (args.to or args.abort):
                raise ProjectError("give --to staging|prod or --abort")
            return _promote(project, args)
        if args.cmd == "deploy-check":
            return _deploy_check(project)
        return _deploy_config(project, args.check, args.base)
    except (ProjectError, PromotionError, SpecError, json.JSONDecodeError) as exc:
        print(f"agentctl: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
