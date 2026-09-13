from __future__ import annotations

import base64
import json
import re
import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hubbleops.closure.source_closure import Classification, SourceClosure
from hubbleops.core.canonical import EMPTY_SHA256
from hubbleops.core.errors import ToolingFailed, ToolingMissing, ToolingTimeout
from hubbleops.core.evidence import make_evidence
from hubbleops.core.observer import ObserverContext
from hubbleops.core.records import as_mapping, as_sequence, as_text
from hubbleops.core.surface import SurfaceSpec
from hubbleops.core.toolchain import ToolBinary, identity, locate
from hubbleops.graph.imports import language_for, module_aliases, resolve_specifier
from hubbleops.observe.deps import classify_manifest

NAME = "text"
RIPGREP = "rg"
RIPGREP_TIMEOUT_SECONDS = 600.0
VERSION_TIMEOUT_SECONDS = 30.0
MAX_LINE_CHARS = 512
MAX_MATCH_CHARS = 200
MAX_BULK_SUBJECTS = 25
MAX_BULK_SAMPLES = 3
BULK_COLLAPSE_RECORDS = 100

REGEX_METACHARACTERS = "\\^$.|?*+()[]{}"
NAMED_GROUP = re.compile(r"\(\?P<[A-Za-z_][A-Za-z0-9_]*>")

LITERAL = "literal"
VERSION = "version"
LANGUAGE = "language"
RESOURCE = "resource"
MATCH = "match"
KEY = "key"
DIRECT_CONTEXT = "direct"
IMPORTED_CONTEXT = "imported"
NO_CONTEXT = "none"

CONTEXT_KINDS = frozenset({"identifier", "host", "package", "config_key", "version_carrier"})
STRONG_CONTEXT_KINDS = frozenset({"host", "package", "config_key", "version_carrier"})
GATED_KINDS = frozenset({"request_resource", "contract_surface"})
IMPORT_KIND = "module_import"
IMPORT_SPECIFIER_REGEX = r"""(?:from|require\(|import)\s*['"]([^'"\s]+)['"]"""

MERGEABLE_CLAIM_TYPES = frozenset(
    {
        "surface_reference",
        "endpoint_reference",
        "package_reference",
        "config_reference",
        "adjacent_contract",
        "request_text",
        "contract_surface",
    }
)

CLAIM_SPECIFICITY = (
    "surface_reference",
    "endpoint_reference",
    "config_reference",
    "adjacent_contract",
    "request_text",
    "contract_surface",
    "package_reference",
)


@dataclass(slots=True)
class BulkReference:
    role: str
    match_count: int = 0
    line_count: int = 0
    subjects: set[str] = field(default_factory=set[str])
    patterns: set[str] = field(default_factory=set[str])
    samples: list[str] = field(default_factory=list[str])
    first_line: int | None = None

    def observe(self, line_number: int | None, line_text: str) -> None:
        self.line_count += 1
        if self.first_line is None:
            self.first_line = line_number
        if len(self.samples) < MAX_BULK_SAMPLES:
            self.samples.append(line_text[:MAX_LINE_CHARS])

    def to_value(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "match_count": self.match_count,
            "line_count": self.line_count,
            "patterns": sorted(self.patterns),
            "subjects": sorted(self.subjects)[:MAX_BULK_SUBJECTS],
            "subjects_truncated": len(self.subjects) > MAX_BULK_SUBJECTS,
            "samples": list(self.samples),
        }


@dataclass(frozen=True, slots=True)
class TextPattern:
    kind: str
    name: str
    pattern: str
    claim_type: str
    fixed: bool
    subject_mode: str
    slot: str | None = None
    languages: tuple[str, ...] = ("any",)
    resource_group: str = "resource"
    known_resources: tuple[str, ...] = ()
    surface_kind: str | None = None
    gated: bool = True

    def knows(self, resource: str) -> bool:
        return resource.lower() in self.known_resources


