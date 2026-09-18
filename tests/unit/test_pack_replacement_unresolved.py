from __future__ import annotations

import json
from typing import Any

import pytest

from hubbleops.core.errors import PackDataError
from hubbleops.packs.google_ads.changes import CHANGES, DATA_ROOT
from hubbleops.packs.google_ads.refresh import migration_table_changes

VERSIONS = ("v19", "v20", "v21", "v22", "v23", "v24", "v25")
SHIPPED_UNBOUND = {"v19": 0, "v20": 0, "v21": 1, "v22": 5, "v23": 4, "v24": 8, "v25": 4}
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


def rows_of(*rows: tuple[str, ...]) -> list[dict[str, Any]]:
    return migration_table_changes(
        "v25",
        (migration_table((HEADER, *rows)),),
        BEFORE_SUBJECTS,
        AFTER_SUBJECTS,
        "https://example.test/release-notes",
        "a" * 64,
    )


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


def test_two_identical_unbound_rows_stay_two_records() -> None:
    records = rows_of(NEITHER_SIDE_BINDS, NEITHER_SIDE_BINDS)
    assert len(records) == 2
    assert len({item["subject"] for item in records}) == 2
    assert len({item["attributes"]["claim"] for item in records}) == 1


@pytest.mark.parametrize("version", VERSIONS)
def test_the_shipped_catalog_carries_one_record_for_every_unbound_row(version: str) -> None:
    assert len(unbound_records(version)) == SHIPPED_UNBOUND[version]


def test_the_shipped_catalogs_still_carry_all_twenty_two_unbound_rows() -> None:
    assert sum(len(unbound_records(version)) for version in VERSIONS) == 22


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
    assert seen == 22


def test_pack_verification_refuses_while_any_row_is_unbound_and_prints_each_one() -> None:
    expected = [item["subject"] for version in VERSIONS for item in sorted_unbound_records(version)]
    assert len(expected) == 22
    with pytest.raises(PackDataError) as refusal:
        CHANGES.verify()
    message = str(refusal.value)
    assert "22" in message
    for subject in expected:
        assert subject in message, subject
    for version in VERSIONS:
        for item in sorted_unbound_records(version):
            assert item["attributes"]["close_with"] in message


def sorted_unbound_records(version: str) -> list[dict[str, Any]]:
    return sorted(unbound_records(version), key=lambda item: str(item["subject"]))
