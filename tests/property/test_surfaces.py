from __future__ import annotations

from pathlib import Path

import pytest

from hubbleops.app import registry
from hubbleops.app.cli import scan_repository
from tests.support import fixture_repos


@pytest.mark.parametrize("fixture", fixture_repos(), ids=lambda path: path.name)
def test_two_surfaces_over_one_closure_differ_and_are_both_explained(fixture: Path) -> None:
    repo = fixture / "repo"
    provider = scan_repository(repo, registry.load_pack("google_ads"))
    mock = scan_repository(repo, registry.load_pack("_mock"))

    assert provider.closure.tree_hash() == mock.closure.tree_hash()
    assert provider.ledger.export() != mock.ledger.export()
    assert provider.ledger.counts()["unexplained"] == 0
    assert mock.ledger.counts()["unexplained"] == 0


@pytest.mark.parametrize("fixture", fixture_repos(), ids=lambda path: path.name)
def test_a_surface_that_matches_nothing_still_accounts_for_the_closure(fixture: Path) -> None:
    mock = scan_repository(fixture / "repo", registry.load_pack("_mock")).ledger
    assert mock.candidates
    for candidate in mock.candidates:
        assert candidate["status"]
        assert candidate["reason"]


def test_the_two_packs_declare_different_surfaces() -> None:
    google = registry.load_pack("google_ads").surface
    mock = registry.load_pack("_mock").surface
    assert google.surface_hash() != mock.surface_hash()
    assert not set(google.package_names) & set(mock.package_names)
    assert not set(google.hosts) & set(mock.hosts)


def test_every_available_pack_loads_and_validates() -> None:
    names = registry.available_packs()
    assert {"google_ads", "_mock"} <= set(names)
    for name in names:
        pack = registry.load_pack(name)
        assert pack.surface.name == name
        assert pack.surface.identifiers
