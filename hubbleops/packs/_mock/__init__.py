from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from hubbleops.core.canonical import blob_hash, canonical_text, content_id
from hubbleops.core.errors import PackDataError
from hubbleops.core.records import as_mapping
from hubbleops.core.surface import SurfaceSpec
from hubbleops.core.verification import FalsifierInput, FalsifierOutcome
from hubbleops.packs._protocol import (
    BuildReport,
    CaptureHooks,
    Catalog,
    CatalogFact,
    ContractDiff,
    DiffFact,
    Falsifier,
    RuleSet,
    TelemetryIssue,
    TelemetryResult,
    ToolSpec,
    Transform,
    ValidationResult,
    Version,
    WireObservation,
    WireResult,
)

ROOT = Path(__file__).resolve().parent


@dataclass(slots=True)
class EmptyBundle:
    language: str
    paths: tuple[Path, ...] = ()


@dataclass(slots=True)
class MockRemovedField:
    name: str = "removed_field_in_request"
    failure_class: str = "request_text"

    def check(self, subject: FalsifierInput) -> FalsifierOutcome:
        removed = {change.subject for change in subject.changes.removed()}
        if not removed:
            return FalsifierOutcome(result="PASS", reason="this Change Pack removes no subject")
        sites: list[str] = []
        for record in subject.evidence_of(self.failure_class):
            value = as_mapping(record.get("value"))
            text = str(value.get("text") or value.get("query") or "")
            sites.extend(
                f"{record.get('path')}:{record.get('line_start')} names {name}"
                for name in sorted(removed)
                if re.search(rf"\b{re.escape(name)}\b", text)
            )
        if sites:
            return FalsifierOutcome(
                result="FAIL",
                reason="a request still names a removed subject",
                sites=tuple(sorted(set(sites))),
            )
        return FalsifierOutcome(result="PASS", reason="no request names a removed subject")


MOCK_FALSIFIER: Falsifier = MockRemovedField()


def _fact(subject: str, kind: str, attributes: Mapping[str, Any]) -> CatalogFact:
    source = "https://api.mockprov.test/contract"
    return CatalogFact(
        subject=subject,
        kind=kind,
        attributes=attributes,
        source_url=source,
        retrieved_at="2026-09-04T00:00:00Z",
        sha256=blob_hash(source.encode()),
        confidence="PROVEN",
    )


FACTS = {
    "v1": (
        _fact("campaigns.id", "field", {"data_type": "INT64", "selectable": True}),
        _fact("campaigns.legacy", "field", {"data_type": "STRING", "selectable": True}),
    ),
    "v2": (
        _fact("campaigns.id", "field", {"data_type": "INT64", "selectable": True}),
        _fact("campaigns.name", "field", {"data_type": "STRING", "selectable": True}),
    ),
}


def _payload(version: str) -> bytes:
    return "".join(f"{canonical_text(fact.to_mapping())}\n" for fact in FACTS[version]).encode()


class MockChanges:
    @property
    def lattice_hash(self) -> str:
        return content_id(
            [
                {"id": version, "catalog_hash": blob_hash(_payload(version))}
                for version in ("v1", "v2")
            ]
        )

    def build(self, output_dir: Path) -> BuildReport:
        output_dir.mkdir(parents=True, exist_ok=True)
        hashes: dict[str, str] = {}
        counts: dict[str, int] = {}
        for version in ("v1", "v2"):
            payload = _payload(version)
            (output_dir / f"catalog_{version}.jsonl").write_bytes(payload)
            hashes[version] = blob_hash(payload)
            counts[version] = len(FACTS[version])
        return BuildReport({}, hashes, counts, self.lattice_hash)

    def verify(self) -> BuildReport:
        return BuildReport(
            {},
            {version: blob_hash(_payload(version)) for version in ("v1", "v2")},
            {version: len(FACTS[version]) for version in ("v1", "v2")},
            self.lattice_hash,
        )


