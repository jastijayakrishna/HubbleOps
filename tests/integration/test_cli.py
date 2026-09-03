from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import hubbleops
from hubbleops.app import cli, registry
from hubbleops.app.cli import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_UNKNOWN,
    OBSERVATION_SOURCES,
    main,
    scan_repository,
)
from hubbleops.core.errors import ToolingTimeout
from hubbleops.core.proof_scope import scanner_fingerprint
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


def test_the_scanner_version_binds_the_observation_pipeline_that_ran() -> None:
    result = scan_repository(FIXTURE, registry.load_pack("google_ads"))
    assert scanner_fingerprint(OBSERVATION_SOURCES) in result.scanner_version
    assert result.proof_scope["scanner_version"] == result.scanner_version


def test_the_scanner_version_binds_the_module_that_chooses_the_observers() -> None:
    assert Path(cli.__file__) in OBSERVATION_SOURCES


def test_the_scanner_version_binds_every_module_that_shapes_a_record() -> None:
    root = Path(hubbleops.__file__).resolve().parent
    uncovered = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if path not in set(OBSERVATION_SOURCES)
    )
    assert uncovered == [], (
        "these modules can change what a scan finds without moving the proof key, "
        "so two disagreeing builds would share one"
    )


def test_exposure_reports_the_surface_the_run_recorded_not_the_one_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    run_scan(state)
    capsys.readouterr()
    recorded = registry.load_pack("google_ads").surface.surface_hash()

    def refuse(name: str) -> registry.LoadedPack:
        raise AssertionError("the map must render from the stored run, not the pack on disk")

    monkeypatch.setattr(registry, "load_pack", refuse)
    assert main(["exposure", "--state-dir", str(state)]) == EXIT_OK
    assert recorded[:8] in capsys.readouterr().out


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


def test_a_tool_timeout_is_reported_as_unknown_not_as_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def timeout(*_: object, **__: object) -> None:
        raise ToolingTimeout("rg", 600.0)

    monkeypatch.setattr("hubbleops.app.cli.scan_repository", timeout)
    assert run_scan(tmp_path / "state") == EXIT_UNKNOWN
    assert "UNKNOWN: rg exceeded its 600s budget" in capsys.readouterr().err
