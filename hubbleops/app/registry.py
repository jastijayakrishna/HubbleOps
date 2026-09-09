from __future__ import annotations

import hashlib
import importlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from hubbleops.core.errors import PackNotFound
from hubbleops.core.records import as_mapping, as_text
from hubbleops.observe.deps import ParsedDependency, normalize_package
from hubbleops.packs._protocol import (
    CaptureHooks,
    ChangeCompiler,
    ContractOracle,
    Falsifier,
    ProviderPack,
    RuleSet,
    SurfaceSpec,
    TelemetryAdapter,
    ToolSpec,
    Transform,
    Version,
    WireSignature,
)

PACKS_ROOT = Path(__file__).resolve().parent.parent / "packs"
SURFACE_FILENAME = "surface.yaml"


@dataclass(frozen=True, slots=True)
class LoadedPack:
    name: str
    root: Path
    surface: SurfaceSpec
    implementation: ProviderPack

    @property
    def wire_signature(self) -> WireSignature:
        return self.implementation.wire_signature

    def versions(self) -> tuple[Version, ...]:
        return self.implementation.versions()

    def rules(self, language: str) -> RuleSet:
        return self.implementation.rules(language)

    @property
    def contract(self) -> ContractOracle:
        return self.implementation.contract

    def verification_contract(self) -> ContractOracle:
        factory = getattr(self.implementation, "verification_contract", None)
        if factory is None:
            return self.contract
        contract = factory()
        if not isinstance(contract, ContractOracle):
            raise PackNotFound(f"pack {self.name!r} returned an invalid verification contract")
        return contract

    @property
    def changes(self) -> ChangeCompiler:
        return self.implementation.changes

    @property
    def telemetry(self) -> TelemetryAdapter:
        return self.implementation.telemetry

    def capture_hooks(self, language: str) -> CaptureHooks:
        return self.implementation.capture_hooks(language)

    def repair_transforms(self) -> list[Transform]:
        return self.implementation.repair_transforms()

    def repair_tools(self) -> list[ToolSpec]:
        return self.implementation.repair_tools()

    def falsifiers(self) -> list[Falsifier]:
        return self.implementation.falsifiers()

    def contract_hash(self) -> str:
        from hubbleops.core.canonical import content_id

        capture = self.root / "capture"
        capture_hashes = {
            path.relative_to(capture).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(capture.rglob("*"))
            if path.is_file()
        }
        return content_id(
            {
                "surface_hash": self.surface.surface_hash(),
                "lattice_hash": self.changes.lattice_hash,
                "capture_hashes": capture_hashes,
            }
        )

    def latest_compatible(self, dependencies: Sequence[ParsedDependency]) -> str:
        package_names = {normalize_package(name) for name in self.surface.package_names}
        installed = [
            item
            for item in dependencies
            if normalize_package(item.name) in package_names and item.version is not None
        ]
        if not installed:
            return "UNKNOWN (SDK compatibility unresolved)"
        targets = {self._target_for(item) for item in installed}
        if None in targets or len(targets) != 1:
            return "UNKNOWN (SDK compatibility unresolved)"
        target = targets.pop()
        return f"latest ({target})"

    def _target_for(self, dependency: ParsedDependency) -> str | None:
        ecosystem = {"csharp": "dotnet"}.get(dependency.ecosystem, dependency.ecosystem)
        installed = _version_numbers(dependency.version or "")
        if installed is None:
            return None
        for version in reversed(self.versions()):
            compatibility = next(
                (
                    fact
                    for fact in self.contract.catalog(version.id).facts
                    if fact.kind == "client_compatibility"
                ),
                None,
            )
            minimums = as_mapping(
                compatibility.attributes.get("minimum_versions") if compatibility else None
            )
            if not minimums:
                continue
            minimum = as_text(minimums.get(ecosystem))
            required = _version_numbers(minimum) if minimum is not None else None
            maximums = as_mapping(
                compatibility.attributes.get("maximum_versions") if compatibility else None
            )
            maximum = as_text(maximums.get(ecosystem))
            ceiling = _version_numbers(maximum) if maximum else None
            if (
                required is not None
                and installed >= required
                and (ceiling is None or installed <= ceiling)
            ):
                return version.id
        return None


def available_packs() -> tuple[str, ...]:
    if not PACKS_ROOT.is_dir():
        return ()
    return tuple(
        sorted(
            child.name
            for child in PACKS_ROOT.iterdir()
            if child.is_dir() and (child / SURFACE_FILENAME).is_file()
        )
    )


def load_pack(name: str) -> LoadedPack:
    if name != Path(name).name or name in ("", ".", ".."):
        raise PackNotFound(f"{name!r} is not a pack name")
    surface_path = PACKS_ROOT / name / SURFACE_FILENAME
    if not surface_path.is_file():
        raise PackNotFound(
            f"no pack named {name!r}; available: {', '.join(available_packs()) or 'none'}"
        )
    module = importlib.import_module(f"hubbleops.packs.{name}")
    implementation = getattr(module, "PACK", None)
    if not isinstance(implementation, ProviderPack):
        raise PackNotFound(f"pack {name!r} does not implement the ProviderPack contract")
    if implementation.name != name or implementation.surface.name != name:
        raise PackNotFound(f"pack directory {name!r} and ProviderPack names must agree")
    return LoadedPack(
        name=name,
        root=PACKS_ROOT / name,
        surface=implementation.surface,
        implementation=implementation,
    )


def _version_numbers(value: str) -> tuple[int, ...] | None:
    match = re.fullmatch(r"v?([0-9]+(?:\.[0-9]+){0,3})", value)
    if match is None:
        return None
    parts = tuple(int(item) for item in match[1].split("."))
    return parts + (0,) * (4 - len(parts))
