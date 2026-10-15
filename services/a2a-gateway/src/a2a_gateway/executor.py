"""Runs a published agent for an A2A task through the run API, as the caller.

The caller's bearer token (already verified by the gateway) is forwarded, so the run API
applies the same identity, policy and audit as for any other client. Run states map to A2A
task states: running → working; interrupted (an approval or input is pending) →
input-required; succeeded → completed with the answer as an artifact; failed or stopped →
failed. Cancelling the task cancels the run.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from a2a.helpers.proto_helpers import new_task_from_user_message, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.tasks import TaskUpdater
from a2a.types import Part


def bearer(context: RequestContext) -> str:
    headers = context.call_context.state.get("headers", {}) if context.call_context else {}
    return str(headers.get("authorization", ""))


def answer_text(run: dict[str, Any]) -> str:
    output = run.get("output")
    if isinstance(output, dict) and "answer" in output:
        output = output["answer"]
    return output if isinstance(output, str) else json.dumps(output, default=str)


def finish_of(sse: str) -> dict[str, Any]:
    """The run's last RUN_FINISHED result or RUN_ERROR from its AG-UI event stream."""
    last: dict[str, Any] = {"status": "failed", "error": "the run ended without a result"}
    for line in sse.splitlines():
        if not line.startswith("data: "):
            continue
        event = json.loads(line[6:])
        if event.get("type") == "RUN_FINISHED":
            last = dict(event.get("result") or {})
            outcome = event.get("outcome")
            if isinstance(outcome, dict) and outcome.get("interrupts"):
                last["interrupts"] = outcome["interrupts"]
        elif event.get("type") == "RUN_ERROR":
            last = {"status": "failed", "error": event.get("message")}
    return last


class RunApiExecutor(AgentExecutor):
    def __init__(self, agent: str, api: httpx.AsyncClient) -> None:
        self.agent = agent
        self.api = api
        self.runs: dict[str, str] = {}  # A2A task id -> run id

    async def execute(self, context: RequestContext, event_queue: Any) -> None:
        if context.message is None:
            raise ValueError("an A2A task needs a message")
        task = context.current_task or new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        auth = {"Authorization": bearer(context)}
        resp = await self.api.post(
            f"/v1/agents/{self.agent}/runs",
            json={"input": {"task": context.get_user_input()}},
            headers={**auth, "Idempotency-Key": f"a2a-{task.id}"},
        )
        if resp.status_code not in (200, 201):
            await updater.failed(new_text_message(f"run refused: {resp.text[:300]}"))
            return
        run_id = resp.json()["run_id"]
        self.runs[task.id] = run_id
        await updater.start_work()
        # the AG-UI stream ends when the run is idle: finished, failed or waiting for input
        events = await self.api.get(f"/v1/runs/{run_id}/events", headers=auth, timeout=None)
        result = finish_of(events.text)
        status = result.get("status")
        if status == "interrupted":
            waiting = "; ".join(
                str(i.get("message") or i.get("reason") or "?")
                for i in result.get("interrupts") or []
            )
            # approvals are given in the platform (run API or its UI), never by the A2A caller
            await updater.requires_input(
                new_text_message(f"waiting for approval or input: {waiting or 'pending'}")
            )
        elif status == "succeeded":
            await updater.add_artifact([Part(text=answer_text(result))], name="answer")
            await updater.complete()
        else:
            reason = result.get("error") or result.get("stop_reason") or ""
            await updater.failed(new_text_message(f"run {status}: {reason}"))

    async def cancel(self, context: RequestContext, event_queue: Any) -> None:
        run_id = self.runs.get(context.task_id or "")
        if run_id:
            await self.api.post(
                f"/v1/runs/{run_id}/cancel", headers={"Authorization": bearer(context)}
            )
        if context.task_id and context.context_id:
            await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()
