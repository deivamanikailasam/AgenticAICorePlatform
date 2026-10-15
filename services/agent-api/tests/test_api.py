"""The run API (Step 17): AG-UI over SSE and WebSocket, runs, approvals from verified tokens."""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from agent_api.app import build_app
from agent_api.assembly import Assembly
from agent_api.runs import RunManager
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.models import Message, Role, SideEffect
from agent_spec import load_spec_text
from agent_spec.platform import load_platform
from agent_tools.builtin import CalculatorTool
from agent_tools.registry import InMemoryToolRegistry
from mcp.server.auth.provider import AccessToken
from starlette.testclient import TestClient

USERS = {
    "t-alice": ("alice", ["/acme/finance"]),
    "t-carol": ("carol", ["/acme/finance"]),
    "t-jsmith": ("j.smith", ["/acme/approvers", "/acme/finance-approvers"]),
    "t-mallory": ("mallory", ["/globex/finance"]),
}

SPEC = """
spec_version: 1
name: pay-total
version: 0.1.0
owner: finance-platform
description: Adds up an amount, explains it, and pays it after approval.
risk_tier: medium
autonomy: workflow
pattern: workflow
runtime: { adapter: langgraph, durability: memory }
budgets: { max_steps: 10, max_wall_seconds: 30, max_tokens: 5000, max_cost_aud: 0.5 }
tools:
  - ref: local://platform/calculator
  - ref: local://test/pay
    approval: { required: true, approvers: "group:finance-approvers" }
graph:
  nodes:
    - { id: total, type: tool, tool: calculator, config: { args: { expression: $input.expression } } }
    - { id: explain, type: llm, config: { template: "Explain the total ${total.result}." } }
    - { id: pay, type: tool, tool: pay, config: { args: { amount_aud: $total.result } } }
  edges: ["total -> explain: on_ok", "explain -> pay: on_ok"]
"""


class Verifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        if token not in USERS:
            return None
        user, groups = USERS[token]
        return AccessToken(
            token=token,
            client_id="test-client",
            scopes=[],
            expires_at=None,
            claims={"preferred_username": user, "groups": groups},
        )


class Pay(CalculatorTool):
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self.calls = calls

    @property
    def spec(self) -> Any:
        return super().spec.model_copy(
            update={
                "name": "pay",
                "ref": "local://test/pay",
                "side_effect": SideEffect.IRREVERSIBLE,
                "idempotent": False,
                "input_schema": {
                    "type": "object",
                    "properties": {"amount_aud": {"type": "number"}},
                    "required": ["amount_aud"],
                },
                "output_schema": None,
            }
        )

    async def invoke(self, arguments: Any, ctx: Any) -> Any:
        self.calls.append(dict(arguments))
        return {"paid": arguments["amount_aud"]}


class CountingCalculator(CalculatorTool):
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self.calls = calls

    async def invoke(self, arguments: Any, ctx: Any) -> Any:
        self.calls.append(dict(arguments))
        return await super().invoke(arguments, ctx)


class World:
    """One API process. Worlds built with the same ``store`` and ``checkpointer`` (and the same
    ``payments``/``sums`` lists, standing in for the outside world) simulate a restart."""

    def __init__(
        self,
        store: Any = None,
        checkpointer: Any = None,
        model: Any = None,
        payments: list[dict[str, Any]] | None = None,
        sums: list[dict[str, Any]] | None = None,
    ) -> None:
        self.payments = payments if payments is not None else []
        self.sums = sums if sums is not None else []
        self.audit = MemoryAuditSink()

        async def registry(spec: Any) -> InMemoryToolRegistry:
            reg = InMemoryToolRegistry()
            await reg.register(CountingCalculator(self.sums))
            await reg.register(Pay(self.payments))
            return reg

        assembly = Assembly(
            load_platform(),
            RulesPolicyDecisionPoint(),
            self.audit,
            model_factory=model
            or (
                lambda spec: ScriptedModelClient(
                    [Message(role=Role.ASSISTANT, content="The total is 42.")]
                )
            ),
            registry_factory=registry,
            checkpointer=checkpointer,
        )
        spec = load_spec_text(SPEC)
        self.manager = RunManager({spec.name: spec}, assembly, store)
        self.app = build_app(self.manager, Verifier())


