from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hubbleops.app.cli import EXIT_FAILED, EXIT_OK, main
from hubbleops.store.sqlite import DATABASE_FILENAME, Store
from tests.support import fixture_repos

FIXTURE = fixture_repos()[0] / "repo"


def run_scan(state: Path, repo: Path = FIXTURE, pack: str = "google_ads") -> int:
    return main(["scan", str(repo), "--pack", pack, "--state-dir", str(state)])


def test_scan_writes_a_ledger_and_records_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    assert run_scan(state) == EXIT_OK
    out = capsys.readouterr().out
    assert "SOURCE CLOSURE" in out
    assert "unexplained  0" in out
    assert (state / DATABASE_FILENAME).is_file()
    ledgers = list((state / "artifacts").rglob("ledger.json"))
    assert len(ledgers) == 1
    payload = json.loads(ledgers[0].read_text(encoding="utf-8"))
    assert payload["counts"]["unexplained"] == 0
    assert payload["candidates"]


def test_two_consecutive_exports_have_the_same_digest(tmp_path: Path) -> None:
    state = tmp_path / "state"
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    assert (
        main(
            [
                "scan",
                str(FIXTURE),
                "--pack",
                "google_ads",
                "--state-dir",
                str(state),
                "--export",
                str(first),
            ]
        )
        == EXIT_OK
    )
    assert (
        main(
            [
                "scan",
                str(FIXTURE),
                "--pack",
                "google_ads",
                "--state-dir",
                str(state),
                "--export",
                str(second),
            ]
        )
        == EXIT_OK
    )
    digest = hashlib.sha256(first.read_bytes()).hexdigest()
    assert digest == hashlib.sha256(second.read_bytes()).hexdigest()


def test_a_rerun_does_not_duplicate_rows(tmp_path: Path) -> None:
    state = tmp_path / "state"
    run_scan(state)
    run_scan(state)
    with Store(state) as store:
        runs = store.connection.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
        row = store.latest_run("google_ads")
        assert row is not None
        evidence = len(store.evidence_for(row.run_id))
        candidates = len(store.candidates_for(row.run_id))
    assert runs == 1
    assert evidence > 0
    assert candidates > 0


def test_exposure_prints_the_map_for_the_latest_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    run_scan(state)
    capsys.readouterr()
    assert main(["exposure", "--state-dir", str(state)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "EXPOSURE MAP" in out
    assert "DISCOVERY" in out
    assert "Unexplained" in out
    assert "AFFECTED" in out
    assert "UNKNOWN" in out
    assert "close with:" in out


def test_exposure_without_a_run_fails_rather_than_printing_an_empty_map(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["exposure", "--state-dir", str(tmp_path / "empty")]) == EXIT_FAILED
    assert "no run found" in capsys.readouterr().err


def test_an_unknown_pack_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        main(["scan", str(FIXTURE), "--pack", "nope", "--state-dir", str(tmp_path)]) == EXIT_FAILED
    )
    assert "no pack named" in capsys.readouterr().err


def test_a_pack_name_cannot_escape_the_pack_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["scan", str(FIXTURE), "--pack", "../packs", "--state-dir", str(tmp_path)])
    assert code == EXIT_FAILED
    assert "not a pack name" in capsys.readouterr().err


def test_the_mock_pack_runs_the_same_pipeline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    assert run_scan(state, pack="_mock") == EXIT_OK
    capsys.readouterr()
    assert main(["exposure", "--state-dir", str(state)]) == EXIT_OK
    assert "MOCK EXPOSURE MAP" in capsys.readouterr().out
