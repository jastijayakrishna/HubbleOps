from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from hubbleops.core.surface import SurfaceSpec


@dataclass(frozen=True, slots=True)
class ObserverContext:
    provider: str
    run_id: str
    proof_scope_hash: str
    repo_sha: str | None
    dependency_context_hash: str | None
    surface: SurfaceSpec


@runtime_checkable
class Observer(Protocol):
    name: str

    def scan(self, closure: Any, ctx: ObserverContext) -> list[dict[str, Any]]: ...
