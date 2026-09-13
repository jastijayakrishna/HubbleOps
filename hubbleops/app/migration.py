from __future__ import annotations

import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hubbleops.app import registry
from hubbleops.app.verification import InjectedOracle, first_party_sources
from hubbleops.closure import source_closure
from hubbleops.core.errors import HubbleOpsError, PackDataError
from hubbleops.core.evidence import provider_versions
from hubbleops.core.repair import TransformInput
from hubbleops.core.subjects import parse_sites
from hubbleops.core.verification import ChangeSet, SubjectChange
from hubbleops.obligations import ObligationInputs
from hubbleops.obligations import build as build_obligations
from hubbleops.observe.ledger import Ledger
from hubbleops.repair import deterministic
from hubbleops.sandbox import DetachedWorktree

DETERMINISTIC = "DETERMINISTIC"
VERSION_PREFIX = "version:"
SUBJECT_PREFIX = "subject:"
GIT_TIMEOUT_SECONDS = 60.0
MAX_NAMED_PATHS = 20
VERSION_SHAPE = re.compile(r"^(?P<prefix>\D*)(?P<ordinal>\d+)$")


class MigrationInvalid(HubbleOpsError):
    pass


def require_head_materialization(repository: Path) -> str:
    root = repository.resolve()
    head = _git(root, "rev-parse", "HEAD").strip()
    if len(head) != 40:
        raise MigrationInvalid(
            f"{root} has no resolvable HEAD, so an obligation derived here would name no base "
            "commit; commit the tree before migrating it"
        )
    pending = [line for line in _git(root, "status", "--porcelain").splitlines() if line.strip()]
    if pending:
        raise MigrationInvalid(
            "the working tree differs from HEAD, so obligations derived from it would bind to a "
            "ProofScope no commit reproduces; commit or stash these first:\n"
            + "\n".join(f"  {entry}" for entry in pending[:MAX_NAMED_PATHS])
        )
    working = source_closure.build(root)
    with tempfile.TemporaryDirectory(prefix="hops-migrate-head-") as scratch:
        with DetachedWorktree(root, Path(scratch) / "head", head) as materialized:
            committed = source_closure.build(materialized)
    divergent = _divergent_paths(working, committed)
    if divergent:
        raise MigrationInvalid(
            f"the bytes on disk are not the bytes {head[:12]} materializes, so `hops verify` "
            "would scan a different tree than this migration did; restore the checkout (a line-"
            "ending or filter setting is the usual cause) before migrating:\n"
            + "\n".join(f"  {path}" for path in divergent[:MAX_NAMED_PATHS])
        )
    return head


def _divergent_paths(
    working: source_closure.SourceClosure, committed: source_closure.SourceClosure
) -> tuple[str, ...]:
    left = {entry.path: entry.blob_sha for entry in working.entries}
    right = {entry.path: entry.blob_sha for entry in committed.entries}
    return tuple(
        sorted(path for path in set(left) | set(right) if left.get(path) != right.get(path))
    )


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        raise MigrationInvalid(
            f"git {' '.join(arguments)} failed in {repository}: "
            f"{completed.stderr.strip() or completed.returncode}"
        )
    return completed.stdout


@dataclass(frozen=True, slots=True)
class MigrationResult:
    obligations: tuple[dict[str, Any], ...]
    report: deterministic.RepairReport
    target: str
    written: tuple[str, ...]
    uncomposable: Mapping[str, str] = field(default_factory=dict[str, str])

    def open_for_human(self) -> tuple[dict[str, Any], ...]:
        discharged = set(self.report.discharged())
        return tuple(
            item
            for item in self.obligations
            if item["id"] not in discharged and item["repair_class"] != "PRESERVE_UNKNOWN"
        )

    def preserved(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            item for item in self.obligations if item["repair_class"] == "PRESERVE_UNKNOWN"
        )


@dataclass(frozen=True, slots=True)
class Composition:
    sets: Mapping[str, ChangeSet]
    uncomposable: Mapping[str, str]


def change_sets_for(pack: registry.LoadedPack, versions: Sequence[str], target: str) -> Composition:
    built: dict[str, ChangeSet] = {}
    uncomposable: dict[str, str] = {}
    for version in sorted(set(versions)):
        if version == target:
            continue
        try:
            diff = pack.contract.diff(version, target)
        except PackDataError as error:
            uncomposable[version] = uncomposable_reason(pack, version, target, error)
            continue
        built[version] = ChangeSet(
            from_version=diff.from_version,
            to_version=diff.to_version,
            pair_hash=diff.pair_hash,
            changes=tuple(
                sorted(
                    (
                        SubjectChange(
                            subject=fact.subject,
                            change=(
                                fact.change
                                if fact.change in ("ADDED", "REMOVED", "CHANGED")
                                else "CHANGED"
                            ),
                            replacement=fact.replacement,
                            kind=fact.result,
                            reason=fact.reason,
                        )
                        for fact in diff.facts
                    ),
                    key=lambda item: item.subject,
                )
            ),
        )
    return Composition(sets=built, uncomposable=uncomposable)


