from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from hubbleops.app import exposure, registry
from hubbleops.app.cli import scan_repository
from hubbleops.core.candidate import OPEN_STATUSES
from tests.support import fixture_repos


def observed(repo: Path, pack: registry.LoadedPack) -> dict[str, Any]:
    book = scan_repository(repo, pack).ledger
    return {
        "fixture": repo.parent.name,
        "pack": pack.name,
        "counts": book.counts(),
        "candidates": [
            {
                "status": candidate["status"],
                "claim_type": book.location_of(candidate).claim_type,
                "location": book.location_of(candidate).display(),
                "provider_subject": book.location_of(candidate).provider_subject,
            }
            for candidate in book.ordered_candidates()
        ],
    }


def expected(fixture: Path) -> dict[str, Any]:
    return json.loads((fixture / "expected_candidates.json").read_text(encoding="utf-8"))


def test_there_are_at_least_six_fixture_repositories() -> None:
    assert len(fixture_repos()) >= 6


@pytest.mark.parametrize("fixture", fixture_repos(), ids=lambda path: path.name)
def test_each_fixture_has_recorded_expectations(fixture: Path) -> None:
    assert (fixture / "expected_candidates.json").is_file()


@pytest.mark.parametrize("fixture", fixture_repos(), ids=lambda path: path.name)
def test_the_ledger_matches_the_recorded_expectations(
    fixture: Path, google_pack: registry.LoadedPack
) -> None:
    assert observed(fixture / "repo", google_pack) == expected(fixture)


@pytest.mark.parametrize("fixture", fixture_repos(), ids=lambda path: path.name)
def test_no_fixture_leaves_an_unexplained_candidate(
    fixture: Path, google_pack: registry.LoadedPack
) -> None:
    book = scan_repository(fixture / "repo", google_pack).ledger
    assert book.counts()["unexplained"] == 0
    assert book.unexplained() == 0


def _site_total(rendered: str) -> int:
    total = 0
    inside = False
    for line in rendered.splitlines():
        if line.startswith("UNKNOWN   "):
            inside = True
            continue
        if inside and line.startswith("─"):
            break
        if inside:
            match = re.match(r"\s+(\d+) sites?(\s|$)", line)
            if match:
                total += int(match.group(1))
    return total


@pytest.mark.parametrize("fixture", fixture_repos(), ids=lambda path: path.name)
def test_every_open_candidate_prints_a_closing_instruction(
    fixture: Path, google_pack: registry.LoadedPack
) -> None:
    book = scan_repository(fixture / "repo", google_pack).ledger
    rendered = exposure.render(
        ledger=book,
        pack_name=google_pack.name,
        changes_hash=google_pack.changes.lattice_hash,
        target="UNKNOWN (SDK compatibility unresolved)",
        repository=str(fixture),
        repo_sha=None,
    )
    for candidate in book.candidates:
        if candidate["status"] in OPEN_STATUSES:
            assert candidate["close_with"]
    for candidate in book.by_status("UNKNOWN"):
        instruction = str(candidate["close_with"])
        assert instruction.split("\n")[0][:60] in " ".join(rendered.split())
    expanded = exposure.render(
        ledger=book,
        pack_name=google_pack.name,
        changes_hash=google_pack.changes.lattice_hash,
        target="UNKNOWN (SDK compatibility unresolved)",
        repository=str(fixture),
        repo_sha=None,
        expand=True,
    )
    assert _site_total(expanded) == len(book.by_status("UNKNOWN"))
    assert "Unexplained             0" in rendered


def test_a_pinned_dependency_and_an_explicit_literal_are_both_affected(
    google_pack: registry.LoadedPack,
) -> None:
    book = scan_repository(Path("tests/fixtures/phase1/python_pinned_v22/repo"), google_pack).ledger
    affected = {
        (book.location_of(candidate).claim_type, book.location_of(candidate).provider_subject)
        for candidate in book.by_status("AFFECTED")
    }
    assert ("sdk_installed", "google-ads") in affected
    assert ("call_version", "v22") in affected


def test_a_runtime_version_key_is_unknown_not_affected(google_pack: registry.LoadedPack) -> None:
    book = scan_repository(
        Path("tests/fixtures/phase1/js_dynamic_version/repo"), google_pack
    ).ledger
    unknown = {
        book.location_of(candidate).provider_subject for candidate in book.by_status("UNKNOWN")
    }
    assert "ADS_API_VERSION" in unknown
    assert not book.by_status("AFFECTED") or all(
        book.location_of(candidate).claim_type != "call_version"
        for candidate in book.by_status("AFFECTED")
    )


def test_a_vendored_copy_is_excluded_while_its_range_stays_unknown(
    google_pack: registry.LoadedPack,
) -> None:
    book = scan_repository(Path("tests/fixtures/phase1/vendored_sdk/repo"), google_pack).ledger
    excluded = book.by_status("EXCLUDED_WITH_EVIDENCE")
    assert excluded
    assert all("vendor/" in book.location_of(candidate).display() for candidate in excluded)
    unknown_claims = {
        book.location_of(candidate).claim_type for candidate in book.by_status("UNKNOWN")
    }
    assert "sdk_installed" in unknown_claims


def test_an_unparsable_manifest_and_a_binary_blob_are_both_accounted_for(
    google_pack: registry.LoadedPack,
) -> None:
    book = scan_repository(Path("tests/fixtures/phase1/unparsable_file/repo"), google_pack).ledger
    unknown = {book.location_of(candidate).claim_type for candidate in book.by_status("UNKNOWN")}
    unscanned = {
        book.location_of(candidate).claim_type for candidate in book.by_status("UNSCANNED")
    }
    assert "dependency_state" in unknown
    assert "file_unscanned" in unscanned


def test_a_parsed_lock_without_the_surface_package_proves_absence(
    google_pack: registry.LoadedPack,
) -> None:
    book = scan_repository(
        Path("tests/fixtures/phase1/monorepo_workspace/repo"), google_pack
    ).ledger
    not_affected = book.by_status("NOT_AFFECTED_WITH_EVIDENCE")
    assert not_affected
    absence = [
        candidate
        for candidate in not_affected
        if book.location_of(candidate).claim_type == "sdk_installed"
    ]
    assert absence
    assert all("lock" in candidate["reason"] for candidate in absence)
    assert all(
        "lock" not in candidate["reason"] for candidate in not_affected if candidate not in absence
    )
