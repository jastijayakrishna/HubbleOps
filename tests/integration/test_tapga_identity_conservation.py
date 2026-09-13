from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hubbleops.app import registry
from hubbleops.app.cli import scan_repository
from hubbleops.core.candidate import STATUSES

CHECKOUT = Path(__file__).resolve().parents[2] / ".hubbleops" / "artifacts" / "e2e" / "tapga"
BASELINE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "real_repo" / "tapga_identities_before.json"
)


def _head(repository: Path) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


@pytest.fixture(scope="module")
def baseline() -> dict[str, Any]:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rescanned(baseline: dict[str, Any], google_pack: registry.LoadedPack) -> dict[str, str]:
    if _head(CHECKOUT) != baseline["repo_sha"]:
        pytest.skip(f"{CHECKOUT} is not checked out at {baseline['repo_sha'][:12]}")
    book = scan_repository(CHECKOUT, google_pack).ledger
    return {str(item["id"]): str(item["status"]) for item in book.candidates}


def test_the_baseline_records_every_candidate_the_engine_raised(baseline: dict[str, Any]) -> None:
    identities = baseline["identities"]
    assert len(identities) == baseline["ledger_counts"]["total"]
    assert len({entry["candidate_id"] for entry in identities}) == len(identities)
    assert baseline["ledger_counts"]["unexplained"] == 0
    assert all(entry["status"] in STATUSES for entry in identities)


def test_no_recorded_identity_disappears_from_a_fresh_scan(
    baseline: dict[str, Any], rescanned: dict[str, str]
) -> None:
    missing = [
        (entry["candidate_id"][:12], entry["claim_type"], entry["location"])
        for entry in baseline["identities"]
        if entry["candidate_id"] not in rescanned
    ]
    assert missing == [], (
        "a candidate never disappears; it gets a status. An identity that legitimately moves "
        "because its claim changed is recorded as a retirement or a rekey (P-024) in this "
        f"baseline, never dropped: {missing[:10]}"
    )


def test_a_fresh_scan_still_explains_every_candidate_it_raises(
    baseline: dict[str, Any], rescanned: dict[str, str], google_pack: registry.LoadedPack
) -> None:
    assert rescanned
    assert all(status in STATUSES for status in rescanned.values())
    book = scan_repository(CHECKOUT, google_pack).ledger
    assert book.counts()["unexplained"] == 0
