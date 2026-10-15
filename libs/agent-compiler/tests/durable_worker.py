"""A worker process for the Step 18 crash test (run by test_durable_runs.py, not collected).

Runs ``record -> slow -> finish``: ``record`` is a write tool that appends a line to a file (a
side effect visible across processes), ``slow`` drops a marker and sleeps so the test can kill
the process mid-run. With an existing checkpoint history for the run id, it resumes instead.

usage: durable_worker.py <adapter> <checkpointer: sqlite:PATH | postgres> <writes> <marker>
                         <run_id> <thread_id> <slow_seconds>
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from agent_compiler.compiler import compile_spec, runtime_for
from agent_compiler.run import executors_for
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.dummies.state import SqliteCheckpointer
from agent_contracts.interfaces import Checkpointer, Tool
from agent_contracts.models import RunContext, SideEffect, ToolSpec
from agent_runtime.durable import CheckpointingNodeRunner, load_journal
from agent_spec import load_spec_text
from agent_tools.authorising import AuthorisingToolExecutor
from agent_tools.executor import PlatformToolExecutor
from agent_tools.registry import InMemoryToolRegistry

adapter, store, writes, marker, run_id, thread_id, slow = sys.argv[1:8]

SPEC = f"""
spec_version: 1
name: durable-test
version: 0.1.0
owner: agent-platform
description: Write, then a slow step, then finish.
risk_tier: low
autonomy: workflow
pattern: workflow
runtime: {{ adapter: {adapter}, durability: memory }}
budgets: {{ max_steps: 10, max_wall_seconds: 120, max_tokens: 1000, max_cost_aud: 0.1 }}
tools:
  - ref: local://test/record
graph:
  nodes:
    - {{ id: record, type: tool, tool: record, config: {{ args: {{ text: $input.text }} }} }}
    - {{ id: slow, type: function, fn: slow }}
    - {{ id: finish, type: function, fn: finish }}
  edges: ["record -> slow: on_ok", "slow -> finish: on_ok"]
"""


class Record(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="record",
            ref="local://test/record",
            side_effect=SideEffect.WRITE,
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        )

    async def invoke(self, arguments: Any, ctx: RunContext) -> Any:
        await asyncio.to_thread(_append, arguments["text"])
        return {"written": arguments["text"]}


def _append(text: str) -> None:
    with Path(writes).open("a") as f:
        f.write(text + "\n")


def slow_fn(values: dict[str, Any]) -> Any:
    Path(marker).write_text("started")
    time.sleep(float(slow))
    return {"slept": float(slow)}


def finish_fn(values: dict[str, Any]) -> Any:
    return {"done": True, "recorded": values["record"]["written"]}


async def main() -> None:
    checkpointer: Checkpointer
    if store.startswith("sqlite:"):
        checkpointer = SqliteCheckpointer(store.removeprefix("sqlite:"))
    else:
        import base64
        import os

        from agent_runtime.checkpoints import FieldCipher, PostgresCheckpointer

        checkpointer = PostgresCheckpointer(
            os.environ["AGENT_DB_DSN"], FieldCipher(base64.b64decode(os.environ["CHECKPOINT_KEY"]))
        )
    spec = load_spec_text(SPEC)
    plan = compile_spec(spec)
    reg = InMemoryToolRegistry()
    await reg.register(Record())
    tools = AuthorisingToolExecutor(
        PlatformToolExecutor(reg), reg, RulesPolicyDecisionPoint(), MemoryAuditSink()
    )
    functions = {"slow": slow_fn, "finish": finish_fn}
    ctx = RunContext(
        tenant_id="t1", user_id="u1", agent_id=spec.name, run_id=run_id, thread_id=thread_id
    )
    journal = await load_journal(checkpointer, ctx)
    runner = CheckpointingNodeRunner(
        functions,
        executors_for(spec, plan, functions, tools=tools),
        replay=journal,
        resume=True,
        checkpointer=checkpointer,
    )
    rt = runtime_for(spec, runner=runner)
    handle = await rt.build(plan)
    result = await rt.invoke(handle, {"text": "paid INV-1"}, ctx)
    print(
        json.dumps(
            {"status": result.status.value, "output": result.output, "resumed": journal is not None}
        )
    )


asyncio.run(main())
