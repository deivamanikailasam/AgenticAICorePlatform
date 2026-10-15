"""Platform run events → AG-UI events (protocol 1.0, ``ag-ui-protocol``).

=====================  =============================================================
platform               AG-UI
=====================  =============================================================
run_started            RUN_STARTED (+ STATE_SNAPSHOT of the input on the first start)
node_started/finished  STEP_STARTED / STEP_FINISHED (open text messages are closed)
text_delta             TEXT_MESSAGE_START, TEXT_MESSAGE_CONTENT…, TEXT_MESSAGE_END
tool_call_started      TOOL_CALL_START, TOOL_CALL_ARGS (JSON), TOOL_CALL_END
tool_call_finished     TOOL_CALL_RESULT (output, or the error)
state_delta            STATE_DELTA (JSON Patch ``add`` of the node's value)
run_finished           RUN_FINISHED with outcome success | interrupt | cancelled
                       (an approval is an ``interrupt`` naming the proposed action)
run_error              RUN_ERROR
=====================  =============================================================
"""

from __future__ import annotations

import json
from typing import Any

from ag_ui.core import (
    BaseEvent,
    Interrupt,
    RunErrorEvent,
    RunFinishedCancelledOutcome,
    RunFinishedEvent,
    RunFinishedInterruptOutcome,
    RunFinishedSuccessOutcome,
    RunStartedEvent,
    StateDeltaEvent,
    StateSnapshotEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from agent_contracts.models import RunEvent, RunEventType, new_id


def _pointer(key: str) -> str:
    return "/" + key.replace("~", "~0").replace("/", "~1")


class Translator:
    """Stateful per run: tracks open text messages so every start has an end."""

    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        self.open: dict[str, str | None] = {}  # message id -> node id
        self.started = False

    def _close(self, node_id: str | None = None) -> list[BaseEvent]:
        done = [m for m, n in self.open.items() if node_id is None or n == node_id]
        for m in done:
            del self.open[m]
        return [TextMessageEndEvent(message_id=m) for m in done]

    def translate(self, event: RunEvent) -> list[BaseEvent]:
        d: dict[str, Any] = event.data
        run_id = event.run_id
        match event.type:
            case RunEventType.RUN_STARTED:
                out: list[BaseEvent] = [RunStartedEvent(thread_id=self.thread_id, run_id=run_id)]
                if not self.started and "input" in d:
                    out.append(StateSnapshotEvent(snapshot={"input": d["input"]}))
                self.started = True
                return out
            case RunEventType.NODE_STARTED:
                return [StepStartedEvent(step_name=str(event.node_id))]
            case RunEventType.NODE_FINISHED:
                return [
                    *self._close(event.node_id),
                    StepFinishedEvent(step_name=str(event.node_id)),
                ]
            case RunEventType.TEXT_DELTA:
                message = str(d["message_id"])
                out = []
                if message not in self.open:
                    self.open[message] = event.node_id
                    out.append(TextMessageStartEvent(message_id=message, role="assistant"))
                if d.get("delta"):
                    out.append(TextMessageContentEvent(message_id=message, delta=str(d["delta"])))
                if d.get("final"):
                    del self.open[message]
                    out.append(TextMessageEndEvent(message_id=message))
                return out
            case RunEventType.TOOL_CALL_STARTED:
                call = str(d["tool_call_id"])
                return [
                    ToolCallStartEvent(tool_call_id=call, tool_call_name=str(d["tool"])),
                    ToolCallArgsEvent(tool_call_id=call, delta=json.dumps(d.get("arguments", {}))),
                    ToolCallEndEvent(tool_call_id=call),
                ]
            case RunEventType.TOOL_CALL_FINISHED:
                body = (
                    d.get("output")
                    if d.get("ok")
                    else {"error": d.get("error"), "code": d.get("error_code")}
                )
                return [
                    ToolCallResultEvent(
                        message_id=new_id(),
                        tool_call_id=str(d["tool_call_id"]),
                        content=json.dumps(body, default=str),
                        role="tool",
                    )
                ]
            case RunEventType.STATE_DELTA:
                values = d.get("values") or {}
                return [
                    StateDeltaEvent.model_validate(
                        {
                            "delta": [
                                {"op": "add", "path": _pointer(k), "value": v}
                                for k, v in values.items()
                            ]
                        }
                    )
                ]
            case RunEventType.RUN_FINISHED:
                closing = self._close()
                status = d.get("status")
                if status == "interrupted":
                    outcome: Any = RunFinishedInterruptOutcome(
                        interrupts=[
                            Interrupt(
                                id=str(i["id"]),
                                reason=str(i.get("reason", "approval")),
                                message=i.get("message"),
                                metadata={
                                    k: v
                                    for k, v in i.items()
                                    if k not in {"id", "reason", "message"}
                                },
                            )
                            for i in d.get("interrupts", [])
                        ]
                    )
                elif status == "cancelled":
                    outcome = RunFinishedCancelledOutcome()
                else:
                    outcome = RunFinishedSuccessOutcome()
                return [
                    *closing,
                    RunFinishedEvent(
                        thread_id=self.thread_id,
                        run_id=run_id,
                        result={
                            "status": status,
                            "output": d.get("output"),
                            "stop_reason": d.get("stop_reason"),
                        },
                        outcome=outcome,
                    ),
                ]
            case RunEventType.RUN_ERROR:
                return [
                    *self._close(),
                    RunErrorEvent(message=str(d.get("message", "run failed")), code=d.get("code")),
                ]
            case _:
                return []
