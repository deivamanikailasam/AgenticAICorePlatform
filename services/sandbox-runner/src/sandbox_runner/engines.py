"""Code sandbox engines.

``GvisorSandbox`` (default) and ``KataSandbox`` run each request in a fresh container through
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
import shutil
import subprocess
import tarfile
import time
import uuid
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
    """Firecracker microVMs. STUB: needs KVM, a jailer and a guest image (see ADR 0019)."""

    component_status = "stub"

    def available(self) -> bool:
        return False

    async def run(self, request: SandboxRequest) -> SandboxResult:
        return SandboxResult(
            status=SandboxStatus.REFUSED,
            engine="firecracker",
            reasons=("firecracker engine is a documented stub (ADR 0019): no KVM on this host",),
        )


class ManagedSandbox(CodeSandbox):
    """A managed sandbox service (vendor API). STUB: needs a contract and credentials (ADR 0019)."""

    component_status = "stub"

    def available(self) -> bool:
        return False

    async def run(self, request: SandboxRequest) -> SandboxResult:
        return SandboxResult(
            status=SandboxStatus.REFUSED,
            engine="managed",
            reasons=(
                "managed sandbox engine is a documented stub (ADR 0019): no vendor configured",
            ),
        )
