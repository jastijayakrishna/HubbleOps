from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Ran:
    command: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    seconds: float
    timed_out: bool

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def tail(self, limit: int = 4000) -> str:
        text = (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()
        if len(text) <= limit:
            return text
        return text[-limit:]

    def both_ends(self, limit: int = 1200) -> str:
        text = (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()
        if len(text) <= limit:
            return text
        half = limit // 2
        return f"{text[:half]}\n...[{len(text) - limit} characters elided]...\n{text[-half:]}"


class BudgetExhausted(Exception):
    def __init__(self, stage: str, spent: float, allowed: float) -> None:
        super().__init__(f"{stage}: budget exhausted after {spent:.1f}s of {allowed:.1f}s")
        self.stage = stage
        self.spent = spent
        self.allowed = allowed


class Budget:
    def __init__(self, seconds: float) -> None:
        self._allowed = seconds
        self._start = time.monotonic()

    @property
    def spent(self) -> float:
        return time.monotonic() - self._start

    @property
    def remaining(self) -> float:
        return max(0.0, self._allowed - self.spent)

    @property
    def allowed(self) -> float:
        return self._allowed

    def claim(self, stage: str, want: float) -> float:
        left = self.remaining
        if left <= 1.0:
            raise BudgetExhausted(stage, self.spent, self._allowed)
        return min(want, left)


def run(
    command: tuple[str, ...],
    *,
    cwd: Path,
    timeout: float,
    env: dict[str, str] | None = None,
) -> Ran:
    merged = dict(os.environ)
    if env is not None:
        merged.update(env)
    started = time.monotonic()
    try:
        done = subprocess.run(
            list(command),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=max(1.0, timeout),
            env=merged,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as expired:
        return Ran(
            command=command,
            exit_code=124,
            stdout=_decode(expired.stdout),
            stderr=_decode(expired.stderr),
            seconds=time.monotonic() - started,
            timed_out=True,
        )
    except OSError as error:
        return Ran(
            command=command,
            exit_code=127,
            stdout="",
            stderr=str(error),
            seconds=time.monotonic() - started,
            timed_out=False,
        )
    return Ran(
        command=command,
        exit_code=done.returncode,
        stdout=done.stdout or "",
        stderr=done.stderr or "",
        seconds=time.monotonic() - started,
        timed_out=False,
    )


def _decode(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def shell_words(line: str) -> tuple[str, ...]:
    out: list[str] = []
    current = ""
    quote = ""
    for char in line:
        if quote:
            if char == quote:
                quote = ""
            else:
                current += char
            continue
        if char in ("'", '"'):
            quote = char
            continue
        if char.isspace():
            if current:
                out.append(current)
                current = ""
            continue
        current += char
    if current:
        out.append(current)
    return tuple(out)
