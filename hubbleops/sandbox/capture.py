from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hubbleops.core.errors import HubbleOpsError, ToolingFailed, ToolingMissing


class WorktreeInvalid(HubbleOpsError):
    pass


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
            self._git("worktree", "remove", "--force", str(self.destination.resolve()))
            self._created = False
        if self._metadata is not None:
            self._after_cleanup = self._entries(self._metadata)
            if self._after_cleanup != self._before:
                raise WorktreeInvalid("Git worktree metadata did not return to its prior state")
        self._completed = True

    def attestation(self) -> dict[str, Any]:
        if self._metadata is None or not self._completed:
            raise WorktreeInvalid("worktree lifecycle has not completed")
        return {
            "after_cleanup": list(self._after_cleanup),
            "before": list(self._before),
            "created": list(self._created_entries),
            "metadata_root": str(self._metadata),
        }

    @staticmethod
    def _entries(metadata: Path) -> tuple[str, ...]:
        if not metadata.exists():
            return ()
        return tuple(sorted(path.name for path in metadata.iterdir() if path.is_dir()))

    def _git(self, *arguments: str) -> str:
        try:
            completed = subprocess.run(
                (self.git_executable, "-C", str(self.repository.resolve()), *arguments),
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
        except FileNotFoundError as error:
            raise ToolingMissing("git", f"{self.git_executable!r} is not executable") from error
        except subprocess.TimeoutExpired as error:
            raise ToolingFailed("git", "worktree command timed out") from error
        if completed.returncode != 0:
            raise ToolingFailed("git", completed.stderr.strip() or "worktree command failed")
        return completed.stdout


__all__ = ["DetachedWorktree", "WorktreeInvalid"]
