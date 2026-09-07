from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, cast

from hubbleops.core.canonical import canonical_bytes, content_id
from hubbleops.core.errors import HubbleOpsError, ToolingFailed, ToolingMissing
from hubbleops.sandbox.image import ImageSpec
from hubbleops.sandbox.limits import ResourceLimits
from hubbleops.sandbox.mounts import Mount, validate_mounts

SAFE_ENV = {"HOME": "/tmp/home", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC"}
MAX_COMMAND_LOG_BYTES = 65_536


class SandboxInvalid(HubbleOpsError):
    pass


@dataclass(frozen=True, slots=True)
class RunSpec:
    image: ImageSpec
    command: str
    mounts: tuple[Mount, ...]
    allowed_mount_roots: tuple[Path, ...]
    limits: ResourceLimits
    artifact_dir: Path
    container_workdir: str = "/workspace"
    network: str = "none"
    environment: tuple[tuple[str, str], ...] = ()
    name: str | None = None

    def __post_init__(self) -> None:
        if not self.command.strip() or "\x00" in self.command:
            raise SandboxInvalid("sandbox command must be non-empty and contain no NUL")
        if not self.container_workdir.startswith("/"):
            raise SandboxInvalid("container working directory must be absolute")
        if self.network != "none" and not self.network.startswith("hops-"):
            raise SandboxInvalid("sandbox network must be none or a per-run hops network")
        if self.name is not None and not self.name.startswith("hops-"):
            raise SandboxInvalid("container name must be a per-run hops name")
        offered = {key for key, _ in self.environment}
        forbidden = [
            key
            for key in offered
            if any(
                token in key.upper()
                for token in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
            )
        ]
        if forbidden:
            raise SandboxInvalid(f"credential-like environment names are forbidden: {forbidden}")
        validate_mounts(self.mounts, self.allowed_mount_roots)

    def fingerprint(self) -> str:
        return content_id(
            {
                "image": self.image.fingerprint(),
                "command": self.command,
                "mounts": [mount.mapping() for mount in sorted(self.mounts)],
                "limits": self.limits.fingerprint(),
                "container_workdir": self.container_workdir,
                "network": self.network,
                "environment": list(sorted(self.environment)),
                "name": self.name,
            }
        )


@dataclass(frozen=True, slots=True)
class RunResult:
    argv: tuple[str, ...]
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    duration_seconds: float
    outcome: str
    log_path: Path


class RootlessPodman:
    def __init__(self, prefix: tuple[str, ...] = ("wsl.exe", "-d", "Ubuntu", "--", "podman")):
        self.prefix = prefix
        self._commands: list[dict[str, Any]] = []

    def identity(self) -> str:
        completed = self._check((*self.prefix, "info", "--format", "json"))
        try:
            document = cast(dict[str, Any], json.loads(completed.stdout))
            host = cast(dict[str, Any], document["host"])
            security = cast(dict[str, Any], host["security"])
            mappings = cast(dict[str, Any], host["idMappings"])
            uidmap = cast(list[object], mappings["uidmap"])
            gidmap = cast(list[object], mappings["gidmap"])
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise SandboxInvalid("selected container engine returned no security report") from error
        if security.get("rootless") is not True:
            raise SandboxInvalid("selected container engine is not rootless")
        if host.get("os") != "linux" or host.get("serviceIsRemote") is not False:
            raise SandboxInvalid("selected container engine must be local Linux")
        if security.get("seccompEnabled") is not True:
            raise SandboxInvalid("selected container engine must enforce seccomp")
        if len(uidmap) < 2 or len(gidmap) < 2:
            raise SandboxInvalid("selected rootless engine has no subordinate uid/gid mapping")
        version = self._check((*self.prefix, "version", "--format", "{{.Client.Version}}"))
        return (
            f"podman={version.stdout.strip()};os=linux;remote=false;seccomp=true;"
            f"cgroup={host.get('cgroupVersion')};uidmap={len(uidmap)};gidmap={len(gidmap)};"
            "rootless=true"
        )

    def command(self, spec: RunSpec) -> tuple[str, ...]:
        arguments = [
            *self.prefix,
            "run",
            "--rm",
            "--pull=never",
            "--user",
            spec.image.user,
            "--cap-drop=all",
            "--security-opt=no-new-privileges",
            "--network",
            spec.network,
            "--read-only",
            "--no-hosts",
            "--workdir",
            spec.container_workdir,
            "--pids-limit",
            str(spec.limits.processes),
            "--memory",
            str(spec.limits.memory_bytes),
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--tmpfs",
            "/tmp/home:rw,noexec,nosuid,nodev,size=8m",
        ]
        if spec.name is not None:
            arguments.extend(("--name", spec.name))
        if spec.image.entrypoint is not None:
            arguments.extend(("--entrypoint", spec.image.entrypoint))
        for key, value in sorted({**SAFE_ENV, **dict(spec.environment)}.items()):
            arguments.extend(("--env", f"{key}={value}"))
        for mount in sorted(spec.mounts):
            arguments.extend(("--volume", mount.argument()))
        arguments.extend((spec.image.reference, "sh", "-ceu", spec.limits.shell(spec.command)))
        return tuple(arguments)

    def run(self, spec: RunSpec) -> RunResult:
        self.identity()
        artifact_dir = spec.artifact_dir.resolve()
        artifact_dir.mkdir(parents=True, exist_ok=True)
        argv = self.command(spec)
        started = time.monotonic()
        try:
            outcome, exit_code, stdout, stderr = bounded_process(
                argv, spec.limits.wall_seconds, spec.limits.output_bytes, "podman"
            )
        except ToolingMissing as error:
            self._commands.append(
                command_record(
                    argv,
                    time.monotonic() - started,
                    None,
                    b"",
                    str(error),
                    "TOOL_MISSING",
                )
            )
            raise
        duration = time.monotonic() - started
        log = {
            "argv": list(argv),
            "duration_seconds": round(duration, 6),
            "exit_code": exit_code,
            "outcome": outcome,
            "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
            "stderr_size": len(stderr),
            "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
            "stdout_size": len(stdout),
        }
        path = artifact_dir / "command.json"
        _exclusive_write(path, canonical_bytes(log) + b"\n")
        self._commands.append(command_record(argv, duration, exit_code, stdout, stderr, outcome))
        return RunResult(argv, exit_code, stdout, stderr, duration, outcome, path)

    def transcript(self) -> bytes:
        return command_transcript(self._commands)

    def _check(self, argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        started = time.monotonic()
        try:
            outcome, exit_code, stdout, stderr = bounded_process(argv, 15, 65_536, "podman")
        except ToolingMissing as error:
            self._commands.append(
                command_record(
                    argv,
                    time.monotonic() - started,
                    None,
                    b"",
                    str(error),
                    "TOOL_MISSING",
                )
            )
            raise ToolingMissing("podman", f"{self.prefix[0]!r} is not executable") from error
        self._commands.append(
            command_record(argv, time.monotonic() - started, exit_code, stdout, stderr, outcome)
        )
        if outcome == "WALL_TIMEOUT":
            raise ToolingFailed("podman", "engine identity check timed out")
        if outcome == "OUTPUT_LIMIT":
            raise ToolingFailed("podman", "engine identity check exceeded its output bound")
        if exit_code != 0:
            detail = stderr.decode("utf-8", errors="replace").strip() or "engine check failed"
            raise ToolingFailed("podman", detail)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )


def bounded_process(
    argv: tuple[str, ...], wall_seconds: float, output_bytes: int, tool: str
) -> tuple[str, int | None, bytes, bytes]:
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_host_process_environment(),
        )
    except FileNotFoundError as error:
        raise ToolingMissing(tool, f"{argv[0]!r} is not executable") from error
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    overflow = threading.Event()
    threads = [
        threading.Thread(
            target=_read_bounded,
            args=(cast(BinaryIO, stream), streams[name], output_bytes, overflow),
            daemon=True,
        )
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + wall_seconds
    outcome = "COMPLETED"
    while process.poll() is None:
        if overflow.is_set():
            outcome = "OUTPUT_LIMIT"
            process.terminate()
            break
        if time.monotonic() >= deadline:
            outcome = "WALL_TIMEOUT"
            process.terminate()
            break
        time.sleep(0.01)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=2)
    if outcome == "COMPLETED" and process.returncode != 0:
        outcome = "NONZERO_EXIT"
    return outcome, process.returncode, bytes(streams["stdout"]), bytes(streams["stderr"])


