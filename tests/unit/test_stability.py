from __future__ import annotations

import re
from itertools import pairwise
from typing import Any

import pytest

from hubbleops.app import stability


@pytest.fixture(scope="module")
def table(google_pack: Any) -> dict[str, str]:
    return stability.compute(google_pack)


def test_a_field_identical_in_every_lattice_version_is_stable(table: dict[str, str]) -> None:
    assert table["metrics.clicks"] == stability.STABLE


def test_surface_identifiers_and_hosts_that_carry_no_version_are_stable(
    google_pack: Any, table: dict[str, str]
) -> None:
    surface = google_pack.surface
    assert table[surface.identifiers[0]] == stability.STABLE
    assert all(table[host] == stability.STABLE for host in surface.hosts)


def test_a_subject_removed_between_two_lattice_versions_changes_at_that_boundary(
    google_pack: Any, table: dict[str, str]
) -> None:
    lattice = [version.id for version in google_pack.versions()]
    removed: list[tuple[str, str]] = []
    for earlier, later in pairwise(lattice):
        before = {fact.subject for fact in google_pack.contract.catalog(earlier).facts}
        after = {fact.subject for fact in google_pack.contract.catalog(later).facts}
        removed.extend((subject, later) for subject in sorted(before - after))
    assert removed
    subject, boundary = removed[0]
    disposition = table[subject]
    assert disposition.startswith(stability.CHANGES_AT)
    assert boundary in disposition[len(stability.CHANGES_AT) :].split(",")


def test_a_subject_the_catalog_could_not_resolve_is_undecided(
    google_pack: Any, table: dict[str, str]
) -> None:
    unresolved = sorted(
        fact.subject
        for version in google_pack.versions()
        for fact in google_pack.contract.catalog(version.id).facts
        if fact.resolution == stability.UNRESOLVED
    )
    assert unresolved
    assert table[unresolved[0]] == stability.UNDECIDED


def test_version_selecting_keys_and_version_naming_entries_are_never_marked_stable(
    google_pack: Any, table: dict[str, str]
) -> None:
    surface = google_pack.surface
    lattice = [version.id for version in google_pack.versions()]
    for key in surface.config_env_keys:
        assert key not in table
    naming = [
        entry
        for entry in surface.identifiers + surface.package_names
        if any(re.search(rf"(?<![0-9a-z]){version}(?![0-9a-z])", entry) for version in lattice)
    ]
    assert naming
    assert all(entry not in table for entry in naming)


def test_every_disposition_is_one_of_the_three_stored_values(table: dict[str, str]) -> None:
    for disposition in table.values():
        assert disposition in (stability.STABLE, stability.UNDECIDED) or disposition.startswith(
            stability.CHANGES_AT
        )


def test_the_table_is_deterministic_and_sorted(google_pack: Any, table: dict[str, str]) -> None:
    assert stability.compute(google_pack) == table
    assert list(table) == sorted(table)
