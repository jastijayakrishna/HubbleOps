from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from hubbleops.core.canonical import blob_hash, canonical_text
from hubbleops.core.errors import PackDataError
from hubbleops.packs.google_ads.changes import CHANGES, DATA_ROOT, GoogleAdsChanges
from hubbleops.packs.google_ads.refresh import MIGRATION_ENTRY_KIND, migration_table_changes

VERSIONS = ("v19", "v20", "v21", "v22", "v23", "v24", "v25")
SHIPPED_UNBOUND = {"v19": 0, "v20": 0, "v21": 1, "v22": 3, "v23": 3, "v24": 8, "v25": 3}
SHIPPED_UNBOUND_TOTAL = 18
UNRESOLVED_KIND = "unresolved_documented_change"
UNRESOLVED_SUBJECT_PREFIX = "docs.unresolved_change."
UNRESOLVED_RESOLUTION = "UNKNOWN_PROVIDER_CONTRACT"
RETIRED_SCALAR = "unresolved_replacement_rows"

HEADER = ("Initial state", "New state", "Change type", "Implementation guidance")
CAMPAIGN_SUITABILITY = "proto_field.resources.campaign.Campaign.video_brand_safety_suitability"
CUSTOMER_SUITABILITY = "proto_field.resources.customer.Customer.video_brand_safety_suitability"
BEFORE_SUBJECTS = (CAMPAIGN_SUITABILITY, CUSTOMER_SUITABILITY)
AFTER_SUBJECTS = (CUSTOMER_SUITABILITY,)

NEITHER_SIDE_BINDS = (
    "views",
    "trueview_views",
    "Removal / replacement",
    "Replace references to views with the new trueview_views field in ReachPlanService .",
)
REPLACEMENT_DOES_NOT_BIND = (
    "Campaign.video_brand_safety_suitability",
    "None",
    "Removal",
    "Campaign-level suitability control is removed.",
)
SUBJECT_DOES_NOT_BIND = (
    "BudgetPerDayMinimumErrorDetails.minimum_bugdet_amount_micros",
    "Customer.video_brand_safety_suitability",
    "Rename / spelling fix",
    "Rename references to use the corrected field name.",
)
BOTH_SIDES_BIND_TO_ONE_SUBJECT = (
    "Customer.video_brand_safety_suitability",
    "Customer.video_brand_safety_suitability",
    "Rename",
    "No change is required.",
)
BOUND_ROW = (
    "Campaign.video_brand_safety_suitability",
    "Customer.video_brand_safety_suitability",
    "Removal / replacement",
    "Use Customer.video_brand_safety_suitability instead.",
)
BEHAVIOURAL_ROW = (
    "Campaign.video_brand_safety_suitability",
    "Required field",
    "Behavioral shift",
    "Use Customer.video_brand_safety_suitability instead.",
)


def migration_table(rows: tuple[tuple[str, ...], ...]) -> str:
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<h2>v25 major and minor versions</h2><table>{body}</table>"


def records_of(*rows: tuple[str, ...]) -> list[dict[str, Any]]:
    return migration_table_changes(
        "v25",
        (migration_table((HEADER, *rows)),),
        BEFORE_SUBJECTS,
        AFTER_SUBJECTS,
        "https://example.test/release-notes",
        "a" * 64,
    )


def rows_of(*rows: tuple[str, ...]) -> list[dict[str, Any]]:
    return [item for item in records_of(*rows) if item["kind"] != MIGRATION_ENTRY_KIND]


def catalog_records(version: str) -> list[dict[str, Any]]:
    path = DATA_ROOT / f"catalog_{version}.jsonl"
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def unbound_records(version: str) -> list[dict[str, Any]]:
    return [item for item in catalog_records(version) if item["kind"] == UNRESOLVED_KIND]


def test_the_compiler_returns_records_only_so_a_failure_cannot_become_a_count() -> None:
    records = rows_of(BOUND_ROW, NEITHER_SIDE_BINDS)
    assert isinstance(records, list)
    assert all(isinstance(item, dict) for item in records)
    assert [item["kind"] for item in records] == ["documented_change", UNRESOLVED_KIND]


def test_a_documented_rename_that_cannot_be_bound_never_collapses_into_a_scalar() -> None:
    unbound = (NEITHER_SIDE_BINDS, REPLACEMENT_DOES_NOT_BIND, SUBJECT_DOES_NOT_BIND)
    records = rows_of(*unbound)
    assert [item["kind"] for item in records] == [UNRESOLVED_KIND] * len(unbound)
    assert len({item["subject"] for item in records}) == len(unbound)
    for item, cells in zip(records, unbound, strict=True):
        assert item["subject"].startswith(UNRESOLVED_SUBJECT_PREFIX)
        assert item["attributes"]["claim"] == " ".join(cells)
        assert item["attributes"]["stated_subject"] == cells[0]
        assert item["attributes"]["stated_replacement"] == cells[1]
        assert (item["attributes"]["from_version"], item["attributes"]["to_version"]) == (
            "v24",
            "v25",
        )