@dataclass(slots=True)
class _Observation:
    path: str
    line: int | None
    subject: str | None
    source_hash: str
    classification: str
    line_text: str
    collapsible: bool
    patterns: list[TextPattern] = field(default_factory=list[TextPattern])
    matches: set[str] = field(default_factory=set[str])
    resource_known: bool | None = None

    def observe(self, pattern: TextPattern, matches: Sequence[str], known: bool | None) -> None:
        if pattern not in self.patterns:
            self.patterns.append(pattern)
        self.matches.update(found[:MAX_MATCH_CHARS] for found in matches)
        if known is not None:
            self.resource_known = known

    def claim_type(self) -> str:
        claims = {pattern.claim_type for pattern in self.patterns}
        if len(claims) == 1:
            return claims.pop()
        if "package_reference" in claims and classify_manifest(self.path) is not None:
            return "package_reference"
        for claim in CLAIM_SPECIFICITY:
            if claim in claims:
                return claim
        return min(claims)

    def winner(self) -> TextPattern:
        claim = self.claim_type()
        return min(
            (pattern for pattern in self.patterns if pattern.claim_type == claim),
            key=lambda pattern: (pattern.kind, pattern.name),
        )

    def to_value(self) -> dict[str, Any]:
        winner = self.winner()
        value: dict[str, Any] = {
            "pattern": winner.name,
            "kind": winner.kind,
            "matches": sorted(self.matches),
            "line": self.line_text[:MAX_LINE_CHARS],
            "line_truncated": len(self.line_text) > MAX_LINE_CHARS,
            "classification": self.classification,
        }
        if winner.slot is not None:
            value["slot"] = winner.slot
        if winner.kind == "request_resource":
            value["resource"] = self.subject
            value["resource_known"] = self.resource_known
        if winner.surface_kind is not None:
            value["surface_kind" if winner.kind == "contract_surface" else "scope"] = (
                winner.surface_kind
            )
        if len(self.patterns) > 1:
            value["claims"] = sorted({pattern.claim_type for pattern in self.patterns})
            value["kinds"] = sorted({pattern.kind for pattern in self.patterns})
            value["patterns"] = sorted({pattern.name for pattern in self.patterns})
        return value


def ripgrep_binary() -> ToolBinary:
    try:
        return locate(RIPGREP)
    except ToolingMissing as error:
        raise ToolingMissing(
            RIPGREP,
            f"{error.detail}; the text observer is the recall layer; without it a ledger would "
            "understate the surface, so the scan stops instead of reporting an empty result",
        ) from error


