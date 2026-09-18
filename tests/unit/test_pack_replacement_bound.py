from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from typing import Any

import pytest

from hubbleops.app import registry
from hubbleops.packs.google_ads.changes import DATA_ROOT, UNRESOLVED_CHANGE_KIND
from hubbleops.packs.google_ads.refresh import (
    DOCUMENTED_CHANGE_KIND,
    MIGRATION_ENTRY_KIND,
    clean,
    migration_rows,
    replacement_accounting,
    replacement_bindings,
    stated_replacements,
    states_replacement,
    tabulated_rows,
)

SOURCES = DATA_ROOT / "sources"
VERSIONS = ("v19", "v20", "v21", "v22", "v23", "v24", "v25")
TABULATED_ROWS = {"v19": 0, "v20": 0, "v21": 7, "v22": 7, "v23": 5, "v24": 13, "v25": 9}
REPLACEMENT_ROWS = {"v19": 0, "v20": 0, "v21": 1, "v22": 5, "v23": 4, "v24": 9, "v25": 5}

VIDEO_RENAMES = {
    "metrics.average_cpv": "metrics.trueview_average_cpv",
    "metrics.video_view_rate": "metrics.video_trueview_view_rate",
    "metrics.video_views": "metrics.video_trueview_views",
    "metrics.video_view_rate_in_feed": "metrics.video_trueview_view_rate_in_feed",
    "metrics.video_view_rate_in_stream": "metrics.video_trueview_view_rate_in_stream",
    "metrics.video_view_rate_shorts": "metrics.video_trueview_view_rate_shorts",
}
CAMPAIGN_RENAMES = {
    "campaign.start_date": "campaign.start_date_time",
    "campaign.end_date": "campaign.end_date_time",
}
REMOVED_WITH_NO_DOCUMENTED_REPLACEMENT = (
    "proto_field.common.ad_type_infos.DemandGenMultiAssetAdInfo.lead_form_only"
)
PLACEHOLDER_NEW_STATE = "None"


def catalog_records(version: str) -> list[dict[str, Any]]:
    path = DATA_ROOT / f"catalog_{version}.jsonl"
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def retained_page(version: str, name: str) -> str:
    inventory = json.loads((SOURCES / "raw" / f"docs_{version}.json").read_bytes())
    page = next(item for item in inventory["pages"] if item["name"] == name)
    return gzip.decompress((SOURCES / "upstream" / f"{page['sha256']}.gz").read_bytes()).decode()


def release_sections(version: str) -> tuple[str, ...]:
    text = retained_page(version, "release")
    if f'id="{version}-top"' not in text:
        text = retained_page(version, "archive")
    return tuple(
        item[0]
        for item in re.finditer(r"<h2\b[^>]*>.*?(?=<h2\b|$)", text, re.S)
        if re.match(rf"{version}(?:\b|\.)", clean(item[0].split("</h2>", 1)[0]))
    )


def subjects_of(version: str) -> tuple[str, ...]:
    return tuple(
        str(json.loads(line)["subject"])
        for family in ("proto", "field")
        for line in (SOURCES / "normalized" / f"{family}_{version}.jsonl")
        .read_text("utf-8")
        .splitlines()
    )


def diff_facts(pair: tuple[str, str]) -> dict[str, Any]:
    pack = registry.load_pack("google_ads")
    return {fact.subject: fact for fact in pack.contract.diff(*pair).facts}


@pytest.mark.parametrize(
    ("pair", "renames"), [(("v21", "v22"), VIDEO_RENAMES), (("v22", "v23"), CAMPAIGN_RENAMES)]
)
def test_a_removed_subject_the_provider_renames_is_bound_to_its_replacement(
    pair: tuple[str, str], renames: dict[str, str]
) -> None:
    facts = diff_facts(pair)
    for subject, replacement in renames.items():
        fact = facts[subject]
        assert fact.change == "REMOVED", subject
        assert fact.replacement == replacement, subject
        assert fact.result == "VALID", subject
        assert replacement in facts and facts[replacement].change == "ADDED"


def test_a_removed_subject_the_provider_does_not_rename_is_never_guessed() -> None:
    facts = diff_facts(("v22", "v23"))
    fact = facts[REMOVED_WITH_NO_DOCUMENTED_REPLACEMENT]
    assert fact.change == "REMOVED"
    assert fact.replacement is None
    unbound = [
        item
        for item in catalog_records("v23")
        if item["kind"] == UNRESOLVED_CHANGE_KIND
        and "lead_form_only" in item["attributes"]["stated_subject"]
    ]
    assert len(unbound) == 1
    assert unbound[0]["attributes"]["stated_replacement"] == PLACEHOLDER_NEW_STATE
    assert unbound[0]["resolution"] == "UNKNOWN_PROVIDER_CONTRACT"
    assert "human decision" in unbound[0]["attributes"]["close_with"]


def test_a_removal_row_whose_new_state_is_the_word_none_binds_to_no_enum_member() -> None:
    previous, current = subjects_of("v22"), subjects_of("v23")
    assert [
        subject
        for subject in current
        if subject.lower().endswith(f".{PLACEHOLDER_NEW_STATE.lower()}")
    ]
    assert (
        replacement_bindings(
            "DemandGenMultiAssetAdInfo.lead_form_only",
            PLACEHOLDER_NEW_STATE,
            previous,
            current,
        )
        == ()
    )


