from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

FIXTURE_ROOT = Path("tests/fixtures/phase3")
FIXTURE_IDS = {
    "generated_marker_scope",
    "language_scoped_carrier",
    "npm_lock_cross_evidence",
    "python_imported_wrapper",
    "python_rest_query_wrapper",
    "typescript_version_symbol",
}
METADATA_FILES = (
    "expected_candidates.json",
    "expected_unknowns.json",
    "falsifier.json",
)
BANNED_SOURCE_IDENTIFIERS = (
    "mcp-google-ads",
    "opteo",
    "google_ads_server.py",
)


def fixture_paths() -> list[Path]:
    return sorted(path for path in FIXTURE_ROOT.iterdir() if path.is_dir())


def load_json(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


@pytest.mark.parametrize("fixture", fixture_paths(), ids=lambda path: path.name)
def test_phase3_real_repo_fixtures_have_complete_machine_readable_contracts(
    fixture: Path,
) -> None:
    assert fixture.name in FIXTURE_IDS
    assert (fixture / "repo").is_dir()
    for filename in METADATA_FILES:
        document = load_json(fixture / filename)
        assert document["fixture_id"] == fixture.name


def test_phase3_real_repo_fixture_set_is_complete() -> None:
    assert {path.name for path in fixture_paths()} == FIXTURE_IDS


@pytest.mark.parametrize("fixture", fixture_paths(), ids=lambda path: path.name)
def test_phase3_fixture_expectations_reference_only_local_synthetic_paths(
    fixture: Path,
) -> None:
    expected = load_json(fixture / "expected_candidates.json")
    for candidate in expected["candidates"]:
        path = candidate["path"]
        assert not Path(path).is_absolute()
        assert "\\" not in path
        assert (fixture / "repo" / path).is_file()


@pytest.mark.parametrize("fixture", fixture_paths(), ids=lambda path: path.name)
def test_phase3_unknowns_are_preserved_with_closing_instructions(fixture: Path) -> None:
    expected = load_json(fixture / "expected_candidates.json")
    unknowns = load_json(fixture / "expected_unknowns.json")["unknowns"]
    candidate_ids = {candidate["id"] for candidate in expected["candidates"]}
    for unknown in unknowns:
        if "candidate_id" in unknown:
            assert unknown["candidate_id"] in candidate_ids
            assert unknown["candidate_status"] == "UNKNOWN"
        else:
            assert unknown["scope"] == "target_resolution"
            assert unknown["related_candidate_id"] in candidate_ids
        assert unknown["disposition"] == "PRESERVED_UNKNOWN"
        assert unknown["reason"]
        assert unknown["close_with"]


@pytest.mark.parametrize("fixture", fixture_paths(), ids=lambda path: path.name)
def test_phase3_falsifiers_name_positive_and_negative_conditions(fixture: Path) -> None:
    falsifier = load_json(fixture / "falsifier.json")
    assert falsifier["phase"] == 3
    assert falsifier["failure_class"]
    assert falsifier["should_be_caught_by"]
    assert falsifier["must_match"]
    assert falsifier["must_not_match"]


@pytest.mark.parametrize("fixture", fixture_paths(), ids=lambda path: path.name)
def test_phase3_fixture_corpus_is_anonymized(fixture: Path) -> None:
    corpus = "\n".join(
        path.read_text(encoding="utf-8", errors="replace").lower()
        for path in sorted(fixture.rglob("*"))
        if path.is_file()
    )
    assert not any(identifier in corpus for identifier in BANNED_SOURCE_IDENTIFIERS)
