from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.phase5_support import (
    ADVERSARIAL_ROOT,
    DEFAULT_SITES,
    build_repository,
    verify,
    write_obligations,
)

MANIFEST = "corruption.json"


@dataclass(frozen=True, slots=True)
class Corruption:
    name: str
    summary: str
    expected_verdict: str
    reason_contains: str
    caught_by: str
    edits: dict[str, str | None]
    obligation_sites: tuple[tuple[str, int, str], ...]


def _load(directory: Path) -> Corruption:
    manifest: Mapping[str, Any] = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
    edits: dict[str, str | None] = {}
    files = directory / "files"
    if files.is_dir():
        for path in sorted(files.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                relative = path.relative_to(files).as_posix()
                edits[relative] = path.read_text(encoding="utf-8")
    for removed in manifest.get("delete", []):
        edits[str(removed)] = None
    return Corruption(
        name=directory.name,
        summary=str(manifest["summary"]),
        expected_verdict=str(manifest["expected_verdict"]),
        reason_contains=str(manifest["reason_contains"]),
        caught_by=str(manifest["caught_by"]),
        edits=edits,
        obligation_sites=tuple(
            (str(site[0]), int(site[1]), str(site[2]))
            for site in manifest.get("obligation_sites", [])
        ),
    )


def corruptions() -> list[Corruption]:
    return [
        _load(child)
        for child in sorted(ADVERSARIAL_ROOT.iterdir())
        if child.is_dir() and (child / MANIFEST).is_file()
    ]


CORRUPTIONS = corruptions()


def test_the_corpus_is_large_enough() -> None:
    assert len(CORRUPTIONS) >= 10, (
        f"the phase requires at least ten deliberately wrong candidates; found {len(CORRUPTIONS)}"
    )


def test_no_corruption_expects_a_verified_verdict() -> None:
    assert all(item.expected_verdict != "VERIFIED_FOR_SCOPE" for item in CORRUPTIONS)


def test_the_corpus_is_not_caught_by_a_single_stage() -> None:
    stages = {item.caught_by.split("(")[0].strip() for item in CORRUPTIONS}
    assert len(stages) >= 4, (
        f"a corpus caught by {len(stages)} stage(s) proves little about the others: {stages}"
    )


@pytest.mark.parametrize("corruption", CORRUPTIONS, ids=lambda item: item.name)
def test_a_corrupted_candidate_is_rejected_for_the_right_reason(
    corruption: Corruption, tmp_path: Path
) -> None:
    repository = build_repository(tmp_path / "repo", corruption.edits)
    obligations = write_obligations(
        tmp_path / "obligations.json",
        repository,
        (*DEFAULT_SITES, *corruption.obligation_sites),
    )
    run = verify(repository, obligations_path=obligations)
    judgement = run.evaluation.judgement

    assert judgement.verdict != "VERIFIED_FOR_SCOPE", (
        f"P0: {corruption.name} reached VERIFIED_FOR_SCOPE. {corruption.summary}"
    )
    assert judgement.verdict == corruption.expected_verdict, (
        f"{corruption.name} expected {corruption.expected_verdict}, got {judgement.verdict}: "
        f"{judgement.reasons}"
    )
    assert any(corruption.reason_contains in reason for reason in judgement.reasons), (
        f"{corruption.name} was rejected, but not for the reason it was written to provoke. "
        f"Expected a reason containing {corruption.reason_contains!r}, got {judgement.reasons}"
    )