def test_a_multi_subject_row_is_paired_by_the_order_the_provider_writes_it() -> None:
    row = next(
        row for row in migration_rows(release_sections("v22")) if "video_trueview_views" in row[1]
    )
    pairs = stated_replacements(row)
    assert pairs == (
        ("average_cpv", "trueview_average_cpv"),
        ("video_view_rate", "video_trueview_view_rate"),
        ("video_views", "video_trueview_views"),
        ("video_view_rate_in_feed", "video_trueview_view_rate_in_feed"),
        ("video_view_rate_in_stream", "video_trueview_view_rate_in_stream"),
        ("video_view_rate_shorts", "video_trueview_view_rate_shorts"),
    )
    for replaced, replacing in pairs:
        assert f"#{replacing}" in row[1], replacing
        assert f">{replaced}<" in row[0].replace(" ", ""), replaced


def test_a_row_the_provider_writes_as_prose_is_never_paired_by_position() -> None:
    row = next(row for row in migration_rows(release_sections("v24")) if "geo_modifiers" in row[0])
    assert states_replacement(row)
    assert len(stated_replacements(row)) == 1
    assert stated_replacements(row)[0][0] == clean(row[0])


@pytest.mark.parametrize("version", VERSIONS)
def test_every_row_the_release_notes_tabulate_leaves_a_record(version: str) -> None:
    sections = release_sections(version)
    assert tabulated_rows(sections) == TABULATED_ROWS[version]
    rows = migration_rows(sections)
    assert len(rows) == TABULATED_ROWS[version]
    assert sum(1 for row in rows if states_replacement(row)) == REPLACEMENT_ROWS[version]
    entries = [item for item in catalog_records(version) if item["kind"] == MIGRATION_ENTRY_KIND]
    assert len(entries) == TABULATED_ROWS[version]
    assert (
        sum(1 for item in entries if item["attributes"]["states_replacement"])
        == (REPLACEMENT_ROWS[version])
    )


@pytest.mark.parametrize("version", VERSIONS)
def test_every_stated_replacement_is_either_bound_or_an_open_unknown(version: str) -> None:
    stated = sum(
        len(stated_replacements(row))
        for row in migration_rows(release_sections(version))
        if states_replacement(row)
    )
    accounting = replacement_accounting(catalog_records(version))
    assert accounting["bound_pairs"] + accounting["unresolved_pairs"] == accounting["stated_pairs"]
    assert accounting["stated_pairs"] >= stated
    assert accounting["migration_rows"] == TABULATED_ROWS[version]
    assert accounting["replacement_rows"] == REPLACEMENT_ROWS[version]
    assert accounting == declared_replacements(version)


def declared_replacements(version: str) -> dict[str, int]:
    manifest = json.loads((SOURCES / "manifest.json").read_bytes())
    entry = next(
        item
        for item in manifest["sources"]
        if item["version"] == version and item["family"] == "docs"
    )
    return dict(entry["expected_replacements"])


def test_the_binding_rule_refuses_a_side_whose_leaf_differs_only_in_case() -> None:
    previous = ("enums.status.StatusEnum.Status.NONE", "resources.campaign.Campaign.start_date")
    current = ("enums.status.StatusEnum.Status.NONE", "resources.campaign.Campaign.start_date_time")
    assert replacement_bindings("Status.None", "Status.None", previous, current) == ()
    assert replacement_bindings(
        "Campaign.start_date", "Campaign.start_date_time", previous, current
    ) == (
        ("resources.campaign.Campaign.start_date", "resources.campaign.Campaign.start_date_time"),
    )


def test_the_binding_rule_never_pairs_across_containers_by_similarity() -> None:
    previous = (
        "proto_field.common.metrics.Metrics.video_view_rate_shorts",
        "proto_field.common.metrics.Metrics.video_view_rate",
    )
    current = ("proto_field.common.metrics.Metrics.video_trueview_view_rate",)
    assert replacement_bindings(
        "video_view_rate_shorts", "video_trueview_view_rate", previous, current
    ) == (
        (
            "proto_field.common.metrics.Metrics.video_view_rate_shorts",
            "proto_field.common.metrics.Metrics.video_trueview_view_rate",
        ),
    )
    assert replacement_bindings("video_view_rate_shorts", "trueview_views", previous, current) == ()


def test_every_binding_carries_the_page_it_came_from_and_the_claim_it_states() -> None:
    seen = 0
    for version in VERSIONS:
        for item in catalog_records(version):
            if item["kind"] != DOCUMENTED_CHANGE_KIND:
                continue
            seen += 1
            attributes = item["attributes"]
            assert attributes["change_kind"] == "REPLACED"
            assert attributes["change_subject"] != attributes["replacement"]
            assert attributes["claim"].strip()
            assert "developers.google.com/google-ads/api/docs/" in item["source_url"]
            assert len(item["sha256"]) == 64
            assert Path(SOURCES / "upstream" / f"{item['sha256']}.gz").is_file()
    assert seen == 26
