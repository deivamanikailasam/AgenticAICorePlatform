"""Code sandbox engines.

``FirecrackerSandbox`` boots a microVM per request (below). ``GvisorSandbox`` (default) and
``KataSandbox`` run each request in a fresh container through
Docker with a hardened profile; they differ only in the OCI runtime (``runsc`` vs
``kata-runtime``), i.e. the kernel boundary: gVisor's user-space kernel or a lightweight VM.

Hardening for every run: no network, read-only root filesystem, tmpfs work area, no host
mounts, all capabilities dropped, ``no-new-privileges``, an unprivileged user, CPU, memory, swap
and PID limits, a wall-clock timeout enforced both inside and outside the container, and capped
output. Files go in and out as a tar stream on stdin/stdout — the container never sees a host
path. The engine refuses to run if its OCI runtime is not installed; it never falls back to
plain ``runc``.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import math
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid
from pathlib import Path
from typing import ClassVar

from agent_contracts.interfaces import CodeSandbox
from agent_contracts.models import SandboxRequest, SandboxResult, SandboxStatus

DEFAULT_IMAGE = "docker.io/library/python:3.12-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1"
GRACE_SECONDS = 15.0

# Runs inside the sandbox. The user's program is /work/in/main.py; its streams go to files so
# the only thing written to the container's stdout is the final tar of the results.
WRAPPER = r"""
set -u
mkdir -p /work/in /work/out
tar -x -C /work/in
cd /work/in
timeout -s KILL {wall}s python3 -I -B main.py > /work/stdout 2> /work/stderr
echo $? > /work/exit
head -c {cap} /work/stdout > /work/stdout.cap; head -c {cap} /work/stderr > /work/stderr.cap
wc -c < /work/stdout > /work/stdout.size; wc -c < /work/stderr > /work/stderr.size
tar -c -C /work stdout.cap stderr.cap stdout.size stderr.size exit out
"""


def _tar_inputs(request: SandboxRequest) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        entries = {
            "main.py": request.code.encode(),
            **{n: base64.b64decode(v) for n, v in request.files.items()},
        }
        for name, data in entries.items():
            if "/" in name or name.startswith(".") or name == "":
                raise ValueError(f"invalid input file name {name!r}")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise FileNotFoundError(f"{name} not found (e2fsprogs)")
    return path


class _DockerSandbox(CodeSandbox):
    runtime: ClassVar[str] = ""
    engine_name: ClassVar[str] = ""

    def __init__(self, image: str = DEFAULT_IMAGE, docker: str = "docker") -> None:
        self.image = image
        self.docker = docker

    def available(self) -> bool:
        if shutil.which(self.docker) is None:
            return False
        try:
            out = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [self.docker, "info", "--format", "{{json .Runtimes}}"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return out.returncode == 0 and f'"{self.runtime}"' in out.stdout

    def _argv(self, name: str, request: SandboxRequest) -> list[str]:
        lim = request.limits
        wrapper = WRAPPER.format(wall=max(1, int(lim.wall_seconds)), cap=lim.output_bytes)
        return [
            self.docker,
            "run",
            "--name",
            name,
            "--interactive",
            f"--runtime={self.runtime}",
            "--network=none",
            "--read-only",
            f"--tmpfs=/work:rw,size={lim.disk_mb}m,mode=1777",
            "--tmpfs=/tmp:rw,size=16m,mode=1777",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--user=65534:65534",
            f"--memory={lim.memory_mb}m",
            f"--memory-swap={lim.memory_mb}m",
            f"--cpus={lim.cpus}",
            f"--pids-limit={lim.pids}",
            "--ulimit=nofile=256:256",
            "--env=PYTHONDONTWRITEBYTECODE=1",
            "--label=agent-platform.sandbox=1",
            self.image,
            "sh",
            "-c",
            wrapper,
        ]

    async def _inspect(self, name: str) -> tuple[bool, int | None]:
        proc = await asyncio.create_subprocess_exec(
            self.docker,
            "inspect",
            "--format",
            "{{.State.OOMKilled}} {{.State.ExitCode}}",
            name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        parts = out.decode().split()
        return (parts[0] == "true", int(parts[1])) if len(parts) == 2 else (False, None)

    async def _remove(self, name: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            self.docker,
            "rm",
            "-f",
            name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

    async def run(self, request: SandboxRequest) -> SandboxResult:
        if not self.available():
            return SandboxResult(
                status=SandboxStatus.REFUSED,
                engine=self.engine_name,
                reasons=(
                    f"OCI runtime '{self.runtime}' is not installed; refusing to run without isolation",
                ),
            )
        try:
            stdin = _tar_inputs(request)
        except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
            return SandboxResult(
                status=SandboxStatus.REFUSED, reasons=(str(exc),), engine=self.engine_name
            )
        name = f"agent-sbx-{uuid.uuid4().hex[:16]}"
        started = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            *self._argv(name, request),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timed_out_outside = False
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(stdin), request.limits.wall_seconds + GRACE_SECONDS
            )
        except TimeoutError:
            timed_out_outside = True
            kill = await asyncio.create_subprocess_exec(
                self.docker,
                "kill",
                name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await kill.wait()
            out, err = await proc.communicate()
        try:
            oom, container_exit = await self._inspect(name)
        finally:
            await self._remove(name)
        duration = int((time.perf_counter() - started) * 1000)
        return self._result(request, out, err, oom, container_exit, timed_out_outside, duration)

    def _result(
        self,
        request: SandboxRequest,
        out: bytes,
        err: bytes,
        oom: bool,
        container_exit: int | None,
        timed_out_outside: bool,
        duration: int,
    ) -> SandboxResult:
        engine, ms = self.engine_name, duration
        if oom:
            return SandboxResult(
                status=SandboxStatus.OOM,
                reasons=(f"memory limit {request.limits.memory_mb} MiB reached",),
                engine=engine,
                duration_ms=ms,
            )
        if timed_out_outside:
            return SandboxResult(
                status=SandboxStatus.TIMEOUT,
                reasons=("killed by the runner after the wall-clock limit",),
                engine=engine,
                duration_ms=ms,
            )
        try:
            members = self._untar(out)
        except (tarfile.TarError, EOFError):
            # The wrapper itself could not report (it needs to fork `tar`): the code exhausted a
            # resource limit — processes, memory or disk — or broke the result stream.
            detail = err.decode(errors="replace").strip()[-300:]
            reasons = (
                "the sandbox produced no result: the code likely exhausted a resource limit "
                f"(processes {request.limits.pids}, memory {request.limits.memory_mb} MiB or disk "
                f"{request.limits.disk_mb} MiB); container exit {container_exit}"
                + (f"; {detail}" if detail else ""),
            )
            return SandboxResult(
                status=SandboxStatus.KILLED,
                exit_code=container_exit,
                reasons=reasons,
                engine=engine,
                duration_ms=ms,
            )
        exit_code = int(members.get("exit", b"1").decode().strip() or 1)
        stdout = members.get("stdout.cap", b"").decode(errors="replace")
        stderr = members.get("stderr.cap", b"").decode(errors="replace")
        sizes = [
            int(members.get(k, b"0").decode().strip() or 0) for k in ("stdout.size", "stderr.size")
        ]
        truncated = any(s > request.limits.output_bytes for s in sizes)
        files = {
            k.removeprefix("out/"): base64.b64encode(v).decode()
            for k, v in members.items()
            if k.startswith("out/")
        }
        why: tuple[str, ...] = ()
        if exit_code == 137:
            status = SandboxStatus.TIMEOUT
            why = (f"wall-clock limit {request.limits.wall_seconds}s reached",)
        elif exit_code == 0:
            status = SandboxStatus.OK
        else:
            status = SandboxStatus.ERROR
            why = (f"exit code {exit_code}",)
        return SandboxResult(
            status=status,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            truncated=truncated,
            files=files,
            reasons=why,
            engine=engine,
            duration_ms=ms,
        )

    @staticmethod
    def _untar(data: bytes) -> dict[str, bytes]:
        found: dict[str, bytes] = {}
        total = 0
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
            for m in tar.getmembers():
                if not m.isfile() or m.name.startswith("/") or ".." in m.name.split("/"):
                    continue
                f = tar.extractfile(m)
                if f is None:
                    continue
                chunk = f.read(min(m.size, 16 * 1024 * 1024))
                total += len(chunk)
                if total > 64 * 1024 * 1024:
                    break
                found[m.name.removeprefix("./")] = chunk
        if "exit" not in found:
            raise tarfile.TarError("no exit status in sandbox output")
        return found


class GvisorSandbox(_DockerSandbox):
    """gVisor (runsc): a user-space kernel between the code and the host kernel. Default."""

    runtime = "runsc"
    engine_name = "gvisor"


class KataSandbox(_DockerSandbox):
    """Kata Containers: each run in a lightweight VM (needs KVM on the host)."""

    runtime = "kata-runtime"
    engine_name = "kata"


class FirecrackerSandbox(CodeSandbox):
    """Firecracker microVMs: each run boots its own Linux kernel under KVM (ADR 0019, revised).

    Needs ``/dev/kvm``, the ``firecracker`` binary and two assets in ``assets_dir``
    (``scripts/install_firecracker.sh`` and ``scripts/build_sandbox_rootfs.sh``): a pinned
    kernel (``vmlinux``) and a read-only root filesystem built from the same pinned Python image
    as the container engines, with ``guest_init.py`` as PID 1 (``rootfs.ext4``).

    Per run: a fresh ext4 work disk carries the program and input files in and the results out
    (written with ``mkfs.ext4 -d``, read with ``debugfs``: no mounts, no root on the host). The VM
    has no network device. Memory is the VM size plus a cgroup inside it; the wall clock is
    enforced inside the guest and by killing the VM from outside.
    """

    engine_name: ClassVar[str] = "firecracker"
    OVERHEAD_MB: ClassVar[int] = 160  # guest kernel, init and page cache beyond the program's limit
    BOOT_GRACE_SECONDS: ClassVar[float] = 20.0

    def __init__(
        self,
        assets_dir: str | None = None,
        firecracker: str | None = None,
        kvm: str = "/dev/kvm",
    ) -> None:
        self.assets = Path(
            assets_dir
            or os.environ.get("AGENT_FIRECRACKER_DIR", "/var/lib/agent-platform/firecracker")
        )
        self.firecracker = firecracker or os.environ.get("AGENT_FIRECRACKER_BIN", "firecracker")
        self.kvm = kvm

    @property
    def kernel(self) -> Path:
        return self.assets / "vmlinux"

    @property
    def rootfs(self) -> Path:
        return self.assets / "rootfs.ext4"

    def missing(self) -> list[str]:
        problems = []
        if shutil.which(self.firecracker) is None:
            problems.append(f"firecracker binary '{self.firecracker}' not found")
        if not os.access(self.kvm, os.R_OK | os.W_OK):
            problems.append(f"{self.kvm} is not available (needs KVM)")
        for asset in (self.kernel, self.rootfs):
            if not asset.is_file():
                problems.append(f"{asset} is missing")
        for tool in ("mkfs.ext4", "debugfs"):
            if shutil.which(tool) is None:
                problems.append(f"{tool} not found (e2fsprogs)")
        return problems

    def available(self) -> bool:
        return not self.missing()

    def vm_config(self, request: SandboxRequest, work_image: Path) -> dict[str, object]:
        lim = request.limits
        return {
            "boot-source": {
                "kernel_image_path": str(self.kernel),
                "boot_args": "console=ttyS0 quiet loglevel=1 reboot=k panic=1 pci=off "
                "init=/sbin/agent-init",
            },
            "drives": [
                {
                    "drive_id": "rootfs",
                    "path_on_host": str(self.rootfs),
                    "is_root_device": True,
                    "is_read_only": True,
                },
                {
                    "drive_id": "work",
                    "path_on_host": str(work_image),
                    "is_root_device": False,
                    "is_read_only": False,
                },
            ],
            "machine-config": {
                "vcpu_count": max(1, math.ceil(lim.cpus)),
                "mem_size_mib": lim.memory_mb + self.OVERHEAD_MB,
            },
            # no "network-interfaces": the VM has no network device at all
        }

    @staticmethod
    def write_work_image(request: SandboxRequest, root: Path) -> Path:
        work = root / "work"
        (work / "in").mkdir(parents=True)
        (work / "out").mkdir()
        with tarfile.open(fileobj=io.BytesIO(_tar_inputs(request))) as tar:
            for member in tar.getmembers():  # names were validated by _tar_inputs
                f = tar.extractfile(member)
                assert f is not None
                (work / "in" / member.name).write_bytes(f.read())
        lim = request.limits
        (work / "limits.json").write_text(
            json.dumps(
                {"wall_seconds": lim.wall_seconds, "memory_mb": lim.memory_mb, "pids": lim.pids}
            )
        )
        image = root / "work.ext4"
        with open(image, "wb") as fh:
            fh.truncate(max(lim.disk_mb, 16) * 1024 * 1024)
        subprocess.run(  # noqa: S603 - fixed argv
            [_tool("mkfs.ext4"), "-q", "-F", "-E", "root_owner=0:0", "-d", str(work), str(image)],
            check=True,
            capture_output=True,
            timeout=60,
        )
        return image

    @staticmethod
    def read_work_image(image: Path, root: Path) -> dict[str, bytes]:
        """Files the guest left on the work disk, keyed like the container engines' tar."""
        dest = root / "result"
        dest.mkdir()
        found: dict[str, bytes] = {}
        for name in ("result.json", "stdout", "stderr"):
            subprocess.run(  # noqa: S603 - fixed argv
                [_tool("debugfs"), "-R", f"dump /{name} {dest / name}", str(image)],
                check=False,
                capture_output=True,
                timeout=60,
            )
            if (dest / name).is_file():
                found[name] = (dest / name).read_bytes()
        subprocess.run(  # noqa: S603 - fixed argv
            [_tool("debugfs"), "-R", f"rdump /out {dest}", str(image)],
            check=False,
            capture_output=True,
            timeout=60,
        )
        total = 0
        for path in sorted((dest / "out").rglob("*")) if (dest / "out").is_dir() else []:
            if path.is_file() and not path.is_symlink():
                data = path.read_bytes()[: 16 * 1024 * 1024]
                total += len(data)
                if total > 64 * 1024 * 1024:
                    break
                found["out/" + str(path.relative_to(dest / "out"))] = data
        return found

    async def run(self, request: SandboxRequest) -> SandboxResult:
        problems = self.missing()
        if problems:
            return SandboxResult(
                status=SandboxStatus.REFUSED,
                engine=self.engine_name,
                reasons=(f"refusing to run without a microVM: {'; '.join(problems)}",),
            )
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="agent-fc-") as tmp:
            root = Path(tmp)
            try:
                image = await asyncio.to_thread(self.write_work_image, request, root)
            except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
                return SandboxResult(
                    status=SandboxStatus.REFUSED, reasons=(str(exc),), engine=self.engine_name
                )
            config = root / "vm.json"
            config.write_text(json.dumps(self.vm_config(request, image)))
            proc = await asyncio.create_subprocess_exec(
                self.firecracker,
                "--no-api",
                "--config-file",
                str(config),
                "--id",
                f"agent-sbx-{uuid.uuid4().hex[:12]}",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            killed_outside = False
            try:
                console, _ = await asyncio.wait_for(
                    proc.communicate(),
                    request.limits.wall_seconds + GRACE_SECONDS + self.BOOT_GRACE_SECONDS,
                )
            except TimeoutError:
                killed_outside = True
                proc.kill()
                console, _ = await proc.communicate()
            members = await asyncio.to_thread(self.read_work_image, image, root)
        duration = int((time.perf_counter() - started) * 1000)
        return self._result(request, members, console or b"", killed_outside, duration)

    def _result(
        self,
        request: SandboxRequest,
        members: dict[str, bytes],
        console: bytes,
        killed_outside: bool,
        duration: int,
    ) -> SandboxResult:
        engine, ms, lim = self.engine_name, duration, request.limits
        if killed_outside:
            return SandboxResult(
                status=SandboxStatus.TIMEOUT,
                reasons=("microVM killed by the runner after the wall-clock limit",),
                engine=engine,
                duration_ms=ms,
            )
        try:
            info = json.loads(members["result.json"])
        except (KeyError, ValueError):
            tail = console.decode(errors="replace").strip()[-300:]
            return SandboxResult(
                status=SandboxStatus.KILLED,
                reasons=(
                    "the microVM produced no result: the code likely exhausted a resource limit "
                    f"(processes {lim.pids}, memory {lim.memory_mb} MiB or disk {lim.disk_mb} MiB)"
                    + (f"; console: {tail}" if tail else ""),
                ),
                engine=engine,
                duration_ms=ms,
            )
        exit_code = int(info.get("exit", 1))
        stdout, stderr = members.get("stdout", b""), members.get("stderr", b"")
        files = {
            k.removeprefix("out/"): base64.b64encode(v).decode()
            for k, v in members.items()
            if k.startswith("out/")
        }
        common = {
            "exit_code": exit_code,
            "stdout": stdout[: lim.output_bytes].decode(errors="replace"),
            "stderr": stderr[: lim.output_bytes].decode(errors="replace"),
            "truncated": max(len(stdout), len(stderr)) > lim.output_bytes,
            "files": files,
            "engine": engine,
            "duration_ms": ms,
        }
        if info.get("oom"):
            return SandboxResult(
                status=SandboxStatus.OOM,
                reasons=(f"memory limit {lim.memory_mb} MiB reached",),
                **common,  # type: ignore[arg-type]
            )
        if info.get("timed_out"):
            return SandboxResult(
                status=SandboxStatus.TIMEOUT,
                reasons=(f"wall-clock limit {lim.wall_seconds}s reached",),
                **common,  # type: ignore[arg-type]
            )
        if exit_code == 0:
            return SandboxResult(status=SandboxStatus.OK, **common)  # type: ignore[arg-type]
        return SandboxResult(
            status=SandboxStatus.ERROR,
            reasons=(f"exit code {exit_code}",),
            **common,  # type: ignore[arg-type]
        )


class ManagedSandbox(CodeSandbox):
    """A managed sandbox service (vendor API). Documented STUB by decision (ADR 0021): sending
    model-written code and tenant data to a third party needs a named vendor, a data-processing
    agreement and Australian data residency, none of which exist yet."""

    component_status = "stub"

    def available(self) -> bool:
        return False

    async def run(self, request: SandboxRequest) -> SandboxResult:
        return SandboxResult(
            status=SandboxStatus.REFUSED,
            engine="managed",
            reasons=("managed sandbox engine is a documented stub (ADR 0021): no vendor approved",),
        )