@pytest.fixture
async def world() -> AsyncIterator[tuple[World, httpx.AsyncClient]]:
    w = World()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=w.app), base_url="http://api.test"
    ) as client:
        yield w, client


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def parse_sse(text: str) -> list[tuple[int, dict[str, Any]]]:
    out = []
    for frame in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines())
        out.append((int(lines["id"]), json.loads(lines["data"])))
    return out


async def start(
    client: httpx.AsyncClient, key: str | None = None, expression: str = "40+2"
) -> dict[str, Any]:
    headers = auth("t-alice") | ({"Idempotency-Key": key} if key else {})
    resp = await client.post(
        "/v1/agents/pay-total/runs", json={"input": {"expression": expression}}, headers=headers
    )
    assert resp.status_code in (200, 201), resp.text
    return dict(resp.json())


async def stream(
    client: httpx.AsyncClient, run_id: str, who: str = "t-alice", after: int = -1
) -> list[tuple[int, dict[str, Any]]]:
    resp = await client.get(f"/v1/runs/{run_id}/events", params={"after": after}, headers=auth(who))
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    return parse_sse(resp.text)


async def test_every_call_needs_a_valid_token(world: tuple[World, httpx.AsyncClient]) -> None:
    _, client = world
    assert (await client.get("/v1/agents")).status_code == 401
    assert (await client.get("/v1/agents", headers=auth("forged"))).status_code == 401
    agents = (await client.get("/v1/agents", headers=auth("t-alice"))).json()["agents"]
    assert [a["name"] for a in agents] == ["pay-total"]


async def test_run_streams_ag_ui_events_up_to_the_approval(
    world: tuple[World, httpx.AsyncClient],
) -> None:
    w, client = world
    run = await start(client)
    events = await stream(client, run["run_id"])
    types = [e["type"] for _, e in events]
    assert [i for i, _ in events] == list(range(len(events)))
    assert types[:2] == ["RUN_STARTED", "STATE_SNAPSHOT"]
    for t in (
        "STEP_STARTED",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "STATE_DELTA",
        "STEP_FINISHED",
    ):
        assert t in types, t
    calc = next(e for _, e in events if e["type"] == "TOOL_CALL_START")
    assert calc["toolCallName"] == "calculator"
    text = "".join(e["delta"] for _, e in events if e["type"] == "TEXT_MESSAGE_CONTENT")
    assert text == "The total is 42."
    finished = events[-1][1]
    assert finished["type"] == "RUN_FINISHED"
    assert finished["outcome"]["type"] == "interrupt"
    interrupt = finished["outcome"]["interrupts"][0]
    assert interrupt["metadata"]["action"] == {"tool": "pay", "arguments": {"amount_aud": 42.0}}
    assert interrupt["metadata"]["approvers"] == "group:finance-approvers"
    assert w.payments == []


async def test_approval_comes_from_the_approvers_verified_token(
    world: tuple[World, httpx.AsyncClient],
) -> None:
    w, client = world
    run = await start(client)
    first = await stream(client, run["run_id"])
    url = f"/v1/runs/{run['run_id']}/approvals"
    own = await client.post(url, json={"approved": True}, headers=auth("t-alice"))
    assert own.status_code == 403, "the requester cannot approve their own run"
    colleague = await client.post(url, json={"approved": True}, headers=auth("t-carol"))
    assert colleague.status_code == 404, "a colleague outside the approver group cannot even see it"
    resp = await client.post(
        url, json={"approved": True, "by": "someone-else"}, headers=auth("t-jsmith")
    )
    assert resp.status_code == 202, resp.text
    rest = await stream(client, run["run_id"], after=first[-1][0])
    assert rest[0][0] == first[-1][0] + 1, "a reconnect continues where it left off"
    assert [e["toolCallName"] for _, e in rest if e["type"] == "TOOL_CALL_START"] == ["pay"]
    assert rest[-1][1]["type"] == "RUN_FINISHED"
    assert rest[-1][1]["outcome"]["type"] == "success"
    assert w.payments == [{"amount_aud": 42.0}]
    paid = [e.event.payload for e in await w.audit.read() if e.event.payload.get("tool") == "pay"]
    assert paid[-1]["approver"] == "j.smith", "the approver is the token's user, not the body's"


