from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hubbleops.app import registry
from hubbleops.closure import Classification
from hubbleops.core.canonical import content_id
from hubbleops.core.errors import HubbleOpsError, PackDataError
from hubbleops.core.observer import StructuralRule
from hubbleops.core.proof_scope import make_proof_scope, proof_scope_hash, run_id_for
from hubbleops.core.records import (
    MAX_JSON_BYTES,
    as_mapping,
    as_sequence,
    is_list,
    is_mapping,
    parse_json,
)
from hubbleops.core.schema import validate
from hubbleops.core.verification import (
    ChangeSet,
    FalsifierView,
    ObligationView,
    OracleOutcome,
    SubjectChange,
)
from hubbleops.graph.imports import AstGrep, ImportGraph, language_for
from hubbleops.graph.imports import build as build_graph
from hubbleops.observe import structure
from hubbleops.observe.dynamic import events_from_jsonl
from hubbleops.packs._protocol import ContractOracle
from hubbleops.sandbox import DetachedWorktree
from hubbleops.sandbox.verifier_image import (
    VERIFIER_IMAGE,
    VerifierIsolationViolated,
    forbidden_source_marker,
)
from hubbleops.verify import authority, gitdiff, suites, unsupported_modules

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
VERIFY_SOURCES = tuple(sorted((PACKAGE_ROOT / "verify").rglob("*.py")))


class VerificationInvalid(HubbleOpsError):
    pass


@dataclass(frozen=True, slots=True)
class VerificationRequest:
    repository: Path
    pack: registry.LoadedPack
    base: str
    candidate: str
    from_version: str | None = None
    to_version: str | None = None
    obligations_path: Path | None = None
    decisions_path: Path | None = None
    base_capture_path: Path | None = None
    candidate_capture_path: Path | None = None


@dataclass(frozen=True, slots=True)
class VerificationRun:
    evaluation: authority.Evaluation
    proof_scope: dict[str, Any]
    proof_scope_hash: str
    run_id: str
    base_sha: str
    candidate_sha: str
    from_version: str
    to_version: str
    changes_hash: str
    oracle_mode: str


class InjectedOracle:
    def __init__(self, contract: ContractOracle) -> None:
        self.contract = contract

    def validate(self, request: Mapping[str, Any], version: str) -> OracleOutcome:
        try:
            result = self.contract.validate(request, version)
        except PackDataError as error:
            return OracleOutcome(code="UNKNOWN_PROVIDER_CONTRACT", reason=str(error))
        return OracleOutcome(code=result.code, reason=result.reason, response=result.response)


