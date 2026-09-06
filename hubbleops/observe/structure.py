from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hubbleops.closure.source_closure import Classification, ClosureEntry, SourceClosure
from hubbleops.core.canonical import blob_hash, content_id
from hubbleops.core.errors import ToolingFailed, ToolingMissing, ToolingTimeout
from hubbleops.core.evidence import make_evidence
from hubbleops.core.observer import ObserverContext, StructuralRule
from hubbleops.core.surface import SurfaceSpec, VersionCarrier
from hubbleops.graph.imports import (
    Assignment,
    AstGrep,
    Call,
    Capture,
    ImportGraph,
    SourceRange,
    SyntaxMatch,
    build,
    language_for,
    scan_rules,
)

name = "structure"
MAX_CALL_DEPTH = 5
SUPPORTED_LANGUAGES = ("javascript", "php", "python", "typescript")
BOUNDARY_NAMES = frozenset(
    {
        "dispatch",
        "emit",
        "enqueue",
        "invoke_remote",
        "publish",
        "request_internal",
        "rpc",
        "send_message",
    }
)
IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$.]*$")
VERSION_LITERAL = re.compile(r"^[vV][0-9]+$")
PYTHON_HOLE = re.compile(r"(?<!\{)\{(?P<hole>[A-Za-z_$][A-Za-z0-9_$.]*)?[^{}]*\}(?!\})")
TEMPLATE_HOLE = re.compile(r"\$\{(?P<hole>[A-Za-z_$][A-Za-z0-9_$.]*)[^{}]*\}")


@dataclass(frozen=True, order=True, slots=True)
class ValuePath:
    path: str
    range: SourceRange
    fragments: tuple[str, ...]
    holes: tuple[str, ...]
    literal: str | None
    terminal: str
    hops: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "range": self.range.to_mapping(),
            "fragments": list(self.fragments),
            "holes": list(self.holes),
            "literal": self.literal,
            "terminal": self.terminal,
            "hops": list(self.hops),
        }


@dataclass(frozen=True, slots=True)
class StructuralCoverage:
    languages: Mapping[str, Mapping[str, int]]

    def to_mapping(self) -> dict[str, dict[str, int]]:
        return {
            language: dict(sorted(counts.items()))
            for language, counts in sorted(self.languages.items())
        }


def rules_hash(rules: Sequence[StructuralRule]) -> str:
    return content_id(
        [
            {
                "id": rule.id,
                "language": rule.language,
                "sha256": rule.sha256,
            }
            for rule in sorted(rules)
        ]
    )


def coverage(
    closure: SourceClosure,
    rules: Sequence[StructuralRule],
    evidence: Sequence[Mapping[str, Any]] = (),
) -> StructuralCoverage:
    active = {rule.language for rule in rules}
    structurally_unscanned = {
        str(record["path"])
        for record in evidence
        if record.get("observer") == name and record.get("claim_type") == "file_unscanned"
    }
    counts: dict[str, dict[str, int]] = {}
    for entry in closure.entries:
        language = language_for(entry.path)
        bucket = counts.setdefault(language, {"supported": 0, "unsupported": 0, "unscanned": 0})
        if entry.classification is Classification.UNSCANNED or entry.path in structurally_unscanned:
            bucket["unscanned"] += 1
        elif entry.classification is Classification.INSIDE:
            bucket["supported" if language in active else "unsupported"] += 1
    return StructuralCoverage(counts)


def scan(closure: SourceClosure, ctx: ObserverContext) -> list[dict[str, Any]]:
    runner = AstGrep(ctx.ast_grep_executable)
    try:
        runner.version()
        return _scan(closure, ctx, runner)
    except (ToolingMissing, ToolingFailed, ToolingTimeout) as error:
        if not ctx.force_structure:
            raise
        return _forced_evidence(closure, ctx, str(error))