async def test_rejection_pays_nothing(world: tuple[World, httpx.AsyncClient]) -> None:
    w, client = world
    run = await start(client)
    await stream(client, run["run_id"])
    resp = await client.post(
        f"/v1/runs/{run['run_id']}/approvals", json={"approved": False}, headers=auth("t-jsmith")
    )
    assert resp.status_code == 202
    await w.manager.wait_idle(w.manager.runs[run["run_id"]])
    assert w.payments == []


async def test_idempotent_creation(world: tuple[World, httpx.AsyncClient]) -> None:
    _, client = world
    first = await start(client, key="k-1")
    again = await client.post(
        "/v1/agents/pay-total/runs",
        json={"input": {"expression": "40+2"}},
        headers=auth("t-alice") | {"Idempotency-Key": "k-1"},
    )
    assert (again.status_code, again.json()["run_id"]) == (200, first["run_id"])
    different = await client.post(
        "/v1/agents/pay-total/runs",
        json={"input": {"expression": "1+1"}},
        headers=auth("t-alice") | {"Idempotency-Key": "k-1"},
    )
    assert different.status_code == 409


async def test_other_tenants_cannot_see_runs(world: tuple[World, httpx.AsyncClient]) -> None:
    _, client = world
    run = await start(client)
    for path in (f"/v1/runs/{run['run_id']}", f"/v1/runs/{run['run_id']}/events"):
        assert (await client.get(path, headers=auth("t-mallory"))).status_code == 404
    assert (await client.get("/v1/threads", headers=auth("t-mallory"))).json() == {"threads": []}
    threads = (await client.get("/v1/threads", headers=auth("t-alice"))).json()["threads"]
    assert threads[0]["runs"][0]["run_id"] == run["run_id"]


async def test_generic_resume_cannot_answer_an_approval_and_cancel_works(
    world: tuple[World, httpx.AsyncClient],
) -> None:
    w, client = world
    run = await start(client)
    await stream(client, run["run_id"])
    forged = await client.post(
        f"/v1/runs/{run['run_id']}/resume",
        json={"approved": True, "by": "j.smith"},
        headers=auth("t-alice"),
    )
    assert forged.status_code == 409
    cancelled = await client.post(f"/v1/runs/{run['run_id']}/cancel", headers=auth("t-alice"))
    assert cancelled.json()["status"] == "cancelled"
    events = await stream(client, run["run_id"])
    assert events[-1][1]["outcome"]["type"] == "cancelled"
    assert w.payments == []


def test_websocket_streams_the_same_events_with_the_token_in_the_first_message() -> None:
    w = World()
    with TestClient(w.app) as client:
        run = client.post(
            "/v1/agents/pay-total/runs",
            json={"input": {"expression": "40+2"}},
            headers=auth("t-alice"),
        ).json()
        with client.websocket_connect(f"/v1/runs/{run['run_id']}/ws") as ws:
            ws.send_json({"token": "t-alice"})
            received = []
            while True:
                event = ws.receive_json()
                received.append(event)
                if event["type"] == "RUN_FINISHED":
                    break
        assert received[0]["type"] == "RUN_STARTED"
        assert received[-1]["outcome"]["type"] == "interrupt"
        with client.websocket_connect(f"/v1/runs/{run['run_id']}/ws") as ws:
            ws.send_json({"token": "t-mallory"})
            with pytest.raises(Exception):  # noqa: B017, PT011 - closed with 4404
                ws.receive_json()


async def test_the_page_uses_only_ag_ui_events(world: tuple[World, httpx.AsyncClient]) -> None:
    _, client = world
    page = await client.get("/")
    assert page.status_code == 200
    assert "default-src 'self'" in page.headers["content-security-policy"]
    html = page.text
    for event in (
        "TOOL_CALL_START",
        "TOOL_CALL_RESULT",
        "TEXT_MESSAGE_CONTENT",
        "RUN_FINISHED",
        "interrupt",
    ):
        assert event in html
    assert "?token=" not in html, "tokens never go in URLs"


# ---------------------------------------------------------------- durability (Step 18)


