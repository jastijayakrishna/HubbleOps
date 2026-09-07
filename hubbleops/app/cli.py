from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml

from hubbleops import __version__
from hubbleops.app import capture, exposure, promotion, registry
from hubbleops.closure import source_closure
from hubbleops.core.canonical import canonical_bytes, content_id, export_bytes
from hubbleops.core.errors import HubbleOpsError, ToolingFailed, ToolingMissing, ToolingTimeout
from hubbleops.core.observer import ObserverContext, StructuralRule
from hubbleops.core.proof_scope import (
    make_proof_scope,
    proof_scope_hash,
    run_id_for,
    scanner_fingerprint,
    short_scope,
)
from hubbleops.core.records import as_mapping
from hubbleops.graph.imports import AstGrep
from hubbleops.observe import deps, ledger, structure, telemetry, text
from hubbleops.observe.dynamic import runner as dynamic
from hubbleops.store.artifacts import write_atomic
from hubbleops.store.sqlite import Store

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
OBSERVATION_SOURCES = tuple(sorted(PACKAGE_ROOT.rglob("*.py")))

DEFAULT_STATE_DIR = ".hubbleops"
EXIT_OK = 0
EXIT_TOOLING_MISSING = 3
EXIT_FAILED = 4
EXIT_UNKNOWN = 5


def main(argv: list[str] | None = None) -> int:
    _use_utf8(sys.stdout)
    _use_utf8(sys.stderr)
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
    show.add_argument("--expand", action="store_true", help="list explained-away candidates too")
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

    pack = subparsers.add_parser("pack", help="inspect and verify provider packs")
    pack_commands = pack.add_subparsers(dest="pack_verb", required=True)
    pack_verify = pack_commands.add_parser("verify", help="verify an offline provider pack")
    pack_verify.add_argument("name", help=f"one of: {', '.join(registry.available_packs())}")
    pack_verify.set_defaults(handler=_pack_verify)

    return parser


@dataclass(frozen=True, slots=True)
class ScanResult:
    target: Path
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
            "control_directories": list(self.closure.control_directories),
            "pack": {
                "changes_hash": self.pack.changes.lattice_hash,
                "target": self.pack.latest_compatible(self.resolution.dependencies),
            },
            "structural_coverage": self.structural_coverage.to_mapping(),
        }


@dataclass(frozen=True, slots=True)
class CaptureResult:
    scan: ScanResult
    attempt: capture.CaptureAttempt
    inputs: tuple[capture.ProductionInput, ...]


def scan_repository(
    target: Path,
    pack: registry.LoadedPack,
    *,
    force: bool = False,
    ast_grep_executable: str = "ast-grep",
) -> ScanResult:
    resolved = target.resolve()
    closure = source_closure.build(resolved)
    resolution = deps.resolve(closure)
    with tempfile.TemporaryDirectory(prefix="hops-promoted-rules-") as temporary:
        rules = (*_structural_rules(pack), *promotion.materialize_active(resolved, Path(temporary)))
        try:
            ast_grep_version = AstGrep(ast_grep_executable).version()
        except (ToolingMissing, ToolingFailed, ToolingTimeout):
            if not force:
                raise
            ast_grep_version = "unavailable-forced"
        scanner_version = (
            f"hubbleops={__version__}"
            f";pipeline={scanner_fingerprint(OBSERVATION_SOURCES)}"
            f";rg={text.ripgrep_version()}"
            f";ast-grep={ast_grep_version}"
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
        run_id = run_id_for(
            scope_hash=scope_hash, provider=pack.name, verb="scan", target=str(resolved)
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
        )
        records = [
            *text.scan(closure, ctx),
            *deps.scan(closure, ctx, resolution),
            *structure.scan(closure, ctx),
        ]
        book = ledger.build(
            provider=pack.name,
            run_id=run_id,
            proof_scope_hash=scope_hash,
            evidence=records,
            closure=closure,
        )
        return ScanResult(
            target=resolved,
            pack=pack,
            closure=closure,
            resolution=resolution,
            proof_scope=scope,
            proof_scope_hash=scope_hash,
            run_id=run_id,
            scanner_version=scanner_version,
            structural_coverage=structure.coverage(closure, rules, records),
            ledger=book,
        )


def _scan(args: argparse.Namespace) -> int:
    result = scan_repository(Path(args.repo), registry.load_pack(args.pack), force=bool(args.force))
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
            target=str(result.target),
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
    print(f"LEDGER  {artifact_path}")
    print(f"  sha256  {digest}")
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
            target=str(target),
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
        rules = (*_structural_rules(pack), *promotion.materialize_active(target, Path(temporary)))
        try:
            ast_grep_version = AstGrep(ast_grep_executable).version()
        except (ToolingMissing, ToolingFailed, ToolingTimeout):
            if not force:
                raise
            ast_grep_version = "unavailable-forced"
        scanner_version = (
            f"hubbleops={__version__}"
            f";pipeline={scanner_fingerprint(OBSERVATION_SOURCES)}"
            f";rg={text.ripgrep_version()}"
            f";ast-grep={ast_grep_version}"
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
            scope_hash=scope_hash, provider=pack.name, verb="capture", target=str(target)
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
        )
        records = [
            *text.scan(closure, ctx),
            *deps.scan(closure, ctx, resolution),
            *structure.scan(closure, ctx),
        ]
        records.extend(dynamic.events_to_evidence(attempt.batch, ctx, target))
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
                    ): telemetry.candidate_ids(
                        observed_book,
                        telemetry.ProductionTuple(
                            str(event["service"]), str(event["method"]), str(event["version"])
                        ),
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
            target,
            pack,
            closure,
            resolution,
            scope,
            scope_hash,
            run_id,
            scanner_version,
            structure.coverage(closure, rules, records),
            book,
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


def _exposure(args: argparse.Namespace) -> int:
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
        target = str(pack_record.get("target", "UNKNOWN (SDK compatibility unresolved)"))
    print(
        exposure.render(
            ledger=book,
            pack_name=row.provider,
            changes_hash=changes_hash,
            target=target,
            repository=row.target,
            repo_sha=row.repo_sha,
            structural_coverage=as_mapping(row.closure.get("structural_coverage")),
            expand_not_affected=args.expand,
        ),
        end="",
    )
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
    control = ", ".join(result.closure.control_directories) or "none"
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
            f"  unknown      {counts['unknown']}",
            f"  unexplained  {counts['unexplained']}",
            f"  evidence     {len(result.ledger.evidence)}",
            "",
        ]
    )
    return chr(10).join(lines)


def _structural_rules(pack: registry.LoadedPack) -> tuple[StructuralRule, ...]:
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
