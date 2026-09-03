from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hubbleops import __version__
from hubbleops.app import exposure, registry
from hubbleops.closure import source_closure
from hubbleops.core.canonical import export_bytes
from hubbleops.core.errors import HubbleOpsError, ToolingMissing, ToolingTimeout
from hubbleops.core.observer import ObserverContext
from hubbleops.core.proof_scope import (
    make_proof_scope,
    proof_scope_hash,
    run_id_for,
    scanner_fingerprint,
    short_scope,
)
from hubbleops.observe import deps, ledger, resolver, text
from hubbleops.store.artifacts import write_atomic
from hubbleops.store.sqlite import Store

OBSERVATION_SOURCES = (
    Path(__file__),
    Path(source_closure.__file__),
    Path(deps.__file__),
    Path(ledger.__file__),
    Path(resolver.__file__),
    Path(text.__file__),
)

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
    scan.set_defaults(handler=_scan)

    show = subparsers.add_parser("exposure", help="print the exposure map for a run")
    show.add_argument("--run", default=None, help="run id; defaults to the most recent run")
    show.add_argument("--pack", default=None, help="restrict the default run lookup to this pack")
    show.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    show.add_argument("--expand", action="store_true", help="list explained-away candidates too")
    show.set_defaults(handler=_exposure)

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
    ledger: ledger.Ledger

    def closure_summary(self) -> dict[str, Any]:
        return {
            "root": str(self.target),
            "entries": len(self.closure.entries),
            "counts": self.closure.counts(),
            "control_directories": list(self.closure.control_directories),
        }


def scan_repository(target: Path, pack: registry.LoadedPack) -> ScanResult:
    resolved = target.resolve()
    closure = source_closure.build(resolved)
    resolution = deps.resolve(closure)
    scanner_version = (
        f"hubbleops={__version__}"
        f";pipeline={scanner_fingerprint(OBSERVATION_SOURCES)}"
        f";rg={text.ripgrep_version()}"
    )
    scope = make_proof_scope(
        repo_sha=closure.repo_sha,
        tree_hash=closure.tree_hash(),
        dependency_resolution_hash=resolution.resolution_hash(),
        provider_contract_hash=pack.surface.surface_hash(),
        scanner_version=scanner_version,
    )
    scope_hash = proof_scope_hash(scope)
    run_id = run_id_for(
        scope_hash=scope_hash, provider=pack.name, verb="scan", target=resolved.name
    )
    ctx = ObserverContext(
        provider=pack.name,
        run_id=run_id,
        proof_scope_hash=scope_hash,
        repo_sha=closure.repo_sha,
        dependency_context_hash=resolution.resolution_hash(),
        surface=pack.surface,
    )
    records = [
        *text.scan(closure, ctx),
        *deps.scan(closure, ctx, resolution),
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
        ledger=book,
    )


def _scan(args: argparse.Namespace) -> int:
    result = scan_repository(Path(args.repo), registry.load_pack(args.pack))
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
        recorded_surface = row.proof_scope["provider_contract_hash"]
    print(
        exposure.render(
            ledger=book,
            pack_name=row.provider,
            surface_hash=recorded_surface,
            target=row.target,
            repo_sha=row.repo_sha,
            expand_not_affected=args.expand,
        ),
        end="",
    )
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


if __name__ == "__main__":
    sys.exit(main())
