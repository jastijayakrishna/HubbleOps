from __future__ import annotations

import json
from typing import Any

import pytest

from hubbleops.packs.google_ads.changes import CHANGES, DATA_ROOT, UNRESOLVED_CHANGE_KIND
from hubbleops.packs.google_ads.refresh import MIGRATION_ENTRY_KIND, migration_table_changes

NAMED_FIELDS = ("customer.id", "metrics.clicks", "segments.date")
VERSIONS = ("v19", "v20", "v21", "v22", "v23", "v24", "v25")
CAMPAIGN_SUITABILITY = "proto_field.resources.campaign.Campaign.video_brand_safety_suitability"
CUSTOMER_SUITABILITY = "proto_field.resources.customer.Customer.video_brand_safety_suitability"
CREATOR_INSIGHTS = (
    "proto_field.services.content_creator_insights_service.GenerateCreatorInsightsRequest"
)
SEARCH_BRAND = f"{CREATOR_INSIGHTS}.search_brand"
SEARCH_TOPICS = f"{CREATOR_INSIGHTS}.search_topics"
FORECAST = "proto_field.services.keyword_plan_idea_service"
MIGRATION_ROWS = (
    ("Initial state", "New state", "Change type", "Implementation guidance"),
    (
        "Campaign.video_brand_safety_suitability",
        "None",
        "Removal",
        "Campaign-level suitability control is removed. Brand safety suitability is still "
        "available on the customer level. Use Customer.video_brand_safety_suitability instead.",
    ),
    (
        "The search_brand field in GenerateCreatorInsightsRequest",
        "None",
        "Removal",
        "Use search_topics instead.",
    ),
    (
        "geo_modifiers and biddable_keywords in "
        "KeywordPlanIdeaService.GenerateKeywordForecastMetrics",
        "geo_target_constants and keywords",
        "Rename / removal",
        "Replaced by CampaignToForecast.geo_target_constants[] and ForecastAdGroup.keywords[] .",
    ),
)
BEFORE_SUBJECTS = (
    CAMPAIGN_SUITABILITY,
    SEARCH_BRAND,
    "message.services.content_creator_insights_service.GenerateCreatorInsightsRequest",
    f"{FORECAST}.GenerateKeywordForecastMetricsRequest.geo_modifiers",
    f"{FORECAST}.GenerateKeywordForecastMetricsRequest.biddable_keywords",
)
AFTER_SUBJECTS = (
    CUSTOMER_SUITABILITY,
    SEARCH_TOPICS,
    "proto_field.services.content_creator_insights_service."
    "GenerateTrendingInsightsRequest.search_topics",
    "message.services.content_creator_insights_service.GenerateCreatorInsightsRequest",
    f"{FORECAST}.CampaignToForecast.geo_target_constants",
    f"{FORECAST}.ForecastAdGroup.keywords",
)


def migration_table(rows: tuple[tuple[str, ...], ...]) -> str:
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<h2>v25 major and minor versions</h2><table>{body}</table>"


def source(family: str, kind: str, attributes: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": "v25",
        "family": family,
        "kind": kind,
        "subject": "metrics.clicks",
        "attributes": attributes,
        "source_url": f"https://example.test/{family}",
        "retrieved_at": "2026-09-04T00:00:00Z",
        "sha256": "a" * 64,
    }


def query_builder_field(**overrides: Any) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "category": "METRIC",
        "data_type": "INT64",
        "filterable": True,
        "repeated": False,
        "selectable": True,
        "sortable": True,
        **overrides,
    }
    return source("field", "field", attributes)


def proto_field(**overrides: Any) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "data_type": "INT64",
        "proto_type": "int64",
        "repeated": False,
        **overrides,
    }
    return source("proto", "field", attributes)


def catalog_records(version: str) -> list[dict[str, Any]]:
    path = DATA_ROOT / f"catalog_{version}.jsonl"
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def test_a_query_builder_only_field_is_documented_not_unknown() -> None:
    fact = CHANGES.compile_version("v25", [query_builder_field()])[0]
    assert (fact.resolution, fact.confidence) == ("RESOLVED", "DOCUMENTED")
    assert "conflict" not in fact.attributes


