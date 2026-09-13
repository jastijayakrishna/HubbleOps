from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from hubbleops.core.records import as_mapping, as_sequence, as_text, is_mapping
from hubbleops.core.verification import ChangeSet, CheckReport, ObligationView, parse_method
from hubbleops.graph import ImportGraph, SyntaxMatch
from hubbleops.observe import Ledger
from hubbleops.verify.oracle import captured_body

REQUEST_CLAIM_TYPE = "request_text"
LITERAL = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"' + r"|'[^'\\]*(?:\\.[^'\\]*)*'")
LITERAL_KINDS = ("string", "template_string")
WORD = re.compile(r"[A-Za-z_]\w*")
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
    changes: ChangeSet | None = None,
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
    subjects = {item.provider_change_id for item in obligations}
    for item in obligations:
        parsed = parse_method(item.verification_method)
        if parsed is not None:
            subjects.add(parsed.argument)
    if changes is not None:
        subjects.update(
            change.replacement
            for change in changes.changes
            if change.replacement and change.subject in subjects
        )
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


def consumers(
    graph: ImportGraph,
    changes: ChangeSet,
    root: Path,
    provider_paths: Collection[str] = (),
) -> ConsumerCheck:
    qualified = {change.subject for change in (*changes.removed(), *changes.renamed())}
    bound = _bound_leaves(changes, qualified)
    watched = qualified | set(bound)
    if not watched:
        return ConsumerCheck(hits=(), unresolved=())
    reachable = frozenset(provider_paths)
    hits: set[str] = set()
    unresolved: set[str] = set()
    sources: dict[str, bytes | None] = {}
    referenced: dict[str, frozenset[str]] = {}
    for atom in graph.atoms:
        name = _unquote(atom.text)
        if name not in watched:
            continue
        if name not in qualified and atom.path not in reachable:
            continue
        source = sources.setdefault(atom.path, _read(root / atom.path))
        if source is None:
            unresolved.add(f"response_consumer_check: {atom.path} could not be read")
            continue
        if not _is_read_position(source, atom.range.end_byte):
            continue
        site = f"{atom.path}:{atom.range.start_line}"
        if name in qualified:
            hits.add(f"{name} at {site}")
            continue
        quoted = name != atom.text.strip()
        if not (quoted or _is_member_access(source, atom.range.start_byte)):
            continue
        for parent in _bound_parents(bound[name], graph, atom.path, referenced):
            hits.add(f"{parent}.{name} at {site}")
    for built in (*graph.concatenations, *graph.formats):
        assembled = _assembled(built)
        if assembled is None:
            unresolved.add(
                f"response_consumer_check: {built.path}:{built.range.start_line} assembles a "
                "name from parts this build cannot resolve"
            )
            continue
        site = f"{built.path}:{built.range.start_line}"
        if assembled in qualified:
            hits.add(f"{assembled} at {site}")
        elif assembled in bound and built.path in reachable:
            for parent in _bound_parents(bound[assembled], graph, built.path, referenced):
                hits.add(f"{parent}.{assembled} at {site}")
    for error in graph.parse_errors:
        unresolved.add(f"response_consumer_check: {error.path} did not parse")
    return ConsumerCheck(hits=tuple(sorted(hits)), unresolved=tuple(sorted(unresolved)))


def _bound_leaves(changes: ChangeSet, qualified: Collection[str]) -> dict[str, frozenset[str]]:
    survivors = {change.subject for change in changes.changes if change.subject not in qualified}
    survivors.update(change.replacement for change in changes.renamed() if change.replacement)
    ambiguous = {subject.rsplit(".", 1)[-1] for subject in survivors}
    parents: dict[str, set[str]] = {}
    for subject in qualified:
        segments = subject.split(".")
        if len(segments) < 2 or segments[-1] in ambiguous:
            continue
        parents.setdefault(segments[-1], set()).add(segments[-2])
    return {leaf: frozenset(found) for leaf, found in parents.items()}


def _bound_parents(
    parents: frozenset[str],
    graph: ImportGraph,
    path: str,
    referenced: dict[str, frozenset[str]],
) -> tuple[str, ...]:
    if path not in referenced:
        referenced[path] = _referenced_names(graph, path)
    return tuple(sorted(parents & referenced[path]))


def _referenced_names(graph: ImportGraph, path: str) -> frozenset[str]:
    names: set[str] = set()
    for atom in graph.atoms_in(path):
        if atom.kind in LITERAL_KINDS:
            names.update(WORD.findall(_unquote(atom.text)))
        else:
            names.add(atom.text.strip())
    return frozenset(names)


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


def _is_member_access(source: bytes, start_byte: int) -> bool:
    index = start_byte - 1
    while index >= 0 and source[index : index + 1].isspace():
        index -= 1
    return index >= 0 and source[index : index + 1] in (b".", b"?")


def _is_read_position(source: bytes, end_byte: int) -> bool:
    index = end_byte
    while index < len(source) and source[index : index + 1].isspace():
        index += 1
    following = source[index : index + 1]
    if following == b":":
        return False
    if following == b"=":
        return source[index + 1 : index + 2] == b"="
    return True


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
