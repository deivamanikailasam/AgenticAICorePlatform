"""Measure routing accuracy on a labelled set (Step 15 "Done when").

Each router routes every held-out case; the report gives accuracy, per-route precision and
recall, the confusion matrix, mean latency and model cost, plus what is needed to quote it
honestly: dataset path and size, the model alias behind each router, and when it ran. The
embedding router learns only from ``train.jsonl``; ``test.jsonl`` is never shown to any router.

    uv run python -m agent_compiler.route_eval evals/labelled/routing
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from agent_contracts.interfaces import Router
from agent_contracts.models import RunContext


@dataclass
class Dataset:
    path: str
    train: list[dict[str, str]]
    test: list[dict[str, str]]
    rules: dict[str, Any]

    @property
    def routes(self) -> list[str]:
        return sorted({c["route"] for c in self.test})

    def examples(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for row in self.train:
            out.setdefault(row["route"], []).append(row["text"])
        return out


def _jsonl(path: Path) -> list[dict[str, str]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_dataset(directory: str | Path) -> Dataset:
    d = Path(directory)
    rules = yaml.safe_load((d / "rules.yaml").read_text()) if (d / "rules.yaml").exists() else {}
    return Dataset(str(d), _jsonl(d / "train.jsonl"), _jsonl(d / "test.jsonl"), rules or {})


@dataclass
class Report:
    router: str
    model: str
    predictions: list[tuple[str, str]] = field(default_factory=list)  # (expected, predicted)
    errors: int = 0
    seconds: float = 0.0
    cost_aud: float = 0.0

    @property
    def accuracy(self) -> float:
        return sum(e == p for e, p in self.predictions) / len(self.predictions)

    def recall(self, route: str) -> float:
        rows = [p for e, p in self.predictions if e == route]
        return sum(p == route for p in rows) / len(rows) if rows else 0.0

    def precision(self, route: str) -> float:
        rows = [e for e, p in self.predictions if p == route]
        return sum(e == route for e in rows) / len(rows) if rows else 0.0


async def evaluate(
    name: str, router: Router, dataset: Dataset, model: str = "-", cost: Any = None
) -> Report:
    """Routes every test case; a router error counts as a wrong answer (``"<error>"``)."""
    report = Report(name, model)
    ctx = RunContext(tenant_id="eval", user_id="eval", agent_id="route-eval", run_id=f"eval-{name}")
    started = time.perf_counter()
    for case in dataset.test:
        try:
            predicted = (await router.route(case["text"], dataset.routes, ctx)).route
        except (ValueError, RuntimeError):
            predicted, report.errors = "<error>", report.errors + 1
        report.predictions.append((case["route"], predicted))
    report.seconds = time.perf_counter() - started
    report.cost_aud = float(cost(ctx.run_id)) if cost else 0.0
    return report


def render(reports: Sequence[Report], dataset: Dataset) -> str:
    n = len(dataset.test)
    lines = [
        f"Dataset: {dataset.path} ({n} test cases, {len(dataset.routes)} routes; "
        f"{len(dataset.train)} training examples) · run at "
        f"{datetime.now(UTC).isoformat(timespec='seconds')}",
        "",
        "| router | model | accuracy | errors | mean latency ms | cost AUD |",
        "|---|---|---|---|---|---|",
    ]
    for r in reports:
        lines.append(
            f"| {r.router} | {r.model} | {sum(e == p for e, p in r.predictions)}/{n} "
            f"({r.accuracy:.1%}) | {r.errors} | {1000 * r.seconds / n:.0f} | {r.cost_aud:.4f} |"
        )
    for r in reports:
        lines += ["", f"**{r.router}** — precision / recall per route", ""]
        lines += ["| route | precision | recall |", "|---|---|---|"]
        lines += [
            f"| {route} | {r.precision(route):.2f} | {r.recall(route):.2f} |"
            for route in dataset.routes
        ]
        lines += ["", "confusion (rows expected, columns predicted)", ""]
        cols = [*dataset.routes, "<error>"] if r.errors else dataset.routes
        lines.append("| | " + " | ".join(cols) + " |")
        lines.append("|---" * (len(cols) + 1) + "|")
        for expected in dataset.routes:
            counts = [sum(e == expected and p == c for e, p in r.predictions) for c in cols]
            lines.append(f"| {expected} | " + " | ".join(map(str, counts)) + " |")
    return "\n".join(lines)


def rules_router(rules: Mapping[str, Any]) -> Router:
    from agent_runtime.routing import RulesRouter

    return RulesRouter(rules.get("rules", ()), rules.get("default"))


async def _main(dataset: Dataset, which: Sequence[str]) -> int:
    from agent_runtime.models.embeddings import GatewayEmbedder
    from agent_runtime.models.gateway import GatewayModelClient
    from agent_runtime.routing import EmbeddingRouter, LlmRouter

    gateway = GatewayModelClient()

    def cost(run_id: str) -> float:
        return gateway.ledger.total(run_id).cost_aud

    reports = []
    if "llm" in which:
        reports.append(
            await evaluate("llm", LlmRouter(gateway), dataset, gateway.resolve("router")[1], cost)
        )
    if "embedding" in which:
        router = EmbeddingRouter(GatewayEmbedder(gateway), dataset.examples())
        reports.append(
            await evaluate("embedding", router, dataset, gateway.resolve("embedder")[1], cost)
        )
    if "rules" in which:
        reports.append(await evaluate("rules", rules_router(dataset.rules), dataset))
    print(render(reports, dataset))
    return 0


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "evals/labelled/routing"
    routers = sys.argv[2].split(",") if len(sys.argv) > 2 else ["llm", "embedding", "rules"]
    sys.exit(asyncio.run(_main(load_dataset(path), routers)))
