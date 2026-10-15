"""Firecracker engine pieces that need no KVM: VM config, the work disk round trip, result
mapping and the guest init logic. Booting real microVMs is in test_engines.py (`-m microvm`)."""

import base64
import json
import os
from pathlib import Path

import pytest
from agent_contracts.models import SandboxLimits, SandboxRequest, SandboxStatus
from sandbox_runner import guest_init
from sandbox_runner.engines import FirecrackerSandbox

REQ = SandboxRequest(
    code="print('hi')",
    files={"data.txt": base64.b64encode(b"abc").decode()},
    limits=SandboxLimits(memory_mb=128, cpus=1.5, wall_seconds=4, disk_mb=32),
)


def test_vm_has_no_network_and_a_read_only_root(tmp_path: Path) -> None:
    cfg = FirecrackerSandbox(assets_dir=str(tmp_path)).vm_config(REQ, tmp_path / "work.ext4")
    assert "network-interfaces" not in cfg
    root, work = cfg["drives"]  # type: ignore[misc]
    assert root["is_root_device"]
    assert root["is_read_only"]
    assert not work["is_root_device"]
    assert not work["is_read_only"]
    assert cfg["machine-config"] == {"vcpu_count": 2, "mem_size_mib": 128 + 160}
    assert "init=/sbin/agent-init" in cfg["boot-source"]["boot_args"]  # type: ignore[index]


def test_refuses_without_kvm_or_assets(tmp_path: Path) -> None:
    sb = FirecrackerSandbox(assets_dir=str(tmp_path), kvm=str(tmp_path / "kvm"))
    assert not sb.available()
    problems = " ".join(sb.missing())
    assert "needs KVM" in problems
    assert "vmlinux is missing" in problems


async def test_refusal_is_a_status(tmp_path: Path) -> None:
    sb = FirecrackerSandbox(assets_dir=str(tmp_path), kvm=str(tmp_path / "kvm"))
    result = await sb.run(REQ)
    assert result.status is SandboxStatus.REFUSED
    assert "refusing to run without a microVM" in result.reasons[0]


def test_work_disk_round_trip(tmp_path: Path) -> None:
    image = FirecrackerSandbox.write_work_image(REQ, tmp_path)
    assert image.stat().st_size == 32 * 1024 * 1024
    back = FirecrackerSandbox.read_work_image(image, tmp_path)
    assert back == {}  # nothing ran: no result, no outputs
    work = tmp_path / "work"
    assert (work / "in" / "main.py").read_text() == "print('hi')"
    assert (work / "in" / "data.txt").read_bytes() == b"abc"
    assert json.loads((work / "limits.json").read_text())["memory_mb"] == 128


def test_guest_init_runs_program_and_reports(tmp_path: Path) -> None:
    """The PID-1 logic, without mounts or reboot (AGENT_INIT_TEST mode)."""
    work = tmp_path
    (work / "in").mkdir()
    (work / "in" / "main.py").write_text(
        "import sys\nprint('out')\nprint('err', file=sys.stderr)\n"
        "open('/dev/null')\nopen('../out/r.txt', 'w').write('R')\nraise SystemExit(5)\n"
    )
    (work / "limits.json").write_text(json.dumps({"wall_seconds": 5, "memory_mb": 64, "pids": 16}))
    result = guest_init.run(work, test_mode=True)
    assert result["exit"] == 5
    assert result["timed_out"] is False
    assert (work / "stdout").read_text() == "out\n"
    assert (work / "stderr").read_text() == "err\n"
    assert (work / "out" / "r.txt").read_text() == "R"
    assert json.loads((work / "result.json").read_text())["exit"] == 5


def test_guest_init_enforces_the_wall_clock(tmp_path: Path) -> None:
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "main.py").write_text("import time\ntime.sleep(60)\n")
    (tmp_path / "limits.json").write_text(
        json.dumps({"wall_seconds": 1, "memory_mb": 64, "pids": 16})
    )
    result = guest_init.run(tmp_path, test_mode=True)
    assert result["timed_out"] is True
    assert result["exit"] == 137


@pytest.mark.parametrize(
    ("info", "status"),
    [
        ({"exit": 0}, SandboxStatus.OK),
        ({"exit": 3}, SandboxStatus.ERROR),
        ({"exit": 137, "timed_out": True}, SandboxStatus.TIMEOUT),
        ({"exit": 137, "oom": True}, SandboxStatus.OOM),
    ],
)
def test_result_mapping(info: dict[str, object], status: SandboxStatus) -> None:
    members = {
        "result.json": json.dumps(info).encode(),
        "stdout": b"x" * 10,
        "out/a.txt": b"A",
    }
    r = FirecrackerSandbox()._result(REQ, members, b"", killed_outside=False, duration=5)
    assert r.status is status
    assert r.files == {"a.txt": base64.b64encode(b"A").decode()}
    assert r.engine == "firecracker"


def test_no_result_means_killed_with_a_reason() -> None:
    r = FirecrackerSandbox()._result(REQ, {}, b"Kernel panic", killed_outside=False, duration=5)
    assert r.status is SandboxStatus.KILLED
    assert "Kernel panic" in r.reasons[0]


def test_output_is_capped() -> None:
    req = REQ.model_copy(update={"limits": REQ.limits.model_copy(update={"output_bytes": 4})})
    members = {"result.json": b'{"exit": 0}', "stdout": b"0123456789"}
    r = FirecrackerSandbox()._result(req, members, b"", killed_outside=False, duration=1)
    assert r.stdout == "0123"
    assert r.truncated


def test_init_has_the_python_shebang_used_in_the_rootfs() -> None:
    first = Path(guest_init.__file__).read_text().splitlines()[0]
    assert first == "#!/usr/local/bin/python3 -I"
    assert os.access(guest_init.__file__, os.R_OK)
