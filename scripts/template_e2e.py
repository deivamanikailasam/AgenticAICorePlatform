"""Make a project from the agent-project template and take it through its first 30 minutes
(Step 39): render it, resolve and install the platform, pass the project's own CI checks, run its
first agent, evaluate it and check its deployment. Every step is timed; the result is written to
``evals/results/template/template-e2e.json``.

template_e2e                       # the platform at this commit, from GitHub (it must be pushed)
template_e2e --ref SHA             # the platform at another commit
template_e2e --local               # the platform from this checkout (no push needed)
template_e2e --skip-deploy-check   # without kustomize (deploy-check renders remote bases)

The checks are the ones the generated ``.github/workflows/ci.yml`` runs.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evals" / "results" / "template"
LIMIT_SECONDS = 30 * 60  # the spec's "under 30 minutes"
ANSWERS = {"project_name": "Claims Desk", "team": "claims-team", "github_owner": "example-org"}
AGENT = "claims-desk-triage"


def sh(args: list[str], cwd: Path, env: dict[str, str]) -> str:
    proc = subprocess.run(  # noqa: S603
        args, cwd=cwd, env=env, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"{' '.join(args)} failed ({proc.returncode}):\n{proc.stdout[-4000:]}\n"
            f"{proc.stderr[-4000:]}"
        )
    return proc.stdout


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="template_e2e", description=__doc__.split("\n\n")[0])
    p.add_argument("--ref", help="platform commit for the project's SDK (default: HEAD)")
    p.add_argument("--local", action="store_true", help="install the platform from this checkout")
    p.add_argument("--skip-deploy-check", action="store_true")
    p.add_argument("--keep", help="make the project here and keep it")
    args = p.parse_args(argv)
    ref = args.ref or sh(["git", "rev-parse", "HEAD"], ROOT, dict(os.environ)).strip()
    # a fresh project: its own environment, its own lock (not the platform's frozen one)
    drop = ("VIRTUAL_ENV", "AGENT_PLATFORM_CONFIG", "UV_FROZEN", "UV_LOCKED")
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.setdefault("GIT_AUTHOR_NAME", "template-e2e")
    env.setdefault("GIT_AUTHOR_EMAIL", "template-e2e@example.invalid")
    env["GIT_COMMITTER_NAME"], env["GIT_COMMITTER_EMAIL"] = (
        env["GIT_AUTHOR_NAME"],
        env["GIT_AUTHOR_EMAIL"],
    )
    work = Path(args.keep) if args.keep else Path(tempfile.mkdtemp(prefix="template-e2e-"))
    project = work / "claims-desk"
    if project.exists():
        shutil.rmtree(project)
    steps: list[dict[str, Any]] = []
    started = time.monotonic()

    def step(name: str, cmd: list[str], cwd: Path = project) -> str:
        t = time.monotonic()
        try:
            out = sh(cmd, cwd, env)
        except RuntimeError as exc:
            steps.append({"step": name, "ok": False, "seconds": round(time.monotonic() - t, 1)})
            print(f"template-e2e: {name}: FAILED\n{exc}", file=sys.stderr)
            raise
        steps.append({"step": name, "ok": True, "seconds": round(time.monotonic() - t, 1)})
        print(f"template-e2e: {name}: ok ({steps[-1]['seconds']} s)")
        return out

    result: dict[str, Any] = {"platform_ref": ref, "local": args.local, "steps": steps}
    ok = False
    try:
        data = [f"--data={k}={v}" for k, v in ANSWERS.items()]
        step(
            "copier copy",
            [
                sys.executable, "-m", "copier", "copy", "--defaults", "--vcs-ref", "HEAD",
                *data, f"--data=platform_ref={ref}", str(ROOT), str(project),
            ],
            cwd=ROOT,
        )  # fmt: skip
        if args.local:  # the platform from this checkout, built as wheels like a git install
            env["UV_NO_CACHE"] = "1"  # rebuilt from the working tree, not a cached wheel
            text = (project / "pyproject.toml").read_text()
            start = text.index("agent-sdk = { git")
            end = text.index("\n", start)
            local = f'agent-sdk = {{ path = "{ROOT / "libs" / "agent-sdk"}", editable = false }}'
            (project / "pyproject.toml").write_text(text[:start] + local + text[end:])
        step("uv lock", ["uv", "lock"])
        step("uv sync", ["uv", "sync", "--locked"])
        step("git init and first commit", ["sh", "-c", "git init -q -b main && git add -A && git commit -qm 'feat: first agent from the platform template'"])  # fmt: skip
        step("ruff check", ["uv", "run", "ruff", "check", "."])
        step("ruff format --check", ["uv", "run", "ruff", "format", "--check", "."])
        step("pytest", ["uv", "run", "pytest", "-q"])
        step("agentctl validate --strict", ["uv", "run", "agentctl", "validate", "--strict"])
        run = step(
            "agentctl run (first agent)",
            [
                "uv", "run", "agentctl", "run", AGENT,
                "--input", json.dumps({"request": "I am locked out and cannot log in"}),
            ],
        )  # fmt: skip
        result["first_run"] = json.loads(run[run.index("{") :])
        out = step("agentctl eval", ["uv", "run", "agentctl", "eval", AGENT])
        result["eval"] = next(line for line in out.splitlines() if "tasks succeeded" in line)
        root_commit = sh(["git", "rev-list", "--max-parents=0", "HEAD"], project, env).strip()
        step(
            "agentctl deploy-config --check",
            ["uv", "run", "agentctl", "deploy-config", "--check", "--base", root_commit],
        )
        if not args.skip_deploy_check:
            step("agentctl deploy-check", ["uv", "run", "agentctl", "deploy-check"])
        ok = result["first_run"]["status"] == "succeeded"
    except RuntimeError:
        ok = False
    finally:
        total = round(time.monotonic() - started, 1)
        result |= {
            "date": datetime.now(UTC).isoformat(timespec="seconds"),
            "seconds_total": total,
            "limit_seconds": LIMIT_SECONDS,
            "passed": ok and total < LIMIT_SECONDS,
        }
        RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / "template-e2e.json").write_text(json.dumps(result, indent=1) + "\n")
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
    print(
        f"template-e2e: {'passed' if result['passed'] else 'FAILED'} in {result['seconds_total']} s"
        f" (limit {LIMIT_SECONDS} s); {RESULTS.relative_to(ROOT) / 'template-e2e.json'}"
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
