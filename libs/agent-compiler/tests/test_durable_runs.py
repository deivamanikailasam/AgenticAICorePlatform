"""Step 18 "Done when": kill the worker mid-run; the run resumes from the last checkpoint without
repeating a completed write tool (both runtime adapters; SQLite checkpoints here, Postgres in
the integration test)."""

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

WORKER = Path(__file__).parent / "durable_worker.py"
ADAPTERS = ["langgraph", "agent_framework"]


def crash_then_resume(
    tmp: Path, adapter: str, store: str, env: dict[str, str] | None = None
) -> dict:
    writes, marker = tmp / "writes.txt", tmp / "slow.started"
    run_id, thread_id = uuid.uuid4().hex, uuid.uuid4().hex
    args = [
        sys.executable,
        str(WORKER),
        adapter,
        store,
        str(writes),
        str(marker),
        run_id,
        thread_id,
    ]
    worker = subprocess.Popen([*args, "60"], env=env)
    for _ in range(200):
        if marker.exists():
            break
        time.sleep(0.1)
    else:
        worker.kill()
        pytest.fail("the worker never reached the slow step")
    os.kill(worker.pid, signal.SIGKILL)  # no clean shutdown, no chance to save anything more
    worker.wait(10)
    assert writes.read_text().splitlines() == ["paid INV-1"], (
        "the write happened once before the crash"
    )
    resumed = subprocess.run(
        [*args, "0"], capture_output=True, text=True, timeout=120, env=env, check=False
    )
    assert resumed.returncode == 0, resumed.stderr
    result = json.loads(resumed.stdout.strip().splitlines()[-1])
    assert writes.read_text().splitlines() == ["paid INV-1"], "the completed write was not repeated"
    return result


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_killed_worker_resumes_without_repeating_the_write(adapter: str, tmp_path: Path) -> None:
    result = crash_then_resume(tmp_path, adapter, f"sqlite:{tmp_path / 'checkpoints.db'}")
    assert result == {
        "status": "succeeded",
        "output": {"done": True, "recorded": "paid INV-1"},
        "resumed": True,
    }


@pytest.mark.integration
def test_killed_worker_resumes_from_postgres_checkpoints(tmp_path: Path) -> None:
    import base64

    if not os.environ.get("AGENT_DB_DSN"):
        pytest.skip("AGENT_DB_DSN not set")
    env = {**os.environ, "CHECKPOINT_KEY": base64.b64encode(os.urandom(32)).decode()}
    result = crash_then_resume(tmp_path, "langgraph", "postgres", env)
    assert (result["status"], result["resumed"]) == ("succeeded", True)
