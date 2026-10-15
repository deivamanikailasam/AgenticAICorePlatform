"""Step 15: the routing-accuracy harness and the labelled set.

The harness is checked on hand-made predictions (known answers); the labelled set is checked for
shape (balanced, train and test disjoint). Accuracy *numbers* for the real routers come only
from ``just eval-routing`` against Project 0's gateway (VERIFY-ON-MAC).
"""

from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from agent_compiler.route_eval import Report, evaluate, load_dataset, render, rules_router
from agent_contracts.dummies.embedder import HashingEmbedder
from agent_contracts.interfaces import Router
from agent_contracts.models import RouteDecision, RunContext
from agent_runtime.routing import EmbeddingRouter

DATASET = Path(__file__).resolve().parents[3] / "evals/labelled/routing"


def test_metrics_on_known_predictions() -> None:
    r = Report("x", "m", [("a", "a"), ("a", "b"), ("b", "b"), ("b", "b")])
    assert r.accuracy == 0.75
    assert (r.recall("a"), r.precision("a")) == (0.5, 1.0)
    assert (r.recall("b"), r.precision("b")) == (1.0, 2 / 3)


def test_the_labelled_set_is_balanced_and_train_and_test_are_disjoint() -> None:
    ds = load_dataset(DATASET)
    assert len(ds.test) == 48
    assert set(Counter(c["route"] for c in ds.test).values()) == {12}
    assert set(Counter(c["route"] for c in ds.train).values()) == {6}
    assert not {c["text"] for c in ds.test} & {c["text"] for c in ds.train}


class Broken(Router):
    async def route(self, text: str, routes: Sequence[str], ctx: RunContext) -> RouteDecision:
        if "card" in text:
            raise ValueError("model reply out of range")
        return RouteDecision(route=routes[0], confidence=0.5)


async def test_every_router_runs_every_case_and_errors_count_as_wrong() -> None:
    ds = load_dataset(DATASET)
    reports = [
        await evaluate("rules", rules_router(ds.rules), ds),
        await evaluate("embedding", EmbeddingRouter(HashingEmbedder(), ds.examples()), ds),
        await evaluate("broken", Broken(), ds),
    ]
    assert all(len(r.predictions) == 48 for r in reports)
    broken = reports[-1]
    assert broken.errors == sum("card" in c["text"] for c in ds.test) > 0
    out = render(reports, ds)
    assert "(48 test cases, 4 routes; 24 training examples)" in out
    assert "| <error>" in out.split("**broken**")[1]
