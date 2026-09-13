from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hubbleops.app import decision, registry
from hubbleops.closure import Classification
from hubbleops.core.canonical import content_id
from hubbleops.core.errors import HubbleOpsError, PackDataError
from hubbleops.core.evidence import declared_versions
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
from hubbleops.observe.dynamic.runner import event_schema_hash
from hubbleops.packs._protocol import ContractOracle
from hubbleops.sandbox import DetachedWorktree
from hubbleops.sandbox.verifier_image import (
    VERIFIER_IMAGE,
    VerifierIsolationViolated,
    forbidden_source_marker,
)
from hubbleops.verify import authority, coverage, gitdiff, suites, unsupported_modules

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
    base_capture_manifest_path: Path | None = None
    candidate_capture_manifest_path: Path | None = None


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


@dataclass(frozen=True, slots=True)
class CaptureArtifact:
    events: tuple[Mapping[str, Any], ...]
    manifest: Mapping[str, Any]
    payload_sha256: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "events": [dict(event) for event in self.events],
            "manifest": dict(self.manifest),
            "payload_sha256": self.payload_sha256,
        }


class InjectedOracle:
    def __init__(self, contract: ContractOracle, context_hash: str | None = None) -> None:
        self.contract = contract
        self.context_hash = context_hash or contract.context_hash()

    def validate(self, request: Mapping[str, Any], version: str) -> OracleOutcome:
        if self.contract.context_hash() != self.context_hash:
            return OracleOutcome(
                code="ORACLE_UNAVAILABLE",
                reason="oracle authority context changed after the verification scope was bound",
            )
        try:
            result = self.contract.validate(request, version)
        except PackDataError as error:
            return OracleOutcome(code="UNKNOWN_PROVIDER_CONTRACT", reason=str(error))
        if self.contract.context_hash() != self.context_hash:
            return OracleOutcome(
                code="ORACLE_UNAVAILABLE",
                reason="oracle authority context changed while validating the request",
            )
        return OracleOutcome(
            code=result.code,
            reason=result.reason,
            response=result.response,
            authority=result.authority,
        )


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
            base_scan = scan_repository(base, request.pack)
            candidate_scan = scan_repository(candidate, request.pack)
            from_version, to_version = _versions(request, base_scan)
            changes = change_set(request.pack, from_version, to_version)

            obligation_records = read_obligation_records(
                request.obligations_path, base_scan.proof_scope_hash, base_scan.run_id
            )
            obligations = authority.obligations_from(obligation_records)
            decisions = read_decisions(
                request.decisions_path,
                base_scan.proof_scope_hash,
                base_scan.run_id,
                base_scan.ledger,
            )
            candidate_ledger = decision.apply(
                candidate_scan.ledger,
                decisions,
                decided_run_id=base_scan.run_id,
                decided_proof_scope_hash=base_scan.proof_scope_hash,
            )
            base_capture = read_scoped_capture(
                request.base_capture_path,
                request.base_capture_manifest_path,
                base_sha,
                request.pack.name,
                "base",
            )
            candidate_capture = read_scoped_capture(
                request.candidate_capture_path,
                request.candidate_capture_manifest_path,
                candidate_sha,
                request.pack.name,
                "candidate",
            )

            plan = suites.stage_frozen(base, candidate, root / "frozen", repository)
            frozen = suites.run_frozen(plan, _python())
            candidate_tests = suites.run_candidate(candidate, root / "candidate-tests", _python())

            contract = request.pack.verification_contract()
            oracle_context_hash = contract.context_hash()
            inputs_hash = verification_inputs_hash(
                base_sha=base_sha,
                candidate_sha=candidate_sha,
                from_version=from_version,
                to_version=to_version,
                obligation_records=obligation_records,
                decisions=decisions,
                base_capture=base_capture,
                candidate_capture=candidate_capture,
                frozen_suite=frozen_suite_identity(base, plan.paths),
            )

            base_graph = _graph(base_scan, request.pack)
            graph = _graph(candidate_scan, request.pack)
            reachable = {entry.path for entry in candidate_scan.closure.entries}
            languages = suites.languages_of(sorted(reachable))
            candidate_paths = first_party_paths(candidate_scan.closure, candidate)
            uncoverable = unsupported_modules(
                reachable, languages, suites.covered_languages(frozen)
            )

            oracle = InjectedOracle(contract, oracle_context_hash)
            evaluation = authority.evaluate(
                authority.Inputs(
                    base_ledger=base_scan.ledger,
                    candidate_ledger=candidate_ledger,
                    base_graph=base_graph,
                    candidate_graph=graph,
                    delta=delta,
                    changes=changes,
                    obligations=obligations,
                    oracle=oracle,
                    falsifiers=falsifier_views(request.pack),
                    frozen_tests=frozen,
                    candidate_tests=candidate_tests,
                    candidate_root=str(candidate),
                    candidate_paths=candidate_paths,
                    base_captured=() if base_capture is None else base_capture.events,
                    candidate_captured=(
                        () if candidate_capture is None else candidate_capture.events
                    ),
                    decisions=decisions,
                    uncoverable=uncoverable,
                    supported_targets=request.pack.supported_targets(
                        candidate_scan.resolution.dependencies
                    ),
                    captures_supplied=(
                        request.base_capture_path is not None
                        or request.candidate_capture_path is not None
                    ),
                )
            )
            scope, scope_hash = _scope(
                candidate_scan,
                request.pack,
                inputs_hash,
                oracle_context_hash,
            )
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
                oracle_mode=evaluation.oracle.authority(),
            )


