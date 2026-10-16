"""Run a reference app's golden task set through the run API and save a versioned report
(Step 34).

    just eval-app invoice-triage                       # stand-in gateway (mock models): mechanics only
    just eval-app invoice-triage --provider gateway --publish    # real models (Mac): a baseline
    just eval-compare evals/results/invoice-triage/A.json evals/results/invoice-triage/B.json

``--provider stand-in`` talks to the stand-in LiteLLM, whose models return canned text: the report
says so and can't be published. ``--provider gateway`` is for real models behind Project 0's
gateway; only those runs may be published to the agent card (``evals/results/<app>.json``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from importlib.metadata import entry_points
from pathlib import Path

from agent_api.eval_runner import Provider, evaluate, summary
from agent_evals.results import compare, publish, save
from agent_runtime.models import GatewayModelClient
from agent_spec.platform import Gateway, load_platform

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "evals" / "results"


def harness(app: str) -> object:
    found = {e.name: e for e in entry_points(group="agent_platform.eval_apps")}
    if app not in found:
        raise SystemExit(
            f"no eval harness for {app!r}; known: {', '.join(sorted(found)) or 'none'}"
        )
    return found[app].load()()


def provider(kind: str, app_harness: object) -> Provider:
    base = load_platform()
    url = os.environ.get("P0_LITELLM_URL", base.gateway.url)
    platform = base.model_copy(update={"gateway": Gateway(url=url)})
    keys = None
    if os.environ.get("LITELLM_MASTER_KEY") and kind == "stand-in":
        keys = {"default": os.environ["LITELLM_MASTER_KEY"]}
    spec = app_harness.spec()  # type: ignore[attr-defined]
    models = {**base.models.roles, **spec.models}
    models.pop("embedder", None)
    return Provider(
        kind,
        lambda s: GatewayModelClient(platform, spec_models=s.models, keys=keys),
        dict(sorted(models.items())),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("app")
    run.add_argument("--provider", choices=["stand-in", "gateway"], default="stand-in")
    run.add_argument("--tasks", type=Path)
    run.add_argument("--limit", type=int)
    run.add_argument("--publish", action="store_true", help="real-model runs only: update the card")
    cmp = sub.add_parser("compare")
    cmp.add_argument("a", type=Path)
    cmp.add_argument("b", type=Path)
    args = parser.parse_args(argv)

    if args.cmd == "compare":
        diff = compare(json.loads(args.a.read_text()), json.loads(args.b.read_text()))
        print(json.dumps(diff, indent=1))
        return 1 if diff["regressions"] else 0

    h = harness(args.app)
    tasks = args.tasks or ROOT / "evals" / "apps" / args.app / "tasks.jsonl"
    report = asyncio.run(evaluate(h, tasks, provider(args.provider, h), args.limit))  # type: ignore[arg-type]
    path = save(report, RESULTS)
    print(summary(report))
    print(f"report: {path.relative_to(ROOT)}")
    if args.publish:
        print(f"card: {publish(report, RESULTS).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