def durable_world(previous: World | None = None, **kw: Any) -> World:
    from agent_api.store import MemoryRunStore
    from agent_contracts.dummies.state import InMemoryCheckpointer

    if previous is None:
        return World(store=MemoryRunStore(), checkpointer=InMemoryCheckpointer(), **kw)
    return World(
        store=previous.manager.store,
        checkpointer=previous.manager.assembly.checkpointer,
        payments=previous.payments,
        sums=previous.sums,
        **kw,
    )


def client_for(w: World) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=w.app), base_url="http://api.test")


async def test_a_paused_run_survives_a_restart_and_can_be_approved() -> None:
    before = durable_world()
    async with client_for(before) as client:
        run = await start(client)
        first = await stream(client, run["run_id"])
    after = durable_world(before)  # the process restarts: new manager, same store and checkpoints
    actions = await after.manager.recover()
    assert actions == [f"{run['run_id']}: waiting for approval again"]
    async with client_for(after) as client:
        state = (await client.get(f"/v1/runs/{run['run_id']}", headers=auth("t-alice"))).json()
        assert state["status"] == "interrupted"
        assert state["events"] == len(first), "nothing was emitted again"
        resp = await client.post(
            f"/v1/runs/{run['run_id']}/approvals", json={"approved": True}, headers=auth("t-jsmith")
        )
        assert resp.status_code == 202, resp.text
        rest = await stream(client, run["run_id"], after=first[-1][0])
    assert rest[-1][1]["outcome"]["type"] == "success"
    assert after.payments == [{"amount_aud": 42.0}]
    assert len(after.sums) == 1, "the calculator step before the restart was not repeated"


class Stuck(ScriptedModelClient):
    """A model that never answers: the worker 'dies' while this step is running."""

    async def chat(self, request: Any) -> Any:
        await asyncio.Event().wait()


async def test_a_running_run_resumes_from_its_last_checkpoint_after_a_restart() -> None:
    before = durable_world(model=lambda spec: Stuck())
    async with client_for(before) as client:
        run = await start(client)
    run_obj = before.manager.runs[run["run_id"]]
    for _ in range(100):
        if any(e["type"] == "STEP_STARTED" and e["stepName"] == "explain" for e in run_obj.events):
            break
        await asyncio.sleep(0.02)
    assert run_obj.task is not None
    from agent_api.store import MemoryRunStore

    class Gone(MemoryRunStore):  # a dead process writes nothing more
        async def save(self, record: Any) -> None:
            return None

        async def append(self, run_id: str, index: int, event: Any) -> None:
            return None

    shared = before.manager.store
    before.manager.store = Gone()
    run_obj.task.cancel()  # the worker dies mid-step; the shared store still says "running"
    with contextlib.suppress(asyncio.CancelledError):
        await run_obj.task
    before.manager.store = shared
    after = durable_world(before)
    assert await after.manager.recover() == [f"{run['run_id']}: resumed from checkpoints"]
    await after.manager.wait_idle(after.manager.runs[run["run_id"]])
    assert after.manager.runs[run["run_id"]].status == "interrupted"
    assert len(after.sums) == 1, "the completed tool step was answered from its checkpoint"


async def test_fork_from_an_earlier_checkpoint_continues_without_repeating_it() -> None:
    w = durable_world()
    async with client_for(w) as client:
        run = await start(client)
        await stream(client, run["run_id"])
        cps = (
            await client.get(f"/v1/threads/{run['thread_id']}/checkpoints", headers=auth("t-alice"))
        ).json()["checkpoints"]
        assert [c["node_id"] for c in cps] == ["total", "explain"]
        assert (
            await client.get(
                f"/v1/threads/{run['thread_id']}/checkpoints", headers=auth("t-mallory")
            )
        ).status_code == 404
        forked = await client.post(
            f"/v1/threads/{run['thread_id']}/fork",
            json={"checkpoint_id": cps[0]["checkpoint_id"]},
            headers=auth("t-alice"),
        )
        assert forked.status_code == 201, forked.text
        new = forked.json()
        assert new["thread_id"] != run["thread_id"]
        events = await stream(client, new["run_id"])
    steps = [e["stepName"] for _, e in events if e["type"] == "STEP_STARTED"]
    assert steps == ["explain"], "the forked run picks up after the chosen checkpoint"
    assert events[-1][1]["outcome"]["type"] == "interrupt"
    assert len(w.sums) == 1
