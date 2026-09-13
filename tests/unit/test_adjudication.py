from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hubbleops.app.cli import scan_repository
from hubbleops.app.registry import load_pack

FIXTURE = Path("tests/fixtures/phase6/adjudication/repo")
LATTICE_REASON = "no migration inside this pack's lattice changes it"


@pytest.fixture(scope="module")
def scanned() -> Any:
    return scan_repository(FIXTURE, load_pack("google_ads"))


def candidate_at(result: Any, path: str, line: int, subject: str) -> dict[str, Any]:
    for item in result.ledger.ordered_candidates():
        records = [
            record for record in result.ledger.evidence if record["id"] in item["evidence_ids"]
        ]
        if any(
            str(record["path"]) == path
            and record["line_start"] == line
            and record["provider_subject"] == subject
            and record["claim_type"] == "surface_reference"
            for record in records
        ):
            return item
    raise AssertionError(f"no surface_reference candidate at {path}:{line} for {subject!r}")


def adjudication(result: Any, path: str, line: int, subject: str) -> dict[str, Any] | None:
    return next(
        (
            record
            for record in result.ledger.evidence
            if record["observer"] == "structure"
            and record["claim_type"] == "surface_reference"
            and str(record["path"]) == path
            and record["line_start"] == line
            and record["provider_subject"] == subject
        ),
        None,
    )


def test_a_provider_name_inside_a_comment_is_explained_by_the_parse_not_dismissed(
    scanned: Any,
) -> None:
    item = candidate_at(scanned, "src/DocumentedOnly.php", 5, "GoogleAdsClient")
    assert item["status"] == "NOT_AFFECTED_WITH_EVIDENCE"
    assert "comment node" in item["reason"]
    record = adjudication(scanned, "src/DocumentedOnly.php", 5, "GoogleAdsClient")
    assert record is not None
    assert record["value"]["node_kind"] == "comment"
    assert record["confidence"] == "PROVEN"


def test_an_identifier_bound_to_a_versioned_import_is_affected_at_that_version(
    scanned: Any,
) -> None:
    item = candidate_at(scanned, "src/BoundImport.php", 8, "SearchGoogleAdsRequest")
    assert item["status"] == "AFFECTED"
    record = adjudication(scanned, "src/BoundImport.php", 8, "SearchGoogleAdsRequest")
    assert record is not None
    assert record["value"]["binding_versions"] == ["v22"]
    assert record["value"]["binding"].endswith("SearchGoogleAdsRequest")


def test_the_construction_site_and_its_import_reach_the_same_version(scanned: Any) -> None:
    declaration = adjudication(scanned, "src/BoundImport.php", 4, "SearchGoogleAdsRequest")
    construction = adjudication(scanned, "src/BoundImport.php", 8, "SearchGoogleAdsRequest")
    assert declaration is not None
    assert construction is not None
    assert declaration["value"]["binding_versions"] == construction["value"]["binding_versions"]


def test_a_first_party_symbol_that_shadows_a_provider_name_is_never_called_safe(
    scanned: Any,
) -> None:
    item = candidate_at(scanned, "src/FirstPartyWrapper.php", 9, "GoogleAdsClient")
    assert item["status"] == "UNKNOWN"
    assert "first-party" in item["reason"]
    assert item["close_with"] is not None
    assert "GoogleAdsClient" in item["close_with"]
    record = adjudication(scanned, "src/FirstPartyWrapper.php", 9, "GoogleAdsClient")
    assert record is not None
    assert record["value"]["first_party_definition"] == "GoogleAdsClient"
    assert record["value"]["binding_versions"] == []


def test_a_line_holding_the_name_in_code_and_in_a_comment_is_not_adjudicated(
    scanned: Any,
) -> None:
    item = candidate_at(scanned, "src/MixedLine.php", 6, "GoogleAdsService")
    assert adjudication(scanned, "src/MixedLine.php", 6, "GoogleAdsService") is None
    assert item["status"] == "NOT_AFFECTED_WITH_EVIDENCE"
    assert LATTICE_REASON in item["reason"]


def test_a_file_the_structural_layer_cannot_parse_is_never_explained_by_a_parse(
    scanned: Any,
) -> None:
    unscanned = [
        item
        for item in scanned.ledger.by_status("UNSCANNED")
        if "src/Broken.php" in str(item["reason"])
    ]
    assert unscanned, "a file whose parse fails has to be a candidate of its own"
    item = candidate_at(scanned, "src/Broken.php", 2, "google-ads")
    assert adjudication(scanned, "src/Broken.php", 2, "google-ads") is None
    assert item["status"] == "NOT_AFFECTED_WITH_EVIDENCE"
    assert LATTICE_REASON in item["reason"]


def test_a_stylesheet_selector_is_explained_by_its_role_not_by_a_parse(scanned: Any) -> None:
    item = candidate_at(scanned, "assets/widget.scss", 1, "google-ads")
    assert item["status"] == "NOT_AFFECTED_WITH_EVIDENCE"
    assert "STYLESHEET" in item["reason"]
    assert adjudication(scanned, "assets/widget.scss", 1, "google-ads") is None


def test_adjudication_never_removes_a_candidate(scanned: Any) -> None:
    counts = scanned.ledger.counts()
    assert counts["unexplained"] == 0
    assert counts["total"] == sum(
        counts[name]
        for name in (
            "affected",
            "not_affected_with_evidence",
            "unknown",
            "human_required",
            "excluded_with_evidence",
            "provider_reference_data",
            "unsupported",
            "unscanned",
            "human_accepted_risk",
        )
    )


def test_every_adjudicated_candidate_carries_the_structural_record_that_moved_it(
    scanned: Any,
) -> None:
    for item in scanned.ledger.ordered_candidates():
        records = [
            record for record in scanned.ledger.evidence if record["id"] in item["evidence_ids"]
        ]
        surface = [record for record in records if record["claim_type"] == "surface_reference"]
        if not surface or item["status"] == "UNKNOWN":
            continue
        moved_by_parse = any(record["observer"] == "structure" for record in surface)
        moved_by_role = "source-closure role" in str(item["reason"])
        moved_by_lattice = LATTICE_REASON in str(item["reason"])
        assert moved_by_parse or moved_by_role or moved_by_lattice
