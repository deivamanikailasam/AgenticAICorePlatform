"""Contract runs for every engine. gVisor runs fully wherever runsc is installed (this CI image
installs it); Kata wherever kata-runtime is installed; the stubs must refuse."""

import asyncio
import base64

import pytest
from agent_contracts.models import SandboxLimits, SandboxRequest, SandboxStatus
from agent_contracts.testing import CodeSandboxContract
from sandbox_runner.engines import (
    FirecrackerSandbox,
    GvisorSandbox,
    KataSandbox,
    ManagedSandbox,
    _tar_inputs,
)

pytestmark = pytest.mark.sandbox


class TestGvisor(CodeSandboxContract):
    def make(self) -> GvisorSandbox:
        return GvisorSandbox()


class TestKata(CodeSandboxContract):
    def make(self) -> KataSandbox:
        return KataSandbox()


@pytest.mark.parametrize("engine", [FirecrackerSandbox(), ManagedSandbox()])
async def test_stubs_refuse(engine: object) -> None:
    result = await engine.run(SandboxRequest(code="print(1)"))  # type: ignore[attr-defined]
    assert result.status is SandboxStatus.REFUSED
    assert "stub" in result.reasons[0]


def test_input_names_cannot_escape_the_work_dir() -> None:
    for bad in ("../x", "/etc/passwd", ".hidden", "a/b"):
        with pytest.raises(ValueError, match="invalid input file name"):
            _tar_inputs(SandboxRequest(code="", files={bad: base64.b64encode(b"x").decode()}))


def _gvisor() -> GvisorSandbox:
    sb = GvisorSandbox()
    if not sb.available():
        pytest.skip("runsc not installed")
    return sb


async def test_fork_bomb_is_contained() -> None:
    code = (
        "import os\nwhile True:\n    try:\n        os.fork()\n    except OSError:\n        pass\n"
    )
    result = await _gvisor().run(
        SandboxRequest(code=code, limits=SandboxLimits(wall_seconds=5, pids=32))
    )
    # where the process limit bites first varies (fork, exec or the reporter): any non-OK status
    assert result.status in (SandboxStatus.TIMEOUT, SandboxStatus.KILLED, SandboxStatus.ERROR), (
        result
    )
    assert result.reasons, "containment is reported with a reason"
    probe = await _gvisor().run(SandboxRequest(code="print('host still fine')"))
    assert probe.status is SandboxStatus.OK


async def test_ten_minute_loop_is_cut_at_the_limit() -> None:
    loop = asyncio.get_running_loop()
    start = loop.time()
    result = await _gvisor().run(
        SandboxRequest(code="import time\ntime.sleep(600)", limits=SandboxLimits(wall_seconds=3))
    )
    assert result.status is SandboxStatus.TIMEOUT
    assert loop.time() - start < 30


async def test_memory_hog_is_stopped() -> None:
    result = await _gvisor().run(
        SandboxRequest(code="x = bytearray(1024 * 1024 * 1024)", limits=SandboxLimits(memory_mb=64))
    )
    assert result.status in (SandboxStatus.OOM, SandboxStatus.ERROR, SandboxStatus.KILLED), result
    assert result.status is not SandboxStatus.OK


async def test_host_filesystem_is_not_visible() -> None:
    code = "import os\nprint(sorted(os.listdir('/work')))\nprint(os.path.exists('/home/user'), os.path.exists('/var/run/docker.sock'))"
    result = await _gvisor().run(SandboxRequest(code=code))
    assert result.status is SandboxStatus.OK
    assert "False False" in result.stdout


async def test_runs_under_gvisor_kernel() -> None:
    result = await _gvisor().run(SandboxRequest(code="import platform; print(platform.release())"))
    assert "gvisor" in result.stdout
