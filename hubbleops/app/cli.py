from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml

from hubbleops import __version__
from hubbleops.app import (
    capture,
    decision,
    exposure,
    impact,
    migration,
    promotion,
    registry,
    replay,
    stability,
    verification,
)
from hubbleops.closure import source_closure
from hubbleops.core import runlog
from hubbleops.core.canonical import canonical_bytes, content_id, export_bytes
from hubbleops.core.errors import HubbleOpsError, ToolingFailed, ToolingMissing, ToolingTimeout
from hubbleops.core.observer import ObserverContext, StructuralRule
from hubbleops.core.precise import SymbolIndex
from hubbleops.core.proof_scope import (
    make_proof_scope,
    proof_scope_hash,
    run_id_for,
    scanner_fingerprint,
    short_scope,
)
from hubbleops.core.records import as_mapping
from hubbleops.graph import indexers
from hubbleops.graph.imports import AstGrep, language_for
from hubbleops.observe import DEFERRED_VALIDATION, deps, ledger, structure, telemetry, text
from hubbleops.observe.dynamic import runner as dynamic
from hubbleops.proof import exposure_workflow, guard, memory, pr_body, receipt
from hubbleops.store.artifacts import write_atomic
from hubbleops.store.sqlite import Store
from hubbleops.verify import static_request

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
OBSERVATION_SOURCES = tuple(sorted(PACKAGE_ROOT.rglob("*.py")))

DEFAULT_STATE_DIR = ".hubbleops"
OBLIGATIONS_FILENAME = "obligations.json"
DEFERRED_SITE_BUDGET = 10
EXIT_OK = 0
EXIT_TOOLING_MISSING = 3
EXIT_FAILED = 4
EXIT_UNKNOWN = 5


def main(argv: list[str] | None = None) -> int:
    _use_utf8(sys.stdout)
    _use_utf8(sys.stderr)
    runlog.configure()
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        handler: Any = args.handler
        return int(handler(args))
    except ToolingMissing as error:
        print(str(error), file=sys.stderr)
        return EXIT_TOOLING_MISSING
    except ToolingTimeout as error:
        print(str(error), file=sys.stderr)
        return EXIT_UNKNOWN
    except HubbleOpsError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return EXIT_FAILED


