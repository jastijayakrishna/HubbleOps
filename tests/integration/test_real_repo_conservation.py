from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from hubbleops.app import registry
from hubbleops.app.cli import scan_repository
from tests.unit.test_real_repo_baseline import FIXTURES, conservation_violations

CHECKOUTS = Path(__file__).resolve().parents[2] / ".hubbleops" / "artifacts" / "phase7-real-repos"
REPOSITORIES = {
    "glna_unknowns_before.json": "google-listings-and-ads",
    "dub_unknowns_before.json": "dub",
}


def _head(repository: Path) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


@pytest.mark.parametrize("name", sorted(REPOSITORIES))
def test_every_frozen_unknown_of_the_ground_truth_survives_a_fresh_scan(
    name: str, google_pack: registry.LoadedPack
) -> None:
    baseline = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    repository = CHECKOUTS / REPOSITORIES[name]
    if _head(repository) != baseline["repo_sha"]:
        pytest.skip(f"{repository} is not checked out at {baseline['repo_sha'][:12]}")
    book = scan_repository(repository, google_pack).ledger
    violations = conservation_violations(baseline, book.candidates)
    assert violations == [], violations
    assert book.counts()["unexplained"] == 0
