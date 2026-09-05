import json
import socket
from pathlib import Path

import pytest

from hubbleops.app.cli import main
from hubbleops.packs.google_ads.changes import CHANGES
from hubbleops.packs.google_ads.contract import CONTRACT


def test_pack_verify_is_offline(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def denied(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline pack attempted network access")

    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    assert main(["pack", "verify", "google_ads"]) == 0
    output = capsys.readouterr().out
    assert "v19" in output and "v25" in output
    assert CHANGES.lattice_hash in output


def test_known_lifecycle_removals_and_current_field_addition() -> None:
    fixture = json.loads(Path("tests/fixtures/google_ads/known_changes.json").read_bytes())
    for case in (fixture, fixture["composed"]):
        facts = {
            item.subject: item
            for item in CONTRACT.diff(case["from_version"], case["to_version"]).facts
        }
        for subject, change in case["expected"].items():
            fact = facts[subject]
            assert (fact.change, fact.confidence, fact.result) == (change, "PROVEN", "VALID")


def test_build_does_not_modify_sources(tmp_path: Path) -> None:
    before = {
        str(path): path.read_bytes() for path in CHANGES.source_root.rglob("*") if path.is_file()
    }
    CHANGES.build(tmp_path)
    assert before == {
        str(path): path.read_bytes() for path in CHANGES.source_root.rglob("*") if path.is_file()
    }