def _scan(closure: SourceClosure, ctx: ObserverContext, runner: AstGrep) -> list[dict[str, Any]]:
    inside = tuple(
        entry for entry in closure.entries if entry.classification is Classification.INSIDE
    )
    active = {rule.language for rule in ctx.rules}
    records = [
        _unsupported(entry, ctx, language_for(entry.path))
        for entry in inside
        if language_for(entry.path) not in active
    ]
    paths_by_language = {
        language: tuple(
            entry.path
            for entry in inside
            if language_for(entry.path) == language and language in active
        )
        for language in sorted(active)
    }
    paths_by_language = {language: paths for language, paths in paths_by_language.items() if paths}
    if not paths_by_language:
        return sorted(records, key=_evidence_sort)
    graph = build(closure.root, paths_by_language, runner)
    parse_paths = {match.path for match in graph.parse_errors}
    entries = closure.by_path()
    for path in sorted(parse_paths):
        records.append(_file_unscanned(entries[path], ctx, "syntax parse contains an ERROR node"))
    clean_paths = {
        language: tuple(path for path in paths if path not in parse_paths)
        for language, paths in paths_by_language.items()
    }
    rule_paths = _rule_paths(ctx.rules)
    hits = scan_rules(closure.root, clean_paths, rule_paths, runner)
    records.extend(_carrier_evidence(hits, graph, entries, ctx))
    records.extend(_sink_evidence(hits, graph, entries, ctx))
    records.extend(_boundary_evidence(graph, entries, ctx))
    return sorted(_deduplicate(records), key=_evidence_sort)


