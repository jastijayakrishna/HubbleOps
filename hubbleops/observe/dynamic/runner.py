from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker

from hubbleops.core.canonical import EMPTY_SHA256, canonical_bytes
from hubbleops.core.errors import HubbleOpsError
from hubbleops.core.evidence import make_evidence
from hubbleops.core.observer import ObserverContext
from hubbleops.core.records import as_mapping, as_sequence, as_text

SCHEMA_PATH = Path(__file__).parent / "schema.json"
MAX_EVENT_FILE_BYTES = 16_777_216
MAX_EVENTS = 10_000


class DynamicEventInvalid(HubbleOpsError):
    pass


@dataclass(frozen=True, slots=True)
class EventIssue:
    code: str
    row: int
    reason: str


@dataclass(frozen=True, slots=True)
class EventBatch:
    events: tuple[dict[str, Any], ...]
    issues: tuple[EventIssue, ...]

    def bytes(self) -> bytes:
        return b"".join(canonical_bytes(event) + b"\n" for event in self.events)


@cache
def event_schema_bytes() -> bytes:
    return SCHEMA_PATH.read_bytes()


@cache
def event_schema_hash() -> str:
    return hashlib.sha256(event_schema_bytes()).hexdigest()


@cache
def _validator() -> Draft202012Validator:
    document: dict[str, Any] = json.loads(event_schema_bytes())
    return Draft202012Validator(document, format_checker=FormatChecker())