def test_agreeing_proto_and_query_builder_facts_stay_proven() -> None:
    fact = CHANGES.compile_version("v25", [proto_field(), query_builder_field()])[0]
    assert (fact.resolution, fact.confidence) == ("RESOLVED", "PROVEN")
    assert fact.corroborating_sources


@pytest.mark.parametrize(
    "overrides",
    [{"data_type": "STRING"}, {"repeated": True}],
)
def test_disagreeing_present_sources_stay_unknown(overrides: dict[str, Any]) -> None:
    fact = CHANGES.compile_version("v25", [proto_field(), query_builder_field(**overrides)])[0]
    assert fact.resolution == "UNKNOWN_PROVIDER_CONTRACT"
    assert fact.attributes["conflict"] == "proto and field catalog type or repetition disagree"


def test_query_builder_sources_that_disagree_with_themselves_stay_unknown() -> None:
    conflicts = [{"source_url": "https://example.test/other", "sha256": "b" * 64}]
    fact = CHANGES.compile_version("v25", [query_builder_field(source_conflicts=conflicts)])[0]
    assert fact.resolution == "UNKNOWN_PROVIDER_CONTRACT"
    assert fact.attributes["conflict"] == "Query Builder sources disagree about the field"


def test_a_proto_field_without_a_query_builder_counterpart_is_not_a_gaql_field() -> None:
    fact = CHANGES.compile_version("v25", [proto_field()])[0]
    assert fact.resolution == "UNKNOWN_PROVIDER_CONTRACT"
    assert fact.attributes["conflict"] == "proto field has no Query Builder counterpart"


def test_more_than_one_record_of_a_family_stays_unknown() -> None:
    fact = CHANGES.compile_version(
        "v25", [query_builder_field(), query_builder_field(sortable=False)]
    )[0]
    assert fact.resolution == "UNKNOWN_PROVIDER_CONTRACT"
    assert fact.attributes["conflict"] == "a source family reports the field more than once"


@pytest.mark.parametrize("version", VERSIONS)
def test_the_field_inventory_stays_complete(version: str) -> None:
    records = catalog_records(version)
    inventory = [item for item in records if item["kind"] == "field_inventory"]
    assert len(inventory) == 1
    assert inventory[0]["attributes"]["complete"] is True
    assert inventory[0]["resolution"] == "RESOLVED"
    fields = [item for item in records if item["kind"] == "field"]
    assert inventory[0]["attributes"]["fields"] == len(fields)


@pytest.mark.parametrize("version", VERSIONS)
def test_the_named_fields_resolve_in_every_shipped_catalog(version: str) -> None:
    by_subject = {item["subject"]: item for item in catalog_records(version)}
    for subject in NAMED_FIELDS:
        fact = by_subject[subject]
        assert fact["resolution"] == "RESOLVED", f"{version} {subject}"
        assert "conflict" not in fact["attributes"]


@pytest.mark.parametrize("version", VERSIONS)
def test_unresolved_facts_always_record_why(version: str) -> None:
    for item in catalog_records(version):
        unresolved = item["resolution"] != "RESOLVED"
        assert unresolved == ("conflict" in item["attributes"]), item["subject"]


@pytest.mark.parametrize("version", VERSIONS)
def test_only_disagreeing_sources_and_unbound_claims_stay_unresolved(version: str) -> None:
    records = catalog_records(version)
    unresolved = [item for item in records if item["resolution"] != "RESOLVED"]
    fields = [item for item in unresolved if item["kind"] == "field"]
    claims = [item for item in unresolved if item["kind"] == UNRESOLVED_CHANGE_KIND]
    assert len(fields) + len(claims) == len(unresolved)
    assert {item["attributes"]["conflict"] for item in fields} <= {
        "proto and field catalog type or repetition disagree",
        "Query Builder sources disagree about the field",
    }
    for item in fields:
        if item["attributes"]["conflict"].startswith("proto and field"):
            assert (
                item["attributes"]["proto_data_type"] != item["attributes"]["data_type"]
                or (item["corroborating_sources"])
            )
        else:
            assert item["attributes"]["source_conflicts"]
    for item in claims:
        assert item["attributes"]["close_with"]
        assert item["attributes"]["claim"]
    assert len(unresolved) < len(records) // 20, f"{version} leaves {len(unresolved)} unresolved"


