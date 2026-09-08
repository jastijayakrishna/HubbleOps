from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time
from collections.abc import Mapping
from typing import Any, BinaryIO, cast

from hubbleops.core.canonical import canonical_bytes
from hubbleops.core.errors import ToolingMissing

MAX_COMMAND_LOG_BYTES = 65_536
HOST_ENVIRONMENT_KEYS = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")


def bounded_process(
    argv: tuple[str, ...],
    wall_seconds: float,
    output_bytes: int,
    tool: str,
    environment: Mapping[str, str] | None = None,
) -> tuple[str, int | None, bytes, bytes]:
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**host_process_environment(), **dict(environment or {})},
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


def host_process_environment() -> dict[str, str]:
    return {key: os.environ[key] for key in HOST_ENVIRONMENT_KEYS if key in os.environ}


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


__all__ = [
    "MAX_COMMAND_LOG_BYTES",
    "bounded_process",
    "command_record",
    "command_transcript",
    "host_process_environment",
]
