from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from hubbleops.core.repair import TransformInput, TransformOutput
from hubbleops.core.surface import (
    RequestLanguage,
    SinkArgument,
    SurfaceSpec,
    VersionCarrier,
)
from hubbleops.core.verification import FalsifierInput, FalsifierOutcome, OracleAuthority

Confidence = Literal["PROVEN", "DOCUMENTED"]
ContractCode = Literal[
    "VALID",
    "INVALID",
    "UNKNOWN_PROVIDER_CONTRACT",
    "ORACLE_UNAVAILABLE",
]
WireCode = Literal["MATCH", "UNKNOWN_WIRE_SIGNATURE"]


@dataclass(frozen=True, slots=True)
class Version:
    id: str
    catalog_hash: str
    released_at: str
    sunset_at: str | None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "catalog_hash": self.catalog_hash,
            "released_at": self.released_at,
            "sunset_at": self.sunset_at,
        }


@dataclass(frozen=True, slots=True)
class CatalogFact:
    subject: str
    kind: str
    attributes: Mapping[str, Any]
    source_url: str
    retrieved_at: str
    sha256: str
    confidence: Confidence
    corroborating_sources: tuple[Mapping[str, str], ...] = ()
    resolution: str = "RESOLVED"

    def to_mapping(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "kind": self.kind,
            "attributes": dict(self.attributes),
            "source_url": self.source_url,
            "retrieved_at": self.retrieved_at,
            "sha256": self.sha256,
            "confidence": self.confidence,
            "corroborating_sources": [dict(item) for item in self.corroborating_sources],
            "resolution": self.resolution,
        }


@dataclass(frozen=True, slots=True)
class Catalog:
    version: str
    facts: tuple[CatalogFact, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class DiffFact:
    subject: str
    change: str
    before: Mapping[str, Any] | None
    after: Mapping[str, Any] | None
    replacement: str | None
    confidence: Confidence
    result: ContractCode
    reason: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "change": self.change,
            "before": None if self.before is None else dict(self.before),
            "after": None if self.after is None else dict(self.after),
            "replacement": self.replacement,
            "confidence": self.confidence,
            "result": self.result,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ContractDiff:
    from_version: str
    to_version: str
    pair_hash: str
    facts: tuple[DiffFact, ...]
    hops: tuple[ContractDiff, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidationResult:
    code: ContractCode
    reason: str
    response: Mapping[str, Any] | None = None
    authority: OracleAuthority | None = None


@dataclass(frozen=True, slots=True)
class WireObservation:
    service: str
    method: str
    version: str


@dataclass(frozen=True, slots=True)
class WireResult:
    code: WireCode
    observation: WireObservation | None
    reason: str


@dataclass(frozen=True, slots=True)
class TelemetryIssue:
    row: int
    value: str
    reason: str


@dataclass(frozen=True, slots=True)
class TelemetryResult:
    observations: tuple[WireObservation, ...]
    issues: tuple[TelemetryIssue, ...]


@dataclass(frozen=True, slots=True)
class BuildReport:
    source_hashes: Mapping[str, str]
    catalog_hashes: Mapping[str, str]
    fact_counts: Mapping[str, int]
    lattice_hash: str


@runtime_checkable
class RuleSet(Protocol):
    """Provider-owned structural rule files for one source language."""

    language: str
    paths: tuple[Path, ...]


@runtime_checkable
class Transport(Protocol):
    """Injected provider transport exposing validation-only request execution."""

    @property
    def available(self) -> bool: ...

    def validate(
        self,
        *,
        service: str,
        method: str,
        version: str,
        request: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


@runtime_checkable
class ContractOracle(Protocol):
    """Provider contract catalogs, computed diffs, and validation-only oracle access."""

    def context_hash(self) -> str: ...

    def catalog(self, version: str) -> Catalog: ...

    def diff(self, version_from: str, version_to: str) -> ContractDiff: ...

    def validate(self, request: Mapping[str, Any], version: str) -> ValidationResult: ...


@runtime_checkable
class ChangeCompiler(Protocol):
    """Offline compiler for hashed provider sources and reproducible version catalogs."""

    @property
    def lattice_hash(self) -> str: ...

    def build(self, output_dir: Path) -> BuildReport: ...

    def verify(self) -> BuildReport: ...


@runtime_checkable
class WireSignature(Protocol):
    """Language-independent request-target parser returning a match or explicit unknown."""

    def parse(self, path: str, headers: Mapping[str, str]) -> WireResult: ...


@runtime_checkable
class TelemetryAdapter(Protocol):
    """Provider usage-export parser returning observations and row-provenance issues."""

    def parse(self, payload: str) -> TelemetryResult: ...


@runtime_checkable
class CaptureHooks(Protocol):
    """Provider capture-hook bundle loaded later by generic dynamic observers."""

    language: str
    paths: tuple[Path, ...]


@runtime_checkable
class Transform(Protocol):
    """Deterministic provider repair transform: precondition, apply, then post-check.

    A transform whose precondition is false does not apply and does not fail the run; its
    obligation stays open for a human. A transform whose postcondition is false after applying
    reverts and names its failure class.
    """

    name: str
    failure_class: str

    def precondition(self, subject: TransformInput) -> bool: ...

    def apply(self, subject: TransformInput) -> TransformOutput: ...

    def postcondition(self, subject: TransformOutput) -> bool: ...


@runtime_checkable
class ToolSpec(Protocol):
    """Provider repair tool specification implemented in a later phase."""

    name: str


@runtime_checkable
class Falsifier(Protocol):
    """Provider adversarial check selected by failure class and executed by the verifier."""

    name: str
    failure_class: str

    def check(self, subject: FalsifierInput) -> FalsifierOutcome: ...


@runtime_checkable
class ProviderPack(Protocol):
    """Complete provider-specific capability object selected only by the application layer."""

    name: str
    surface: SurfaceSpec
    wire_signature: WireSignature

    def versions(self) -> tuple[Version, ...]: ...

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


__all__ = [
    "BuildReport",
    "CaptureHooks",
    "Catalog",
    "CatalogFact",
    "ChangeCompiler",
    "Confidence",
    "ContractCode",
    "ContractDiff",
    "ContractOracle",
    "DiffFact",
    "Falsifier",
    "FalsifierInput",
    "FalsifierOutcome",
    "ProviderPack",
    "RequestLanguage",
    "RuleSet",
    "SinkArgument",
    "SurfaceSpec",
    "TelemetryAdapter",
    "TelemetryIssue",
    "TelemetryResult",
    "ToolSpec",
    "Transform",
    "TransformInput",
    "TransformOutput",
    "Transport",
    "ValidationResult",
    "Version",
    "VersionCarrier",
    "WireCode",
    "WireObservation",
    "WireResult",
    "WireSignature",
]
