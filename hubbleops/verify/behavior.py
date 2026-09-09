from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from hubbleops.core.records import as_mapping, as_sequence, as_text, is_mapping
from hubbleops.core.verification import ChangeSet, CheckReport, ObligationView
from hubbleops.graph import ImportGraph, SyntaxMatch
from hubbleops.observe import Ledger
from hubbleops.verify.oracle import captured_body

REQUEST_CLAIM_TYPE = "request_text"
LITERAL = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"' + r"|'[^'\\]*(?:\\.[^'\\]*)*'")
ASSEMBLY_GLUE = ("", "%s", "{}", "+", ".")
STATIC_SOURCE = "STATIC_SKELETON"
CAPTURED_SOURCE = "DYNAMIC_CAPTURE"


@dataclass(frozen=True, slots=True)
class Shape:
    service: str
    method: str
    fields: tuple[str, ...]
    version: str

    def identity(self) -> str:
        return f"{self.service}.{self.method}@{self.version}({','.join(self.fields)})"


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
                passed=True,
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
        skeleton = as_mapping(value.get("skeleton"))
        fields = as_sequence(skeleton.get("fragments")) or as_sequence(value.get("matches"))
        if not fields:
            continue
        found.add(
            Shape(
                service=as_text(value.get("service")) or "",
                method=as_text(value.get("method")) or "",
                fields=tuple(sorted(str(item) for item in fields)),
                version=as_text(value.get("version")) or "",
            )
        )
    found.update(captured_shapes(captured))
    return tuple(sorted(found, key=lambda item: item.identity()))


def captured_shapes(captured: Sequence[Mapping[str, Any]]) -> tuple[Shape, ...]:
    found: set[Shape] = set()
    for event in captured:
        body = captured_body(event)
        if body is None:
            continue
        found.add(
            Shape(
                service=as_text(event.get("service")) or "",
                method=as_text(event.get("method")) or "",
                fields=_field_paths(body),
                version=as_text(event.get("version")) or "",
            )
        )
    return tuple(sorted(found, key=lambda item: item.identity()))


def _field_paths(body: Mapping[str, Any], prefix: str = "") -> tuple[str, ...]:
    paths: list[str] = []
    for key in sorted(body):
        name = f"{prefix}{key}"
        paths.extend(_value_paths(body[key], name))
    return tuple(paths)


def _value_paths(value: Any, name: str) -> tuple[str, ...]:
    if is_mapping(value):
        nested = _field_paths(as_mapping(value), f"{name}.")
        return nested or (name,)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        items = cast(Sequence[Any], value)
        nested = {path for item in items for path in _value_paths(item, f"{name}[]")}
        return tuple(sorted(nested)) or (f"{name}[]",)
    return (name,)


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
    patterns = [
        re.compile(rf"(?<![\w.]){re.escape(subject)}(?![\w.])")
        for subject in sorted(subjects)
        if subject
    ]
    mapped: list[str] = []
    unmapped: list[str] = []
    for shape in (*added, *removed):
        if any(pattern.search(shape) for pattern in patterns):
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


def consumers(graph: ImportGraph, changes: ChangeSet, root: Path) -> ConsumerCheck:
    watched: set[str] = set()
    for change in (*changes.removed(), *changes.renamed()):
        watched.add(change.subject)
        leaf = change.subject.rsplit(".", 1)[-1]
        if leaf:
            watched.add(leaf)
    if not watched:
        return ConsumerCheck(hits=(), unresolved=())
    hits: set[str] = set()
    unresolved: set[str] = set()
    sources: dict[str, bytes | None] = {}
    for atom in graph.atoms:
        name = _unquote(atom.text)
        if name not in watched:
            continue
        source = sources.setdefault(atom.path, _read(root / atom.path))
        if source is None:
            unresolved.add(f"response_consumer_check: {atom.path} could not be read")
            continue
        if _is_read_position(source, atom.range.end_byte):
            hits.add(f"{name} at {atom.path}:{atom.range.start_line}")
    for built in (*graph.concatenations, *graph.formats):
        assembled = _assembled(built)
        if assembled is None:
            unresolved.add(
                f"response_consumer_check: {built.path}:{built.range.start_line} assembles a "
                "name from parts this build cannot resolve"
            )
            continue
        if assembled in watched:
            hits.add(f"{assembled} at {built.path}:{built.range.start_line}")
    for error in graph.parse_errors:
        unresolved.add(f"response_consumer_check: {error.path} did not parse")
    return ConsumerCheck(hits=tuple(sorted(hits)), unresolved=tuple(sorted(unresolved)))


def _assembled(built: SyntaxMatch) -> str | None:
    pieces = [_unquote(token.group(0)) for token in LITERAL.finditer(built.text)]
    if not pieces:
        return None
    glue = LITERAL.sub("\x00", built.text).replace("\x00", " ").split()
    if any(token not in ASSEMBLY_GLUE for token in glue):
        return None
    return "".join(pieces)


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _is_read_position(source: bytes, end_byte: int) -> bool:
    index = end_byte
    while index < len(source) and source[index : index + 1].isspace():
        index += 1
    return source[index : index + 1] != b":"


def _unquote(text: str) -> str:
    stripped = text.strip()
    for quote in ("'''", '"""', "'", '"'):
        if (
            stripped.startswith(quote)
            and stripped.endswith(quote)
            and len(stripped) >= 2 * len(quote)
        ):
            return stripped[len(quote) : -len(quote)]
    return stripped


__all__ = [
    "CAPTURED_SOURCE",
    "STATIC_SOURCE",
    "ConsumerCheck",
    "Shape",
    "ShapeDifferential",
    "captured_shapes",
    "consumers",
    "differential",
    "shapes_of",
]