def execute(request: VerificationRequest) -> VerificationRun:
    from hubbleops.app.cli import scan_repository

    repository = request.repository.resolve()
    gitdiff.available()
    base_sha = gitdiff.resolve(repository, request.base)
    candidate_sha = gitdiff.resolve(repository, request.candidate)
    if base_sha == candidate_sha:
        raise VerificationInvalid(
            "the base and candidate resolve to the same commit, so there is nothing to verify"
        )
    delta = gitdiff.read(repository, base_sha, candidate_sha)

    with tempfile.TemporaryDirectory(prefix="hops-verify-") as scratch:
        root = Path(scratch)
        with (
            DetachedWorktree(
                repository=repository, destination=root / "base", sha=base_sha
            ) as base,
            DetachedWorktree(
                repository=repository, destination=root / "candidate", sha=candidate_sha
            ) as candidate,
        ):
            refuse_repair_inputs(request)
            base_scan = scan_repository(base, request.pack, run_target=f"commit:{base_sha}")
            candidate_scan = scan_repository(
                candidate, request.pack, run_target=f"commit:{candidate_sha}"
            )
            from_version, to_version = _versions(request, base_scan)
            changes = change_set(request.pack, from_version, to_version)

            plan = suites.stage_frozen(base, candidate, root / "frozen")
            frozen = suites.run_frozen(plan, _python())
            candidate_tests = suites.run_candidate(candidate, root / "candidate-tests", _python())

            base_graph = _graph(base_scan, request.pack)
            graph = _graph(candidate_scan, request.pack)
            reachable = {entry.path for entry in candidate_scan.closure.entries}
            languages = suites.languages_of(sorted(reachable))
            test_roots = suites.suite_paths(candidate)
            candidate_paths = tuple(
                sorted(
                    entry.path
                    for entry in candidate_scan.closure.entries
                    if entry.classification is Classification.INSIDE
                    and languages[entry.path] != "unknown"
                    and not any(
                        entry.path == root or entry.path.startswith(f"{root}/")
                        for root in test_roots
                    )
                )
            )
            uncoverable = unsupported_modules(reachable, languages)

            oracle = InjectedOracle(request.pack.verification_contract())
            base_capture = read_capture(request.base_capture_path)
            candidate_capture = read_capture(request.candidate_capture_path)
            evaluation = authority.evaluate(
                authority.Inputs(
                    base_ledger=base_scan.ledger,
                    candidate_ledger=candidate_scan.ledger,
                    base_graph=base_graph,
                    candidate_graph=graph,
                    delta=delta,
                    changes=changes,
                    obligations=read_obligations(request.obligations_path),
                    oracle=oracle,
                    falsifiers=falsifier_views(request.pack),
                    frozen_tests=frozen,
                    candidate_tests=candidate_tests,
                    candidate_root=str(candidate),
                    candidate_paths=candidate_paths,
                    base_captured=base_capture,
                    candidate_captured=candidate_capture,
                    decisions=read_decisions(request.decisions_path),
                    uncoverable=uncoverable,
                    captures_supplied=(
                        request.base_capture_path is not None
                        or request.candidate_capture_path is not None
                    ),
                )
            )
            scope, scope_hash = _scope(candidate_scan, request.pack)
            return VerificationRun(
                evaluation=evaluation,
                proof_scope=scope,
                proof_scope_hash=scope_hash,
                run_id=run_id_for(
                    scope_hash=scope_hash,
                    provider=request.pack.name,
                    verb="verify",
                    target=f"{base_sha}..{candidate_sha}",
                ),
                base_sha=base_sha,
                candidate_sha=candidate_sha,
                from_version=from_version,
                to_version=to_version,
                changes_hash=request.pack.changes.lattice_hash,
                oracle_mode="LIVE" if evaluation.oracle.available else "ORACLE_UNAVAILABLE",
            )


def refuse_repair_inputs(request: VerificationRequest) -> None:
    for label, path in (
        ("obligations", request.obligations_path),
        ("decisions", request.decisions_path),
        ("base capture", request.base_capture_path),
        ("candidate capture", request.candidate_capture_path),
    ):
        if path is None:
            continue
        marker = forbidden_source_marker(path)
        if marker is not None:
            raise VerifierIsolationViolated(f"the {label} input {path} comes from the repair side")


def change_set(pack: registry.LoadedPack, from_version: str, to_version: str) -> ChangeSet:
    diff = pack.contract.diff(from_version, to_version)
    changes = tuple(
        SubjectChange(
            subject=fact.subject,
            change=fact.change if fact.change in ("ADDED", "REMOVED", "CHANGED") else "CHANGED",
            replacement=fact.replacement,
            kind=fact.result,
            reason=fact.reason,
        )
        for fact in diff.facts
    )
    return ChangeSet(
        from_version=diff.from_version,
        to_version=diff.to_version,
        pair_hash=diff.pair_hash,
        changes=tuple(sorted(changes, key=lambda item: item.subject)),
    )


def falsifier_views(pack: registry.LoadedPack) -> tuple[FalsifierView, ...]:
    falsifiers = tuple(pack.falsifiers())
    invalid = [type(item).__name__ for item in falsifiers if not isinstance(item, FalsifierView)]
    if invalid:
        raise VerificationInvalid(
            f"pack {pack.name!r} returned invalid falsifier entries: {', '.join(invalid)}"
        )
    return falsifiers


def read_obligations(path: Path | None) -> tuple[ObligationView, ...]:
    if path is None:
        return ()
    parsed = parse_json(_read_input(path))
    if not parsed.ok():
        raise VerificationInvalid(f"{path} is not readable as obligations: {parsed.reason}")
    if is_list(parsed.value):
        records = as_sequence(parsed.value)
    elif is_mapping(parsed.value) and is_list(as_mapping(parsed.value).get("obligations")):
        records = as_sequence(as_mapping(parsed.value).get("obligations"))
    else:
        raise VerificationInvalid(f"{path} must contain an obligation list")
    if any(not is_mapping(record) for record in records):
        raise VerificationInvalid(f"{path} contains a non-object obligation")
    return authority.obligations_from(
        [validate("obligation", dict(as_mapping(record))) for record in records]
    )


