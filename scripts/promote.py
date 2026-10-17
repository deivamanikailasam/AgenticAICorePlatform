"""Promote an agent version, as a reviewable change (Step 38).

promote AGENT --to staging              # snapshot agents/AGENT at its version; staging serves it
promote AGENT --to prod [--shadow]      # staging's version becomes prod's 10% canary (or shadow);
                                        # an agent's first prod release goes in as stable
promote AGENT --to prod --full --api URL
                                        # the canary becomes prod's stable version, only if its
                                        # online evaluation passed (GET /v1/admin/releases,
                                        # token in AGENT_API_TOKEN)
promote AGENT --abort                   # drop prod's candidate (after a rollback, or by choice)

Each command edits deploy/specs and deploy/releases, regenerates the deployment config, checks
the promotion rules against the last commit, and with --commit puts it on a branch
`promote/<agent>-<version>-<env>` for a pull request. Merging the pull request (with its
approvals and checks) is the promotion: Argo CD then syncs the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

import yaml
from agent_governance.promotion import Policy, problems

from scripts.promotion_check import state_at

ROOT = Path(__file__).resolve().parents[1]
RELEASES = ROOT / "deploy" / "releases"
SPECS = ROOT / "deploy" / "specs"


def load(env: str) -> dict[str, Any]:
    path = RELEASES / f"{env}.yaml"
    return dict(yaml.safe_load(path.read_text()) or {}) if path.exists() else {}


def save(env: str, data: dict[str, Any]) -> None:
    RELEASES.mkdir(parents=True, exist_ok=True)
    head = f"# What serves in {env} (Step 38). Changed only by `just promote` in a pull request.\n"
    (RELEASES / f"{env}.yaml").write_text(head + yaml.safe_dump(data, sort_keys=True))


def dev_version(agent: str) -> str:
    spec = yaml.safe_load((ROOT / "agents" / agent / "agent.yaml").read_text())
    return str(spec["version"])


def snapshot(agent: str, version: str) -> bool:
    """Copy agents/AGENT into deploy/specs/AGENT/VERSION; False if it is already there."""
    target = SPECS / agent / version
    if target.exists():
        return False
    source = ROOT / "agents" / agent
    target.mkdir(parents=True)
    shutil.copy2(source / "agent.yaml", target / "agent.yaml")
    if (source / "workers").is_dir():
        shutil.copytree(source / "workers", target / "workers")
    return True


def online_verdict(api: str, agent: str, verdict_file: str | None) -> dict[str, Any]:
    if verdict_file:
        report = json.loads(Path(verdict_file).read_text())
    else:
        req = urllib.request.Request(  # noqa: S310 - the operator's own API URL
            f"{api.rstrip('/')}/v1/admin/releases",
            headers={"Authorization": f"Bearer {os.environ.get('AGENT_API_TOKEN', '')}"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            report = json.load(resp)
    entry = next((r for r in report["releases"] if r["agent"] == agent), None)
    if entry is None:
        raise SystemExit(f"promote: the API does not serve {agent}")
    return dict(entry)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="promote", description=__doc__.split("\n\n")[0])
    p.add_argument("agent")
    p.add_argument("--to", choices=["staging", "prod"])
    p.add_argument("--shadow", action="store_true", help="prod candidate in shadow mode")
    p.add_argument("--full", action="store_true", help="the prod canary becomes stable")
    p.add_argument("--abort", action="store_true", help="remove prod's candidate")
    p.add_argument("--api", help="prod run API, for the online-eval verdict (with --full)")
    p.add_argument("--verdict-file", help="a saved GET /v1/admin/releases response")
    p.add_argument("--commit", action="store_true", help="commit on a promote/ branch")
    args = p.parse_args(argv)
    agent = args.agent
    policy = Policy.of(yaml.safe_load((ROOT / "deploy" / "promotion.yaml").read_text()))
    if args.abort:
        prod = load("prod")
        entry = (prod.get("agents") or {}).get(agent) or {}
        if not entry.pop("candidate", None):
            raise SystemExit(f"promote: prod has no candidate for {agent}")
        save("prod", prod)
        env, version, what = "prod", entry["stable"], "abort the candidate"
    elif args.to == "staging":
        version = dev_version(agent)
        made = snapshot(agent, version)
        staging = load("staging")
        staging.setdefault("agents", {})[agent] = {"stable": version}
        save("staging", staging)
        env, what = "staging", f"serve {version}" + (" (new snapshot)" if made else "")
    elif args.to == "prod":
        staging_version = ((load("staging").get("agents") or {}).get(agent) or {}).get("stable")
        if not staging_version:
            raise SystemExit(f"promote: staging does not serve {agent}; promote it there first")
        prod = load("prod")
        agents = prod.setdefault("agents", {})
        entry = agents.get(agent)
        if args.full:
            cand = (entry or {}).get("candidate")
            if not cand:
                raise SystemExit(f"promote: prod has no candidate for {agent}")
            if not (args.api or args.verdict_file):
                raise SystemExit(
                    "promote: --full needs --api (or --verdict-file) for the online eval"
                )
            verdict = online_verdict(args.api or "", agent, args.verdict_file)
            online = verdict.get("online_eval") or {}
            if (verdict.get("candidate") or {}).get("rolled_back"):
                raise SystemExit(
                    f"promote: {cand['version']} was rolled back; it cannot go to prod"
                )
            if online.get("decision") != "pass":
                raise SystemExit(
                    f"promote: online evaluation of {cand['version']} is"
                    f" {online.get('decision')!r}, not 'pass': {online}"
                )
            version = str(cand["version"])
            agents[agent] = {k: v for k, v in entry.items() if k != "candidate"} | {
                "stable": version
            }
            what = f"full rollout of {version} (online eval passed: {online})"
        elif entry is None:
            version = staging_version
            agents[agent] = {"stable": version}
            what = f"first release of {version}"
        else:
            version = staging_version
            mode = "shadow" if args.shadow else "canary"
            entry["candidate"] = {"version": version, "mode": mode, "weight": policy.canary_weight}
            what = f"{version} as a {policy.canary_weight}% {mode} candidate"
        save("prod", prod)
        env = "prod"
    else:
        p.error("give --to staging|prod or --abort")
    subprocess.run([sys.executable, "-m", "scripts.deploy_config", "write"], cwd=ROOT, check=True)
    found = problems(state_at("HEAD"), state_at(None), policy)
    for line in found:
        print(f"promote: {line}", file=sys.stderr)
    if found:
        return 1
    message = f"deploy({env}): {agent} {what}"
    print(f"promote: {message}")
    if args.commit:
        branch = f"promote/{agent}-{version}-{env}"
        git = ["git", "-C", str(ROOT)]
        subprocess.run([*git, "switch", "-c", branch], check=True)  # noqa: S603
        subprocess.run([*git, "add", "deploy"], check=True)  # noqa: S603
        subprocess.run([*git, "commit", "-m", message], check=True)  # noqa: S603
        print(f"promote: on branch {branch}; push it and open a pull request")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