def _use_utf8(stream: Any) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hops", description="HubbleOps truth engine")
    parser.add_argument("--version", action="version", version=f"hubbleops {__version__}")
    subparsers = parser.add_subparsers(dest="verb", required=True)

    scan = subparsers.add_parser("scan", help="build the candidate ledger for a repository")
    scan.add_argument("repo", help="path to the repository to scan")
    scan.add_argument(
        "--pack", required=True, help=f"one of: {', '.join(registry.available_packs())}"
    )
    scan.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    scan.add_argument("--export", default=None, help="also write the ledger export to this path")
    scan.add_argument(
        "--target", default=None, help="version the exposure map reads against; default is latest"
    )
    scan.add_argument(
        "--force",
        action="store_true",
        help="continue fail-closed when structural tooling is unavailable",
    )
    scan.set_defaults(handler=_scan)

    capture_command = subparsers.add_parser(
        "capture", help="build a ledger from isolated test execution"
    )
    capture_command.add_argument("repo", help="path to the repository to capture")
    capture_command.add_argument(
        "--pack", required=True, help=f"one of: {', '.join(registry.available_packs())}"
    )
    capture_command.add_argument("--cmd", required=True, help="test command executed in isolation")
    capture_command.add_argument("--capture-mode", choices=("proxy", "hook"), default="proxy")
    capture_command.add_argument(
        "--language",
        choices=("python", "php", "javascript", "typescript", "node"),
        default="python",
    )
    capture_command.add_argument(
        "--allow", action="append", default=[], help="exact scheme://host:port proxy destination"
    )
    capture_command.add_argument("--telemetry-export")
    capture_command.add_argument("--sentinel-events")
    capture_command.add_argument("--sentinel-manifest")
    capture_command.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    capture_command.add_argument("--export", default=None)
    capture_command.add_argument("--force", action="store_true")
    capture_command.set_defaults(handler=_capture)

    show = subparsers.add_parser("exposure", help="print the exposure map for a run")
    show.add_argument("--run", default=None, help="run id; defaults to the most recent run")
    show.add_argument("--pack", default=None, help="restrict the default run lookup to this pack")
    show.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    show.add_argument(
        "--target", default=None, help="version the map reads against; default is latest"
    )
    show.add_argument("--expand", action="store_true", help="list every site and every file")
    show.add_argument(
        "--install-workflow",
        action="store_true",
        help="write the read-only pull_request Action that produces this map in CI",
    )
    show.add_argument("--repo", default=".", help="repository the workflow is written into")
    show.add_argument(
        "--source",
        default=None,
        help="what `uvx --from` installs in CI; defaults to this package's pinned version",
    )
    show.set_defaults(handler=_exposure)

    promote_command = subparsers.add_parser(
        "promote", help="promote or revoke a captured repository wrapper"
    )
    promote_command.add_argument("repo", help="path to the repository")
    promote_command.add_argument("--run", required=True, help="capture run id")
    promote_command.add_argument("--candidate", required=True, help="candidate or promotion id")
    promote_command.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    promote_command.add_argument("--revoke", action="store_true")
    promote_command.set_defaults(handler=_promote)

    decide_command = subparsers.add_parser(
        "decide", help="record a source-bound human decision for an open candidate"
    )
    decide_command.add_argument("unknown_id", help="full candidate id or an unambiguous prefix")
    decide_command.add_argument("--value", choices=decision.DECISION_VALUES, required=True)
    decide_command.add_argument("--by", required=True, dest="decided_by")
    decide_command.add_argument("--run", default=None, help="scan run id; defaults to the latest")
    decide_command.add_argument("--repo", default=".", help="repository receiving decisions.yml")
    decide_command.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    decide_command.set_defaults(handler=_decide)

    verify_command = subparsers.add_parser(
        "verify", help="judge a candidate SHA against a base SHA and return a Receipt"
    )
    verify_command.add_argument("base_sha", help="the commit the migration starts from")
    verify_command.add_argument("candidate_sha", help="the commit the migration produced")
    verify_command.add_argument(
        "--pack", required=True, help=f"one of: {', '.join(registry.available_packs())}"
    )
    verify_command.add_argument("--repo", default=".", help="path to the repository")
    verify_command.add_argument("--from", dest="from_version", default=None)
    verify_command.add_argument("--to", dest="to_version", default=None)
    verify_command.add_argument("--obligations", default=None)
    verify_command.add_argument("--decisions", default=None)
    verify_command.add_argument("--base-capture", default=None)
    verify_command.add_argument("--candidate-capture", default=None)
    verify_command.add_argument("--base-capture-manifest", default=None)
    verify_command.add_argument("--candidate-capture-manifest", default=None)
    verify_command.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    verify_command.add_argument("--receipt", default=None, help="also write receipt.json here")
    verify_command.set_defaults(handler=_verify)

    migrate_command = subparsers.add_parser(
        "migrate", help="apply deterministic repairs and write the obligations they discharge"
    )
    migrate_command.add_argument("repo", help="path to the repository to migrate")
    migrate_command.add_argument(
        "--pack", required=True, help=f"one of: {', '.join(registry.available_packs())}"
    )
    migrate_command.add_argument(
        "--target", default=None, help="target version; defaults to the pack's latest"
    )
    migrate_command.add_argument(
        "--obligations",
        default=None,
        help="write the obligation list here; defaults to the state directory",
    )
    migrate_command.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing any file",
    )
    migrate_command.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    migrate_command.set_defaults(handler=_migrate)

    impact_command = subparsers.add_parser(
        "impact", help="report provider impact from a full clean repository rescan"
    )
    impact_command.add_argument("repo", help="path to the repository to analyze")
    impact_command.add_argument(
        "--pack", required=True, help=f"one of: {', '.join(registry.available_packs())}"
    )
    impact_command.add_argument(
        "--target", default=None, help="target version; defaults to the pack's latest"
    )
    impact_command.add_argument("--output", default=None, help="also write impact.json here")
    impact_command.add_argument("--force", action="store_true")
    impact_command.set_defaults(handler=_impact)

    replay_command = subparsers.add_parser(
        "replay", help="verify the identity and artifacts of a completed run"
    )
    replay_command.add_argument("run_id", help="content-addressed run id")
    replay_command.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    replay_command.set_defaults(handler=_replay)

    guard_command = subparsers.add_parser(
        "guard", help="fail when a cumulative retired surface is reintroduced"
    )
    guard_command.add_argument("--repo", default=".")
    guard_command.add_argument("--retired", default=None)
    guard_command.add_argument("--rg", default="rg")
    guard_command.add_argument("--install", action="store_true")
    guard_command.add_argument("--command", default="uv run hops")
    guard_command.set_defaults(handler=_guard)

    prepare_command = subparsers.add_parser(
        "prepare-pr", help="write the Proof Pack body, repo memory, and exact-SHA Actions"
    )
    prepare_command.add_argument("receipt", help="verified receipt.json")
    prepare_command.add_argument("--repo", default=".")
    prepare_command.add_argument(
        "--body",
        default=".hubbleops/artifacts/hubbleops-pr-body.md",
        help="where the pull request description is written; run output, never committed",
    )
    prepare_command.add_argument(
        "--obligations",
        default=None,
        help="the obligation list hops migrate wrote; defaults to the state directory",
    )
    prepare_command.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    prepare_command.add_argument("--command", default="uv run hops")
    prepare_command.set_defaults(handler=_prepare_pr)

    pack = subparsers.add_parser("pack", help="inspect and verify provider packs")
    pack_commands = pack.add_subparsers(dest="pack_verb", required=True)
    pack_verify = pack_commands.add_parser("verify", help="verify an offline provider pack")
    pack_verify.add_argument("name", help=f"one of: {', '.join(registry.available_packs())}")
    pack_verify.set_defaults(handler=_pack_verify)

    return parser


@dataclass(frozen=True, slots=True)
class ScanResult:
    target: Path
    identity: str
    pack: registry.LoadedPack
    closure: source_closure.SourceClosure
    resolution: deps.DependencyResolution
    proof_scope: dict[str, Any]
    proof_scope_hash: str
    run_id: str
    scanner_version: str
    structural_coverage: structure.StructuralCoverage
    ledger: ledger.Ledger

    def closure_summary(self) -> dict[str, Any]:
        return {
            "root": str(self.target),
            "entries": len(self.closure.entries),
            "counts": self.closure.counts(),
            "roles": self.closure.role_counts(),
            "control_entries": list(self.closure.control_entries),
            "pack": {
                "changes_hash": self.pack.changes.lattice_hash,
                "target": self.pack.latest_compatible(self.resolution.dependencies),
            },
            "structural_coverage": self.structural_coverage.to_mapping(),
            "precise_indexes": self.structural_coverage.indexes_mapping(),
        }


@dataclass(frozen=True, slots=True)
class CaptureResult:
    scan: ScanResult
    attempt: capture.CaptureAttempt
    inputs: tuple[capture.ProductionInput, ...]


