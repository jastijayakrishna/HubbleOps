from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from hubbleops.core.records import as_mapping, as_sequence, as_text
from hubbleops.core.verification import FalsifierInput, FalsifierOutcome
from hubbleops.packs._protocol import Falsifier

VERSION = re.compile(r"\bv(\d+)\b")
NAMESPACE_VERSION = re.compile(r"[._/]v(\d+)\b")


def _site(record: Mapping[str, Any]) -> str:
    line = record.get("line_start")
    return f"{record.get('path')}:{line}" if line else str(record.get("path"))


def _versions(record: Mapping[str, Any]) -> set[str]:
    value = as_mapping(record.get("value"))
    found: set[str] = set()
    for key in ("version", "detected", "target", "endpoint", "namespace", "package"):
        text = as_text(value.get(key))
        if text:
            found.update(f"v{match}" for match in VERSION.findall(text))
    for item in as_sequence(value.get("versions")):
        found.update(f"v{match}" for match in VERSION.findall(str(item)))
    subject = as_text(record.get("provider_subject"))
    if subject:
        found.update(f"v{match}" for match in VERSION.findall(subject))
    return found


def _resolution(record: Mapping[str, Any]) -> str:
    value = as_mapping(record.get("value"))
    return str(value.get("resolution") or value.get("state") or "RESOLVED")


@dataclass(slots=True)
class VersionResidue:
    name: str
    failure_class: str
    description: str

    def check(self, subject: FalsifierInput) -> FalsifierOutcome:
        target = subject.changes.to_version
        offending: list[str] = []
        undecided: list[str] = []
        for record in subject.evidence_of(self.failure_class):
            versions = _versions(record)
            if not versions:
                if _resolution(record) not in ("RESOLVED", "PRESENT", "ABSENT"):
                    undecided.append(_site(record))
                continue
            stale = sorted(version for version in versions if version != target)
            if stale:
                offending.append(f"{_site(record)} carries {', '.join(stale)}")
        if offending:
            return FalsifierOutcome(
                result="FAIL",
                reason=f"{self.description} still names a version other than {target}",
                sites=tuple(sorted(offending)),
            )
        if undecided:
            return FalsifierOutcome(
                result="UNKNOWN",
                reason=f"{self.description} could not be resolved to a version",
                sites=tuple(sorted(undecided)),
            )
        return FalsifierOutcome(
            result="PASS",
            reason=f"{self.description} names only {target}",
        )


@dataclass(slots=True)
class GeneratedNamespace:
    name: str = "generated_namespace"
    failure_class: str = "package_reference"
    description: str = "generated client namespace"

    def check(self, subject: FalsifierInput) -> FalsifierOutcome:
        target = subject.changes.to_version
        offending: list[str] = []
        for record in subject.evidence_of(self.failure_class):
            value = as_mapping(record.get("value"))
            text = " ".join(
                item
                for item in (
                    as_text(value.get("package")),
                    as_text(value.get("namespace")),
                    as_text(record.get("provider_subject")),
                )
                if item
            )
            stale = sorted({f"v{found}" for found in NAMESPACE_VERSION.findall(text)} - {target})
            if stale:
                offending.append(f"{_site(record)} imports {', '.join(stale)}")
        if offending:
            return FalsifierOutcome(
                result="FAIL",
                reason=f"a generated namespace other than {target} is still imported",
                sites=tuple(sorted(offending)),
            )
        return FalsifierOutcome(
            result="PASS", reason=f"every generated namespace reference names {target}"
        )


@dataclass(slots=True)
class SdkConstraint:
    name: str = "sdk_constraint"
    failure_class: str = "sdk_installed"
    description: str = "installed client library"

    def check(self, subject: FalsifierInput) -> FalsifierOutcome:
        absent: list[str] = []
        for record in subject.evidence_of(self.failure_class):
            value = as_mapping(record.get("value"))
            if str(value.get("state")) == "ABSENT":
                continue
            if as_text(value.get("version")) is None:
                absent.append(f"{_site(record)} pins no resolvable version")
        if absent:
            return FalsifierOutcome(
                result="UNKNOWN",
                reason="an installed client library resolves to no version",
                sites=tuple(sorted(absent)),
            )
        return FalsifierOutcome(
            result="PASS", reason="every installed client library resolves to a version"
        )


@dataclass(slots=True)
class SubjectResidue:
    name: str
    failure_class: str
    description: str
    renamed: bool

    def check(self, subject: FalsifierInput) -> FalsifierOutcome:
        watched = {
            change.subject: change.replacement
            for change in (subject.changes.renamed() if self.renamed else subject.changes.removed())
        }
        if not watched:
            return FalsifierOutcome(
                result="PASS", reason=f"this Change Pack carries no {self.description}"
            )
        offending: list[str] = []
        for record in subject.evidence_of(self.failure_class):
            value = as_mapping(record.get("value"))
            text = " ".join(
                item
                for item in (
                    as_text(value.get("text")),
                    as_text(value.get("query")),
                    as_text(record.get("provider_subject")),
                )
                if item
            )
            for name, replacement in sorted(watched.items()):
                if re.search(rf"\b{re.escape(name)}\b", text):
                    suffix = f" (use {replacement})" if replacement else ""
                    offending.append(f"{_site(record)} names {name}{suffix}")
        if offending:
            return FalsifierOutcome(
                result="FAIL",
                reason=f"a request still names a {self.description}",
                sites=tuple(sorted(set(offending))),
            )
        return FalsifierOutcome(result="PASS", reason=f"no request names a {self.description}")


@dataclass(slots=True)
class ProductionResidue:
    name: str = "production_version_residue"
    failure_class: str = "production_version"
    description: str = "production traffic"

    def check(self, subject: FalsifierInput) -> FalsifierOutcome:
        target = subject.changes.to_version
        offending: list[str] = []
        for record in subject.evidence_of(self.failure_class):
            value = as_mapping(record.get("value"))
            version = as_text(value.get("version"))
            if version and version != target:
                offending.append(
                    f"{value.get('service')}.{value.get('method')} observed on {version}"
                )
        if offending:
            return FalsifierOutcome(
                result="FAIL",
                reason=f"production traffic is still observed on a version other than {target}",
                sites=tuple(sorted(set(offending))),
            )
        return FalsifierOutcome(
            result="PASS", reason=f"observed production traffic names only {target}"
        )


FALSIFIERS: tuple[Falsifier, ...] = (
    VersionResidue("per_call_version_override", "call_version", "a per-call version override"),
    VersionResidue("rest_endpoint_version", "endpoint_reference", "a REST endpoint path"),
    VersionResidue("dynamic_version_config", "config_reference", "a configured version"),
    GeneratedNamespace(),
    SdkConstraint(),
    SubjectResidue(
        "removed_subject_in_request", "request_text", "removed provider subject", renamed=False
    ),
    SubjectResidue(
        "renamed_subject_in_request", "request_text", "renamed provider subject", renamed=True
    ),
    ProductionResidue(),
)


__all__ = [
    "FALSIFIERS",
    "GeneratedNamespace",
    "ProductionResidue",
    "SdkConstraint",
    "SubjectResidue",
    "VersionResidue",
]
