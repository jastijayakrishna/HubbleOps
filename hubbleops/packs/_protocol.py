from __future__ import annotations

from typing import Protocol, runtime_checkable

from hubbleops.core.surface import (
    RequestLanguage,
    SinkArgument,
    SurfaceSpec,
    VersionCarrier,
)

__all__ = [
    "CaptureHooks",
    "ChangeCompiler",
    "ContractOracle",
    "Falsifier",
    "ProviderPack",
    "RequestLanguage",
    "RuleSet",
    "SinkArgument",
    "SurfaceSpec",
    "TelemetryAdapter",
    "ToolSpec",
    "Transform",
    "VersionCarrier",
]


class RuleSet(Protocol):
    """ast-grep rule files for one language: sinks, version carriers, request-string sinks.

    Filled in Phase 3. Rules ship with the pack and are tested with must-match and
    must-not-match snippets; the structure observer only executes them.
    """


class ContractOracle(Protocol):
    """The provider's own answer to 'is this request valid at this version?'.

    Filled in Phase 2. `ORACLE_UNAVAILABLE` caps a verdict at UNKNOWN rather than
    letting an unanswered question pass as an accepted one.
    """


class ChangeCompiler(Protocol):
    """Compiles provider sources into a hashed, provenance-carrying Change Pack.

    Filled in Phase 2. Cross-checked facts are PROVEN; documentation-only facts are
    DOCUMENTED; disagreement is UNKNOWN_PROVIDER_CONTRACT. No LLM adjudication.
    """


class TelemetryAdapter(Protocol):
    """Parses the provider's usage export into (service, method, version) tuples.

    Filled in Phase 4. Every tuple must map to at least one explained candidate;
    unmatched tuples are TELEMETRY_UNEXPLAINED.
    """


class CaptureHooks(Protocol):
    """Interceptor or patch code for one language, loaded by the generic dynamic loaders.

    Filled in Phase 4. Emits the versioned capture event schema; the loaders never
    know which provider they are capturing.
    """


class Transform(Protocol):
    """One deterministic, precondition-checked repair. Filled in Phase 6."""


class ToolSpec(Protocol):
    """A provider-supplied repair tool made available inside the repair image. Filled in Phase 6."""


class Falsifier(Protocol):
    """An adversarial check keyed to a known failure class. Filled in Phase 5."""


@runtime_checkable
class ProviderPack(Protocol):
    """Everything provider-specific, in one object.

    `app/` selects a pack and passes its parts into the generic layers as parameters.
    The generic layers never import this module and contain no provider names (law L5),
    so adding a provider is filling this contract and writing rules, with zero diff to
    generic code.
    """

    name: str
    surface: SurfaceSpec

    def rules(self, language: str) -> RuleSet: ...

    @property
    def contract(self) -> ContractOracle: ...

    @property
    def changes(self) -> ChangeCompiler: ...

    @property
    def telemetry(self) -> TelemetryAdapter: ...

    def capture_hooks(self, language: str) -> CaptureHooks: ...

    def repair_transforms(self) -> list[Transform]: ...

    def repair_tools(self) -> list[ToolSpec]: ...

    def falsifiers(self) -> list[Falsifier]: ...