def test_the_migration_table_resolves_only_unambiguous_rows() -> None:
    records = migration_table_changes(
        "v25",
        (migration_table(MIGRATION_ROWS),),
        BEFORE_SUBJECTS,
        AFTER_SUBJECTS,
        "https://example.test/release-notes",
        "a" * 64,
    )
    bound = [item for item in records if item["kind"] == "documented_change"]
    unbound = [item for item in records if item["kind"] == UNRESOLVED_CHANGE_KIND]
    entries = [item for item in records if item["kind"] == MIGRATION_ENTRY_KIND]
    carried = {
        item["attributes"]["change_subject"]: item["attributes"]["replacement"] for item in bound
    }
    assert carried == {
        CAMPAIGN_SUITABILITY: CUSTOMER_SUITABILITY,
        SEARCH_BRAND: SEARCH_TOPICS,
    }
    assert len(unbound) == 1
    assert len(entries) == len(MIGRATION_ROWS) - 1
    assert all(item["attributes"]["change_kind"] == "REPLACED" for item in bound)
    assert len(bound) + len(unbound) + len(entries) == len(records)


def test_a_row_naming_two_subjects_becomes_an_unresolved_record_of_its_own() -> None:
    records = migration_table_changes(
        "v25",
        (migration_table((MIGRATION_ROWS[0], MIGRATION_ROWS[3])),),
        BEFORE_SUBJECTS,
        AFTER_SUBJECTS,
        "https://example.test/release-notes",
        "a" * 64,
    )
    assert [item["kind"] for item in records] == [MIGRATION_ENTRY_KIND, UNRESOLVED_CHANGE_KIND]
    assert records[1]["attributes"]["claim"] == " ".join(MIGRATION_ROWS[3])


def test_a_behavioural_row_is_never_read_as_a_replacement_and_is_never_silent() -> None:
    row = (
        "Campaign.video_brand_safety_suitability",
        "Required field",
        "Behavioral shift",
        "Use Customer.video_brand_safety_suitability instead.",
    )
    records = migration_table_changes(
        "v25",
        (migration_table((MIGRATION_ROWS[0], row)),),
        BEFORE_SUBJECTS,
        AFTER_SUBJECTS,
        "https://example.test/release-notes",
        "a" * 64,
    )
    assert [item["kind"] for item in records] == [MIGRATION_ENTRY_KIND]
    assert records[0]["attributes"]["states_replacement"] is False
    assert records[0]["attributes"]["initial_state"] == row[0]
    assert records[0]["attributes"]["new_state"] == row[1]


@pytest.mark.parametrize("version", VERSIONS)
def test_every_row_the_release_notes_could_not_resolve_is_its_own_record(version: str) -> None:
    records = catalog_records(version)
    release = [item for item in records if item["kind"] == "release_notes"]
    assert len(release) == 1
    assert "unresolved_replacement_rows" not in release[0]["attributes"]
    for item in records:
        if item["kind"] != UNRESOLVED_CHANGE_KIND:
            continue
        assert item["resolution"] != "RESOLVED"
        assert item["attributes"]["to_version"] == version
        assert item["attributes"]["conflict"]
        assert item["attributes"]["close_with"]


@pytest.mark.parametrize(
    ("version", "subject", "replacement"),
    [
        ("v24", CAMPAIGN_SUITABILITY, CUSTOMER_SUITABILITY),
        ("v25", SEARCH_BRAND, SEARCH_TOPICS),
    ],
)
def test_the_shipped_catalog_carries_the_documented_replacement(
    version: str, subject: str, replacement: str
) -> None:
    changes = [
        item
        for item in catalog_records(version)
        if item["kind"] == "documented_change" and item["attributes"]["change_subject"] == subject
    ]
    assert [item["attributes"]["replacement"] for item in changes] == [replacement]
    assert all(item["resolution"] == "RESOLVED" for item in changes)