def uncomposable_reason(
    pack: registry.LoadedPack, version: str, target: str, error: PackDataError
) -> str:
    lattice = tuple(entry.id for entry in pack.versions())
    if (
        lattice
        and version.lower() not in {entry.lower() for entry in lattice}
        and _below(version, lattice[0])
    ):
        return f"{version} is below this pack's lattice floor ({lattice[0]})"
    return f"the pack composes no diff from {version} to {target}: {error}"


def _below(version: str, floor: str) -> bool:
    found = VERSION_SHAPE.match(version.lower())
    first = VERSION_SHAPE.match(floor.lower())
    if found is None or first is None or found["prefix"] != first["prefix"]:
        return False
    return int(found["ordinal"]) < int(first["ordinal"])


def detected_versions(ledger: Ledger) -> tuple[str, ...]:
    return tuple(
        sorted({version for record in ledger.evidence for version in provider_versions(record)})
    )


def transform_requests(
    *,
    obligations: Sequence[Mapping[str, Any]],
    ledger: Ledger,
    change_sets: Mapping[str, ChangeSet],
    root: Path,
    target: str,
) -> tuple[TransformInput, ...]:
    index = ledger.evidence_by_id()
    by_candidate = {str(item["id"]): item for item in ledger.candidates}
    requests: list[TransformInput] = []
    for obligation in obligations:
        if obligation["repair_class"] != DETERMINISTIC:
            continue
        located = _locate(obligation, index, by_candidate, ledger)
        if located is None:
            continue
        claim_type = located[2]
        subject, replacement = _subject_of(obligation, change_sets)
        for path, line in edit_sites(obligation, located):
            source = root / path
            try:
                text = source.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            requests.append(
                TransformInput(
                    obligation_id=str(obligation["id"]),
                    path=path,
                    text=text,
                    current_state=str(obligation["current_state"]),
                    required_state=str(obligation["required_state"]),
                    from_version=_from_version(obligation, target),
                    to_version=target,
                    claim_type=claim_type,
                    line=line,
                    subject=subject,
                    replacement=replacement,
                )
            )
    return tuple(requests)


def edit_sites(
    obligation: Mapping[str, Any], located: tuple[str, int | None, str]
) -> tuple[tuple[str, int | None], ...]:
    state = str(obligation["current_state"])
    head, marker, written = state.partition(" written at ")
    if marker:
        literal_sites = parse_sites(written)
        if literal_sites:
            return literal_sites
    declared = parse_sites(head)
    if declared:
        return declared
    return ((located[0], located[1]),)


def _locate(
    obligation: Mapping[str, Any],
    index: Mapping[str, dict[str, Any]],
    by_candidate: Mapping[str, Mapping[str, Any]],
    ledger: Ledger,
) -> tuple[str, int | None, str] | None:
    attached = [index[eid] for eid in obligation["evidence_ids"] if eid in index]
    if not attached:
        return None
    for candidate in by_candidate.values():
        if set(obligation["evidence_ids"]) <= set(candidate["evidence_ids"]):
            location = ledger.location_of(candidate)
            return location.path, location.line, location.claim_type
    chosen = attached[0]
    return str(chosen["path"]), chosen.get("line_start"), str(chosen["claim_type"])


def _subject_of(
    obligation: Mapping[str, Any], change_sets: Mapping[str, ChangeSet]
) -> tuple[str | None, str | None]:
    identifier = str(obligation["provider_change_id"])
    if not identifier.startswith(SUBJECT_PREFIX):
        return None, None
    subject = identifier[len(SUBJECT_PREFIX) :]
    for changes in change_sets.values():
        for change in changes.changes:
            if change.subject == subject:
                return subject, change.replacement
    return subject, None


def _from_version(obligation: Mapping[str, Any], target: str) -> str:
    identifier = str(obligation["provider_change_id"])
    if identifier.startswith(VERSION_PREFIX):
        pair = identifier[len(VERSION_PREFIX) :]
        if "->" in pair:
            return pair.split("->", 1)[0]
    return target


def migrate(
    *,
    pack: registry.LoadedPack,
    ledger: Ledger,
    closure: source_closure.SourceClosure,
    root: Path,
    target: str,
    write: bool = True,
) -> MigrationResult:
    composition = change_sets_for(pack, detected_versions(ledger), target)
    obligations = build_obligations(
        ObligationInputs(
            ledger=ledger,
            change_sets=composition.sets,
            oracle=InjectedOracle(pack.contract),
            target=target,
            sources=first_party_sources(closure, root),
            uncomposable=composition.uncomposable,
        )
    )
    requests = transform_requests(
        obligations=obligations,
        ledger=ledger,
        change_sets=composition.sets,
        root=root,
        target=target,
    )
    report = deterministic.run(transforms=pack.repair_transforms(), requests=requests)
    written: list[str] = []
    if write:
        for path, text in report.texts.items():
            (root / path).write_text(text, encoding="utf-8", newline="")
            written.append(path)
    return MigrationResult(
        obligations=obligations,
        report=report,
        target=target,
        written=tuple(sorted(written)),
        uncomposable=composition.uncomposable,
    )


__all__ = [
    "Composition",
    "MigrationInvalid",
    "MigrationResult",
    "change_sets_for",
    "detected_versions",
    "edit_sites",
    "migrate",
    "transform_requests",
    "uncomposable_reason",
]