def _read_bounded(
    stream: BinaryIO, target: bytearray, maximum: int, overflow: threading.Event
) -> None:
    while True:
        chunk = stream.read(65_536)
        if not chunk:
            return
        available = maximum - len(target)
        if available > 0:
            target.extend(chunk[:available])
        if len(chunk) > available:
            overflow.set()


def _host_process_environment() -> dict[str, str]:
    allowed = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
    return {key: os.environ[key] for key in allowed if key in os.environ}


def _exclusive_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as error:
        raise SandboxInvalid(f"capture artifact already exists: {path}") from error


def command_record(
    argv: tuple[str, ...],
    duration_seconds: float,
    exit_code: int | None,
    stdout: str | bytes,
    stderr: str | bytes,
    outcome: str,
) -> dict[str, Any]:
    stdout_bytes = stdout.encode("utf-8", errors="replace") if isinstance(stdout, str) else stdout
    stderr_bytes = stderr.encode("utf-8", errors="replace") if isinstance(stderr, str) else stderr
    return {
        "argv": list(argv),
        "duration_seconds": round(duration_seconds, 6),
        "exit_code": exit_code,
        "outcome": outcome,
        "stderr": stderr_bytes[:MAX_COMMAND_LOG_BYTES].decode("utf-8", errors="replace"),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "stderr_size": len(stderr_bytes),
        "stderr_truncated": len(stderr_bytes) > MAX_COMMAND_LOG_BYTES,
        "stdout": stdout_bytes[:MAX_COMMAND_LOG_BYTES].decode("utf-8", errors="replace"),
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "stdout_size": len(stdout_bytes),
        "stdout_truncated": len(stdout_bytes) > MAX_COMMAND_LOG_BYTES,
    }


def command_transcript(records: list[dict[str, Any]]) -> bytes:
    return b"".join(canonical_bytes(record) + b"\n" for record in records)


__all__ = [
    "RootlessPodman",
    "RunResult",
    "RunSpec",
    "SandboxInvalid",
    "bounded_process",
    "command_record",
    "command_transcript",
]