class MockContract:
    def catalog(self, version: str) -> Catalog:
        if version not in FACTS:
            raise PackDataError(f"unsupported mock version {version!r}")
        return Catalog(version, FACTS[version], blob_hash(_payload(version)))

    def diff(self, version_from: str, version_to: str) -> ContractDiff:
        if (version_from, version_to) != ("v1", "v2"):
            raise PackDataError("mock diffs support only v1 to v2")
        source = {fact.subject: fact for fact in FACTS[version_from]}
        target = {fact.subject: fact for fact in FACTS[version_to]}
        facts: list[DiffFact] = []
        for subject in sorted(set(source) | set(target)):
            left = source.get(subject)
            right = target.get(subject)
            if left == right:
                continue
            facts.append(
                DiffFact(
                    subject=subject,
                    change="ADDED" if left is None else "REMOVED" if right is None else "CHANGED",
                    before=None if left is None else left.attributes,
                    after=None if right is None else right.attributes,
                    replacement="campaigns.name" if subject == "campaigns.legacy" else None,
                    confidence="PROVEN",
                    result="VALID",
                    reason="computed from mock catalogs",
                )
            )
        return ContractDiff(
            version_from,
            version_to,
            content_id([blob_hash(_payload(version_from)), blob_hash(_payload(version_to))]),
            tuple(facts),
        )

    def validate(self, request: Mapping[str, Any], version: str) -> ValidationResult:
        self.catalog(version)
        if request.get("bad") is True:
            return ValidationResult(code="INVALID", reason="mock known-bad request")
        return ValidationResult(code="VALID", reason="mock request accepted")


class MockWireSignature:
    def parse(self, path: str, headers: Mapping[str, str]) -> WireResult:
        header_path = next((value for key, value in headers.items() if key.lower() == ":path"), "")
        targets = tuple(value for value in (path, header_path) if value)
        versions = {
            match[1] for value in targets for match in [re.search(r"/(v[0-9]+)/", value)] if match
        }
        if len(targets) == 0 or len(versions) != 1:
            return WireResult(
                "UNKNOWN_WIRE_SIGNATURE", None, "mock request target is absent or ambiguous"
            )
        return WireResult(
            "MATCH", WireObservation("MockService", "Call", versions.pop()), "matched"
        )


class MockTelemetry:
    def parse(self, payload: str) -> TelemetryResult:
        reader = csv.DictReader(io.StringIO(payload))
        if reader.fieldnames is None or "Method" not in reader.fieldnames:
            return TelemetryResult((), (TelemetryIssue(1, "", "Method column is missing"),))
        observations: list[WireObservation] = []
        issues: list[TelemetryIssue] = []
        pattern = re.compile(r"^mockprov\.(v[0-9]+)\.MockService\.Call$")
        for number, row in enumerate(reader, start=2):
            value = row.get("Method", "")
            match = pattern.fullmatch(value)
            if match is None:
                issues.append(TelemetryIssue(number, value, "invalid mock method"))
            else:
                observations.append(WireObservation("MockService", "Call", match[1]))
        return TelemetryResult(tuple(observations), tuple(issues))


class MockPack:
    name = "_mock"
    surface = SurfaceSpec.from_mapping(yaml.safe_load((ROOT / "surface.yaml").read_text("utf-8")))
    wire_signature = MockWireSignature()
    contract = MockContract()
    changes = MockChanges()
    telemetry = MockTelemetry()

    def versions(self) -> tuple[Version, ...]:
        return tuple(
            Version(version, blob_hash(_payload(version)), f"202{number}-01-01", None)
            for number, version in enumerate(("v1", "v2"), start=4)
        )

    def rules(self, language: str) -> RuleSet:
        normalized = language.lower()
        paths = (ROOT / "rules" / "python.yml",) if normalized == "python" else ()
        return EmptyBundle(normalized, paths)

    def capture_hooks(self, language: str) -> CaptureHooks:
        normalized = language.lower()
        path = ROOT / "capture" / "python" / "sitecustomize.py"
        return EmptyBundle(normalized, (path,) if normalized == "python" else ())

    def repair_transforms(self) -> list[Transform]:
        return []

    def repair_tools(self) -> list[ToolSpec]:
        return []

    def falsifiers(self) -> list[Falsifier]:
        return [MOCK_FALSIFIER]


PACK = MockPack()