def _carrier_evidence(
    hits: Sequence[SyntaxMatch],
    graph: ImportGraph,
    entries: Mapping[str, ClosureEntry],
    ctx: ObserverContext,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for hit in hits:
        carrier = hit.capture("CARRIER")
        version = hit.capture("VERSION")
        if carrier is None and version is None:
            if _carrier_for(hit.text, hit.language, ctx.surface) is None:
                continue
            carrier = Capture("CARRIER", hit.text, hit.range)
        capture = version or carrier
        if capture is None:
            continue
        owner = _owner_id(graph, hit.path, hit.range)
        values = list(
            _resolve_capture(
                graph,
                hit.path,
                capture,
                owner,
                0,
                (f"carrier {hit.path}:{hit.range.start_line}",),
                frozenset(),
            )
        )
        for atom in _contained_atoms(graph, hit.path, capture.range):
            if atom.kind != "identifier":
                continue
            values.extend(
                _resolve_capture(
                    graph,
                    hit.path,
                    Capture("CARRIER_HOLE", atom.text, atom.range),
                    owner,
                    0,
                    (
                        f"carrier {hit.path}:{hit.range.start_line}",
                        f"carrier hole {atom.text}",
                    ),
                    frozenset(),
                )
            )
        values = list(sorted(set(values)))
        direct = _versions(hit.text, hit.language, ctx.surface)
        resolved = direct | {
            item.literal.lower()
            for item in values
            if item.literal is not None and VERSION_LITERAL.fullmatch(item.literal)
        }
        scoped = _carrier_for(hit.text, hit.language, ctx.surface)
        if resolved:
            for subject in sorted(resolved):
                records.append(
                    _evidence(
                        ctx,
                        entries[hit.path],
                        "call_version",
                        hit.range,
                        {
                            "slot": scoped.slot if scoped else "per_call",
                            "pattern": scoped.name if scoped else "computed_version_carrier",
                            "paths": [item.to_mapping() for item in values],
                        },
                        subject,
                    )
                )
            config_keys = sorted(
                {item.literal for item in values if item.literal in ctx.surface.config_env_keys}
            )
            if config_keys:
                records.append(
                    _evidence(
                        ctx,
                        entries[hit.path],
                        "call_version",
                        hit.range,
                        {
                            "slot": scoped.slot if scoped else "per_call",
                            "pattern": scoped.name if scoped else "computed_version_carrier",
                            "terminal": f"UNKNOWN_CONFIG({','.join(config_keys)})",
                            "paths": [item.to_mapping() for item in values],
                        },
                        None,
                    )
                )
                records.extend(_config_records(values, entries, ctx))
        else:
            records.append(
                _evidence(
                    ctx,
                    entries[hit.path],
                    "call_version",
                    hit.range,
                    {
                        "slot": scoped.slot if scoped else "UNKNOWN",
                        "pattern": scoped.name if scoped else "computed_version_carrier",
                        "terminal": "UNRESOLVED_VERSION_CARRIER",
                        "paths": [item.to_mapping() for item in values],
                    },
                    None,
                )
            )
    return records


def _sink_evidence(
    hits: Sequence[SyntaxMatch],
    graph: ImportGraph,
    entries: Mapping[str, ClosureEntry],
    ctx: ObserverContext,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for hit in hits:
        sink = hit.capture("SINK")
        if sink is None:
            continue
        call = _matching_call(graph, hit)
        if call is None:
            continue
        values: list[ValuePath] = []
        for index, argument in enumerate(call.arguments):
            values.extend(
                _resolve_capture(
                    graph,
                    call.path,
                    argument,
                    call.definition_id,
                    0,
                    (f"sink {sink.text} argument {index} at {call.path}:{call.range.start_line}",),
                    frozenset(),
                )
            )
        records.extend(_request_records(values, entries, ctx))
        records.extend(_version_records(values, hit, entries, ctx))
        records.extend(_config_records(values, entries, ctx))
    return records


def _boundary_evidence(
    graph: ImportGraph,
    entries: Mapping[str, ClosureEntry],
    ctx: ObserverContext,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for call in graph.calls:
        if call.callee.rsplit(".", 1)[-1] not in BOUNDARY_NAMES:
            continue
        for index, argument in enumerate(call.arguments):
            values = _resolve_capture(
                graph,
                call.path,
                argument,
                call.definition_id,
                0,
                (f"boundary {call.callee} argument {index}",),
                frozenset(),
            )
            carried = [
                value
                for value in values
                if _request_language(value, ctx.surface) is not None
                or (value.literal is not None and VERSION_LITERAL.fullmatch(value.literal))
            ]
            if not carried:
                continue
            payload = content_id([item.to_mapping() for item in carried])
            records.append(
                _evidence(
                    ctx,
                    entries[call.path],
                    "external_boundary",
                    call.range,
                    {
                        "reason": "captured provider payload crosses a process or service boundary",
                        "payload": payload,
                        "callee": call.callee,
                        "paths": [item.to_mapping() for item in carried],
                    },
                    None,
                )
            )
    return records


def _resolve_capture(
    graph: ImportGraph,
    path: str,
    capture: Capture,
    owner_id: str | None,
    depth: int,
    hops: tuple[str, ...],
    seen: frozenset[tuple[str, int, str]],
) -> tuple[ValuePath, ...]:
    key = (path, capture.range.start_byte, capture.text)
    if key in seen:
        return (_unknown(path, capture.range, "AMBIGUOUS_CYCLE", hops),)
    current_seen = seen | {key}
    if depth > MAX_CALL_DEPTH:
        return (_unknown(path, capture.range, "MAX_DEPTH_5", hops),)
    concatenation = _exact_match(graph.concatenations, path, capture.range)
    if concatenation is not None:
        left = concatenation.capture("LEFT")
        right = concatenation.capture("RIGHT")
        if left is not None and right is not None:
            left_values = _resolve_capture(graph, path, left, owner_id, depth, hops, current_seen)
            right_values = _resolve_capture(graph, path, right, owner_id, depth, hops, current_seen)
            return tuple(
                sorted(
                    {
                        _combine(left_value, right_value, capture.range)
                        for left_value in left_values
                        for right_value in right_values
                    }
                )
            )
    formatting = _exact_match(graph.formats, path, capture.range)
    if formatting is not None:
        format_capture = formatting.capture("FORMAT")
        if format_capture is not None:
            return _resolve_capture(
                graph,
                path,
                format_capture,
                owner_id,
                depth,
                (*hops, f"format expression at {path}:{capture.range.start_line}"),
                current_seen,
            )
    exact_string = _exact_atom(graph, path, capture.range, ("string", "template_string"))
    if exact_string is not None:
        fragments, holes = _skeleton(exact_string.text)
        literal = fragments[0] if len(fragments) == 1 and not holes else None
        terminal = "UNKNOWN_QUERY_HOLE" if holes else "LITERAL"
        return (ValuePath(path, capture.range, fragments, holes, literal, terminal, hops),)
    if IDENTIFIER.fullmatch(capture.text):
        assignment = _assignment(graph, path, capture.text, capture.range, owner_id)
        if assignment is not None:
            return _resolve_capture(
                graph,
                assignment.path,
                assignment.value,
                assignment.definition_id,
                depth,
                (
                    *hops,
                    f"assignment {assignment.target.text} at {path}:{assignment.range.start_line}",
                ),
                current_seen,
            )
        imported = _import_assignment(graph, path, capture.text)
        if imported is not None:
            return _resolve_capture(
                graph,
                imported.path,
                imported.value,
                imported.definition_id,
                depth,
                (*hops, f"import {capture.text} from {imported.path}"),
                current_seen,
            )
        owner = graph.definition(owner_id)
        if owner is not None and capture.text in owner.parameters:
            position = owner.parameters.index(capture.text)
            callers = graph.callers(owner)
            contextual_hops = (*hops, *graph.wrapper_context(owner))
            if not callers:
                return (
                    _unknown(
                        path,
                        capture.range,
                        f"UNRESOLVED_PARAMETER({capture.text})",
                        contextual_hops,
                    ),
                )
            if depth == MAX_CALL_DEPTH:
                return (_unknown(path, capture.range, "MAX_DEPTH_5", contextual_hops),)
            values: list[ValuePath] = []
            for caller in callers:
                argument_position = position
                if len(owner.parameters) == len(caller.arguments) + 1:
                    argument_position -= 1
                if argument_position < 0 or argument_position >= len(caller.arguments):
                    values.append(
                        _unknown(
                            caller.path,
                            caller.range,
                            f"MISSING_ARGUMENT({capture.text})",
                            contextual_hops,
                        )
                    )
                    continue
                values.extend(
                    _resolve_capture(
                        graph,
                        caller.path,
                        caller.arguments[argument_position],
                        caller.definition_id,
                        depth + 1,
                        (
                            *contextual_hops,
                            f"call {caller.callee} carries {capture.text} at "
                            f"{caller.path}:{caller.range.start_line}",
                        ),
                        current_seen,
                    )
                )
            return tuple(sorted(set(values)))
        return (_unknown(path, capture.range, f"UNRESOLVED_SYMBOL({capture.text})", hops),)
    atoms = _contained_atoms(graph, path, capture.range)
    values: list[ValuePath] = []
    for atom in atoms:
        if atom.kind in ("string", "template_string"):
            fragments, holes = _skeleton(atom.text)
            literal = fragments[0] if len(fragments) == 1 and not holes else None
            values.append(
                ValuePath(
                    path,
                    atom.range,
                    fragments,
                    holes,
                    literal,
                    "UNKNOWN_QUERY_HOLE" if holes else "LITERAL",
                    hops,
                )
            )
        elif atom.kind == "identifier" and _leaf_identifier(atom, atoms):
            atom_capture = Capture("ATOM", atom.text, atom.range)
            values.extend(
                _resolve_capture(
                    graph,
                    path,
                    atom_capture,
                    owner_id,
                    depth,
                    hops,
                    current_seen,
                )
            )
    return tuple(sorted(set(values))) or (
        _unknown(path, capture.range, f"UNRESOLVED_EXPRESSION({capture.text})", hops),
    )


def _request_records(
    values: Sequence[ValuePath],
    entries: Mapping[str, ClosureEntry],
    ctx: ObserverContext,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for value in values:
        language = _request_language(value, ctx.surface)
        unresolved_query = value.terminal.startswith(
            ("AMBIGUOUS", "MAX_DEPTH", "MISSING_ARGUMENT", "UNRESOLVED")
        ) and any(
            f" {name} " in f" {hop.lower()} "
            for hop in value.hops
            for name in ("query", "request", "payload")
        )
        if language is None and not unresolved_query:
            continue
        subject = (
            language
            if language is not None
            else ctx.surface.request_languages[0].name
            if ctx.surface.request_languages
            else "request"
        )
        resolution = (
            "UNKNOWN_QUERY_HOLE"
            if value.holes
            else value.terminal
            if unresolved_query
            else "CONTRACT_VALIDATION_DEFERRED"
        )
        records.append(
            _evidence(
                ctx,
                entries[value.path],
                "request_text",
                value.range,
                {
                    "skeleton": {
                        "fragments": list(value.fragments),
                        "holes": list(value.holes),
                    },
                    "resolution": resolution,
                    "wrapper_chain": list(value.hops),
                },
                subject,
            )
        )
    return records


def _version_records(
    values: Sequence[ValuePath],
    hit: SyntaxMatch,
    entries: Mapping[str, ClosureEntry],
    ctx: ObserverContext,
) -> list[dict[str, Any]]:
    versions = sorted(
        {
            value.literal.lower()
            for value in values
            if value.literal is not None and VERSION_LITERAL.fullmatch(value.literal)
        }
    )
    if not versions:
        return []
    return [
        _evidence(
            ctx,
            entries[hit.path],
            "call_version",
            hit.range,
            {
                "slot": "per_call",
                "pattern": "wrapper_resolved_version",
                "paths": [value.to_mapping() for value in values],
            },
            version,
        )
        for version in versions
    ]


def _config_records(
    values: Sequence[ValuePath],
    entries: Mapping[str, ClosureEntry],
    ctx: ObserverContext,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for value in values:
        keys = sorted(key for key in ctx.surface.config_env_keys if key in value.fragments)
        for key in keys:
            records.append(
                _evidence(
                    ctx,
                    entries[value.path],
                    "config_reference",
                    value.range,
                    {
                        "terminal": f"UNKNOWN_CONFIG({key})",
                        "wrapper_chain": list(value.hops),
                    },
                    key,
                )
            )
    return records


def _unsupported(entry: ClosureEntry, ctx: ObserverContext, language: str) -> dict[str, Any]:
    source_hash = entry.blob_sha or blob_hash(b"")
    return make_evidence(
        run_id=ctx.run_id,
        proof_scope_hash=ctx.proof_scope_hash,
        claim_type="structure_unsupported",
        observer=name,
        repo_sha=ctx.repo_sha,
        path=entry.path,
        line_start=None,
        line_end=None,
        source_hash=source_hash,
        value={
            "language": language,
            "reason": "no active structural rule bundle for this INSIDE file",
        },
        provider_subject=None,
        dependency_context_hash=ctx.dependency_context_hash,
        derivation="OBSERVED",
        confidence="PROVEN",
    )


def _file_unscanned(entry: ClosureEntry, ctx: ObserverContext, reason: str) -> dict[str, Any]:
    return make_evidence(
        run_id=ctx.run_id,
        proof_scope_hash=ctx.proof_scope_hash,
        claim_type="file_unscanned",
        observer=name,
        repo_sha=ctx.repo_sha,
        path=entry.path,
        line_start=None,
        line_end=None,
        source_hash=entry.blob_sha or blob_hash(b""),
        value={"reason": reason, "channel": "structure"},
        provider_subject=None,
        dependency_context_hash=ctx.dependency_context_hash,
        derivation="OBSERVED",
        confidence="PROVEN",
    )


def _forced_evidence(
    closure: SourceClosure, ctx: ObserverContext, reason: str
) -> list[dict[str, Any]]:
    return [
        _file_unscanned(entry, ctx, reason)
        for entry in closure.entries
        if entry.classification is Classification.INSIDE
    ]


def _evidence(
    ctx: ObserverContext,
    entry: ClosureEntry,
    claim_type: str,
    source_range: SourceRange,
    value: Any,
    subject: str | None,
) -> dict[str, Any]:
    return make_evidence(
        run_id=ctx.run_id,
        proof_scope_hash=ctx.proof_scope_hash,
        claim_type=claim_type,
        observer=name,
        repo_sha=ctx.repo_sha,
        path=entry.path,
        line_start=source_range.start_line,
        line_end=source_range.end_line,
        source_hash=entry.blob_sha or blob_hash(b""),
        value=value,
        provider_subject=subject,
        dependency_context_hash=ctx.dependency_context_hash,
        derivation="DERIVED_DETERMINISTIC",
        confidence="PROVEN",
    )


def _matching_call(graph: ImportGraph, hit: SyntaxMatch) -> Call | None:
    return next(
        (
            call
            for call in graph.calls
            if call.path == hit.path
            and call.range.start_byte == hit.range.start_byte
            and call.range.end_byte == hit.range.end_byte
        ),
        None,
    )


def _owner_id(graph: ImportGraph, path: str, source_range: SourceRange) -> str | None:
    definitions = [
        item
        for item in graph.definitions
        if item.path == path and item.range.contains(source_range)
    ]
    if not definitions:
        return None
    return min(definitions, key=lambda item: item.range.end_byte - item.range.start_byte).id


def _assignment(
    graph: ImportGraph,
    path: str,
    name_value: str,
    source_range: SourceRange,
    owner_id: str | None,
) -> Assignment | None:
    candidates = [
        item
        for item in graph.assignments
        if item.path == path
        and item.target.text == name_value
        and item.range.start_byte < source_range.start_byte
        and item.definition_id in (None, owner_id)
    ]
    return max(candidates, key=lambda item: item.range.start_byte) if candidates else None


def _import_assignment(graph: ImportGraph, path: str, symbol: str) -> Assignment | None:
    targets = {
        item.target_path
        for item in graph.imports
        if item.path == path and symbol in item.symbols and item.target_path is not None
    }
    candidates = [
        item
        for item in graph.assignments
        if item.path in targets and item.target.text == symbol and item.definition_id is None
    ]
    return (
        min(candidates, key=lambda item: (item.path, item.range.start_byte)) if candidates else None
    )


def _exact_atom(
    graph: ImportGraph,
    path: str,
    source_range: SourceRange,
    kinds: Sequence[str],
) -> SyntaxMatch | None:
    return next(
        (
            item
            for item in graph.atoms
            if item.path == path
            and item.kind in kinds
            and item.range.start_byte == source_range.start_byte
            and item.range.end_byte == source_range.end_byte
        ),
        None,
    )


def _contained_atoms(
    graph: ImportGraph, path: str, source_range: SourceRange
) -> tuple[SyntaxMatch, ...]:
    atoms = [
        item for item in graph.atoms if item.path == path and source_range.contains(item.range)
    ]
    return tuple(sorted(atoms, key=lambda item: (item.range.start_byte, -item.range.end_byte)))


def _exact_match(
    matches: Sequence[SyntaxMatch], path: str, source_range: SourceRange
) -> SyntaxMatch | None:
    return next(
        (
            item
            for item in matches
            if item.path == path
            and item.range.start_byte == source_range.start_byte
            and item.range.end_byte == source_range.end_byte
        ),
        None,
    )


def _leaf_identifier(atom: SyntaxMatch, atoms: Sequence[SyntaxMatch]) -> bool:
    return not any(
        other.kind in ("string", "template_string") and other.range.contains(atom.range)
        for other in atoms
    )


def _skeleton(value: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    body = _string_body(value)
    pattern = TEMPLATE_HOLE if "${" in body else PYTHON_HOLE
    fragments: list[str] = []
    holes: list[str] = []
    position = 0
    for index, match in enumerate(pattern.finditer(body)):
        fragments.append(body[position : match.start()])
        holes.append(match.group("hole") or f"format_{index}")
        position = match.end()
    fragments.append(body[position:])
    return tuple(fragments), tuple(holes)


def _string_body(value: str) -> str:
    stripped = value.strip()
    while stripped and stripped[0].lower() in "rubf" and len(stripped) > 1:
        stripped = stripped[1:]
    for delimiter in ('"""', "'''", '"', "'", "`"):
        if stripped.startswith(delimiter) and stripped.endswith(delimiter):
            return stripped[len(delimiter) : -len(delimiter)]
    return stripped


def _request_language(value: ValuePath, surface: SurfaceSpec) -> str | None:
    text = "".join(value.fragments)
    for language in surface.request_languages:
        if any(re.search(anchor, text, re.IGNORECASE | re.DOTALL) for anchor in language.anchors):
            return language.name
    return None


def _versions(text: str, language: str, surface: SurfaceSpec) -> set[str]:
    versions: set[str] = set()
    for carrier in surface.version_carriers:
        if not _language_applies(carrier, language):
            continue
        for match in re.finditer(carrier.regex, text):
            version = match.groupdict().get("version")
            if version is not None:
                versions.add(version.lower())
    return versions


def _carrier_for(text: str, language: str, surface: SurfaceSpec) -> VersionCarrier | None:
    return next(
        (
            carrier
            for carrier in surface.version_carriers
            if _language_applies(carrier, language) and re.search(carrier.regex, text)
        ),
        None,
    )


def _language_applies(carrier: VersionCarrier, language: str) -> bool:
    return "any" in carrier.languages or language.lower() in carrier.languages


def _rule_paths(rules: Sequence[StructuralRule]) -> dict[str, tuple[Path, ...]]:
    grouped: dict[str, list[Path]] = {}
    for rule in rules:
        grouped.setdefault(rule.language, []).append(rule.path)
    return {language: tuple(sorted(paths)) for language, paths in grouped.items()}


def _unknown(
    path: str, source_range: SourceRange, terminal: str, hops: tuple[str, ...]
) -> ValuePath:
    return ValuePath(path, source_range, (), (), None, terminal, hops)


def _combine(left: ValuePath, right: ValuePath, source_range: SourceRange) -> ValuePath:
    literal = (
        f"{left.literal}{right.literal}"
        if left.literal is not None and right.literal is not None
        else None
    )
    holes = (*left.holes, *right.holes)
    terminal = (
        "UNKNOWN_QUERY_HOLE"
        if holes
        else left.terminal
        if left.terminal != "LITERAL"
        else right.terminal
    )
    return ValuePath(
        left.path,
        source_range,
        (*left.fragments, *right.fragments),
        holes,
        literal,
        terminal,
        (*left.hops, *right.hops),
    )


def _deduplicate(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return list({str(record["id"]): record for record in records}.values())


def _evidence_sort(record: Mapping[str, Any]) -> tuple[str, int, str, str]:
    return (
        str(record["path"]),
        int(record["line_start"] or 0),
        str(record["claim_type"]),
        str(record["id"]),
    )


__all__ = [
    "MAX_CALL_DEPTH",
    "SUPPORTED_LANGUAGES",
    "StructuralCoverage",
    "coverage",
    "name",
    "rules_hash",
    "scan",
]
