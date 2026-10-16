"""Run the attack libraries against the run API and import their results (Step 35).

    redteam_libraries promptfoo [--generate] [--report PATH]
    redteam_libraries garak     [--report PATH]

Both attack a running agent-api (``AGENT_REDTEAM_*``, see evals/redteam/libraries/target.py).

* promptfoo 0.123.1 runs through ``npx`` (or ``PROMPTFOO_CMD``) with remote generation, sharing,
  telemetry and update checks switched off. Default: the fixed attack library. ``--generate``:
  attacks for all ten OWASP agentic categories generated and graded by the local model through
  the gateway (VERIFY-ON-MAC).
* garak 0.17.0 runs from its own environment (``GARAK_PYTHON``, default
  ``.tmp/garak/bin/python``; ``just redteam-garak-install`` creates it), since it brings torch.

Each writes a red-team report (source ``promptfoo`` / ``garak``) that ``just release-gate``
merges with the suite's; a high or critical finding fails the command.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from agent_evals.redteam import Report, gate
from agent_evals.redteam_libraries import from_garak, from_promptfoo, garak_probes
from agent_evals.results import git_commit, now

ROOT = Path(__file__).resolve().parents[1]
LIBS = ROOT / "evals" / "redteam" / "libraries"
OUT = ROOT / "evals" / "results" / "redteam"
PROMPTFOO = "npx --yes promptfoo@0.123.1"
PRIVATE = {
    "PROMPTFOO_DISABLE_REMOTE_GENERATION": "true",
    "PROMPTFOO_DISABLE_SHARING": "1",
    "PROMPTFOO_DISABLE_TELEMETRY": "1",
    "PROMPTFOO_DISABLE_UPDATE": "1",
}


def _note() -> str:
    kind = os.environ.get("AGENT_REDTEAM_PROVIDER", "stand-in")
    target = os.environ.get("AGENT_REDTEAM_AGENT", "support-handoff")
    if kind == "gateway":
        return f"target {target} with the real model behind the gateway"
    return (
        f"target {target} on the stand-in gateway (canned model answers): checks the attack "
        "path and the platform's controls, not a model's susceptibility"
    )


def finish(report: Report, path: Path) -> int:
    report.save(path)
    print(report.summary())
    print(
        f"redteam: report written to {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}"
    )
    decision = gate(report, require_all_categories=False)
    for reason in decision.reasons:
        print(f"redteam: BLOCKING: {reason}", file=sys.stderr)
    return 0 if decision.release else 1


def promptfoo(generate: bool, path: Path) -> int:
    config = LIBS / ("promptfooconfig.redteam.yaml" if generate else "promptfooconfig.yaml")
    env = {
        **os.environ,
        **PRIVATE,
        "PROMPTFOO_PYTHON": os.environ.get("PROMPTFOO_PYTHON", sys.executable),
    }
    cmd = shlex.split(os.environ.get("PROMPTFOO_CMD", PROMPTFOO))
    with tempfile.TemporaryDirectory() as tmp:
        results = Path(tmp) / "results.json"
        if generate:
            tests = Path(tmp) / "redteam.yaml"
            subprocess.run(  # noqa: S603 - fixed tool command
                [*cmd, "redteam", "generate", "-c", str(config), "-o", str(tests)],
                env=env,
                cwd=LIBS,
                check=True,
            )
            config = tests
        run = subprocess.run(  # noqa: S603 - fixed tool command
            [
                *cmd,
                "eval",
                "-c",
                str(config),
                "-o",
                str(results),
                "--no-cache",
                "--max-concurrency",
                "2",
            ],
            env=env,
            cwd=LIBS,
        )
        if not results.exists():
            print(
                f"redteam: promptfoo produced no results (exit {run.returncode})", file=sys.stderr
            )
            return 2
        rows = from_promptfoo(json.loads(results.read_text()))
    report = Report(
        git_commit(ROOT), now(), "platform (live run API)", rows, ("promptfoo",), [_note()]
    )
    return finish(report, path)


def garak(path: Path) -> int:
    python = os.environ.get("GARAK_PYTHON", str(ROOT / ".tmp" / "garak" / "bin" / "python"))
    mapping = (yaml.safe_load((LIBS / "mapping.yaml").read_text()) or {})["garak"]
    with tempfile.TemporaryDirectory() as tmp:
        env = {
            **os.environ,
            "PYTHONPATH": str(LIBS),
            "XDG_DATA_HOME": tmp,
            "XDG_CONFIG_HOME": tmp,
            "XDG_CACHE_HOME": tmp,
        }
        subprocess.run(  # noqa: S603 - fixed tool command
            [
                python,
                "-m",
                "garak",
                "--config",
                str(LIBS / "garak.yaml"),
                "--report_prefix",
                "redteam",
            ],
            env=env,
            cwd=tmp,
            check=False,
        )
        reports = list(Path(tmp).rglob("redteam.report.jsonl"))
        if not reports:
            print("redteam: garak produced no report", file=sys.stderr)
            return 2
        config = yaml.safe_load((LIBS / "garak.yaml").read_text()) or {}
        rows = from_garak(reports[0].read_text().splitlines(), mapping, garak_probes(config))
    report = Report(git_commit(ROOT), now(), "platform (live run API)", rows, ("garak",), [_note()])
    return finish(report, path)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="redteam_libraries")
    sub = parser.add_subparsers(dest="tool", required=True)
    pf = sub.add_parser("promptfoo")
    pf.add_argument("--generate", action="store_true")
    pf.add_argument("--report", type=Path, default=OUT / "promptfoo.json")
    ga = sub.add_parser("garak")
    ga.add_argument("--report", type=Path, default=OUT / "garak.json")
    args = parser.parse_args(argv)
    if args.tool == "promptfoo":
        return promptfoo(args.generate, args.report.resolve())
    return garak(args.report.resolve())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
