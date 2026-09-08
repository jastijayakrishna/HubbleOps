from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from hubbleops.core.records import as_mapping, as_sequence, as_text, is_mapping
from hubbleops.core.verification import ChangeSet, CheckReport, ObligationView
from hubbleops.graph.imports import ImportGraph
from hubbleops.observe.ledger import Ledger

REQUEST_CLAIM_TYPE = "request_text"
STATIC_SOURCE = "STATIC_SKELETON"
CAPTURED_SOURCE = "DYNAMIC_CAPTURE"


@dataclass(frozen=True, slots=True)
class Shape:
    service: str
    method: str
    fields: tuple[str, ...]

    def identity(self) -> str:
        return f"{self.service}.{self.method}({','.join(self.fields)})"


@dataclass(frozen=True, slots=True)
class ShapeDifferential:
    source: str
    added: tuple[str, ...]
    removed: tuple[str, ...]
    mapped: tuple[str, ...]
    unmapped: tuple[str, ...]
    resolved: bool
    reason: str

    def report(self) -> CheckReport:
        if not self.resolved:
            return CheckReport(
                name="request_shape_differential",
                passed=False,
                reasons=(self.reason,),
                unresolved=(f"request_shape_differential: {self.reason}",),
                detail={"source": self.source},
            )
        return CheckReport(
            name="request_shape_differential",
            passed=not self.unmapped,
            reasons=tuple(
                f"changed request shape maps to no obligation: {shape}" for shape in self.unmapped
            ),
            detail={
                "source": self.source,
                "added": list(self.added),
                "removed": list(self.removed),
                "mapped": list(self.mapped),
                "unmapped": list(self.unmapped),
            },
        )


@dataclass(frozen=True, slots=True)
class ConsumerCheck:
    hits: tuple[str, ...]
    unresolved: tuple[str, ...]

    def report(self) -> CheckReport:
        return CheckReport(
            name="response_consumer_check",
            passed=not self.hits,
            reasons=tuple(f"response handling still reads {hit}" for hit in self.hits),
            unresolved=self.unresolved,
            detail={"hits": list(self.hits)},
        )


def shapes_of(ledger: Ledger, captured: Sequence[Mapping[str, Any]]) -> tuple[Shape, ...]:
    found: set[Shape] = set()
    evidence = ledger.evidence_by_id()
    attached = {eid for candidate in ledger.candidates for eid in candidate["evidence_ids"]}
    for eid in sorted(attached):
        record = evidence.get(eid)
        if record is None or record["claim_type"] != REQUEST_CLAIM_TYPE:
            continue
        value = as_mapping(record.get("value"))
        fields = as_sequence(value.get("fragments")) or as_sequence(value.get("fields"))
        found.add(
            Shape(
                service=as_text(value.get("service")) or "",
                method=as_text(value.get("method")) or "",
                fields=tuple(sorted(str(item) for item in fields)),
            )
        )
    for event in captured:
        body = as_mapping(event.get("request"))
        found.add(
            Shape(
                service=as_text(event.get("service")) or "",
                method=as_text(event.get("method")) or "",
                fields=_field_paths(body),
            )
        )
    return tuple(sorted(found, key=lambda item: item.identity()))


def _field_paths(body: Mapping[str, Any], prefix: str = "") -> tuple[str, ...]:
    paths: list[str] = []
    for key in sorted(body):
        name = f"{prefix}{key}"
        value: Any = body[key]
        if is_mapping(value):
            paths.extend(_field_paths(as_mapping(value), f"{name}."))
        else:
            paths.append(name)
    return tuple(paths)


def differential(
    base: Sequence[Shape],
    candidate: Sequence[Shape],
    obligations: Sequence[ObligationView],
    source: str,
    resolved: bool = True,
    reason: str = "",
) -> ShapeDifferential:
    if not resolved:
        return ShapeDifferential(
            source=source,
            added=(),
            removed=(),
            mapped=(),
            unmapped=(),
            resolved=False,
            reason=reason,
        )
    before = {shape.identity() for shape in base}
    after = {shape.identity() for shape in candidate}
    added = tuple(sorted(after - before))
    removed = tuple(sorted(before - after))
    subjects = {item.provider_change_id for item in obligations} | {
        item.verification_method.partition(":")[2].strip() for item in obligations
    }
    mapped: list[str] = []
    unmapped: list[str] = []
    for shape in (*added, *removed):
        if any(subject and subject in shape for subject in subjects):
            mapped.append(shape)
        else:
            unmapped.append(shape)
    return ShapeDifferential(
        source=source,
        added=added,
        removed=removed,
        mapped=tuple(sorted(mapped)),
        unmapped=tuple(sorted(unmapped)),
        resolved=True,
        reason="",
    )


def consumers(graph: ImportGraph, changes: ChangeSet) -> ConsumerCheck:
    watched: dict[str, str] = {}
    for change in (*changes.removed(), *changes.renamed()):
        leaf = change.subject.rsplit(".", 1)[-1]
        if leaf:
            watched[leaf] = change.subject
    if not watched:
        return ConsumerCheck(hits=(), unresolved=())
    hits: set[str] = set()
    unresolved: set[str] = set()
    for atom in graph.atoms:
        subject = watched.get(_leaf(atom.text))
        if subject is None:
            continue
        hits.add(f"{subject} at {atom.path}:{atom.range.start_line}")
    for error in graph.parse_errors:
        unresolved.add(f"response_consumer_check: {error.path} did not parse")
    return ConsumerCheck(hits=tuple(sorted(hits)), unresolved=tuple(sorted(unresolved)))


def _leaf(text: str) -> str:
    stripped = text.strip().strip("'\"[]")
    return stripped.rsplit(".", 1)[-1]


__all__ = [
    "CAPTURED_SOURCE",
    "STATIC_SOURCE",
    "ConsumerCheck",
    "Shape",
    "ShapeDifferential",
    "consumers",
    "differential",
    "shapes_of",
]
