from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

from hubbleops.core.canonical import content_id
from hubbleops.core.errors import HubbleOpsError, ToolingFailed, ToolingMissing, ToolingTimeout
from hubbleops.core.records import as_mapping, as_sequence
from hubbleops.core.toolchain import locate
from hubbleops.store import write_atomic

RIPGREP = "rg"
MAX_PATTERNS = 10_000
MAX_PATTERN_LENGTH = 512
TIMEOUT_SECONDS = 2.0
ENTRY_FIELDS = frozenset({"id", "pattern", "provider", "proof_scope_hash"})


class GuardInvalid(HubbleOpsError):
    pass


@dataclass(frozen=True, slots=True)
class GuardResult:
    patterns: int
    matches: tuple[str, ...]


def load(path: Path) -> tuple[dict[str, str], ...]:
    if not path.is_file():
        return ()
    try:
        raw = cast(object, yaml.safe_load(path.read_bytes()))
    except (OSError, yaml.YAMLError) as error:
        raise GuardInvalid(f"retired surface is unreadable: {error}") from error
    document = as_mapping(raw)
    if document.get("schema_version") != 1 or not isinstance(document.get("entries"), list):
        raise GuardInvalid("retired surface must contain schema_version 1 and an entries list")
    entries = tuple(dict(as_mapping(item)) for item in as_sequence(document["entries"]))
    if len(entries) > MAX_PATTERNS:
        raise GuardInvalid(f"retired surface exceeds the {MAX_PATTERNS} pattern bound")
    for entry in entries:
        if frozenset(entry) != ENTRY_FIELDS:
            raise GuardInvalid("retired surface entry fields do not match schema version 1")
        if any(not isinstance(entry[field], str) or not entry[field] for field in ENTRY_FIELDS):
            raise GuardInvalid("retired surface entry fields must be non-empty strings")
        if len(entry["pattern"]) > MAX_PATTERN_LENGTH:
            raise GuardInvalid(f"retired pattern exceeds the {MAX_PATTERN_LENGTH} character bound")
        expected = content_id({key: entry[key] for key in ENTRY_FIELDS if key != "id"})
        if entry["id"] != expected:
            raise GuardInvalid(f"retired entry {entry['id']} does not match its content")
    return tuple(sorted(entries, key=lambda item: item["id"]))


def run(repository: Path, retired: Path, rg: str = RIPGREP) -> GuardResult:
    entries = load(retired)
    if not entries:
        return GuardResult(0, ())
    return GuardResult(
        len(entries), search(repository, [entry["pattern"] for entry in entries], rg)
    )


def present(
    repository: Path, patterns: Sequence[str], rg: str = RIPGREP
) -> dict[str, tuple[str, ...]]:
    return {pattern: search(repository, [pattern], rg) for pattern in sorted(set(patterns))}


def search(repository: Path, patterns: Sequence[str], rg: str = RIPGREP) -> tuple[str, ...]:
    if not patterns:
        return ()
    command = [
        _executable(rg),
        "--fixed-strings",
        "--line-number",
        "--no-heading",
        "--color",
        "never",
        "--glob",
        "!.git/**",
        "--glob",
        "!.hubbleops/**",
    ]
    for pattern in patterns:
        command.extend(("--regexp", pattern))
    command.append(".")
    try:
        completed = subprocess.run(
            command,
            cwd=repository.resolve(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as error:
        raise ToolingMissing(rg, str(error)) from error
    except subprocess.TimeoutExpired as error:
        raise ToolingTimeout(rg, TIMEOUT_SECONDS) from error
    if completed.returncode not in (0, 1):
        raise ToolingFailed(rg, completed.stderr.strip() or f"exit {completed.returncode}")
    return tuple(sorted(line for line in completed.stdout.splitlines() if line.strip()))


def _executable(rg: str) -> str:
    return locate(RIPGREP, None if rg == RIPGREP else rg).path


def write_retired(
    repository: Path,
    *,
    patterns: tuple[str, ...],
    provider: str,
    proof_scope_hash: str,
) -> Path:
    path = repository.resolve() / ".hubbleops" / "retired.yml"
    retained = list(load(path))
    for pattern in sorted(set(patterns)):
        if not pattern or len(pattern) > MAX_PATTERN_LENGTH:
            raise GuardInvalid("retired patterns must be non-empty and bounded")
        body = {
            "pattern": pattern,
            "provider": provider,
            "proof_scope_hash": proof_scope_hash,
        }
        retained.append({"id": content_id(body), **body})
    unique = {item["id"]: item for item in retained}
    payload = yaml.safe_dump(
        {"schema_version": 1, "entries": [unique[key] for key in sorted(unique)]},
        allow_unicode=True,
        sort_keys=True,
    ).encode("utf-8")
    write_atomic(path, payload)
    return path


def workflow(command: str = "uv run hops") -> bytes:
    if re.fullmatch(r"[A-Za-z0-9._/ -]+", command) is None:
        raise GuardInvalid("workflow command contains unsupported shell characters")
    document = f"""name: HubbleOps backslide guard
on:
  pull_request:
  merge_group:
    types: [checks_requested]
permissions:
  contents: read
jobs:
  guard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: {command} guard --repo .
"""
    return document.encode("utf-8")


def install_workflow(repository: Path, command: str = "uv run hops") -> Path:
    path = repository.resolve() / ".github" / "workflows" / "hubbleops-guard.yml"
    write_atomic(path, workflow(command))
    return path


__all__ = [
    "GuardInvalid",
    "GuardResult",
    "install_workflow",
    "load",
    "present",
    "run",
    "search",
    "workflow",
    "write_retired",
]
