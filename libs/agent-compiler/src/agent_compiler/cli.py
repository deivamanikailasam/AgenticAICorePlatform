"""``agent-compile compile <spec>`` prints the plan; ``agent-compile run <spec>`` runs it traced."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from agent_contracts.models import RunContext, RunStatus
from agent_spec import SpecError, load_spec

from agent_compiler.compiler import CompileError, compile_spec
from agent_compiler.run import run_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-compile")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compile", help="print the ExecutablePlan as JSON")
    c.add_argument("spec")
    r = sub.add_parser("run", help="run the spec once, traced to the OTel Collector")
    r.add_argument("spec")
    r.add_argument("--input", default="{}", help="JSON object passed as the run input")
    r.add_argument("--tenant", default="dev")
    r.add_argument("--user", default="dev-user")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "compile":
            plan = compile_spec(load_spec(args.spec))
            print(json.dumps(plan.model_dump(mode="json"), indent=2))
            return 0
        spec = load_spec(args.spec)
        ctx = RunContext(
            tenant_id=args.tenant, user_id=args.user, agent_id=spec.name, agent_version=spec.version
        )
        result, trace_id = asyncio.run(run_file(args.spec, json.loads(args.input), ctx))
    except (SpecError, CompileError, LookupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "status": result.status.value,
                "output": result.output,
                "trace_id": trace_id,
            },
            indent=2,
        )
    )
    return 0 if result.status is RunStatus.SUCCEEDED else 1


if __name__ == "__main__":
    sys.exit(main())
