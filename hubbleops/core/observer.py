from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from hubbleops.core.surface import SurfaceSpec


@dataclass(frozen=True, order=True, slots=True)
class StructuralRule:
    id: str
    language: str
    path: Path
    sha256: str


@dataclass(frozen=True, slots=True)
class ObserverContext:
    provider: str
    run_id: str
    proof_scope_hash: str
    repo_sha: str | None
    dependency_context_hash: str | None
    surface: SurfaceSpec
    rules: tuple[StructuralRule, ...] = ()
    ast_grep_executable: str = "ast-grep"
    force_structure: bool = False


@runtime_checkable
class Observer(Protocol):
    name: str

    def scan(self, closure: Any, ctx: ObserverContext) -> list[dict[str, Any]]: ...
