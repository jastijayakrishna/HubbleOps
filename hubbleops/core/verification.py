from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from hubbleops.core.canonical import content_id
from hubbleops.core.records import as_mapping, as_sequence, as_text

OracleCode = Literal["VALID", "INVALID", "UNKNOWN_PROVIDER_CONTRACT", "ORACLE_UNAVAILABLE"]
FalsifierResult = Literal["PASS", "FAIL", "UNKNOWN"]
SubjectChangeKind = Literal["ADDED", "REMOVED", "CHANGED"]
Verdict = Literal["VERIFIED_FOR_SCOPE", "HUMAN_REQUIRED", "UNKNOWN", "FAILED"]

VERDICTS: tuple[Verdict, ...] = ("VERIFIED_FOR_SCOPE", "HUMAN_REQUIRED", "UNKNOWN", "FAILED")


@dataclass(frozen=True, slots=True)
class SubjectChange:
    subject: str
    change: SubjectChangeKind
    replacement: str | None
    kind: str
    reason: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "change": self.change,
            "replacement": self.replacement,
            "kind": self.kind,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ChangeSet:
    from_version: str
    to_version: str
    pair_hash: str
    changes: tuple[SubjectChange, ...]

    def removed(self) -> tuple[SubjectChange, ...]:
        return tuple(item for item in self.changes if item.change == "REMOVED")

    def renamed(self) -> tuple[SubjectChange, ...]:
        return tuple(item for item in self.changes if item.replacement is not None)

    def unresolved(self) -> tuple[SubjectChange, ...]:
        return tuple(item for item in self.changes if item.kind == "UNKNOWN_PROVIDER_CONTRACT")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "from_version": self.from_version,
            "to_version": self.to_version,
            "pair_hash": self.pair_hash,
            "changes": [item.to_mapping() for item in self.changes],
        }


@dataclass(frozen=True, slots=True)
class OracleOutcome:
    code: OracleCode
    reason: str
    response: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class OracleCheck:
    request_hash: str
    service: str
    method: str
    version: str
    origin: str
    code: OracleCode
    reason: str
    checked_at: str

    def body(self) -> dict[str, Any]:
        return {
            "request_hash": self.request_hash,
            "service": self.service,
            "method": self.method,
            "version": self.version,
            "origin": self.origin,
            "code": self.code,
            "reason": self.reason,
        }

    def to_mapping(self) -> dict[str, Any]:
        return {**self.body(), "checked_at": self.checked_at}


@runtime_checkable
class OracleView(Protocol):
    def validate(self, request: Mapping[str, Any], version: str) -> OracleOutcome: ...


@dataclass(frozen=True, slots=True)
class FalsifierInput:
    changes: ChangeSet
    candidate_root: str
    evidence: tuple[Mapping[str, Any], ...]
    candidates: tuple[Mapping[str, Any], ...]
    captured_requests: tuple[Mapping[str, Any], ...]

    def evidence_of(self, claim_type: str) -> tuple[Mapping[str, Any], ...]:
        return tuple(item for item in self.evidence if item.get("claim_type") == claim_type)


@dataclass(frozen=True, slots=True)
class FalsifierOutcome:
    result: FalsifierResult
    reason: str
    sites: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, Any]:
        return {"result": self.result, "reason": self.reason, "sites": list(self.sites)}


@runtime_checkable
class FalsifierView(Protocol):
    name: str
    failure_class: str

    def check(self, subject: FalsifierInput) -> FalsifierOutcome: ...


@dataclass(frozen=True, slots=True)
class ObligationView:
    id: str
    provider_change_id: str
    evidence_ids: tuple[str, ...]
    current_state: str
    required_state: str
    repair_class: str
    verification_method: str
    status: str

    @staticmethod
    def from_record(record: Mapping[str, Any]) -> ObligationView:
        return ObligationView(
            id=str(record["id"]),
            provider_change_id=str(record["provider_change_id"]),
            evidence_ids=tuple(str(item) for item in record["evidence_ids"]),
            current_state=str(record["current_state"]),
            required_state=str(record["required_state"]),
            repair_class=str(record["repair_class"]),
            verification_method=str(record["verification_method"]),
            status=str(record["status"]),
        )


@dataclass(frozen=True, slots=True)
class Hunk:
    path: str
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    added: tuple[str, ...]
    removed: tuple[str, ...]

    def identity(self) -> str:
        return f"{self.path}@{self.old_start},{self.old_lines}+{self.new_start},{self.new_lines}"

    def spans(self, line: int) -> bool:
        if self.new_lines == 0:
            return line == self.new_start
        return self.new_start <= line < self.new_start + self.new_lines

    def to_mapping(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "identity": self.identity(),
            "old_start": self.old_start,
            "old_lines": self.old_lines,
            "new_start": self.new_start,
            "new_lines": self.new_lines,
        }


@dataclass(frozen=True, slots=True)
class HunkMapping:
    hunk: Hunk
    disposition: Literal["OBLIGATION", "COLLATERAL", "UNEXPLAINED"]
    obligation_id: str | None
    reason: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            **self.hunk.to_mapping(),
            "disposition": self.disposition,
            "obligation_id": self.obligation_id,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class SuiteCase:
    name: str
    outcome: str
    files: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {"name": self.name, "outcome": self.outcome, "files": list(self.files)}


@dataclass(frozen=True, slots=True)
class SuiteRun:
    source: Literal["FROZEN_BASELINE", "CANDIDATE"]
    executed: bool
    passed: int
    failed: int
    skipped: int
    outcome: str
    reason: str
    tests: tuple[SuiteCase, ...] = ()

    def all_passed(self) -> bool:
        return self.executed and self.failed == 0 and self.outcome == "COMPLETED"

    def covered_files(self) -> frozenset[str]:
        return frozenset(
            path for test in self.tests if test.outcome == "passed" for path in test.files
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "executed": self.executed,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "outcome": self.outcome,
            "reason": self.reason,
            "tests": [test.to_mapping() for test in self.tests],
        }


@dataclass(frozen=True, slots=True)
class CheckReport:
    name: str
    passed: bool
    reasons: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict[str, Any])

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "reasons": list(self.reasons),
            "unresolved": list(self.unresolved),
            "detail": dict(self.detail),
        }


@dataclass(frozen=True, slots=True)
class VerificationResult:
    audit_pass: bool
    oracle_all_accepted: bool
    zero_unexplained_hunks: bool
    request_shape_differential_pass: bool
    response_consumer_check_pass: bool
    frozen_baseline_tests_pass: bool
    falsifiers_pass: bool
    unknown_conservation_pass: bool
    unknown_blast: tuple[str, ...]
    oracle_available: bool
    unresolved: tuple[str, ...]
    reasons: tuple[str, ...]

    def flags(self) -> dict[str, bool]:
        return {
            "audit_pass": self.audit_pass,
            "oracle_all_accepted": self.oracle_all_accepted,
            "zero_unexplained_hunks": self.zero_unexplained_hunks,
            "request_shape_differential_pass": self.request_shape_differential_pass,
            "response_consumer_check_pass": self.response_consumer_check_pass,
            "frozen_baseline_tests_pass": self.frozen_baseline_tests_pass,
            "falsifiers_pass": self.falsifiers_pass,
            "unknown_conservation_pass": self.unknown_conservation_pass,
        }


def request_identity(request: Mapping[str, Any]) -> str:
    return content_id(dict(request))


def request_text_of(record: Mapping[str, Any]) -> str:
    value = as_mapping(record.get("value"))
    skeleton = as_mapping(value.get("skeleton"))
    parts = [
        as_text(value.get("text")) or "",
        as_text(value.get("query")) or "",
        as_text(value.get("line")) or "",
        *(str(item) for item in as_sequence(skeleton.get("fragments"))),
        *(str(item) for item in as_sequence(value.get("matches"))),
    ]
    return "\n".join(part for part in parts if part)


def sorted_unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


__all__ = [
    "VERDICTS",
    "ChangeSet",
    "CheckReport",
    "FalsifierInput",
    "FalsifierOutcome",
    "FalsifierResult",
    "FalsifierView",
    "Hunk",
    "HunkMapping",
    "ObligationView",
    "OracleCheck",
    "OracleCode",
    "OracleOutcome",
    "OracleView",
    "SubjectChange",
    "SubjectChangeKind",
    "SuiteCase",
    "SuiteRun",
    "Verdict",
    "VerificationResult",
    "request_identity",
    "request_text_of",
    "sorted_unique",
]