def refuse_repair_inputs(request: VerificationRequest) -> None:
    for label, path in (
        ("obligations", request.obligations_path),
        ("decisions", request.decisions_path),
        ("base capture", request.base_capture_path),
        ("candidate capture", request.candidate_capture_path),
        ("base capture manifest", request.base_capture_manifest_path),
        ("candidate capture manifest", request.candidate_capture_manifest_path),
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


def read_obligation_records(
    path: Path | None,
    expected_scope_hash: str | None = None,
    expected_run_id: str | None = None,
) -> tuple[dict[str, Any], ...]:
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
    validated = tuple(
        sorted(
            (validate("obligation", dict(as_mapping(record))) for record in records),
            key=lambda record: str(record["id"]),
        )
    )
    _require_scope("obligation", validated, expected_scope_hash, expected_run_id)
    return validated


def read_obligations(
    path: Path | None,
    expected_scope_hash: str | None = None,
    expected_run_id: str | None = None,
) -> tuple[ObligationView, ...]:
    return authority.obligations_from(
        read_obligation_records(path, expected_scope_hash, expected_run_id)
    )


def read_decisions(
    path: Path | None,
    expected_scope_hash: str | None = None,
    expected_run_id: str | None = None,
    book: Any | None = None,
) -> tuple[Mapping[str, Any], ...]:
    if path is None:
        return ()
    if path.suffix.casefold() in (".yaml", ".yml"):
        records = decision.load(path)
    else:
        parsed = parse_json(_read_input(path))
        if not parsed.ok():
            raise VerificationInvalid(f"{path} is not readable as decisions: {parsed.reason}")
        if not is_list(parsed.value):
            raise VerificationInvalid(f"{path} must contain a decision list")
        if any(not is_mapping(record) for record in as_sequence(parsed.value)):
            raise VerificationInvalid(f"{path} contains a non-object decision")
        records = tuple(dict(as_mapping(record)) for record in as_sequence(parsed.value))
    ordered = tuple(
        sorted(
            records,
            key=lambda record: (str(record.get("candidate_id", "")), str(record.get("id", ""))),
        )
    )
    if expected_scope_hash is None or expected_run_id is None or book is None:
        return ordered
    return decision.bound_records(ordered, expected_scope_hash, expected_run_id, book)


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


def read_scoped_capture(
    path: Path | None,
    manifest_path: Path | None,
    expected_repo_sha: str,
    expected_provider: str,
    label: str,
) -> CaptureArtifact | None:
    if path is None:
        if manifest_path is not None:
            raise VerificationInvalid(f"the {label} capture manifest has no capture event file")
        return None
    payload = _read_input(path)
    batch = events_from_jsonl(payload)
    if batch.issues:
        detail = "; ".join(
            f"{path}:{issue.row} {issue.code}: {issue.reason}" for issue in batch.issues
        )
        raise VerificationInvalid(f"capture input is invalid: {detail}")
    selected_manifest = manifest_path or path.with_name("execution-manifest.json")
    marker = forbidden_source_marker(selected_manifest)
    if marker is not None:
        raise VerifierIsolationViolated(
            f"the {label} capture manifest {selected_manifest} comes from the repair side"
        )
    parsed = parse_json(_read_input(selected_manifest))
    if not parsed.ok() or not is_mapping(parsed.value):
        raise VerificationInvalid(
            f"{selected_manifest} is not a valid capture execution manifest: "
            f"{parsed.reason or 'expected an object'}"
        )
    manifest = dict(as_mapping(parsed.value))
    payload_hash = hashlib.sha256(payload).hexdigest()
    if manifest.get("repo_sha") != expected_repo_sha:
        raise VerificationInvalid(
            f"the {label} capture manifest is bound to {manifest.get('repo_sha')}, "
            f"not {expected_repo_sha}"
        )
    if manifest.get("provider") != expected_provider:
        raise VerificationInvalid(
            f"the {label} capture manifest is for provider {manifest.get('provider')}, "
            f"not {expected_provider}"
        )
    if manifest.get("events_sha256") != payload_hash:
        raise VerificationInvalid(
            f"the {label} capture bytes do not match their execution manifest"
        )
    if manifest.get("event_schema_sha256") != event_schema_hash():
        raise VerificationInvalid(f"the {label} capture manifest names a different event schema")
    if not isinstance(manifest.get("execution_spec"), str) or not manifest["execution_spec"]:
        raise VerificationInvalid(f"the {label} capture manifest carries no execution identity")
    return CaptureArtifact(tuple(batch.events), manifest, payload_hash)


def verification_inputs_hash(
    *,
    base_sha: str,
    candidate_sha: str,
    from_version: str,
    to_version: str,
    obligation_records: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    base_capture: CaptureArtifact | None,
    candidate_capture: CaptureArtifact | None,
    frozen_suite: Mapping[str, Any],
) -> str:
    return content_id(
        {
            "schema_version": 1,
            "base_sha": base_sha,
            "candidate_sha": candidate_sha,
            "from_version": from_version,
            "to_version": to_version,
            "obligations": [dict(record) for record in obligation_records],
            "decisions": [dict(record) for record in decisions],
            "base_capture": None if base_capture is None else base_capture.to_mapping(),
            "candidate_capture": (
                None if candidate_capture is None else candidate_capture.to_mapping()
            ),
            "frozen_baseline_suite": dict(frozen_suite),
        }
    )


def frozen_suite_identity(root: Path, paths: Sequence[str]) -> dict[str, Any]:
    files: dict[str, str] = {}
    for name in paths:
        selected = root / name
        members = (selected,) if selected.is_file() else tuple(sorted(selected.rglob("*")))
        for member in members:
            if member.is_file() and ".git" not in member.parts:
                files[member.relative_to(root).as_posix()] = hashlib.sha256(
                    member.read_bytes()
                ).hexdigest()
    return {
        "paths": list(paths),
        "files": files,
        "coverage_plugin_sha256": hashlib.sha256(
            coverage.plugin_source().encode("utf-8")
        ).hexdigest(),
    }


def _require_scope(
    label: str,
    records: Sequence[Mapping[str, Any]],
    expected_scope_hash: str | None,
    expected_run_id: str | None = None,
) -> None:
    if expected_scope_hash is None:
        return
    mismatched = [
        str(record.get("id", "unnamed"))
        for record in records
        if record.get("proof_scope_hash") != expected_scope_hash
    ]
    if mismatched:
        raise VerificationInvalid(
            f"{label} input is not bound to the base ProofScope {expected_scope_hash}: "
            f"{', '.join(mismatched[:5])}"
        )
    wrong_run = [
        str(record.get("id", "unnamed"))
        for record in records
        if expected_run_id is not None and record.get("run_id") != expected_run_id
    ]
    if wrong_run:
        raise VerificationInvalid(
            f"{label} input is not bound to the base run {expected_run_id}: "
            f"{', '.join(wrong_run[:5])}"
        )


def _read_input(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            payload = handle.read(MAX_JSON_BYTES + 1)
    except OSError as error:
        raise VerificationInvalid(f"{path} cannot be read: {error}") from error
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
    known = set(lattice)
    return tuple(
        sorted(
            {
                version
                for record in scan.ledger.evidence
                for version in declared_versions(record)
                if version in known
            }
        )
    )


def first_party_paths(closure: Any, tree: Path) -> tuple[str, ...]:
    languages = suites.languages_of(sorted(entry.path for entry in closure.entries))
    test_roots = suites.suite_paths(tree)
    return tuple(
        sorted(
            entry.path
            for entry in closure.entries
            if entry.classification is Classification.INSIDE
            and languages[entry.path] != "unknown"
            and not any(
                entry.path == root or entry.path.startswith(f"{root}/") for root in test_roots
            )
        )
    )


def first_party_sources(closure: Any, tree: Path) -> dict[str, str]:
    sources: dict[str, str] = {}
    for relative in first_party_paths(closure, tree):
        try:
            sources[relative] = (tree / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return sources


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


def _scope(
    scan: Any,
    pack: registry.LoadedPack,
    inputs_hash: str,
    oracle_context_hash: str,
) -> tuple[dict[str, Any], str]:
    scope = make_proof_scope(
        repo_sha=scan.closure.repo_sha,
        tree_hash=scan.closure.tree_hash(),
        dependency_resolution_hash=scan.resolution.resolution_hash(),
        provider_contract_hash=pack.contract_hash(),
        rules_hash=structure.rules_hash(structural_rules(pack)),
        scanner_version=scan.scanner_version,
        verifier_version=verifier_version(),
        verifier_image_hash=VERIFIER_IMAGE.fingerprint(),
        verification_inputs_hash=inputs_hash,
        oracle_context_hash=oracle_context_hash,
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
    "CaptureArtifact",
    "InjectedOracle",
    "VerificationInvalid",
    "VerificationRequest",
    "VerificationRun",
    "change_set",
    "execute",
    "falsifier_views",
    "read_capture",
    "read_decisions",
    "read_obligation_records",
    "read_obligations",
    "read_scoped_capture",
    "refuse_repair_inputs",
    "structural_rules",
    "verification_inputs_hash",
    "verifier_version",
]
