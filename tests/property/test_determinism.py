from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from hubbleops.app import registry
from hubbleops.app.cli import scan_repository
from hubbleops.core.canonical import export_bytes
from tests.support import fixture_repos


@pytest.mark.parametrize("fixture", fixture_repos(), ids=lambda path: path.name)
def test_two_consecutive_scans_are_byte_identical(fixture: Path) -> None:
    pack = registry.load_pack("google_ads")
    first = export_bytes(scan_repository(fixture / "repo", pack).ledger.export())
    second = export_bytes(scan_repository(fixture / "repo", pack).ledger.export())
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
    assert first == second


@pytest.mark.parametrize("fixture", fixture_repos(), ids=lambda path: path.name)
def test_the_proof_scope_and_run_id_are_reproduced_exactly(fixture: Path) -> None:
    pack = registry.load_pack("google_ads")
    first = scan_repository(fixture / "repo", pack)
    second = scan_repository(fixture / "repo", pack)
    assert first.proof_scope == second.proof_scope
    assert first.proof_scope_hash == second.proof_scope_hash
    assert first.run_id == second.run_id


def test_a_content_change_produces_a_new_proof_scope(tmp_path: Path) -> None:
    pack = registry.load_pack("google_ads")
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    source = repo / "src" / "client.py"
    source.write_text('VERSION = "v22"\n', encoding="utf-8")
    before = scan_repository(repo, pack)
    source.write_text('VERSION = "v23"\n', encoding="utf-8")
    after = scan_repository(repo, pack)
    assert before.proof_scope_hash != after.proof_scope_hash
    assert before.run_id != after.run_id


@pytest.mark.parametrize("fixture", fixture_repos(), ids=lambda path: path.name)
def test_the_unknown_set_is_reproduced_across_runs(fixture: Path) -> None:
    pack = registry.load_pack("google_ads")
    first = scan_repository(fixture / "repo", pack).ledger
    second = scan_repository(fixture / "repo", pack).ledger
    assert {candidate["id"] for candidate in first.by_status("UNKNOWN")} == {
        candidate["id"] for candidate in second.by_status("UNKNOWN")
    }
