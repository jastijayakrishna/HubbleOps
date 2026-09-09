from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from hubbleops.app import registry, verification
from hubbleops.core.canonical import content_id
from hubbleops.core.verification import ObligationView
from hubbleops.verify import authority

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
        shutil.copy2(item, target)
    for relative, content in sorted(edits.items()):
        target = destination / relative
        if content is None:
            if target.is_file():
                target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def build_repository(root: Path, edits: Mapping[str, str | None] | None = None) -> Repository:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "--quiet", "--initial-branch=main")
    git(root, "config", "user.email", "fixture@hubbleops.test")
    git(root, "config", "user.name", "HubbleOps Fixture")
    git(root, "config", "commit.gpgsign", "false")

    _write_tree(root, BASE_TREE, {})
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
) -> list[dict[str, object]]:
    return [
        {
            "id": content_id({"path": path, "line": line, "method": method}),
            "run_id": content_id({"run": "phase5-fixture"}),
            "proof_scope_hash": content_id({"scope": "phase5-fixture"}),
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
    destination: Path, sites: Sequence[tuple[str, int, str]] = DEFAULT_SITES
) -> Path:
    destination.write_text(
        json.dumps(obligation_records(sites), indent=2, sort_keys=True), encoding="utf-8"
    )
    return destination


def verify(
    repository: Repository,
    *,
    obligations_path: Path | None = None,
    decisions_path: Path | None = None,
    base_capture_path: Path | None = None,
    candidate_capture_path: Path | None = None,
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
        )
    )


__all__ = [
    "ADVERSARIAL_ROOT",
    "BASE_TREE",
    "CANDIDATE_TREE",
    "DEFAULT_SITES",
    "FIXTURE_ROOT",
    "Repository",
    "build_repository",
    "git",
    "mock_pack",
    "obligation_records",
    "obligations_for",
    "verify",
    "write_obligations",
]