def normalize_event(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DynamicEventInvalid("dynamic event must be an object")
    record = dict(as_mapping(cast(object, value)))
    validator = cast(Any, _validator())
    errors = sorted(validator.iter_errors(record), key=lambda item: list(item.absolute_path))
    if errors:
        detail = "; ".join(
            f"{'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
            for error in errors
        )
        raise DynamicEventInvalid(f"dynamic event rejected: {detail}")
    timestamp = as_text(record.get("ts"))
    if timestamp is None or not timestamp.endswith("Z"):
        raise DynamicEventInvalid("dynamic event timestamp must be RFC 3339 UTC ending in Z")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise DynamicEventInvalid("dynamic event timestamp is invalid") from error
    if parsed.tzinfo != UTC:
        raise DynamicEventInvalid("dynamic event timestamp must use UTC")
    stack: list[dict[str, Any]] = []
    for raw in as_sequence(record.get("stack")):
        frame = dict(as_mapping(raw))
        path = as_text(frame.get("path")) or ""
        if "\\" in path or "\x00" in path:
            raise DynamicEventInvalid("stack paths must be slash-normalized and contain no NUL")
        if frame.get("kind") == "repository":
            pure = PurePosixPath(path)
            if pure.is_absolute() or ".." in pure.parts:
                raise DynamicEventInvalid("repository stack paths must be relative and confined")
        elif frame.get("kind") in ("dependency", "runtime"):
            expected = f"<{frame['kind']}>/"
            if not path.startswith(expected):
                raise DynamicEventInvalid(f"{frame['kind']} stack paths must start with {expected}")
        stack.append(frame)
    record["stack"] = stack
    return record


def events_from_jsonl(data: bytes) -> EventBatch:
    if len(data) > MAX_EVENT_FILE_BYTES:
        return EventBatch((), (EventIssue("EVENT_FILE_TOO_LARGE", 0, "event input exceeds bound"),))
    events: list[dict[str, Any]] = []
    issues: list[EventIssue] = []
    for row, line in enumerate(data.splitlines(), start=1):
        if not line.strip():
            continue
        if len(events) >= MAX_EVENTS:
            issues.append(EventIssue("EVENT_COUNT_LIMIT", row, "event count exceeds bound"))
            break
        try:
            raw = json.loads(line)
            event = normalize_event(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, DynamicEventInvalid) as error:
            issues.append(EventIssue("EVENT_INVALID", row, str(error)))
            continue
        if any(as_mapping(frame).get("kind") == "truncation" for frame in event["stack"]):
            issues.append(EventIssue("STACK_TRUNCATED", row, "event stack exceeded frame bound"))
        events.append(event)
    ordered = tuple(sorted(events, key=canonical_bytes))
    return EventBatch(ordered, tuple(issues))


def events_to_evidence(
    batch: EventBatch,
    ctx: ObserverContext,
    repository: Path,
    *,
    observer: str = "dynamic",
    allow_site_claims: bool = True,
    candidate_ids: Mapping[tuple[str, str, str], tuple[str, ...]] | None = None,
) -> list[dict[str, Any]]:
    if observer == "sentinel" and allow_site_claims:
        raise DynamicEventInvalid("sentinel evidence cannot attach to static call-site identities")
    records: list[dict[str, Any]] = []
    for event in batch.events:
        key = (str(event["service"]), str(event["method"]), str(event["version"]))
        mappings = () if candidate_ids is None else candidate_ids.get(key, ())
        stack_sources = _stack_sources(event, repository)
        records.append(_production_evidence(event, ctx, observer, mappings, stack_sources))
        if allow_site_claims:
            frame = _repository_frame(event)
            if frame is not None:
                records.extend(_site_evidence(event, frame, ctx, repository, stack_sources))
    for issue in batch.issues:
        records.append(_issue_evidence(issue, ctx, observer))
    return records


def _production_evidence(
    event: dict[str, Any],
    ctx: ObserverContext,
    observer: str,
    candidate_ids: tuple[str, ...],
    stack_sources: tuple[dict[str, str], ...],
) -> dict[str, Any]:
    value = {
        "event_hash": hashlib.sha256(canonical_bytes(event)).hexdigest(),
        "method": event["method"],
        "mode": event.get("mode"),
        "request_type": event["request_type"],
        "service": event["service"],
        "version": event["version"],
        "stack": event["stack"],
        "stack_sources": list(stack_sources),
        "candidate_ids": list(candidate_ids),
    }
    return make_evidence(
        run_id=ctx.run_id,
        proof_scope_hash=ctx.proof_scope_hash,
        claim_type="production_version",
        observer=observer,
        repo_sha=ctx.repo_sha,
        path=".",
        line_start=None,
        line_end=None,
        source_hash=EMPTY_SHA256,
        value=value,
        provider_subject=str(event["version"]),
        dependency_context_hash=ctx.dependency_context_hash,
        derivation="OBSERVED",
        confidence="PROVEN",
    )


def _site_evidence(
    event: dict[str, Any],
    frame: dict[str, Any],
    ctx: ObserverContext,
    repository: Path,
    stack_sources: tuple[dict[str, str], ...],
) -> list[dict[str, Any]]:
    relative = str(frame["path"])
    source = (repository.resolve() / PurePosixPath(relative)).resolve()
    try:
        source.relative_to(repository.resolve())
    except ValueError as error:
        raise DynamicEventInvalid(f"event stack path escapes repository: {relative}") from error
    if not source.is_file():
        raise DynamicEventInvalid(f"event stack source is missing: {relative}")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    line = frame.get("line")
    common = {
        "method": event["method"],
        "mode": event.get("mode"),
        "service": event["service"],
        "stack": event["stack"],
        "stack_sources": list(stack_sources),
    }
    records = [
        make_evidence(
            run_id=ctx.run_id,
            proof_scope_hash=ctx.proof_scope_hash,
            claim_type="call_version",
            observer="dynamic",
            repo_sha=ctx.repo_sha,
            path=relative,
            line_start=line,
            line_end=line,
            source_hash=source_hash,
            value={**common, "slot": "per_call", "pattern": "executed_stack"},
            provider_subject=str(event["version"]),
            dependency_context_hash=ctx.dependency_context_hash,
            derivation="OBSERVED",
            confidence="PROVEN",
        )
    ]
    if event["request_text"] is not None:
        records.append(
            make_evidence(
                run_id=ctx.run_id,
                proof_scope_hash=ctx.proof_scope_hash,
                claim_type="request_text",
                observer="dynamic",
                repo_sha=ctx.repo_sha,
                path=relative,
                line_start=line,
                line_end=line,
                source_hash=source_hash,
                value={**common, "request_text": event["request_text"], "resolution": "EXECUTED"},
                provider_subject=str(event["request_type"]),
                dependency_context_hash=ctx.dependency_context_hash,
                derivation="OBSERVED",
                confidence="PROVEN",
            )
        )
    return records


def _stack_sources(event: dict[str, Any], repository: Path) -> tuple[dict[str, str], ...]:
    root = repository.resolve()
    sources: dict[str, str] = {}
    for raw in as_sequence(event.get("stack")):
        frame = as_mapping(raw)
        if frame.get("kind") != "repository":
            continue
        relative = as_text(frame.get("path"))
        if relative is None:
            raise DynamicEventInvalid("repository stack frame has no source path")
        source = (root / PurePosixPath(relative)).resolve()
        try:
            source.relative_to(root)
        except ValueError as error:
            raise DynamicEventInvalid(f"event stack path escapes repository: {relative}") from error
        if not source.is_file():
            raise DynamicEventInvalid(f"event stack source is missing: {relative}")
        sources[relative] = hashlib.sha256(source.read_bytes()).hexdigest()
    return tuple(
        {"path": path, "source_hash": source_hash} for path, source_hash in sorted(sources.items())
    )


def _repository_frame(event: dict[str, Any]) -> dict[str, Any] | None:
    for raw in event["stack"]:
        frame = dict(as_mapping(raw))
        if frame.get("kind") == "repository":
            return frame
    return None


def _issue_evidence(issue: EventIssue, ctx: ObserverContext, observer: str) -> dict[str, Any]:
    return make_evidence(
        run_id=ctx.run_id,
        proof_scope_hash=ctx.proof_scope_hash,
        claim_type=f"{observer}_state",
        observer=observer,
        repo_sha=ctx.repo_sha,
        path=".",
        line_start=None,
        line_end=None,
        source_hash=EMPTY_SHA256,
        value={"code": issue.code, "reason": issue.reason, "row": issue.row},
        provider_subject=None,
        dependency_context_hash=ctx.dependency_context_hash,
        derivation="OBSERVED",
        confidence="PROVEN",
    )


__all__ = [
    "DynamicEventInvalid",
    "EventBatch",
    "EventIssue",
    "event_schema_bytes",
    "event_schema_hash",
    "events_from_jsonl",
    "events_to_evidence",
    "normalize_event",
]
