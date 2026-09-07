from __future__ import annotations

import os
import shutil
import stat
import subprocess
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hubbleops.core.errors import HubbleOpsError, ToolingFailed, ToolingMissing
from hubbleops.sandbox.runner import command_record

RELEASE_SECONDS = 15.0
RELEASE_INTERVAL = 0.2


class WorktreeInvalid(HubbleOpsError):
    pass


def remove_tree(path: Path) -> None:
    def writable(action: Callable[..., object], target: str, error: BaseException) -> None:
        os.chmod(target, stat.S_IWRITE)
        action(target)

    with suppress(OSError):
        shutil.rmtree(path, onexc=writable)


@dataclass(slots=True)
class DetachedWorktree:
    repository: Path
    destination: Path
    sha: str
    git_executable: str = "git"
    _created: bool = False
    _metadata: Path | None = None
    _before: tuple[str, ...] = ()
    _created_entries: tuple[str, ...] = ()
    _after_cleanup: tuple[str, ...] = ()
    _completed: bool = False
    _recovered: bool = False
    _commands: list[dict[str, Any]] = field(
        default_factory=lambda: list[dict[str, Any]](), init=False
    )

    def __enter__(self) -> Path:
        repository = self.repository.resolve()
        destination = self.destination.resolve()
        if destination.exists():
            raise WorktreeInvalid(f"worktree destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        common = Path(self._git("rev-parse", "--path-format=absolute", "--git-common-dir").strip())
        if not common.is_absolute():
            common = (repository / common).resolve()
        metadata = (common / "worktrees").resolve()
        if metadata.parent != common:
            raise WorktreeInvalid("git worktree metadata escaped the git common directory")
        self._metadata = metadata
        self._before = self._entries(metadata)
        resolved_sha = self._git("rev-parse", f"{self.sha}^{{commit}}").strip()
        self._git("worktree", "add", "--detach", str(destination), resolved_sha)
        self._created = True
        after = self._entries(metadata)
        self._created_entries = tuple(sorted(set(after) - set(self._before)))
        if len(self._created_entries) != 1 or not set(self._before) <= set(after):
            self._git("worktree", "remove", "--force", str(destination))
            self._created = False
            raise WorktreeInvalid("Git changed unexpected worktree metadata entries")
        return destination

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._created:
            self._release()
            self._created = False
        if self._metadata is not None:
            self._after_cleanup = self._entries(self._metadata)
            if self._after_cleanup != self._before:
                raise WorktreeInvalid("Git worktree metadata did not return to its prior state")
        self._completed = True

    def _release(self) -> None:
        destination = self.destination.resolve()
        deadline = time.monotonic() + RELEASE_SECONDS
        while True:
            try:
                self._git("worktree", "remove", "--force", str(destination))
                return
            except ToolingFailed:
                if time.monotonic() >= deadline:
                    break
                time.sleep(RELEASE_INTERVAL)
        self._recovered = True
        deadline = time.monotonic() + RELEASE_SECONDS
        while True:
            remove_tree(destination)
            with suppress(ToolingFailed):
                self._git("worktree", "prune")
            if self._metadata is None:
                return
            residue = sorted(set(self._entries(self._metadata)) - set(self._before))
            if not residue:
                return
            for name in residue:
                remove_tree(self._metadata / name)
            if time.monotonic() >= deadline:
                return
            time.sleep(RELEASE_INTERVAL)

    def attestation(self) -> dict[str, Any]:
        if self._metadata is None or not self._completed:
            raise WorktreeInvalid("worktree lifecycle has not completed")
        return {
            "after_cleanup": list(self._after_cleanup),
            "before": list(self._before),
            "created": list(self._created_entries),
            "commands": list(self._commands),
            "metadata_root": str(self._metadata),
            "recovered": self._recovered,
        }

    @staticmethod
    def _entries(metadata: Path) -> tuple[str, ...]:
        if not metadata.exists():
            return ()
        return tuple(sorted(path.name for path in metadata.iterdir() if path.is_dir()))

    def _git(self, *arguments: str) -> str:
        argv = (self.git_executable, "-C", str(self.repository.resolve()), *arguments)
        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
        except FileNotFoundError as error:
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
            raise ToolingMissing("git", f"{self.git_executable!r} is not executable") from error
        except subprocess.TimeoutExpired as error:
            self._commands.append(
                command_record(
                    argv,
                    time.monotonic() - started,
                    None,
                    error.stdout or b"",
                    error.stderr or b"",
                    "WALL_TIMEOUT",
                )
            )
            raise ToolingFailed("git", "worktree command timed out") from error
        self._commands.append(
            command_record(
                argv,
                time.monotonic() - started,
                completed.returncode,
                completed.stdout,
                completed.stderr,
                "COMPLETED" if completed.returncode == 0 else "NONZERO_EXIT",
            )
        )
        if completed.returncode != 0:
            raise ToolingFailed("git", completed.stderr.strip() or "worktree command failed")
        return completed.stdout


__all__ = ["DetachedWorktree", "WorktreeInvalid", "remove_tree"]