@pytest.mark.parametrize(
    ("cells", "conflict", "bound"),
    [
        (
            NEITHER_SIDE_BINDS,
            "neither side of this documented replacement binds to a single catalog subject",
            {},
        ),
        (
            REPLACEMENT_DOES_NOT_BIND,
            "the replacement side of this documented replacement binds to no single catalog "
            "subject",
            {"change_subject": CAMPAIGN_SUITABILITY},
        ),
        (
            SUBJECT_DOES_NOT_BIND,
            "the replaced side of this documented replacement binds to no single catalog subject",
            {"replacement": CUSTOMER_SUITABILITY},
        ),
        (
            BOTH_SIDES_BIND_TO_ONE_SUBJECT,
            "both sides of this documented replacement bind to the same catalog subject",
            {"change_subject": CUSTOMER_SUITABILITY, "replacement": CUSTOMER_SUITABILITY},
        ),
    ],
)
def test_each_binding_failure_states_its_own_reason_and_the_evidence_that_closes_it(
    cells: tuple[str, ...], conflict: str, bound: dict[str, str]
) -> None:
    records = rows_of(cells)
    assert len(records) == 1
    attributes = records[0]["attributes"]
    assert attributes["conflict"] == conflict
    assert "catalog_v" in attributes["close_with"]
    assert "human decision" in attributes["close_with"]
    for key, value in bound.items():
        assert attributes[key] == value
    for key in ("change_subject", "replacement"):
        assert (key in attributes) == (key in bound)


def test_a_row_the_compiler_can_bind_is_still_a_documented_change() -> None:
    records = rows_of(BOUND_ROW)
    assert [item["kind"] for item in records] == ["documented_change"]
    assert records[0]["attributes"]["change_subject"] == CAMPAIGN_SUITABILITY
    assert records[0]["attributes"]["replacement"] == CUSTOMER_SUITABILITY
    assert "conflict" not in records[0]["attributes"]


def test_a_behavioural_row_is_neither_a_replacement_nor_an_unbound_row() -> None:
    assert rows_of(BEHAVIOURAL_ROW) == []
    entries = records_of(BEHAVIOURAL_ROW)
    assert [item["kind"] for item in entries] == [MIGRATION_ENTRY_KIND]
    assert entries[0]["attributes"]["states_replacement"] is False


def test_two_identical_unbound_rows_stay_two_records() -> None:
    records = rows_of(NEITHER_SIDE_BINDS, NEITHER_SIDE_BINDS)
    assert len(records) == 2
    assert len({item["subject"] for item in records}) == 2
    assert len({item["attributes"]["claim"] for item in records}) == 1


@pytest.mark.parametrize("version", VERSIONS)
def test_the_shipped_catalog_carries_one_record_for_every_unbound_row(version: str) -> None:
    assert len(unbound_records(version)) == SHIPPED_UNBOUND[version]


def test_the_shipped_catalogs_still_carry_every_unbound_row() -> None:
    assert sum(len(unbound_records(version)) for version in VERSIONS) == SHIPPED_UNBOUND_TOTAL


@pytest.mark.parametrize("version", VERSIONS)
def test_no_shipped_record_reports_an_unbound_row_as_a_number(version: str) -> None:
    records = catalog_records(version)
    assert [item for item in records if item["kind"] == "release_notes"]
    assert [item for item in records if RETIRED_SCALAR in item["attributes"]] == []


def test_every_unbound_row_is_an_unknown_with_a_closing_instruction() -> None:
    seen = 0
    for version in VERSIONS:
        for item in unbound_records(version):
            seen += 1
            assert item["resolution"] == UNRESOLVED_RESOLUTION
            assert item["confidence"] == "DOCUMENTED"
            attributes = item["attributes"]
            assert attributes["claim"].strip()
            assert attributes["conflict"].strip()
            assert attributes["close_with"].strip()
            assert "catalog_v" in attributes["close_with"]
            assert attributes["to_version"] == version
            assert attributes["from_version"] == f"v{int(version[1:]) - 1}"
            assert attributes["stated_subject"] in attributes["claim"]
            assert attributes["stated_replacement"] in attributes["claim"]
    assert seen == SHIPPED_UNBOUND_TOTAL


def test_pack_verification_passes_while_every_unbound_row_is_recorded() -> None:
    expected = [item["subject"] for version in VERSIONS for item in sorted_unbound_records(version)]
    assert len(expected) == SHIPPED_UNBOUND_TOTAL
    report = CHANGES.verify()
    assert sorted(report.catalog_hashes) == sorted(VERSIONS)
    recorded = CHANGES.unresolved_replacements()
    assert sorted(str(item["subject"]) for item in recorded) == sorted(expected)
    for item in recorded:
        assert str(item["attributes"]["close_with"]).strip()


def test_pack_verification_refuses_when_an_unbound_row_goes_silent(tmp_path: Path) -> None:
    data = tmp_path / "data"
    shutil.copytree(DATA_ROOT, data)
    dropped = drop_one_unbound_row(data, "v22")
    with pytest.raises(PackDataError) as refusal:
        GoogleAdsChanges(data).verify()
    message = str(refusal.value)
    assert "do not reconcile" in message
    assert "unresolved_pairs" in message
    assert dropped not in (data / "sources" / "normalized" / "docs_v22.jsonl").read_text("utf-8")


def drop_one_unbound_row(data: Path, version: str) -> str:
    normalized = data / "sources" / "normalized" / f"docs_{version}.jsonl"
    lines = normalized.read_text("utf-8").splitlines()
    kept = [line for line in lines if UNRESOLVED_SUBJECT_PREFIX not in line]
    dropped = next(line for line in lines if UNRESOLVED_SUBJECT_PREFIX in line)
    payload = "".join(f"{line}\n" for line in kept).encode()
    normalized.write_bytes(payload)
    manifest_path = data / "sources" / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    for entry in manifest["sources"]:
        if entry["version"] == version and entry["family"] == "docs":
            entry["sha256"] = blob_hash(payload)
            entry["expected_records"] = len(kept)
    manifest_path.write_text(canonical_text(manifest), encoding="utf-8")
    rebuilt = GoogleAdsChanges(data)
    rebuilt.build(data)
    return dropped


def sorted_unbound_records(version: str) -> list[dict[str, Any]]:
    return sorted(unbound_records(version), key=lambda item: str(item["subject"]))
