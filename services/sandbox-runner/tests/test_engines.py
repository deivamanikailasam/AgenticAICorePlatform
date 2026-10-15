"""Contract and hostile-code runs for every engine. gVisor runs wherever runsc is installed (CI
job `sandbox`); Firecracker and Kata wherever KVM and their assets are (CI job `microvm`,
`-m microvm`); the managed stub must refuse."""

import asyncio
import base64
import os

import pytest
from agent_contracts.interfaces import CodeSandbox
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


@pytest.mark.microvm
class TestKata(CodeSandboxContract):
    def make(self) -> KataSandbox:
        return KataSandbox()


@pytest.mark.microvm
class TestFirecracker(CodeSandboxContract):
    def make(self) -> FirecrackerSandbox:
        return FirecrackerSandbox()


async def test_managed_stub_refuses() -> None:
    result = await ManagedSandbox().run(SandboxRequest(code="print(1)"))
    assert result.status is SandboxStatus.REFUSED
    assert "stub" in result.reasons[0]


def test_input_names_cannot_escape_the_work_dir() -> None:
    for bad in ("../x", "/etc/passwd", ".hidden", "a/b"):
        with pytest.raises(ValueError, match="invalid input file name"):
            _tar_inputs(SandboxRequest(code="", files={bad: base64.b64encode(b"x").decode()}))


ENGINES = [
    pytest.param(GvisorSandbox, id="gvisor"),
    pytest.param(FirecrackerSandbox, id="firecracker", marks=pytest.mark.microvm),
]


def _engine(cls: type[CodeSandbox]) -> CodeSandbox:
    sb = cls()
    if not sb.available():
        pytest.skip(f"{cls.__name__} not available on this host")
    return sb


def _gvisor() -> CodeSandbox:
    return _engine(GvisorSandbox)


@pytest.mark.parametrize("cls", ENGINES)
async def test_fork_bomb_is_contained(cls: type[CodeSandbox]) -> None:
    code = (
        "import os\nwhile True:\n    try:\n        os.fork()\n    except OSError:\n        pass\n"
    )
    result = await _engine(cls).run(
        SandboxRequest(code=code, limits=SandboxLimits(wall_seconds=5, pids=32))
    )
    # where the process limit bites first varies (fork, exec or the reporter): any non-OK status
    assert result.status in (SandboxStatus.TIMEOUT, SandboxStatus.KILLED, SandboxStatus.ERROR), (
        result
    )
    assert result.reasons, "containment is reported with a reason"
    probe = await _engine(cls).run(SandboxRequest(code="print('host still fine')"))
    assert probe.status is SandboxStatus.OK


@pytest.mark.parametrize("cls", ENGINES)
async def test_ten_minute_loop_is_cut_at_the_limit(cls: type[CodeSandbox]) -> None:
    loop = asyncio.get_running_loop()
    start = loop.time()
    result = await _engine(cls).run(
        SandboxRequest(code="import time\ntime.sleep(600)", limits=SandboxLimits(wall_seconds=3))
    )
    assert result.status is SandboxStatus.TIMEOUT
    assert loop.time() - start < 30


@pytest.mark.parametrize("cls", ENGINES)
async def test_memory_hog_is_stopped(cls: type[CodeSandbox]) -> None:
    result = await _engine(cls).run(
        SandboxRequest(code="x = bytearray(1024 * 1024 * 1024)", limits=SandboxLimits(memory_mb=64))
    )
    assert result.status in (SandboxStatus.OOM, SandboxStatus.ERROR, SandboxStatus.KILLED), result
    assert result.status is not SandboxStatus.OK


@pytest.mark.parametrize("cls", ENGINES)
async def test_host_filesystem_is_not_visible(cls: type[CodeSandbox]) -> None:
    code = "import os\nprint(sorted(os.listdir('/work')))\nprint(os.path.exists('/home/user'), os.path.exists('/var/run/docker.sock'))"
    result = await _engine(cls).run(SandboxRequest(code=code))
    assert result.status is SandboxStatus.OK
    assert "False False" in result.stdout


async def test_runs_under_gvisor_kernel() -> None:
    result = await _gvisor().run(SandboxRequest(code="import platform; print(platform.release())"))
    assert "gvisor" in result.stdout


@pytest.mark.microvm
async def test_runs_under_its_own_guest_kernel() -> None:
    result = await _engine(FirecrackerSandbox).run(
        SandboxRequest(code="import platform; print(platform.release())")
    )
    assert result.status is SandboxStatus.OK, result
    assert result.stdout.startswith("6.1."), "the pinned guest kernel, not the host's"


REQUIRED = [e for e in os.environ.get("AGENT_REQUIRE_ENGINES", "").split(",") if e]


@pytest.mark.microvm
@pytest.mark.parametrize("name", REQUIRED or ["none-required"])
def test_required_engines_are_available(name: str) -> None:
    """In CI jobs that install an engine, "not available" must fail, never skip."""
    if name == "none-required":
        pytest.skip("AGENT_REQUIRE_ENGINES not set")
    engines = {"gvisor": GvisorSandbox, "kata": KataSandbox, "firecracker": FirecrackerSandbox}
    sb = engines[name]()
    assert sb.available(), getattr(sb, "missing", lambda: [f"{name} runtime not registered"])()
