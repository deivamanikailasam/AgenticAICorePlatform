#!/usr/local/bin/python3 -I
"""PID 1 inside a Firecracker sandbox microVM (installed as ``/sbin/agent-init`` in the rootfs).

The VM has no network device. Its root filesystem is read-only; the second disk (``/dev/vdb``)
is the per-run work disk the host prepared::

    /limits.json   {"wall_seconds", "memory_mb", "pids"}
    /in/main.py    the program, plus input files
    /out/          files the program writes come back from here

The program runs as nobody (65534) in its own session, under a cgroup (memory and process
limits) when the kernel offers one and process/file rlimits always, with a wall-clock limit.
``/result.json`` records the exit code, whether the time limit or the memory limit hit, then
the VM reboots, which ends the Firecracker process on the host.

``AGENT_INIT_TEST=<dir>`` runs the same logic against a directory without mounting or
rebooting, so the guest behaviour can be tested in an ordinary container.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path

NOBODY = 65534
CGROUP = Path("/sys/fs/cgroup/sandbox")


def _run(*argv: str) -> None:
    # pipes, not /dev/null: /dev may not be mounted yet
    subprocess.run(argv, check=False, capture_output=True)  # noqa: S603 - fixed argv


def _boot() -> Path:
    os.environ["PATH"] = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    _run("mount", "-t", "proc", "proc", "/proc")
    _run("mount", "-t", "sysfs", "sysfs", "/sys")
    if not Path("/dev/null").exists():  # the kernel may have mounted devtmpfs already
        _run("mount", "-t", "devtmpfs", "devtmpfs", "/dev")
    _run("mount", "-t", "tmpfs", "-o", "size=16m,mode=1777", "tmpfs", "/tmp")  # noqa: S108 - the guest's own tmpfs
    _run("mount", "-t", "cgroup2", "cgroup2", "/sys/fs/cgroup")
    _run("mount", "-t", "ext4", "-o", "nosuid,nodev", "/dev/vdb", "/work")
    return Path("/work")


def _oom_kills() -> int:
    for line in (
        Path("/proc/vmstat").read_text().splitlines() if Path("/proc/vmstat").exists() else []
    ):
        name, _, value = line.partition(" ")
        if name == "oom_kill":
            return int(value)
    return 0


def _cgroup(memory_mb: int, pids: int) -> bool:
    """A cgroup v2 group with memory and pids limits; False if the kernel has none."""
    try:
        Path("/sys/fs/cgroup/cgroup.subtree_control").write_text("+memory +pids")
        CGROUP.mkdir(exist_ok=True)
        (CGROUP / "memory.max").write_text(str(memory_mb * 1024 * 1024))
        (CGROUP / "memory.swap.max").write_text("0")
        (CGROUP / "pids.max").write_text(str(pids))
        return True
    except OSError:
        return False


def _cgroup_oom() -> bool:
    try:
        events = (CGROUP / "memory.events").read_text()
    except OSError:
        return False
    return any(
        line.split()[0] == "oom_kill" and int(line.split()[1]) > 0 for line in events.splitlines()
    )


def run(work: Path, test_mode: bool = False) -> dict[str, object]:
    limits = json.loads((work / "limits.json").read_text())
    wall, memory_mb, pids = (
        float(limits["wall_seconds"]),
        int(limits["memory_mb"]),
        int(limits["pids"]),
    )
    out = work / "out"
    out.mkdir(exist_ok=True)
    if os.geteuid() == 0:
        os.chown(out, NOBODY, NOBODY)
    in_cgroup = False if test_mode else _cgroup(memory_mb, pids)
    oom_before = _oom_kills()

    def drop() -> None:
        os.setsid()
        if in_cgroup:
            (CGROUP / "cgroup.procs").write_text(str(os.getpid()))
        resource.setrlimit(resource.RLIMIT_NPROC, (pids, pids))
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if os.geteuid() == 0 and not test_mode:
            os.setgroups([])
            os.setgid(NOBODY)
            os.setuid(NOBODY)

    timed_out = False
    with open(work / "stdout", "wb") as so, open(work / "stderr", "wb") as se:
        proc = subprocess.Popen(  # noqa: S603 - fixed argv inside the sandbox
            [sys.executable if test_mode else "/usr/local/bin/python3", "-I", "-B", "main.py"],
            cwd=work / "in",
            stdin=subprocess.DEVNULL,
            stdout=so,
            stderr=se,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": "/tmp",  # noqa: S108 - the guest's own tmpfs
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            preexec_fn=drop,
        )
        try:
            code = proc.wait(timeout=wall)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            code = proc.wait()
    # anything the program left behind dies with it
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    result: dict[str, object] = {
        "exit": 128 - code if code < 0 else code,  # killed by signal N -> 128+N, like a shell
        "timed_out": timed_out,
        "oom": _cgroup_oom() or _oom_kills() > oom_before,
        "cgroup": in_cgroup,
    }
    (work / "result.json").write_text(json.dumps(result))
    return result


def main() -> None:
    test_dir = os.environ.get("AGENT_INIT_TEST")
    if test_dir:
        run(Path(test_dir), test_mode=True)
        return
    started = time.monotonic()
    try:
        work = _boot()
        result = run(work)
        result["guest_seconds"] = round(time.monotonic() - started, 3)
        (work / "result.json").write_text(json.dumps(result))
    finally:
        os.sync()
        _run("umount", "/work")
        libc = ctypes.CDLL(None, use_errno=True)
        libc.reboot(0x01234567)  # LINUX_REBOOT_CMD_RESTART: Firecracker exits on guest reboot


if __name__ == "__main__":
    main()
