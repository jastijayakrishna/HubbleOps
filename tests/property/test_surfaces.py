from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from hubbleops.app import registry
from hubbleops.app.cli import scan_repository
from hubbleops.core.surface import SurfaceSpec
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


@pytest.mark.parametrize("fixture", fixture_repos(), ids=lambda path: path.name)
def test_two_surfaces_over_one_tree_never_share_a_proof_key(fixture: Path) -> None:
    repo = fixture / "repo"
    google = registry.load_pack("google_ads")
    mock = registry.load_pack("_mock")
    provider = scan_repository(repo, google)
    other = scan_repository(repo, mock)

    assert provider.closure.tree_hash() == other.closure.tree_hash()
    assert provider.proof_scope["provider_contract_hash"] == google.surface.surface_hash()
    assert other.proof_scope["provider_contract_hash"] == mock.surface.surface_hash()
    assert provider.proof_scope_hash != other.proof_scope_hash
    assert provider.run_id != other.run_id


def test_changing_any_surface_field_moves_the_surface_hash() -> None:
    base = registry.load_pack("google_ads").surface
    for item in dataclasses.fields(SurfaceSpec):
        current = getattr(base, item.name)
        altered = f"{current}_x" if isinstance(current, str) else (*current, current[0])
        variant = dataclasses.replace(base, **{item.name: altered})
        assert variant.surface_hash() != base.surface_hash(), (
            f"{item.name} does not reach surface_hash(), so two surfaces differing only in it "
            "would share one proof key"
        )


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
