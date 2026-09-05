from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hubbleops.closure.source_closure import Classification, SourceClosure
from hubbleops.core.canonical import EMPTY_SHA256
from hubbleops.core.errors import ToolingFailed, ToolingMissing, ToolingTimeout
from hubbleops.core.evidence import make_evidence
from hubbleops.core.observer import ObserverContext
from hubbleops.core.records import as_mapping, as_text
from hubbleops.core.surface import SurfaceSpec

NAME = "text"
RIPGREP = "rg"
RIPGREP_TIMEOUT_SECONDS = 600.0
VERSION_TIMEOUT_SECONDS = 30.0
MAX_LINE_CHARS = 512
MAX_MATCH_CHARS = 200

REGEX_METACHARACTERS = "\\^$.|?*+()[]{}"
NAMED_GROUP = re.compile(r"\(\?P<[A-Za-z_][A-Za-z0-9_]*>")

LITERAL = "literal"
VERSION = "version"
LANGUAGE = "language"


@dataclass(frozen=True, slots=True)
class TextPattern:
    kind: str
    name: str
    pattern: str
    claim_type: str
    fixed: bool
    subject_mode: str
    slot: str | None = None


def ripgrep_version() -> str:
    if shutil.which(RIPGREP) is None:
        raise ToolingMissing(
            RIPGREP,
            "the text observer is the recall layer; without it a ledger would understate the "
            "surface, so the scan stops instead of reporting an empty result",
        )
    try:
        completed = subprocess.run(
            [RIPGREP, "--version"],
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
    return lines[0]


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
                pattern=key,
                claim_type="config_reference",
                fixed=True,
                subject_mode=LITERAL,
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
    return tuple(sorted(patterns, key=lambda item: (item.kind, item.name, item.pattern)))


def scan(closure: SourceClosure, ctx: ObserverContext) -> list[dict[str, Any]]:
    ripgrep_version()
    patterns = [
        (pattern, None if pattern.fixed else _compile(pattern))
        for pattern in patterns_for(ctx.surface)
    ]
    entries = closure.by_path()
    records: dict[str, dict[str, Any]] = {}
    undecodable: dict[str, str] = {}

    enumerated = frozenset(entries)
    for hit in _search(closure.root, [pattern for pattern, _ in patterns], enumerated):
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
        attributed = 0
        for pattern, regex in patterns:
            matches = _pattern_matches(pattern, regex, hit.line_text)
            if not matches:
                continue
            attributed += 1
            for subject in _subjects(pattern, regex, hit.line_text):
                value: dict[str, Any] = {
                    "pattern": pattern.name,
                    "kind": pattern.kind,
                    "matches": sorted({found[:MAX_MATCH_CHARS] for found in matches}),
                    "line": hit.line_text[:MAX_LINE_CHARS],
                    "line_truncated": len(hit.line_text) > MAX_LINE_CHARS,
                    "classification": entry.classification.value,
                }
                if pattern.slot is not None:
                    value["slot"] = pattern.slot
                record = make_evidence(
                    run_id=ctx.run_id,
                    proof_scope_hash=ctx.proof_scope_hash,
                    claim_type=pattern.claim_type,
                    observer=NAME,
                    repo_sha=ctx.repo_sha,
                    path=hit.path,
                    line_start=hit.line_number,
                    line_end=hit.line_number,
                    source_hash=entry.blob_sha or EMPTY_SHA256,
                    value=value,
                    provider_subject=subject,
                    dependency_context_hash=ctx.dependency_context_hash,
                    derivation="OBSERVED",
                    confidence="RAW",
                )
                records[record["id"]] = record
        if attributed == 0:
            raise ToolingFailed(
                RIPGREP,
                f"{hit.path}:{hit.line_number} was reported as a surface match but no surface "
                "pattern claims it; the scan stops rather than dropping an observation",
            )

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
    if pattern.subject_mode == LANGUAGE:
        return (pattern.name.split("#", 1)[0],)
    if compiled is None or "version" not in compiled.groupindex:
        return (None,)
    found = sorted(
        {match.group("version") for match in compiled.finditer(line) if match.group("version")}
    )
    return tuple(found) if found else (None,)


def _escape_literal(value: str) -> str:
    return "".join(
        "\\" + character if character in REGEX_METACHARACTERS else character for character in value
    )


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
    root: Path, patterns: Sequence[TextPattern], accounted: frozenset[str]
) -> Iterator[TextHit]:
    if not patterns:
        return
    args = [
        RIPGREP,
        "--json",
        "--no-config",
        "--hidden",
        "--no-ignore",
        "--color=never",
        "-g",
        "!.git/",
    ]
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
    for line in completed.stdout.decode("utf-8", errors="replace").splitlines():
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
