from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.corpus.challenge import CHALLENGES, apply_one, catching_stage, signals

SOURCE_VERSIONS = re.compile(r"(?<![A-Za-z0-9_.])[vV](19|2[0-4])(?![0-9A-Za-z_])")


def test_every_challenge_has_a_distinct_id_and_a_stated_intent() -> None:
    ids = [item.challenge_id for item in CHALLENGES]
    assert len(ids) == len(set(ids))
    for item in CHALLENGES:
        assert item.intent.strip()
        assert item.should_be_caught_by.strip()
        assert item.languages
        assert item.suffixes


def test_every_challenge_pattern_compiles() -> None:
    for item in CHALLENGES:
        re.compile(item.find, re.MULTILINE)


@pytest.mark.parametrize(
    ("challenge_id", "language", "filename", "body", "must_contain"),
    [
        (
            "X01",
            "php",
            "Client.php",
            "<?php\nuse Google\\Ads\\GoogleAds\\V25\\Services\\Foo;\n",
            "GoogleAds\\v25",
        ),
        (
            "X02",
            "php",
            "config.php",
            "<?php\n'version' => env('GOOGLE_ADS_API_VERSION', 'v25'),\n",
            "'v23'",
        ),
        (
            "X03",
            "python",
            "client.py",
            'import os\nVERSION = "v25"\n',
            '"v2" + "4"',
        ),
        (
            "X04",
            "python",
            "client.py",
            "import os\nX = 1\n",
            "google.ads.googleads.v22.services",
        ),
        (
            "X05",
            "python",
            "rest.py",
            'URL = "https://googleads.googleapis.com/v25/customers/"\n',
            "googleapis.com/v21",
        ),
        (
            "X07",
            "python",
            "requirements.txt",
            "google-ads>=31.2.0\n",
            "google-ads>=25",
        ),
    ],
)
def test_each_corruption_actually_lands(
    tmp_path: Path,
    challenge_id: str,
    language: str,
    filename: str,
    body: str,
    must_contain: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / filename).write_text(body, encoding="utf-8")
    corruption = next(item for item in CHALLENGES if item.challenge_id == challenge_id)
    applied = apply_one(repo, corruption, language)
    assert applied.applied, applied.reason
    text = (repo / filename).read_text(encoding="utf-8")
    assert must_contain in text, text


def test_the_smuggling_corruption_really_hides_a_source_version(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "client.py").write_text('VERSION = "v25"\n', encoding="utf-8")
    corruption = next(item for item in CHALLENGES if item.challenge_id == "X03")
    assert apply_one(repo, corruption, "python").applied
    text = (repo / "client.py").read_text(encoding="utf-8")
    assert SOURCE_VERSIONS.search(text) is None, (
        "X03 is only interesting if no single token reads as a source version; "
        f"found one in {text!r}"
    )
    assert eval(text.split("=", 1)[1].strip()) == "v24"


def test_the_zero_width_corruption_is_invisible_to_a_version_scanner(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "client.py").write_text('VERSION = "v25"\n', encoding="utf-8")
    corruption = next(item for item in CHALLENGES if item.challenge_id == "X06")
    assert apply_one(repo, corruption, "python").applied
    text = (repo / "client.py").read_text(encoding="utf-8")
    assert "​" in text
    assert re.search(r'"v25"', text) is None


def test_a_corruption_with_no_site_reports_why_and_changes_nothing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "empty.py").write_text("X = 1\n", encoding="utf-8")
    corruption = next(item for item in CHALLENGES if item.challenge_id == "X05")
    applied = apply_one(repo, corruption, "python")
    assert not applied.applied
    assert "no site" in applied.reason
    assert (repo / "empty.py").read_text(encoding="utf-8") == "X = 1\n"


def test_a_corruption_outside_its_language_is_not_applied(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Client.php").write_text(
        "<?php\nuse Google\\Ads\\GoogleAds\\V25\\Services\\Foo;\n", encoding="utf-8"
    )
    corruption = next(item for item in CHALLENGES if item.challenge_id == "X01")
    applied = apply_one(repo, corruption, "python")
    assert not applied.applied
    assert "not applicable" in applied.reason


def test_catching_stage_names_only_what_newly_fires() -> None:
    baseline = {
        "verdict": "FAILED",
        "reasons": ["audit_pass is FAIL"],
        "conjuncts": {"response_consumer_check": True, "migration_audit": False},
        "falsifiers": {"per_call_version_override": "PASS"},
    }
    corrupted = {
        "verdict": "FAILED",
        "reasons": ["audit_pass is FAIL", "v22 is still present at a.py:3"],
        "conjuncts": {"response_consumer_check": True, "migration_audit": False},
        "falsifiers": {"per_call_version_override": "FAILED"},
    }
    outcome = catching_stage(baseline, corrupted)
    assert outcome["caught"] is True
    assert "falsifier:per_call_version_override" in outcome["catching_stage"]
    assert outcome["new_reasons"] == ["v22 is still present at a.py:3"]


def test_a_corruption_that_changes_nothing_is_recorded_as_escaped() -> None:
    baseline = {
        "verdict": "FAILED",
        "reasons": ["audit_pass is FAIL"],
        "conjuncts": {"migration_audit": False},
        "falsifiers": {"per_call_version_override": "PASS"},
    }
    outcome = catching_stage(baseline, dict(baseline))
    assert outcome["caught"] is False
    assert outcome["catching_stage"] == []


def test_a_verified_verdict_on_a_corrupted_tree_is_always_an_escape() -> None:
    baseline = {"verdict": "FAILED", "reasons": [], "conjuncts": {}, "falsifiers": {}}
    corrupted = {
        "verdict": "VERIFIED_FOR_SCOPE",
        "reasons": ["something new"],
        "conjuncts": {},
        "falsifiers": {},
    }
    assert catching_stage(baseline, corrupted)["caught"] is False


def test_signals_of_a_missing_receipt_are_empty_not_an_exception(tmp_path: Path) -> None:
    body = signals(tmp_path / "absent.json")
    assert body["verdict"] == ""
    assert body["reasons"] == []
