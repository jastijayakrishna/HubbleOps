from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from hubbleops.core.errors import ToolingFailed, ToolingMissing, ToolingTimeout
from hubbleops.core.process import bounded_process
from hubbleops.core.verification import Hunk

HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
DIFF_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")
SHA = re.compile(r"^[0-9a-f]{4,40}$")
DIFF_WALL_SECONDS = 120.0
DIFF_OUTPUT_BYTES = 33_554_432


@dataclass(frozen=True, slots=True)
class Delta:
    base_sha: str
    candidate_sha: str
    hunks: tuple[Hunk, ...]
    paths: tuple[str, ...]

    def hunks_in(self, path: str) -> tuple[Hunk, ...]:
        return tuple(hunk for hunk in self.hunks if hunk.path == path)


def resolve(repository: Path, revision: str, git_executable: str = "git") -> str:
    output = _git(repository, ("rev-parse", f"{revision}^{{commit}}"), git_executable)
    resolved = output.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", resolved):
        raise ToolingFailed("git", f"{revision!r} did not resolve to a commit")
    return resolved


def read(
    repository: Path,
    base_sha: str,
    candidate_sha: str,
    git_executable: str = "git",
) -> Delta:
    base = _checked_revision(base_sha)
    candidate = _checked_revision(candidate_sha)
    unified = _git(
        repository,
        ("diff", "--unified=0", "--no-color", "--no-ext-diff", "--find-renames", base, candidate),
        git_executable,
    )
    numstat = _git(
        repository,
        ("diff", "--numstat", "--no-color", "--no-ext-diff", "--find-renames", base, candidate),
        git_executable,
    )
    hunks = parse_hunks(unified)
    declared = _numstat_paths(numstat)
    observed = {hunk.path for hunk in hunks}
    missing = sorted(declared - observed)
    extra = sorted(observed - declared)
    if extra:
        raise ToolingFailed("git", f"unified diff names paths --numstat did not: {extra}")
    textual = [path for path in missing if path not in _binary_paths(numstat)]
    if textual:
        raise ToolingFailed(
            "git",
            f"--numstat names {len(textual)} changed text path(s) the unified diff did not, "
            f"starting with {textual[0]}. A narrowed diff would hide a change from containment.",
        )
    return Delta(
        base_sha=base,
        candidate_sha=candidate,
        hunks=hunks,
        paths=tuple(sorted(declared | observed)),
    )


def _binary_paths(numstat: str) -> set[str]:
    return {
        line.split("\t", 2)[2].strip()
        for line in numstat.splitlines()
        if line.count("\t") >= 2 and line.split("\t", 2)[0] == "-"
    }


def _checked_revision(value: str) -> str:
    candidate = value.strip()
    if not SHA.fullmatch(candidate):
        raise ToolingFailed("git", f"{value!r} is not a commit identifier")
    return candidate


def _numstat_paths(numstat: str) -> set[str]:
    paths: set[str] = set()
    for line in numstat.splitlines():
        if line.count("\t") < 2:
            continue
        path = line.split("\t", 2)[2].strip()
        paths.add(path.split(" => ")[-1].strip("{}") if " => " in path else path)
    return paths


def parse_hunks(unified: str) -> tuple[Hunk, ...]:
    hunks: list[Hunk] = []
    path: str | None = None
    header: re.Match[str] | None = None
    added: list[str] = []
    removed: list[str] = []

    def flush() -> None:
        if header is None or path is None:
            return
        hunks.append(
            Hunk(
                path=path,
                old_start=int(header[1]),
                old_lines=1 if header[2] is None else int(header[2]),
                new_start=int(header[3]),
                new_lines=1 if header[4] is None else int(header[4]),
                added=tuple(added),
                removed=tuple(removed),
            )
        )

    for line in unified.splitlines():
        file_header = DIFF_HEADER.match(line)
        if file_header is not None:
            flush()
            header = None
            added, removed = [], []
            path = file_header[2]
            continue
        found = HUNK_HEADER.match(line)
        if found is not None:
            flush()
            header = found
            added, removed = [], []
            continue
        if header is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(line[1:])
    flush()
    return tuple(hunks)


def _git(repository: Path, arguments: tuple[str, ...], git_executable: str) -> str:
    argv = (git_executable, "-C", str(repository.resolve()), *arguments)
    outcome, exit_code, stdout, stderr = bounded_process(
        argv, DIFF_WALL_SECONDS, DIFF_OUTPUT_BYTES, "git"
    )
    if outcome == "WALL_TIMEOUT":
        raise ToolingTimeout("git", DIFF_WALL_SECONDS)
    if outcome == "OUTPUT_LIMIT":
        raise ToolingFailed("git", f"{arguments[0]} exceeded its {DIFF_OUTPUT_BYTES} byte bound")
    if exit_code != 0:
        detail = stderr.decode("utf-8", errors="replace").strip() or f"git {arguments[0]} failed"
        raise ToolingFailed("git", detail)
    return stdout.decode("utf-8", errors="replace")


def available(git_executable: str = "git") -> str:
    outcome, exit_code, stdout, stderr = bounded_process(
        (git_executable, "--version"), 15, 65_536, "git"
    )
    if outcome != "COMPLETED" or exit_code != 0:
        raise ToolingMissing("git", stderr.decode("utf-8", errors="replace").strip() or outcome)
    return stdout.decode("utf-8", errors="replace").strip()


__all__ = ["Delta", "available", "parse_hunks", "read", "resolve"]
