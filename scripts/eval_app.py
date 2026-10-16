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
from typing import Any

from agent_api.eval_runner import Provider, evaluate, summary
from agent_evals.results import Report, compare, publish, save
from agent_runtime.models import GatewayModelClient
from agent_spec.platform import Gateway, load_platform

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "evals" / "results"
BASELINES = ROOT / "evals" / "baselines"


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


def apps() -> list[str]:
    return sorted(e.name for e in entry_points(group="agent_platform.eval_apps"))


def mechanics(report: Report) -> dict[str, Any]:
    """What a mechanics baseline keeps: only facts that are stable from run to run."""
    return {
        "kind": "mechanics",
        "note": "stand-in gateway with canned mock responses: checks the harness and the "
        "platform's deterministic parts, not model quality (real-model baseline: VERIFY-ON-MAC)",
        "app": report.app,
        "dataset": report.dataset,
        "versions": report.versions,
        "provider": report.provider,
        "succeeded": sum(t.success for t in report.results),
        "tasks": {t.task: {"success": t.success, "status": t.status} for t in report.results},
    }


def baseline(names: list[str], write: bool) -> int:
    regressions = 0
    for app in names:
        h: Any = harness(app)
        report = asyncio.run(
            evaluate(h, ROOT / "evals" / "apps" / app / "tasks.jsonl", provider("stand-in", h))
        )
        fresh = mechanics(report)
        tasks: dict[str, dict[str, Any]] = fresh["tasks"]
        path = BASELINES / f"{app}.mechanics.json"
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(fresh, indent=1, sort_keys=True) + "\n")
            print(
                f"{app}: {fresh['succeeded']}/{len(tasks)} (mechanics) -> {path.relative_to(ROOT)}"
            )
            continue
        old: dict[str, Any] = json.loads(path.read_text())
        if old["dataset"]["sha256"] != fresh["dataset"]["sha256"]:
            print(f"{app}: the task set changed since the baseline; re-run just eval-baseline")
            regressions += 1
            continue
        worse = [
            t
            for t, o in old["tasks"].items()
            if o["success"] and not tasks.get(t, {}).get("success")
        ]
        better = [
            t
            for t, o in old["tasks"].items()
            if not o["success"] and tasks.get(t, {}).get("success")
        ]
        regressions += len(worse)
        print(
            f"{app}: {fresh['succeeded']}/{len(tasks)} vs baseline {old['succeeded']}; "
            f"{len(worse)} regression(s){': ' + ', '.join(worse[:10]) if worse else ''}"
            f"{f'; {len(better)} newly passing (update the baseline)' if better else ''}"
        )
    return 1 if regressions else 0


def calibrate_report(report_path: Path, rubric_name: str, judge_model: object | None = None) -> int:
    """Grade every item of a real-model report with the rubric's judge, then compare with the
    human labels in evals/labels/<rubric>.jsonl. Without labels the result says "not measured"."""
    from agent_evals.judge import Judge, Rubric, calibrate

    report = json.loads(report_path.read_text())
    if not report.get("baseline_eligible"):
        print("refused: calibrate on real-model outputs only (this report used a mock provider)")
        return 2
    rubric = Rubric.load(ROOT / "evals" / "rubrics" / f"{rubric_name}.md")
    if judge_model is None:
        base = load_platform()
        url = os.environ.get("P0_LITELLM_URL", base.gateway.url)
        judge_model = GatewayModelClient(base.model_copy(update={"gateway": Gateway(url=url)}))
    judge = Judge(judge_model, rubric)  # type: ignore[arg-type]

    async def grade_all() -> list[object]:
        return [
            await judge.grade(r["task"], {"task": r["task"]}, r.get("output"))
            for r in report["results"]
            if r.get("output") is not None
        ]

    verdicts = asyncio.run(grade_all())
    result = calibrate(rubric, verdicts, ROOT / "evals" / "labels" / f"{rubric_name}.jsonl")  # type: ignore[arg-type]
    out = RESULTS / "calibration" / f"{rubric_name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "rubric": rubric_name,
                "report": str(report_path),
                **result.__dict__,
                "summary": result.summary(),
            },
            indent=1,
            default=list,
        )
        + "\n"
    )
    print(result.summary())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("app")
    run.add_argument("--provider", choices=["stand-in", "gateway"], default="stand-in")
    run.add_argument("--tasks", type=Path)
    run.add_argument("--limit", type=int)
    run.add_argument("--publish", action="store_true", help="real-model runs only: update the card")
    base = sub.add_parser("baseline", help="write a mechanics baseline (stand-in provider)")
    base.add_argument("apps", nargs="*")
    check = sub.add_parser("baseline-check", help="re-run and fail on any regression")
    check.add_argument("apps", nargs="*")
    cal = sub.add_parser(
        "calibrate", help="judge a real-model report and measure agreement with human labels"
    )
    cal.add_argument("report", type=Path)
    cal.add_argument("rubric")
    cmp = sub.add_parser("compare")
    cmp.add_argument("a", type=Path)
    cmp.add_argument("b", type=Path)
    args = parser.parse_args(argv)

    if args.cmd in ("baseline", "baseline-check"):
        return baseline(args.apps or apps(), write=args.cmd == "baseline")

    if args.cmd == "calibrate":
        return calibrate_report(args.report, args.rubric)

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