def read_decisions(path: Path | None) -> tuple[Mapping[str, Any], ...]:
    if path is None:
        return ()
    parsed = parse_json(_read_input(path))
    if not parsed.ok():
        raise VerificationInvalid(f"{path} is not readable as decisions: {parsed.reason}")
    if not is_list(parsed.value):
        raise VerificationInvalid(f"{path} must contain a decision list")
    if any(not is_mapping(record) for record in as_sequence(parsed.value)):
        raise VerificationInvalid(f"{path} contains a non-object decision")
    return tuple(dict(as_mapping(record)) for record in as_sequence(parsed.value))


def read_capture(path: Path | None) -> tuple[Mapping[str, Any], ...]:
    if path is None:
        return ()
    batch = events_from_jsonl(_read_input(path))
    if batch.issues:
        detail = "; ".join(
            f"{path}:{issue.row} {issue.code}: {issue.reason}" for issue in batch.issues
        )
        raise VerificationInvalid(f"capture input is invalid: {detail}")
    return tuple(batch.events)


def _read_input(path: Path) -> bytes:
    with path.open("rb") as handle:
        payload = handle.read(MAX_JSON_BYTES + 1)
    if len(payload) > MAX_JSON_BYTES:
        raise VerificationInvalid(
            f"{path} exceeds the {MAX_JSON_BYTES} byte verification-input bound"
        )
    return payload


def _versions(request: VerificationRequest, base_scan: Any) -> tuple[str, str]:
    lattice = tuple(item.id for item in request.pack.versions())
    target = request.to_version or (lattice[-1] if lattice else "")
    if not target:
        raise VerificationInvalid("the pack declares no version lattice, so no target exists")
    if target not in lattice:
        raise VerificationInvalid(f"{target} is not a version this pack knows")
    if request.from_version:
        return request.from_version, target
    detected = _detected(base_scan, lattice)
    if len(detected) != 1:
        found = ", ".join(detected) if detected else "nothing"
        raise VerificationInvalid(
            f"the base tree detects {found}; pass --from to name the source version "
            "rather than have the verifier guess it"
        )
    return detected[0], target


def _detected(scan: Any, lattice: Sequence[str]) -> tuple[str, ...]:
    found: set[str] = set()
    for record in scan.ledger.evidence:
        value = as_mapping(record.get("value"))
        for key in ("version", "detected"):
            text = value.get(key)
            if isinstance(text, str) and text.lower() in lattice:
                found.add(text.lower())
    return tuple(sorted(found))


def structural_rules(pack: registry.LoadedPack) -> tuple[StructuralRule, ...]:
    from hubbleops.app.cli import structural_rules_of

    return structural_rules_of(pack)


def _graph(scan: Any, pack: registry.LoadedPack) -> ImportGraph:
    active = {rule.language for rule in structural_rules(pack)}
    paths_by_language: dict[str, tuple[str, ...]] = {}
    for entry in scan.closure.entries:
        if entry.classification is not Classification.INSIDE:
            continue
        language = language_for(entry.path)
        if language in active:
            paths_by_language[language] = (*paths_by_language.get(language, ()), entry.path)
    return build_graph(scan.closure.root, paths_by_language, AstGrep())


def _scope(scan: Any, pack: registry.LoadedPack) -> tuple[dict[str, Any], str]:
    scope = make_proof_scope(
        repo_sha=scan.closure.repo_sha,
        tree_hash=scan.closure.tree_hash(),
        dependency_resolution_hash=scan.resolution.resolution_hash(),
        provider_contract_hash=pack.contract_hash(),
        rules_hash=structure.rules_hash(structural_rules(pack)),
        scanner_version=scan.scanner_version,
        verifier_version=verifier_version(),
        verifier_image_hash=VERIFIER_IMAGE.fingerprint(),
    )
    return scope, proof_scope_hash(scope)


def verifier_version() -> str:
    from hubbleops import __version__

    root = PACKAGE_ROOT / "verify"
    digest = content_id(
        {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in VERIFY_SOURCES
        }
    )
    return f"hubbleops={__version__};verifier={digest};image={VERIFIER_IMAGE.reference}"


def _python() -> str:
    return os.environ.get("HOPS_VERIFY_PYTHON") or sys.executable


__all__ = [
    "InjectedOracle",
    "VerificationInvalid",
    "VerificationRequest",
    "VerificationRun",
    "change_set",
    "execute",
    "falsifier_views",
    "read_capture",
    "read_decisions",
    "read_obligations",
    "refuse_repair_inputs",
    "structural_rules",
    "verifier_version",
]