def _observe(
    log: runlog.RunLogger, name: str, run: Callable[[], list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    with log.stage("observer", observer=name) as fields:
        records = run()
        fields["records"] = len(records)
        return records


def _observe_all(
    closure: source_closure.SourceClosure,
    ctx: ObserverContext,
    resolution: deps.DependencyResolution,
    run_id: str,
) -> list[dict[str, Any]]:
    log = runlog.logger("scan", run_id)
    with log.stage("observers") as totals:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [
                pool.submit(_observe, log, "text", lambda: text.scan(closure, ctx)),
                pool.submit(_observe, log, "deps", lambda: deps.scan(closure, ctx, resolution)),
                pool.submit(_observe, log, "structure", lambda: structure.scan(closure, ctx)),
            ]
            records = [record for future in futures for record in future.result()]
        totals["records"] = len(records)
    return records


def judge_skeletons(
    pack: registry.LoadedPack, records: Sequence[Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    contract = pack.contract
    versions = [version.id for version in pack.versions()]
    judgements: dict[str, dict[str, Any]] = {}
    for record in sorted(records, key=lambda item: str(item["id"])):
        if record["observer"] != "structure" or record["claim_type"] != "request_text":
            continue
        if as_mapping(record["value"]).get("resolution") != DEFERRED_VALIDATION:
            continue
        request = static_request(record)
        if request is None:
            continue
        accepted: list[str] = []
        rejected: dict[str, str] = {}
        undecided: dict[str, str] = {}
        authority: str | None = None
        for version in versions:
            try:
                outcome = contract.validate(request, version)
            except Exception as error:
                undecided[version] = f"{type(error).__name__}: {error}"
                continue
            if outcome.code == "VALID" and outcome.authority is not None:
                accepted.append(version)
                authority = str(outcome.authority)
            elif outcome.code == "INVALID":
                rejected[version] = outcome.reason
            else:
                undecided[version] = outcome.reason
        judgements[str(record["id"])] = {
            "accepted": accepted,
            "rejected": rejected,
            "undecided": undecided,
            "authority": authority,
        }
    return judgements


def scan_identity(closure: source_closure.SourceClosure) -> str:
    if closure.repo_sha is not None:
        return f"commit:{closure.repo_sha}"
    return f"tree:{closure.tree_hash()}"


@dataclass(frozen=True, slots=True)
class PreciseLayer:
    indexes: tuple[SymbolIndex, ...]
    status: dict[str, str]
    identity_segment: str


def precise_layer(
    closure: source_closure.SourceClosure, executables: Mapping[str, str], force: bool
) -> PreciseLayer:
    inside = [
        entry
        for entry in closure.entries
        if entry.classification is source_closure.Classification.INSIDE
        and not entry.carries_bulk_data()
    ]
    present = {language_for(entry.path) for entry in inside}
    status = {
        language: indexers.recall_only_sentence(language)
        for language in present
        if language in indexers.RECALL_ONLY_LANGUAGES
    }
    indexes: list[SymbolIndex] = []
    segments: list[str] = []
    for runner in indexers.runners(executables):
        sources = sorted(
            entry.path for entry in inside if language_for(entry.path) in runner.languages
        )
        if not sources:
            continue
        family = sorted(present & set(runner.languages))
        try:
            identity = runner.identity()
        except ToolingMissing as error:
            for language in family:
                status[language] = _missing_status(runner.name, error)
            continue
        except (ToolingFailed, ToolingTimeout) as error:
            if not force:
                raise
            for language in family:
                status[language] = _forced_status(runner.name, error)
            segments.append(f";{runner.name}=unavailable-forced")
            continue
        configs = sorted(
            entry.path
            for entry in inside
            if entry.path.rsplit("/", 1)[-1] in runner.config_basenames
        )
        try:
            run = runner.index(closure.root, sources, configs)
        except (ToolingFailed, ToolingTimeout) as error:
            if not force:
                raise
            for language in family:
                status[language] = _forced_status(runner.name, error)
            segments.append(f";{runner.name}={identity}+forced-recall-only")
            continue
        segments.append(f";{runner.name}={identity}")
        covered = run.index.counts()["documents"]
        detail = f"precise: {runner.name} {identity} · documents {covered}"
        if run.rewritten_configs:
            detail += f" · configs rewritten without their extends {len(run.rewritten_configs)}"
        if run.dropped_documents:
            detail += f" · documents outside the sources dropped {run.dropped_documents}"
        detail += f" · external symbol occurrences dropped {run.dropped_occurrences}"
        for language in family:
            status[language] = detail
        indexes.append(run.index)
    return PreciseLayer(tuple(indexes), status, "".join(segments))


def _missing_status(name: str, error: ToolingMissing) -> str:
    if error.detail == indexers.WINDOWS_START_FAILURE:
        return f"recall-only: {name} {error.detail}"
    return f"recall-only: {name} is not installed on this host"


def _forced_status(name: str, error: HubbleOpsError) -> str:
    outcome = "timed out" if isinstance(error, ToolingTimeout) else "failed"
    return f"recall-only (forced): {name} {outcome}; every claim in its languages stays name-bound"


def scan_repository(
    target: Path,
    pack: registry.LoadedPack,
    *,
    force: bool = False,
    ast_grep_executable: str = "ast-grep",
    indexer_executables: Mapping[str, str] | None = None,
) -> ScanResult:
    resolved = target.resolve()
    boot = runlog.logger("scan")
    with boot.stage("source_closure", target=str(resolved)) as counts:
        closure = source_closure.build(resolved)
        counts["entries"] = len(closure.entries)
    identity = scan_identity(closure)
    with boot.stage("dependency_resolution"):
        resolution = deps.resolve(closure)
    with tempfile.TemporaryDirectory(prefix="hops-promoted-rules-") as temporary:
        rules = (
            *structural_rules_of(pack),
            *promotion.materialize_active(resolved, Path(temporary)),
        )
        try:
            ast_grep_version = AstGrep(ast_grep_executable).version()
        except (ToolingMissing, ToolingFailed, ToolingTimeout):
            if not force:
                raise
            ast_grep_version = "unavailable-forced"
        with boot.stage("precise_index") as counts:
            precise = precise_layer(closure, indexer_executables or {}, force)
            counts["indexes"] = len(precise.indexes)
        scanner_version = (
            f"hubbleops={__version__}"
            f";pipeline={scanner_fingerprint(OBSERVATION_SOURCES)}"
            f";rg={text.ripgrep_version()}"
            f";ast-grep={ast_grep_version}"
            f"{precise.identity_segment}"
        )
        scope = make_proof_scope(
            repo_sha=closure.repo_sha,
            tree_hash=closure.tree_hash(),
            dependency_resolution_hash=resolution.resolution_hash(),
            provider_contract_hash=pack.contract_hash(),
            rules_hash=structure.rules_hash(rules),
            scanner_version=scanner_version,
        )
        scope_hash = proof_scope_hash(scope)
        run_id = run_id_for(scope_hash=scope_hash, provider=pack.name, verb="scan", target=identity)
        ctx = ObserverContext(
            provider=pack.name,
            run_id=run_id,
            proof_scope_hash=scope_hash,
            repo_sha=closure.repo_sha,
            dependency_context_hash=resolution.resolution_hash(),
            surface=pack.surface,
            rules=rules,
            ast_grep_executable=ast_grep_executable,
            force_structure=force,
            precise_indexes=precise.indexes,
        )
        records = _observe_all(closure, ctx, resolution, run_id)
        book = ledger.build(
            provider=pack.name,
            run_id=run_id,
            proof_scope_hash=scope_hash,
            evidence=records,
            closure=closure,
            surface=pack.surface,
            validations=judge_skeletons(pack, records),
            stability=stability.compute(pack),
            lattice=[version.id for version in pack.versions()],
        )
        return ScanResult(
            target=resolved,
            identity=identity,
            pack=pack,
            closure=closure,
            resolution=resolution,
            proof_scope=scope,
            proof_scope_hash=scope_hash,
            run_id=run_id,
            scanner_version=scanner_version,
            structural_coverage=structure.coverage(
                closure, rules, records, precise.indexes, precise.status
            ),
            ledger=book,
        )


def chosen_target(pack: registry.LoadedPack, requested: str | None) -> str:
    known = tuple(version.id for version in pack.versions())
    if not known:
        raise HubbleOpsError(f"pack {pack.name} publishes no versions, so no target can be chosen")
    if requested is None:
        return known[-1]
    if requested not in known:
        raise HubbleOpsError(
            f"{requested} is not a version of {pack.name}; known: {', '.join(known)}"
        )
    return requested


def _scan(args: argparse.Namespace) -> int:
    pack = registry.load_pack(args.pack)
    target = chosen_target(pack, args.target)
    result = scan_repository(Path(args.repo), pack, force=bool(args.force))
    payload = export_bytes(result.ledger.export())
    state_dir = Path(args.state_dir).resolve()
    artifact_path = state_dir / "artifacts" / result.run_id / "ledger.json"
    with Store(state_dir) as store:
        store.start_run(
            run_id=result.run_id,
            proof_scope=result.proof_scope,
            proof_scope_hash=result.proof_scope_hash,
            provider=result.pack.name,
            verb="scan",
            target=result.identity,
            closure_summary=result.closure_summary(),
            started_at=datetime.now(UTC).isoformat(),
        )
        store.write_evidence(result.ledger.evidence)
        store.write_candidates(result.ledger.candidates)
        digest = write_atomic(artifact_path, payload)
        store.write_artifact(
            run_id=result.run_id,
            proof_scope_hash=result.proof_scope_hash,
            kind="ledger",
            path=str(artifact_path),
            sha256=digest,
            size=len(payload),
        )
        store.finish_run(result.run_id, datetime.now(UTC).isoformat())
    if args.export:
        write_atomic(Path(args.export).resolve(), payload)
    print(_scan_summary(result))
    print(
        exposure.render_queries(
            exposure.queries(pack=pack, ledger=result.ledger, target=target), target, expand=False
        )
    )
    print(f"LEDGER  {artifact_path}")
    print(f"  sha256  {digest}")
    print(
        f"TARGET  {target} is not stored; read the full map with `hops exposure --target {target}`"
    )
    return EXIT_OK


def _capture(args: argparse.Namespace) -> int:
    if bool(args.sentinel_events) != bool(args.sentinel_manifest):
        raise capture.CaptureInvalid(
            "--sentinel-events and --sentinel-manifest must be supplied together"
        )
    target = Path(args.repo).resolve()
    pack = registry.load_pack(args.pack)
    state_dir = Path(args.state_dir).resolve()
    initial = source_closure.build(target)
    attempt = capture.execute(
        target,
        initial.repo_sha,
        pack,
        str(args.cmd),
        str(args.language),
        str(args.capture_mode),
        tuple(str(item) for item in args.allow),
        state_dir / "attempts",
    )
    inputs: list[capture.ProductionInput] = []
    if args.telemetry_export:
        telemetry_path = Path(args.telemetry_export).resolve()
        try:
            inputs.append(capture.telemetry_input(telemetry_path, pack))
        except (OSError, UnicodeDecodeError, capture.CaptureInvalid) as error:
            inputs.append(_failed_input("telemetry", (telemetry_path,), error))
    if args.sentinel_events and args.sentinel_manifest:
        event_path = Path(args.sentinel_events).resolve()
        manifest_path = Path(args.sentinel_manifest).resolve()
        try:
            inputs.append(capture.sentinel_input(event_path, manifest_path, pack))
        except (OSError, capture.CaptureInvalid) as error:
            inputs.append(_failed_input("sentinel", (event_path, manifest_path), error))
    result = _capture_repository(
        target,
        pack,
        attempt,
        tuple(inputs),
        str(args.cmd),
        force=bool(args.force),
    )
    payload = export_bytes(result.scan.ledger.export())
    run_dir = state_dir / "artifacts" / result.scan.run_id
    artifact_payloads = {
        **result.attempt.artifacts,
        "execution-manifest.json": canonical_bytes(result.attempt.manifest) + b"\n",
        "ledger.json": payload,
    }
    for index, imported in enumerate(result.inputs, start=1):
        name = f"input-{index}-manifest.json"
        artifact_payloads[name] = canonical_bytes(imported.manifest) + b"\n"
        for artifact_name, data in imported.artifacts.items():
            artifact_payloads[f"input-{index}-{artifact_name}"] = data
    written = _write_capture_artifacts(run_dir, artifact_payloads)
    with Store(state_dir) as store:
        store.start_run(
            run_id=result.scan.run_id,
            proof_scope=result.scan.proof_scope,
            proof_scope_hash=result.scan.proof_scope_hash,
            provider=pack.name,
            verb="capture",
            target=result.scan.identity,
            closure_summary=result.scan.closure_summary(),
            started_at=datetime.now(UTC).isoformat(),
        )
        store.write_evidence(result.scan.ledger.evidence)
        store.write_candidates(result.scan.ledger.candidates)
        for name, path in written.items():
            data = artifact_payloads[name]
            store.write_artifact(
                run_id=result.scan.run_id,
                proof_scope_hash=result.scan.proof_scope_hash,
                kind=name,
                path=str(path),
                sha256=hashlib.sha256(data).hexdigest(),
                size=len(data),
            )
        store.finish_run(result.scan.run_id, datetime.now(UTC).isoformat())
    if args.export:
        write_atomic(Path(args.export).resolve(), payload)
    print(_capture_summary(result))
    print(f"LEDGER  {written['ledger.json']}")
    return EXIT_UNKNOWN if result.attempt.batch.issues else EXIT_OK


def _capture_repository(
    target: Path,
    pack: registry.LoadedPack,
    attempt: capture.CaptureAttempt,
    inputs: tuple[capture.ProductionInput, ...],
    command: str,
    *,
    force: bool,
    ast_grep_executable: str = "ast-grep",
    indexer_executables: Mapping[str, str] | None = None,
) -> CaptureResult:
    closure = source_closure.build(target)
    resolution = deps.resolve(closure)
    bound_inputs = tuple(
        capture.ProductionInput(
            {
                **item.manifest,
                "dependency_resolution_hash": resolution.resolution_hash(),
                "repo_sha": closure.repo_sha,
                "tree_hash": closure.tree_hash(),
            },
            item.batch,
            item.observations,
            item.issues,
            item.artifacts,
        )
        for item in inputs
    )
    configuration = content_id(
        {
            "execution": attempt.manifest,
            "production_inputs": [item.manifest for item in bound_inputs],
        }
    )
    with tempfile.TemporaryDirectory(prefix="hops-promoted-rules-") as temporary:
        rules = (*structural_rules_of(pack), *promotion.materialize_active(target, Path(temporary)))
        try:
            ast_grep_version = AstGrep(ast_grep_executable).version()
        except (ToolingMissing, ToolingFailed, ToolingTimeout):
            if not force:
                raise
            ast_grep_version = "unavailable-forced"
        precise = precise_layer(closure, indexer_executables or {}, force)
        scanner_version = (
            f"hubbleops={__version__}"
            f";pipeline={scanner_fingerprint(OBSERVATION_SOURCES)}"
            f";rg={text.ripgrep_version()}"
            f";ast-grep={ast_grep_version}"
            f"{precise.identity_segment}"
        )
        scope = make_proof_scope(
            repo_sha=closure.repo_sha,
            tree_hash=closure.tree_hash(),
            dependency_resolution_hash=resolution.resolution_hash(),
            build_command=command,
            build_config_hash=configuration,
            provider_contract_hash=pack.contract_hash(),
            rules_hash=structure.rules_hash(rules),
            scanner_version=scanner_version,
        )
        scope_hash = proof_scope_hash(scope)
        run_id = run_id_for(
            scope_hash=scope_hash, provider=pack.name, verb="capture", target=scan_identity(closure)
        )
        ctx = ObserverContext(
            provider=pack.name,
            run_id=run_id,
            proof_scope_hash=scope_hash,
            repo_sha=closure.repo_sha,
            dependency_context_hash=resolution.resolution_hash(),
            surface=pack.surface,
            rules=rules,
            ast_grep_executable=ast_grep_executable,
            force_structure=force,
            precise_indexes=precise.indexes,
        )
        records = _observe_all(closure, ctx, resolution, run_id)
        static_book = ledger.build(
            provider=pack.name,
            run_id=run_id,
            proof_scope_hash=scope_hash,
            evidence=records,
            closure=closure,
        )
        provisional = dynamic.events_to_evidence(attempt.batch, ctx, target)
        provisional_book = ledger.build(
            provider=pack.name,
            run_id=run_id,
            proof_scope_hash=scope_hash,
            evidence=(*records, *provisional),
            closure=closure,
        )
        static_ids = {str(candidate["id"]) for candidate in static_book.candidates}
        dynamic_mappings = {
            (
                str(event["service"]),
                str(event["method"]),
                str(event["version"]),
            ): tuple(
                candidate_id
                for candidate_id in telemetry.candidate_ids(
                    provisional_book,
                    telemetry.ProductionTuple(
                        str(event["service"]), str(event["method"]), str(event["version"])
                    ),
                )
                if candidate_id in static_ids
            )
            for event in attempt.batch.events
        }
        records.extend(
            dynamic.events_to_evidence(attempt.batch, ctx, target, candidate_ids=dynamic_mappings)
        )
        observed_book = ledger.build(
            provider=pack.name,
            run_id=run_id,
            proof_scope_hash=scope_hash,
            evidence=records,
            closure=closure,
        )
        for imported in bound_inputs:
            if imported.batch is not None:
                mappings = {
                    (
                        str(event["service"]),
                        str(event["method"]),
                        str(event["version"]),
                    ): tuple(
                        candidate_id
                        for candidate_id in telemetry.candidate_ids(
                            observed_book,
                            telemetry.ProductionTuple(
                                str(event["service"]),
                                str(event["method"]),
                                str(event["version"]),
                            ),
                        )
                        if candidate_id in static_ids
                    )
                    for event in imported.batch.events
                }
                records.extend(
                    dynamic.events_to_evidence(
                        imported.batch,
                        ctx,
                        target,
                        observer="sentinel",
                        allow_site_claims=False,
                        candidate_ids=mappings,
                    )
                )
            elif imported.observations or imported.issues:
                records.extend(
                    telemetry.reconcile(
                        imported.observations, imported.issues, observed_book, ctx
                    ).evidence
                )
        book = ledger.build(
            provider=pack.name,
            run_id=run_id,
            proof_scope_hash=scope_hash,
            evidence=records,
            closure=closure,
        )
        scan = ScanResult(
            target=target,
            identity=scan_identity(closure),
            pack=pack,
            closure=closure,
            resolution=resolution,
            proof_scope=scope,
            proof_scope_hash=scope_hash,
            run_id=run_id,
            scanner_version=scanner_version,
            structural_coverage=structure.coverage(
                closure, rules, records, precise.indexes, precise.status
            ),
            ledger=book,
        )
        return CaptureResult(scan, attempt, bound_inputs)


def _failed_input(
    kind: str, paths: tuple[Path, ...], error: BaseException
) -> capture.ProductionInput:
    records = [_bounded_artifact(path, dynamic.MAX_EVENT_FILE_BYTES) for path in paths]
    manifest = {
        "error": str(error),
        "input_kind": kind,
        "inputs": [
            {"name": path.name, "sha256": digest, "size": size}
            for path, (digest, size, _) in zip(paths, records, strict=True)
        ],
        "parser_limit": dynamic.MAX_EVENT_FILE_BYTES,
    }
    artifacts = {
        f"{kind}-{index}-{path.name}": retained
        for index, (path, (_, _, retained)) in enumerate(zip(paths, records, strict=True), start=1)
    }
    if kind == "sentinel":
        batch = dynamic.EventBatch(
            (),
            (dynamic.EventIssue("SENTINEL_IMPORT_INVALID", 0, str(error)),),
        )
        return capture.ProductionInput(manifest, batch=batch, artifacts=artifacts)
    issue = telemetry.AdapterIssue(0, "", f"{kind.upper()}_IMPORT_INVALID: {error}")
    return capture.ProductionInput(manifest, issues=(issue,), artifacts=artifacts)


def _bounded_artifact(path: Path, maximum: int) -> tuple[str, int, bytes]:
    digest = hashlib.sha256()
    size = 0
    retained = bytearray()
    if not path.is_file():
        return digest.hexdigest(), size, bytes(retained)
    with path.open("rb") as handle:
        while chunk := handle.read(65_536):
            digest.update(chunk)
            size += len(chunk)
            remaining = maximum - len(retained)
            if remaining > 0:
                retained.extend(chunk[:remaining])
    return digest.hexdigest(), size, bytes(retained)


def _write_capture_artifacts(run_dir: Path, values: Mapping[str, bytes]) -> dict[str, Path]:
    run_dir.mkdir(parents=True, exist_ok=False)
    paths: dict[str, Path] = {}
    for name, data in sorted(values.items()):
        path = run_dir / name
        with path.open("xb") as handle:
            handle.write(data)
            handle.flush()
        paths[name] = path
    return paths


def _capture_summary(result: CaptureResult) -> str:
    issues = ", ".join(sorted({item.code for item in result.attempt.batch.issues})) or "none"
    return "\n".join(
        (
            f"HubbleOps capture - {result.scan.pack.name}",
            f"  Repository   {result.scan.target}",
            f"  ProofScope   {short_scope(result.scan.proof_scope_hash)}",
            f"  Run          {result.scan.run_id}",
            f"  Events       {len(result.attempt.batch.events)}",
            f"  Issues       {issues}",
            f"  Unexplained  {result.scan.ledger.unexplained()}",
        )
    )


def _promote(args: argparse.Namespace) -> int:
    with Store(Path(args.state_dir).resolve()) as store:
        path = promotion.promote(
            Path(args.repo),
            store,
            str(args.run),
            str(args.candidate),
            revoke=bool(args.revoke),
        )
    action = "REVOKED" if args.revoke else "PROMOTED"
    print(f"{action}  {path}")
    return EXIT_OK


def _decide(args: argparse.Namespace) -> int:
    destination, item = decision.write(
        repository=Path(args.repo),
        state_dir=Path(args.state_dir).resolve(),
        candidate_prefix=str(args.unknown_id),
        value=str(args.value),
        decided_by=str(args.decided_by),
        run_id=args.run,
    )
    print(f"DECIDED  {item['candidate_id']}")
    print(f"  value  {item['value']}")
    print(f"  blob   {item['blob_hash']}")
    print(f"  file   {destination}")
    return EXIT_OK


def _exposure(args: argparse.Namespace) -> int:
    if args.install_workflow:
        if not args.pack:
            print("--install-workflow needs --pack", file=sys.stderr)
            return EXIT_FAILED
        pack = registry.load_pack(args.pack)
        target = chosen_target(pack, args.target)
        destination = exposure_workflow.install(
            Path(args.repo),
            pack.name,
            target,
            args.source or exposure_workflow.default_source(),
        )
        print(f"EXPOSURE WORKFLOW  {destination}")
        return EXIT_OK
    state_dir = Path(args.state_dir).resolve()
    with Store(state_dir) as store:
        row = store.run(args.run) if args.run else store.latest_run(args.pack)
        if row is None:
            print(
                f"no run found in {state_dir}; run `hops scan <repo> --pack <name>` first",
                file=sys.stderr,
            )
            return EXIT_FAILED
        book = ledger.Ledger(
            provider=row.provider,
            run_id=row.run_id,
            proof_scope_hash=row.proof_scope_hash,
            evidence=store.evidence_for(row.run_id),
            candidates=store.candidates_for(row.run_id),
        )
        pack_record = as_mapping(row.closure.get("pack"))
        changes_hash = str(pack_record.get("changes_hash", "UNKNOWN"))
    pack = registry.load_pack(row.provider)
    target = chosen_target(pack, args.target)
    bound = pack.changes.lattice_hash == changes_hash
    if not bound:
        print(
            f"the {pack.name} pack on disk is not the one run {row.run_id} recorded, "
            "so no migration finding is read against it; rescan to bind a new ProofScope",
            file=sys.stderr,
        )
    print(
        exposure.render(
            ledger=book,
            pack_name=row.provider,
            changes_hash=changes_hash,
            target=target,
            repository=row.target,
            repo_sha=row.repo_sha,
            structural_coverage=as_mapping(row.closure.get("structural_coverage")),
            precise_indexes=as_mapping(row.closure.get("precise_indexes")),
            closure=row.closure,
            findings=exposure.findings(pack=pack, ledger=book, target=target) if bound else None,
            expand=args.expand,
        ),
        end="",
    )
    return EXIT_OK


def _verify(args: argparse.Namespace) -> int:
    pack = registry.load_pack(args.pack)
    request = verification.VerificationRequest(
        repository=Path(args.repo),
        pack=pack,
        base=str(args.base_sha),
        candidate=str(args.candidate_sha),
        from_version=args.from_version,
        to_version=args.to_version,
        obligations_path=_optional_path(args.obligations),
        decisions_path=_optional_path(args.decisions),
        base_capture_path=_optional_path(args.base_capture),
        candidate_capture_path=_optional_path(args.candidate_capture),
        base_capture_manifest_path=_optional_path(args.base_capture_manifest),
        candidate_capture_manifest_path=_optional_path(args.candidate_capture_manifest),
    )
    run = verification.execute(request)
    document = receipt.build(
        evaluation=run.evaluation,
        proof_scope=run.proof_scope,
        provider=pack.name,
        changes_hash=run.changes_hash,
        base_sha=run.base_sha,
        candidate_sha=run.candidate_sha,
        from_version=run.from_version,
        to_version=run.to_version,
        retired_patterns=_retired_patterns(pack, run.from_version, run.to_version),
    )
    state_dir = Path(args.state_dir).resolve()
    run_dir = state_dir / "artifacts" / run.run_id
    payload = document.json_bytes()
    rendered = pr_body.render(document).encode("utf-8")
    json_path = run_dir / "receipt.json"
    markdown_path = run_dir / "receipt.md"
    with Store(state_dir) as store:
        store.start_run(
            run_id=run.run_id,
            proof_scope=run.proof_scope,
            proof_scope_hash=run.proof_scope_hash,
            provider=pack.name,
            verb="verify",
            target=f"{run.base_sha}..{run.candidate_sha}",
            closure_summary={
                "base_sha": run.base_sha,
                "candidate_sha": run.candidate_sha,
                "from_version": run.from_version,
                "to_version": run.to_version,
                "oracle_mode": run.oracle_mode,
                "verdict": document.verdict(),
                "receipt_body_hash": document.body_hash(),
            },
            started_at=datetime.now(UTC).isoformat(),
        )
        for path, data, kind in (
            (json_path, payload, "receipt.json"),
            (markdown_path, rendered, "receipt.md"),
        ):
            digest = write_atomic(path, data)
            store.write_artifact(
                run_id=run.run_id,
                proof_scope_hash=run.proof_scope_hash,
                kind=kind,
                path=str(path),
                sha256=digest,
                size=len(data),
            )
        store.finish_run(run.run_id, datetime.now(UTC).isoformat())
    if args.receipt:
        write_atomic(Path(args.receipt).resolve(), payload)
    print(receipt.render(document))
    print(f"RECEIPT  {json_path}")
    print(f"  body   {document.body_hash()}")
    return _verdict_exit(document.verdict())


def _verdict_exit(verdict: str) -> int:
    if verdict == "VERIFIED_FOR_SCOPE":
        return EXIT_OK
    if verdict == "FAILED":
        return EXIT_FAILED
    return EXIT_UNKNOWN


def _optional_path(value: str | None) -> Path | None:
    return Path(value).resolve() if value else None


def _repository_state_dir(args: argparse.Namespace, root: Path) -> Path:
    offered = Path(args.state_dir)
    return offered.resolve() if offered.is_absolute() else (root / offered).resolve()


def _retired_patterns(
    pack: registry.LoadedPack, from_version: str, to_version: str
) -> tuple[str, ...]:
    changes = verification.change_set(pack, from_version, to_version)
    patterns = {item.subject for item in changes.changes if item.change == "REMOVED"}
    if from_version != to_version:
        patterns.add(from_version)
    return tuple(sorted(patterns))


def _migrate(args: argparse.Namespace) -> int:
    pack = registry.load_pack(args.pack)
    root = Path(args.repo).resolve()
    head = migration.require_head_materialization(root)
    scan = scan_repository(root, pack)
    target = args.target or pack.versions()[-1].id
    result = migration.migrate(
        pack=pack,
        ledger=scan.ledger,
        closure=scan.closure,
        root=root,
        target=target,
        write=not args.dry_run,
    )
    destination = (
        Path(args.obligations).resolve()
        if args.obligations
        else _repository_state_dir(args, root) / OBLIGATIONS_FILENAME
    )
    write_atomic(destination, export_bytes({"obligations": list(result.obligations)}))
    counts = {
        "total": len(result.obligations),
        "discharged": len(result.report.discharged()),
        "human": len(result.open_for_human()),
        "preserved": len(result.preserved()),
    }
    projected = impact.project(scan.ledger, result.obligations)
    real_work, unresolved_version, carried = impact.partition(projected)
    deterministic_items = [item for item in real_work if item["repair_class"] == "DETERMINISTIC"]
    human_items = [item for item in real_work if item["repair_class"] == "HUMAN"]
    print(f"HubbleOps MIGRATE  {pack.name}  ->  {target}")
    print(f"  base                  {head}")
    print(f"  obligations           {counts['total']}")
    print(f"  discharged            {counts['discharged']}")
    print(f"  open for a human      {counts['human']}")
    print(f"  preserved UNKNOWN     {counts['preserved']}")
    print(f"    unresolved version  {len(unresolved_version)}")
    print(f"    carried             {len(carried)}")
    print("  deterministic edits, by finding")
    for line in impact.finding_lines(deterministic_items):
        print(f"  {line}")
    print("  human decisions, by subject")
    for line in impact.finding_lines(human_items):
        print(f"  {line}")
    print(f"  files {'that would change' if args.dry_run else 'changed'}   {len(result.written)}")
    for path in result.written:
        print(f"    {path}")
    for outcome in result.report.undischarged():
        print(f"  {outcome.result}  {outcome.path}  {outcome.reason}")
    print(f"  obligations written   {destination}")
    print("  this command produces a candidate, never a verdict; run `hops verify` to judge it")
    return 0


def _impact(args: argparse.Namespace) -> int:
    pack = registry.load_pack(args.pack)
    root = Path(args.repo).resolve()
    scan = scan_repository(root, pack, force=bool(args.force))
    target = args.target or pack.versions()[-1].id
    report = impact.build(
        pack=pack, ledger=scan.ledger, closure=scan.closure, root=root, target=target
    )
    if args.output:
        write_atomic(Path(args.output).resolve(), report.json_bytes())
    print(impact.render(report), end="")
    return EXIT_OK


def _replay(args: argparse.Namespace) -> int:
    result = replay.verify(Path(args.state_dir), str(args.run_id))
    print(replay.render(result), end="")
    return EXIT_OK


def _guard(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    retired = Path(args.retired).resolve() if args.retired else root / ".hubbleops" / "retired.yml"
    if args.install:
        destination = guard.install_workflow(root, str(args.command))
        print(f"GUARD WORKFLOW  {destination}")
    result = guard.run(root, retired, str(args.rg))
    print(f"HubbleOps GUARD  {result.patterns} cumulative retired patterns")
    if result.matches:
        print(f"  REINTRODUCED  {len(result.matches)}")
        for match in result.matches:
            print(f"    {match}")
        return EXIT_FAILED
    print("  PASS  no retired surface was reintroduced")
    return EXIT_OK


def _prepare_pr(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    receipt = Path(args.receipt)
    pack = registry.load_pack(memory.receipt_provider(receipt))
    offered_body = Path(args.body)
    body = offered_body.resolve() if offered_body.is_absolute() else root / offered_body
    prepared = memory.prepare(
        root,
        receipt,
        body,
        obligations=(
            Path(args.obligations).resolve()
            if args.obligations
            else _repository_state_dir(args, root) / OBLIGATIONS_FILENAME
        ),
        command=str(args.command),
        environment=pack.verification_environment(),
    )
    print("HubbleOps PR MATERIALS PREPARED")
    for path in (
        prepared.body,
        prepared.surface,
        prepared.bindings,
        prepared.decisions,
        prepared.retired,
        prepared.guard_workflow,
        prepared.verification_workflow,
    ):
        print(f"  {path}")
    for pattern, sites in sorted(prepared.deferred.items()):
        print(
            f"  NOT RETIRED  {pattern}  still written at {len(sites)} site(s) the audit proved "
            "are data, not reads; the guard adopts it once the tree is textually clean"
        )
        for site in sites[:DEFERRED_SITE_BUDGET]:
            print(f"    {site}")
    print("  commit these files, then let the exact-SHA verification Action judge the result")
    return EXIT_OK


def _pack_verify(args: argparse.Namespace) -> int:
    pack = registry.load_pack(args.name)
    report = pack.changes.verify()
    print(f"PACK VERIFIED  {pack.name}")
    print(f"  lattice      {report.lattice_hash}")
    for version, digest in report.catalog_hashes.items():
        print(f"  {version:<12} {digest}  facts={report.fact_counts[version]}")
    print(f"  sources      {len(report.source_hashes)}")
    return EXIT_OK


def _scan_summary(result: ScanResult) -> str:
    counts = result.ledger.counts()
    control = ", ".join(result.closure.control_entries) or "none"
    lines = [
        f"HubbleOps scan - {result.pack.name}",
        f"  Repository   {result.target}",
        f"  Commit       {result.closure.repo_sha or 'not a git tree'}",
        f"  ProofScope   {short_scope(result.proof_scope_hash)}",
        f"  Scanner      {result.scanner_version}",
        f"  Run          {result.run_id}",
        "",
        "SOURCE CLOSURE",
        f"  entries      {len(result.closure.entries)}",
    ]
    for name, value in sorted(result.closure.counts().items()):
        lines.append(f"  {name.lower():<12} {value}")
    lines.append(f"  control dirs not walked: {control}")
    lines.extend(
        [
            "",
            "DISCOVERY",
            f"  candidates   {counts['total']}",
            f"  affected     {counts['affected']}",
            f"  not affected {counts['not_affected_with_evidence']}",
            f"  excluded     {counts['excluded_with_evidence']}",
            f"  reference    {counts['provider_reference_data']}",
            f"  unsupported  {counts['unsupported']}",
            f"  unscanned    {counts['unscanned']}",
            f"  accepted risk {counts['human_accepted_risk']}",
            f"  unknown      {counts['unknown']}",
            f"  unexplained  {counts['unexplained']}",
            f"  evidence     {len(result.ledger.evidence)}",
            "",
        ]
    )
    return chr(10).join(lines)


def structural_rules_of(pack: registry.LoadedPack) -> tuple[StructuralRule, ...]:
    rules: list[StructuralRule] = []
    for language in structure.SUPPORTED_LANGUAGES:
        bundle = pack.rules(language)
        for path in bundle.paths:
            payload = path.read_bytes()
            loaded: object = yaml.safe_load(payload)
            if not isinstance(loaded, dict):
                raise ToolingFailed("ast-grep", f"{path} has no stable rule id")
            document = cast(Mapping[str, object], loaded)
            rule_id = document.get("id")
            if not isinstance(rule_id, str):
                raise ToolingFailed("ast-grep", f"{path} has no stable rule id")
            rules.append(
                StructuralRule(
                    id=rule_id,
                    language=language,
                    path=path.resolve(),
                    sha256=hashlib.sha256(payload).hexdigest(),
                )
            )
    return tuple(sorted(rules))


if __name__ == "__main__":
    sys.exit(main())