def ripgrep_version() -> str:
    binary = ripgrep_binary()
    try:
        completed = subprocess.run(
            [binary.path, "--version"],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as error:
        raise ToolingMissing(RIPGREP, f"resolved on PATH but not executable: {error}") from error
    except subprocess.TimeoutExpired as error:
        raise ToolingTimeout(RIPGREP, VERSION_TIMEOUT_SECONDS) from error
    if completed.returncode != 0:
        raise ToolingFailed(RIPGREP, completed.stderr.strip() or f"exit {completed.returncode}")
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise ToolingFailed(RIPGREP, "--version produced no output")
    return identity(binary, lines[0])


def patterns_for(surface: SurfaceSpec) -> tuple[TextPattern, ...]:
    patterns: list[TextPattern] = []
    for literal in surface.identifiers:
        patterns.append(
            TextPattern(
                kind="identifier",
                name=literal,
                pattern=literal,
                claim_type="surface_reference",
                fixed=True,
                subject_mode=LITERAL,
            )
        )
    for host in surface.hosts:
        patterns.append(
            TextPattern(
                kind="host",
                name=host,
                pattern=host,
                claim_type="endpoint_reference",
                fixed=True,
                subject_mode=LITERAL,
            )
        )
    for package in surface.package_names:
        patterns.append(
            TextPattern(
                kind="package",
                name=package,
                pattern=package,
                claim_type="package_reference",
                fixed=True,
                subject_mode=LITERAL,
            )
        )
    for key in surface.config_env_keys:
        patterns.append(
            TextPattern(
                kind="config_key",
                name=key,
                pattern=_identifier_bounded(key),
                claim_type="config_reference",
                fixed=False,
                subject_mode=KEY,
            )
        )
    for carrier in surface.version_carriers:
        patterns.append(
            TextPattern(
                kind="version_carrier",
                name=carrier.name,
                pattern=carrier.regex,
                claim_type="call_version",
                fixed=False,
                subject_mode=VERSION,
                slot=carrier.slot,
                languages=carrier.languages,
                surface_kind=carrier.scope,
            )
        )
    for language in surface.request_languages:
        for index, anchor in enumerate(language.anchors):
            patterns.append(
                TextPattern(
                    kind="request_language",
                    name=f"{language.name}#{index}",
                    pattern=anchor,
                    claim_type="request_text",
                    fixed=False,
                    subject_mode=LANGUAGE,
                )
            )
        for index, shape in enumerate(language.shapes):
            patterns.append(
                TextPattern(
                    kind="request_resource",
                    name=f"{language.name}#resource{index}",
                    pattern=shape,
                    claim_type="request_text",
                    fixed=False,
                    subject_mode=RESOURCE,
                    resource_group=language.resource_group,
                    known_resources=language.known_resources,
                )
            )
    for contract in surface.adjacent_contracts:
        for host in contract.hosts:
            patterns.append(
                TextPattern(
                    kind="adjacent_contract",
                    name=contract.name,
                    pattern=host,
                    claim_type="adjacent_contract",
                    fixed=True,
                    subject_mode=LITERAL,
                )
            )
    for item in surface.contract_surfaces:
        patterns.append(
            TextPattern(
                kind="contract_surface",
                name=item.name,
                pattern=item.shape,
                claim_type="contract_surface",
                fixed=False,
                subject_mode=MATCH,
                surface_kind=item.kind,
                gated=item.gated,
            )
        )
    if any(item.kind in GATED_KINDS for item in patterns):
        patterns.append(
            TextPattern(
                kind=IMPORT_KIND,
                name=IMPORT_KIND,
                pattern=IMPORT_SPECIFIER_REGEX,
                claim_type="contract_surface",
                fixed=False,
                subject_mode=MATCH,
            )
        )
    return tuple(sorted(patterns, key=lambda item: (item.kind, item.name, item.pattern)))


def scan(closure: SourceClosure, ctx: ObserverContext) -> list[dict[str, Any]]:
    ripgrep_version()
    patterns = [
        (pattern, None if pattern.fixed else _compile(pattern))
        for pattern in patterns_for(ctx.surface)
    ]
    entries = closure.by_path()
    records: dict[str, dict[str, Any]] = {}
    pending: dict[tuple[str, int | None, str | None, str], _Observation] = {}
    undecodable: dict[str, str] = {}
    bulk: dict[str, BulkReference] = {}
    dense: dict[str, list[str]] = {}
    context_paths: set[str] = set()
    strong_paths: set[str] = set()
    import_edges: list[tuple[str, str]] = []
    context_lines: dict[str, set[int | None]] = {}
    import_lines: dict[tuple[str, int | None], set[str]] = {}
    deferred: list[tuple[TextPattern, str, int | None, str, str, tuple[str, ...], str]] = []

    enumerated = frozenset(entries)
    for hit in _search(
        closure.root,
        [pattern for pattern, _ in patterns],
        enumerated,
        closure.search_exclusion_globs(),
    ):
        entry = entries.get(hit.path)
        if entry is None:
            raise ToolingFailed(
                RIPGREP,
                f"{hit.path}:{hit.line_number} matched a surface pattern but the source closure "
                "never enumerated that path; the scan stops rather than dropping an observation "
                "the closure cannot account for",
            )
        if hit.line_text is None:
            undecodable[hit.path] = (
                hit.reason or "non_utf8: matched content is not decodable as UTF-8"
            )
            continue
        if entry.carries_bulk_data():
            reference = bulk.setdefault(hit.path, BulkReference(role=entry.role.value))
            reference.observe(hit.line_number, hit.line_text)
            for pattern, regex in patterns:
                if (
                    "any" not in pattern.languages
                    and language_for(hit.path) not in pattern.languages
                ):
                    continue
                matches = _pattern_matches(pattern, regex, hit.line_text)
                if not matches:
                    continue
                reference.match_count += len(matches)
                reference.patterns.add(pattern.name)
                reference.subjects.update(
                    subject for subject in _subjects(pattern, regex, hit.line_text) if subject
                )
            continue
        attributed = 0
        for pattern, regex in patterns:
            if "any" not in pattern.languages and language_for(hit.path) not in pattern.languages:
                continue
            matches = _pattern_matches(pattern, regex, hit.line_text)
            if not matches:
                continue
            if pattern.kind == IMPORT_KIND:
                for found in _specifiers(regex, hit.line_text):
                    import_edges.append((hit.path, found))
                    import_lines.setdefault((hit.path, hit.line_number), set()).add(found)
                continue
            attributed += 1
            if pattern.kind in CONTEXT_KINDS:
                context_paths.add(hit.path)
                context_lines.setdefault(hit.path, set()).add(hit.line_number)
            if pattern.kind in STRONG_CONTEXT_KINDS:
                strong_paths.add(hit.path)
            if pattern.kind in GATED_KINDS:
                deferred.append(
                    (
                        pattern,
                        hit.path,
                        hit.line_number,
                        hit.line_text,
                        entry.blob_sha or EMPTY_SHA256,
                        tuple(sorted({found[:MAX_MATCH_CHARS] for found in matches})),
                        entry.classification.value,
                    )
                )
                continue
            for subject in _subjects(pattern, regex, hit.line_text):
                _observe(
                    pending,
                    pattern=pattern,
                    path=hit.path,
                    line=hit.line_number,
                    subject=subject,
                    matches=matches,
                    line_text=hit.line_text,
                    source_hash=entry.blob_sha or EMPTY_SHA256,
                    classification=entry.classification.value,
                    collapsible=entry.may_collapse_references(),
                    known=None,
                )
        if attributed == 0:
            continue

    first_party_import_lines: set[tuple[str, int | None]] = set()
    reaches: dict[str, set[str]] = {}
    if import_edges:
        aliases = module_aliases(closure.root)
        resolved_specifiers: dict[tuple[str, str], str | None] = {}
        for importer, specifier in sorted(set(import_edges)):
            target = resolve_specifier(
                importer, specifier, language_for(importer), tuple(enumerated), aliases
            )
            resolved_specifiers[(importer, specifier)] = target
            if target is not None and target != importer:
                reaches.setdefault(importer, set()).add(target)
        for (path, line_number), specifiers in import_lines.items():
            if all(resolved_specifiers.get((path, item)) is not None for item in specifiers):
                first_party_import_lines.add((path, line_number))
    if deferred and reaches:
        growing = True
        while growing:
            growing = False
            for importer in sorted(reaches):
                if importer in context_paths:
                    continue
                if any(target in context_paths for target in reaches[importer]):
                    context_paths.add(importer)
                    growing = True

    direct_paths = {
        path
        for path, lines in context_lines.items()
        if any((path, line_number) not in first_party_import_lines for line_number in lines)
    }
    file_contexts = {
        path: (
            DIRECT_CONTEXT
            if path in direct_paths
            else IMPORTED_CONTEXT
            if path in context_paths
            else NO_CONTEXT
        )
        for path in enumerated
    }

    compiled_by_name = {pattern.name: regex for pattern, regex in patterns}
    for pattern, path, line_number, line_text, blob, matches, classification in sorted(
        deferred, key=lambda item: (item[1], item[2] or 0, item[0].kind, item[0].name)
    ):
        regex = compiled_by_name.get(pattern.name)
        for subject in _subjects(pattern, regex, line_text):
            if subject is None:
                continue
            known = pattern.kind == "request_resource" and pattern.knows(subject)
            if pattern.kind == "request_resource" and not known:
                if path not in strong_paths:
                    continue
            elif not known and pattern.gated and path not in context_paths:
                continue
            _observe(
                pending,
                pattern=pattern,
                path=path,
                line=line_number,
                subject=subject,
                matches=matches,
                line_text=line_text,
                source_hash=blob,
                classification=classification,
                collapsible=False,
                known=known if pattern.kind == "request_resource" else None,
            )

    for key in sorted(pending, key=lambda item: (item[0], item[1] or 0, item[2] or "", item[3])):
        observation = pending[key]
        record = make_evidence(
            run_id=ctx.run_id,
            proof_scope_hash=ctx.proof_scope_hash,
            claim_type=observation.claim_type(),
            observer=NAME,
            repo_sha=ctx.repo_sha,
            path=observation.path,
            line_start=observation.line,
            line_end=observation.line,
            source_hash=observation.source_hash,
            value={
                **observation.to_value(),
                "file_context": file_contexts.get(observation.path, NO_CONTEXT),
            },
            provider_subject=observation.subject,
            dependency_context_hash=ctx.dependency_context_hash,
            derivation="OBSERVED",
            confidence="RAW",
        )
        records[record["id"]] = record
        if observation.collapsible:
            dense.setdefault(observation.path, []).append(record["id"])

    for path in sorted(dense):
        ids = dense[path]
        if len(ids) < BULK_COLLAPSE_RECORDS:
            continue
        entry = entries[path]
        reference = BulkReference(role=entry.role.value)
        for identifier in ids:
            collapsed = records.pop(identifier)
            value = dict(as_mapping(collapsed["value"]))
            reference.observe(collapsed["line_start"], as_text(value.get("line")) or "")
            reference.match_count += len(as_sequence(value.get("matches")))
            reference.patterns.add(as_text(value.get("pattern")) or "")
            if collapsed["provider_subject"] is not None:
                reference.subjects.add(str(collapsed["provider_subject"]))
        reference.line_count = len(ids)
        record = make_evidence(
            run_id=ctx.run_id,
            proof_scope_hash=ctx.proof_scope_hash,
            claim_type="bulk_data_reference",
            observer=NAME,
            repo_sha=ctx.repo_sha,
            path=path,
            line_start=reference.first_line,
            line_end=reference.first_line,
            source_hash=entry.blob_sha or EMPTY_SHA256,
            value=reference.to_value(),
            provider_subject=None,
            dependency_context_hash=ctx.dependency_context_hash,
            derivation="OBSERVED",
            confidence="RAW",
        )
        records[record["id"]] = record

    for path in sorted(undecodable):
        entry = entries[path]
        record = make_evidence(
            run_id=ctx.run_id,
            proof_scope_hash=ctx.proof_scope_hash,
            claim_type="file_unscanned",
            observer=NAME,
            repo_sha=ctx.repo_sha,
            path=path,
            line_start=None,
            line_end=None,
            source_hash=entry.blob_sha or EMPTY_SHA256,
            value={"reason": undecodable[path]},
            provider_subject=None,
            dependency_context_hash=ctx.dependency_context_hash,
            derivation="OBSERVED",
            confidence="RAW",
        )
        records[record["id"]] = record

    for path in sorted(bulk):
        reference = bulk[path]
        if reference.match_count == 0:
            continue
        entry = entries[path]
        record = make_evidence(
            run_id=ctx.run_id,
            proof_scope_hash=ctx.proof_scope_hash,
            claim_type="bulk_data_reference",
            observer=NAME,
            repo_sha=ctx.repo_sha,
            path=path,
            line_start=reference.first_line,
            line_end=reference.first_line,
            source_hash=entry.blob_sha or EMPTY_SHA256,
            value=reference.to_value(),
            provider_subject=None,
            dependency_context_hash=ctx.dependency_context_hash,
            derivation="OBSERVED",
            confidence="RAW",
        )
        records[record["id"]] = record

    for record in _closure_records(closure, ctx):
        records[record["id"]] = record

    return sorted(
        records.values(),
        key=lambda item: (
            item["path"],
            item["line_start"] or 0,
            item["claim_type"],
            item["provider_subject"] or "",
            item["id"],
        ),
    )


def _observe(
    pending: dict[tuple[str, int | None, str | None, str], _Observation],
    *,
    pattern: TextPattern,
    path: str,
    line: int | None,
    subject: str | None,
    matches: Sequence[str],
    line_text: str,
    source_hash: str,
    classification: str,
    collapsible: bool,
    known: bool | None,
) -> None:
    bucket = (
        ""
        if pattern.claim_type in MERGEABLE_CLAIM_TYPES
        else f"{pattern.claim_type}:{pattern.name}"
    )
    key = (path, line, subject, bucket)
    observation = pending.get(key)
    if observation is None:
        observation = _Observation(
            path=path,
            line=line,
            subject=subject,
            source_hash=source_hash,
            classification=classification,
            line_text=line_text,
            collapsible=collapsible,
        )
        pending[key] = observation
    observation.observe(pattern, matches, known)


def _closure_records(closure: SourceClosure, ctx: ObserverContext) -> Iterator[dict[str, Any]]:
    for entry in closure.entries:
        if entry.classification is Classification.UNSCANNED:
            yield make_evidence(
                run_id=ctx.run_id,
                proof_scope_hash=ctx.proof_scope_hash,
                claim_type="file_unscanned",
                observer=NAME,
                repo_sha=ctx.repo_sha,
                path=entry.path,
                line_start=None,
                line_end=None,
                source_hash=entry.blob_sha or EMPTY_SHA256,
                value={"reason": entry.reason},
                provider_subject=None,
                dependency_context_hash=ctx.dependency_context_hash,
                derivation="OBSERVED",
                confidence="RAW",
            )
        elif entry.classification is Classification.EXTERNAL_BOUNDARY:
            yield make_evidence(
                run_id=ctx.run_id,
                proof_scope_hash=ctx.proof_scope_hash,
                claim_type="external_boundary",
                observer=NAME,
                repo_sha=ctx.repo_sha,
                path=entry.path,
                line_start=None,
                line_end=None,
                source_hash=entry.blob_sha or EMPTY_SHA256,
                value={"reason": entry.reason},
                provider_subject=None,
                dependency_context_hash=ctx.dependency_context_hash,
                derivation="OBSERVED",
                confidence="RAW",
            )


@dataclass(frozen=True, slots=True)
class TextHit:
    path: str
    line_number: int
    line_text: str | None
    reason: str | None = None


def _compile(pattern: TextPattern) -> re.Pattern[str]:
    try:
        return re.compile(pattern.pattern)
    except re.error as error:
        raise ToolingFailed(
            RIPGREP, f"surface pattern {pattern.name!r} is not a usable regex: {error}"
        ) from error


def _pattern_matches(
    pattern: TextPattern, compiled: re.Pattern[str] | None, line: str
) -> tuple[str, ...]:
    if pattern.fixed:
        return (pattern.pattern,) if pattern.pattern in line else ()
    if compiled is None:
        return ()
    return tuple(match.group(0) for match in compiled.finditer(line))


def _subjects(
    pattern: TextPattern, compiled: re.Pattern[str] | None, line: str
) -> tuple[str | None, ...]:
    if pattern.subject_mode == LITERAL:
        return (pattern.pattern,)
    if pattern.subject_mode == KEY:
        return (pattern.name,)
    if pattern.subject_mode == LANGUAGE:
        return (pattern.name.split("#", 1)[0],)
    if pattern.subject_mode == RESOURCE:
        if compiled is None or pattern.resource_group not in compiled.groupindex:
            return (None,)
        found = sorted(
            {
                match.group(pattern.resource_group).lower()
                for match in compiled.finditer(line)
                if match.group(pattern.resource_group)
            }
        )
        return tuple(found) if found else (None,)
    if pattern.subject_mode == MATCH:
        if compiled is None:
            return (None,)
        found = sorted({match.group(0)[:MAX_MATCH_CHARS] for match in compiled.finditer(line)})
        return tuple(found) if found else (None,)
    if compiled is None or "version" not in compiled.groupindex:
        return (None,)
    found = sorted(
        {match.group("version") for match in compiled.finditer(line) if match.group("version")}
    )
    return tuple(found) if found else (None,)


def _specifiers(compiled: re.Pattern[str] | None, line: str) -> tuple[str, ...]:
    if compiled is None:
        return ()
    return tuple(sorted({match.group(1) for match in compiled.finditer(line) if match.lastindex}))


def _escape_literal(value: str) -> str:
    return "".join(
        "\\" + character if character in REGEX_METACHARACTERS else character for character in value
    )


def _identifier_bounded(value: str) -> str:
    return rf"(?:^|[^A-Za-z0-9_]){_escape_literal(value)}(?:[^A-Za-z0-9_]|$)"


def _ripgrep_pattern(pattern: TextPattern) -> str:
    if pattern.fixed:
        return _escape_literal(pattern.pattern)
    return NAMED_GROUP.sub("(?:", pattern.pattern)


def unreadable_failures(stderr: str) -> dict[str, str]:
    prefix = f"{RIPGREP}: "
    found: dict[str, str] = {}
    for line in stderr.splitlines():
        text = line.strip()
        if not text.startswith(prefix):
            continue
        head, separator, detail = text[len(prefix) :].partition(": ")
        if separator and head:
            found[_normalize(head)] = detail
    return found


def unreadable_paths(stderr: str) -> tuple[str, ...]:
    return tuple(unreadable_failures(stderr))


def _search(
    root: Path,
    patterns: Sequence[TextPattern],
    accounted: frozenset[str],
    exclusion_globs: Sequence[str],
) -> Iterator[TextHit]:
    if not patterns:
        return
    args = [
        ripgrep_binary().path,
        "--json",
        "--no-config",
        "--hidden",
        "--no-ignore",
        "--color=never",
    ]
    for glob in exclusion_globs:
        args.extend(["-g", glob])
    for pattern in patterns:
        args.extend(["-e", _ripgrep_pattern(pattern)])
    args.extend(["--", "."])
    try:
        completed = subprocess.run(
            args,
            cwd=root,
            capture_output=True,
            timeout=RIPGREP_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as error:
        raise ToolingMissing(RIPGREP, f"disappeared from PATH mid-scan: {error}") from error
    except subprocess.TimeoutExpired as error:
        raise ToolingTimeout(RIPGREP, RIPGREP_TIMEOUT_SECONDS) from error
    stderr = completed.stderr.decode("utf-8", errors="replace")
    if completed.returncode not in (0, 1):
        unreadable = unreadable_failures(stderr)
        unexpected = tuple(path for path in unreadable if path not in accounted)
        if unexpected or not unreadable:
            raise ToolingFailed(RIPGREP, f"exit {completed.returncode}: {stderr.strip()}")
    for line in completed.stdout.decode("utf-8", errors="replace").split("\n"):
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("type") != "match":
            continue
        data = event["data"]
        path = _decode(data.get("path"))
        if path is None:
            raise ToolingFailed(RIPGREP, "non-UTF-8 path in match output")
        yield TextHit(
            path=_normalize(path),
            line_number=int(data["line_number"]),
            line_text=_decode(data.get("lines")),
        )
    for path, detail in sorted(unreadable_failures(stderr).items()):
        if path in accounted:
            yield TextHit(
                path=path,
                line_number=0,
                line_text=None,
                reason=f"ripgrep_read_error: {detail}",
            )


def _decode(payload: Any) -> str | None:
    fields = as_mapping(payload)
    text = as_text(fields.get("text"))
    if text is not None:
        return text
    raw = as_text(fields.get("bytes"))
    if raw is None:
        return None
    try:
        return base64.b64decode(raw).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def _normalize(path: str) -> str:
    cleaned = path.replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned
