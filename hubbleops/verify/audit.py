from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hubbleops.core.records import as_mapping, as_sequence, as_text
from hubbleops.core.verification import ChangeSet, CheckReport, ObligationView
from hubbleops.observe import Ledger

RESIDUE_CLAIM_TYPES = ("call_version", "endpoint_reference", "config_reference")
SURFACE_CLAIM_TYPES = ("surface_reference", "request_text")
OPEN_STATUS = "OPEN"
DISCHARGED_STATUS = "DISCHARGED"
UNRECONCILABLE_STATUS = "UNRECONCILABLE"
METHOD_PREFIXES = ("absent:", "present:", "version:")
SOURCE_JOINER_CHARACTERS = frozenset(" \t\r\n'\"+()\\")


class _TrieNode:
    __slots__ = ("children", "terminal")

    def __init__(self) -> None:
        self.children: dict[str, _TrieNode] = {}
        self.terminal: str | None = None


@dataclass(frozen=True, slots=True)
class Reconciliation:
    obligation_id: str
    status: str
    reason: str
    evidence_ids: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "status": self.status,
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True, slots=True)
class Audit:
    residue: tuple[str, ...]
    reintroduced: tuple[str, ...]
    reconciliations: tuple[Reconciliation, ...]
    unresolved: tuple[str, ...]

    def open_obligations(self) -> tuple[Reconciliation, ...]:
        return tuple(item for item in self.reconciliations if item.status == OPEN_STATUS)

    def report(self) -> CheckReport:
        reasons = (
            *(f"old-version residue at {site}" for site in self.residue),
            *(f"removed subject still present: {subject}" for subject in self.reintroduced),
            *(
                f"obligation {item.obligation_id} is OPEN: {item.reason}"
                for item in self.open_obligations()
            ),
        )
        return CheckReport(
            name="migration_audit",
            passed=not reasons,
            reasons=reasons,
            unresolved=self.unresolved,
            detail={
                "old_version_residue": list(self.residue),
                "removed_subjects_present": list(self.reintroduced),
                "obligations": [item.to_mapping() for item in self.reconciliations],
            },
        )


def run(
    ledger: Ledger,
    changes: ChangeSet,
    obligations: Sequence[ObligationView],
    candidate_root: Path | None = None,
    candidate_paths: Sequence[str] = (),
    captured: Sequence[Mapping[str, Any]] = (),
) -> Audit:
    evidence = ledger.evidence_by_id()
    residue = tuple(
        sorted(
            {
                *_residue(ledger, evidence, changes.from_version),
                *_captured_residue(captured, changes.from_version),
            }
        )
    )
    reintroduced = tuple(
        sorted(
            {
                *_reintroduced(ledger, evidence, changes),
                *_source_reintroduced(candidate_root, candidate_paths, changes),
                *_captured_reintroduced(captured, changes),
            }
        )
    )
    reconciliations = tuple(
        _reconcile(item, ledger, evidence, changes) for item in sorted(obligations, key=_key)
    )
    unresolved = tuple(
        sorted(
            f"obligation {item.obligation_id}: {item.reason}"
            for item in reconciliations
            if item.status == UNRECONCILABLE_STATUS
        )
        + sorted(f"contract subject {item.subject}" for item in changes.unresolved())
    )
    return Audit(
        residue=residue,
        reintroduced=reintroduced,
        reconciliations=reconciliations,
        unresolved=unresolved,
    )


def _captured_residue(
    captured: Sequence[Mapping[str, Any]], source_version: str
) -> tuple[str, ...]:
    return tuple(
        f"captured event {index} ({event.get('service')}.{event.get('method')})"
        for index, event in enumerate(captured, start=1)
        if as_text(event.get("version")) == source_version
    )


def _captured_reintroduced(
    captured: Sequence[Mapping[str, Any]], changes: ChangeSet
) -> tuple[str, ...]:
    watched = tuple(sorted({item.subject for item in (*changes.removed(), *changes.renamed())}))
    if not watched:
        return ()
    trie = _subject_trie(watched)
    found: set[str] = set()
    for index, event in enumerate(captured, start=1):
        text = as_text(event.get("request_text")) or ""
        for subject, _ in _subject_occurrences(text, trie):
            found.add(f"{subject} at captured event {index}")
    return tuple(sorted(found))


def _source_reintroduced(
    root: Path | None, paths: Sequence[str], changes: ChangeSet
) -> tuple[str, ...]:
    if root is None:
        return ()
    watched = tuple(sorted({item.subject for item in (*changes.removed(), *changes.renamed())}))
    if not watched:
        return ()
    trie = _subject_trie(watched)
    found: set[str] = set()
    for relative in sorted(set(paths)):
        try:
            text = (root / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for subject, offset in _subject_occurrences(text, trie):
            line = text.count("\n", 0, offset) + 1
            found.add(f"{subject} at {relative}:{line} (independent source extinction)")
    return tuple(sorted(found))


def _subject_trie(subjects: Sequence[str]) -> _TrieNode:
    root = _TrieNode()
    for subject in subjects:
        node = root
        for character in subject:
            node = node.children.setdefault(character, _TrieNode())
        node.terminal = subject
    return root


def _subject_occurrences(text: str, trie: _TrieNode) -> tuple[tuple[str, int], ...]:
    normalized = tuple(
        (character, offset)
        for offset, character in enumerate(text)
        if character not in SOURCE_JOINER_CHARACTERS
    )
    found: set[tuple[str, int]] = set()
    for start, (character, offset) in enumerate(normalized):
        if offset and _word_character(text[offset - 1]):
            continue
        child = trie.children.get(character)
        if child is None:
            continue
        node = child
        cursor = start + 1
        while True:
            terminal = node.terminal
            end = normalized[cursor - 1][1] + 1
            if isinstance(terminal, str) and (end == len(text) or not _word_character(text[end])):
                found.add((terminal, offset))
            if cursor >= len(normalized):
                break
            child = node.children.get(normalized[cursor][0])
            if child is None:
                break
            node = child
            cursor += 1
    return tuple(sorted(found))


def _word_character(character: str) -> bool:
    return character == "_" or character.isalnum()


def _key(obligation: ObligationView) -> str:
    return obligation.id


def _residue(
    ledger: Ledger, evidence: Mapping[str, Mapping[str, Any]], source_version: str
) -> tuple[str, ...]:
    found: set[str] = set()
    for candidate in ledger.candidates:
        if candidate["status"] != "AFFECTED":
            continue
        for eid in candidate["evidence_ids"]:
            record = evidence.get(eid)
            if record is None or record["claim_type"] not in RESIDUE_CLAIM_TYPES:
                continue
            if source_version in _version_values(record):
                found.add(f"{record['path']}:{record['line_start'] or 0}")
    return tuple(sorted(found))


def _version_values(record: Mapping[str, Any]) -> set[str]:
    value = as_mapping(record.get("value"))
    versions: set[str] = set()
    for key in ("version", "detected", "target"):
        text = as_text(value.get(key))
        if text:
            versions.add(text.lower())
    versions.update(str(item).lower() for item in as_sequence(value.get("versions")))
    subject = as_text(record.get("provider_subject"))
    if subject:
        versions.add(subject.lower())
    return versions


def _reintroduced(
    ledger: Ledger, evidence: Mapping[str, Mapping[str, Any]], changes: ChangeSet
) -> tuple[str, ...]:
    removed = {item.subject for item in changes.removed()}
    if not removed:
        return ()
    found: set[str] = set()
    for candidate in ledger.candidates:
        if candidate["status"] not in ("AFFECTED", "UNKNOWN", "HUMAN_REQUIRED"):
            continue
        for eid in candidate["evidence_ids"]:
            record = evidence.get(eid)
            if record is None or record["claim_type"] not in SURFACE_CLAIM_TYPES:
                continue
            subject = as_text(record.get("provider_subject"))
            if subject in removed:
                found.add(f"{subject} at {record['path']}:{record['line_start'] or 0}")
    return tuple(sorted(found))


def _reconcile(
    obligation: ObligationView,
    ledger: Ledger,
    evidence: Mapping[str, Mapping[str, Any]],
    changes: ChangeSet,
) -> Reconciliation:
    method = obligation.verification_method.strip()
    if not method.startswith(METHOD_PREFIXES):
        return Reconciliation(
            obligation_id=obligation.id,
            status=UNRECONCILABLE_STATUS,
            reason=(
                f"verification_method {method!r} names no check this build executes; "
                f"expected one of {', '.join(METHOD_PREFIXES)}"
            ),
            evidence_ids=(),
        )
    kind, _, argument = method.partition(":")
    subject = argument.strip()
    matches = _subject_sites(ledger, evidence, subject)
    if kind == "absent":
        return _decide(obligation, not matches, matches, f"{subject} is still present")
    if kind == "present":
        return _decide(obligation, bool(matches), matches, f"{subject} is absent")
    known = {item.subject for item in changes.changes} | {changes.to_version}
    if subject not in known:
        return Reconciliation(
            obligation_id=obligation.id,
            status=UNRECONCILABLE_STATUS,
            reason=f"{subject} is not a subject or version this Change Pack knows",
            evidence_ids=(),
        )
    return _decide(obligation, bool(matches), matches, f"{subject} is not observed")


def _decide(
    obligation: ObligationView,
    satisfied: bool,
    matches: tuple[tuple[str, str], ...],
    failure: str,
) -> Reconciliation:
    return Reconciliation(
        obligation_id=obligation.id,
        status=DISCHARGED_STATUS if satisfied else OPEN_STATUS,
        reason=f"{obligation.verification_method} holds" if satisfied else failure,
        evidence_ids=tuple(sorted(eid for eid, _ in matches)),
    )


def _subject_sites(
    ledger: Ledger, evidence: Mapping[str, Mapping[str, Any]], subject: str
) -> tuple[tuple[str, str], ...]:
    sites: set[tuple[str, str]] = set()
    attached = {eid for candidate in ledger.candidates for eid in candidate["evidence_ids"]}
    pattern = re.compile(rf"(?<![\w.]){re.escape(subject)}(?![\w.])")
    for eid in sorted(attached):
        record = evidence.get(eid)
        if record is None:
            continue
        if (
            as_text(record.get("provider_subject")) == subject
            or subject in _version_values(record)
            or pattern.search(_searchable_text(record))
        ):
            sites.add((eid, f"{record['path']}:{record['line_start'] or 0}"))
    return tuple(sorted(sites))


def _searchable_text(record: Mapping[str, Any]) -> str:
    value = as_mapping(record.get("value"))
    parts = [as_text(value.get("line")) or ""]
    parts.extend(str(item) for item in as_sequence(value.get("matches")))
    skeleton = as_mapping(value.get("skeleton"))
    parts.extend(str(item) for item in as_sequence(skeleton.get("fragments")))
    return "\n".join(parts)


__all__ = ["Audit", "Reconciliation", "run"]
