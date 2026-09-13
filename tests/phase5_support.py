from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from hubbleops.app import decision, registry, verification
from hubbleops.core.candidate import OPEN_STATUSES
from hubbleops.core.canonical import content_id
from hubbleops.core.verification import ObligationView
from hubbleops.sandbox import DetachedWorktree
from hubbleops.verify import authority

if TYPE_CHECKING:
    from hubbleops.app.cli import ScanResult

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "phase5"
BASE_TREE = FIXTURE_ROOT / "base"
CANDIDATE_TREE = FIXTURE_ROOT / "candidate"
ADVERSARIAL_ROOT = Path(__file__).resolve().parent / "adversarial"


@dataclass(frozen=True, slots=True)
class Repository:
    path: Path
    base_sha: str
    candidate_sha: str


def git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def _write_tree(destination: Path, source: Path, edits: Mapping[str, str | None]) -> None:
    for child in sorted(destination.iterdir()):
        if child.name == ".git":
            continue
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    for item in sorted(source.rglob("*")):
        if not item.is_file():
            continue
        target = destination / item.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, target)
    for relative, content in sorted(edits.items()):
        target = destination / relative
        if content is None:
            if target.is_file():
                target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def build_repository(
    root: Path,
    edits: Mapping[str, str | None] | None = None,
    base_edits: Mapping[str, str | None] | None = None,
) -> Repository:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "--quiet", "--initial-branch=main")
    git(root, "config", "user.email", "fixture@hubbleops.test")
    git(root, "config", "user.name", "HubbleOps Fixture")
    git(root, "config", "commit.gpgsign", "false")

    _write_tree(root, BASE_TREE, base_edits or {})
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "-m", "Base tree before the migration")
    base_sha = git(root, "rev-parse", "HEAD").strip()

    _write_tree(root, CANDIDATE_TREE, edits or {})
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "-m", "Candidate tree after the migration")
    candidate_sha = git(root, "rev-parse", "HEAD").strip()
    return Repository(path=root, base_sha=base_sha, candidate_sha=candidate_sha)


def mock_pack() -> registry.LoadedPack:
    return registry.load_pack("_mock")


DEFAULT_SITES: tuple[tuple[str, int, str], ...] = (
    ("client.py", 1, "absent:campaigns.legacy"),
    ("client.py", 5, "absent:campaigns.legacy"),
    ("client.py", 14, "absent:campaigns.legacy"),
    ("reporting.py", 3, "present:campaigns.name"),
    ("reporting.py", 12, "present:campaigns.name"),
)


def obligation_records(
    sites: Sequence[tuple[str, int, str]] = DEFAULT_SITES,
    *,
    run_id: str | None = None,
    proof_scope_hash: str | None = None,
) -> list[dict[str, object]]:
    return [
        {
            "id": content_id({"path": path, "line": line, "method": method}),
            "run_id": run_id or content_id({"run": "phase5-fixture"}),
            "proof_scope_hash": proof_scope_hash or content_id({"scope": "phase5-fixture"}),
            "provider_change_id": method.partition(":")[2],
            "evidence_ids": [content_id({"path": path, "line": line})],
            "current_state": f"{path}:{line} carries the pre-migration state",
            "required_state": f"{path}:{line} carries the post-migration state",
            "repair_class": "DETERMINISTIC",
            "verification_method": method,
            "status": "OPEN",
        }
        for path, line, method in sites
    ]


def obligations_for(
    sites: Sequence[tuple[str, int, str]] = DEFAULT_SITES,
) -> tuple[ObligationView, ...]:
    return authority.obligations_from(obligation_records(sites))


def write_obligations(
    destination: Path,
    repository: Repository,
    sites: Sequence[tuple[str, int, str]] = DEFAULT_SITES,
) -> Path:
    run_id, scope_hash = base_scan_identity(repository)
    destination.write_text(
        json.dumps(
            obligation_records(sites, run_id=run_id, proof_scope_hash=scope_hash),
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return destination


def base_scan(repository: Repository) -> ScanResult:
    from hubbleops.app.cli import scan_repository

    with tempfile.TemporaryDirectory(prefix="hops-test-base-") as scratch:
        with DetachedWorktree(repository.path, Path(scratch) / "base", repository.base_sha) as base:
            return scan_repository(base, mock_pack())


def base_scan_identity(repository: Repository) -> tuple[str, str]:
    scan = base_scan(repository)
    return scan.run_id, scan.proof_scope_hash


def write_decision(
    destination: Path,
    repository: Repository,
    claim_type: str,
    path: str,
    value: str = "HUMAN_ACCEPTED_RISK",
    decided_by: str = "Reviewer",
) -> tuple[Path, str]:
    scan = base_scan(repository)
    evidence = scan.ledger.evidence_by_id()
    chosen = next(
        item
        for item in scan.ledger.ordered_candidates()
        if item["status"] in OPEN_STATUSES
        and any(
            evidence[eid]["claim_type"] == claim_type and evidence[eid]["path"] == path
            for eid in item["evidence_ids"]
            if eid in evidence
        )
    )
    record = decision.record(scan.ledger, str(chosen["id"]), value, decided_by)
    destination.write_text(json.dumps([record], indent=2, sort_keys=True), encoding="utf-8")
    return destination, str(chosen["id"])


def verify(
    repository: Repository,
    *,
    obligations_path: Path | None = None,
    decisions_path: Path | None = None,
    base_capture_path: Path | None = None,
    candidate_capture_path: Path | None = None,
    base_capture_manifest_path: Path | None = None,
    candidate_capture_manifest_path: Path | None = None,
    from_version: str = "v1",
    to_version: str = "v2",
) -> verification.VerificationRun:
    return verification.execute(
        verification.VerificationRequest(
            repository=repository.path,
            pack=mock_pack(),
            base=repository.base_sha,
            candidate=repository.candidate_sha,
            from_version=from_version,
            to_version=to_version,
            obligations_path=obligations_path,
            decisions_path=decisions_path,
            base_capture_path=base_capture_path,
            candidate_capture_path=candidate_capture_path,
            base_capture_manifest_path=base_capture_manifest_path,
            candidate_capture_manifest_path=candidate_capture_manifest_path,
        )
    )


__all__ = [
    "ADVERSARIAL_ROOT",
    "BASE_TREE",
    "CANDIDATE_TREE",
    "DEFAULT_SITES",
    "FIXTURE_ROOT",
    "Repository",
    "base_scan",
    "base_scan_identity",
    "build_repository",
    "git",
    "mock_pack",
    "obligation_records",
    "obligations_for",
    "verify",
    "write_decision",
    "write_obligations",
]
