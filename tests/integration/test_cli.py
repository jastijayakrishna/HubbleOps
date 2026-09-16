from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

import hubbleops
from hubbleops.app import cli, registry
from hubbleops.app.cli import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_TOOLING_MISSING,
    EXIT_UNKNOWN,
    OBSERVATION_SOURCES,
    main,
    scan_repository,
)
from hubbleops.core.candidate import DECISION_REASON_PREFIX, OPEN_STATUSES
from hubbleops.core.errors import ToolingTimeout
from hubbleops.core.proof_scope import scanner_fingerprint
from hubbleops.proof import guard
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
    changes = registry.load_pack("google_ads").changes
    recorded = changes.lattice_hash
    monkeypatch.setattr(type(changes), "lattice_hash", property(lambda _: "f" * 64))
    assert main(["exposure", "--state-dir", str(state)]) == EXIT_OK
    captured = capsys.readouterr()
    assert f"google_ads@{recorded[:8]}" in captured.out
    assert f"google_ads@{'f' * 8}" not in captured.out
    assert "no provider contract was supplied to this rendering" in captured.out
    assert "is not the one run" in captured.err


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


def test_a_vendored_binary_whose_hash_was_swapped_stops_the_scan_and_never_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vendored = tmp_path / "_toolchain" / "win_amd64"
    vendored.mkdir(parents=True)
    (vendored / "rg.exe").write_bytes(b"not the pinned ripgrep")
    (vendored / "manifest.json").write_text(
        json.dumps(
            {
                "platform": "win_amd64",
                "tools": {"rg": {"file": "rg.exe", "version": "14.1.1", "sha256": "0" * 64}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("hubbleops.core.toolchain.vendored_root", lambda: tmp_path / "_toolchain")
    monkeypatch.setattr("hubbleops.core.toolchain.platform_tag", lambda: "win_amd64")
    state = tmp_path / "state"
    assert run_scan(state) == EXIT_TOOLING_MISSING
    assert "vendored binary hash mismatch" in capsys.readouterr().err
    assert not (state / DATABASE_FILENAME).exists()


def test_decide_writes_an_idempotent_source_bound_human_decision(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    repository = tmp_path / "repository"
    repository.mkdir()
    assert run_scan(state, pack="_mock") == EXIT_OK
    capsys.readouterr()
    with Store(state) as store:
        run = store.latest_run("_mock")
        assert run is not None
        candidate = next(
            item for item in store.candidates_for(run.run_id) if item["status"] in OPEN_STATUSES
        )
    arguments = [
        "decide",
        str(candidate["id"])[:12],
        "--value",
        "HUMAN_ACCEPTED_RISK",
        "--by",
        "Reviewer",
        "--run",
        run.run_id,
        "--repo",
        str(repository),
        "--state-dir",
        str(state),
    ]
    assert main(arguments) == EXIT_OK
    output = capsys.readouterr().out
    destination = repository / ".hubbleops" / "decisions.yml"
    first = destination.read_bytes()
    document = yaml.safe_load(first)
    item = document["decisions"][0]
    assert item["candidate_id"] == candidate["id"]
    assert item["proof_scope_hash"] == run.proof_scope_hash
    assert item["run_id"] == run.run_id
    assert len(item["blob_hash"]) == 64
    assert "DECIDED" in output
    assert main(arguments) == EXIT_OK
    assert destination.read_bytes() == first


def test_hops_decide_moves_the_stored_candidate_out_of_unknown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    repository = tmp_path / "repository"
    repository.mkdir()
    assert run_scan(state, pack="_mock") == EXIT_OK
    capsys.readouterr()
    with Store(state) as store:
        run = store.latest_run("_mock")
        assert run is not None
        candidate = next(
            item for item in store.candidates_for(run.run_id) if item["status"] in OPEN_STATUSES
        )
    arguments = [
        "decide",
        str(candidate["id"])[:12],
        "--value",
        "HUMAN_ACCEPTED_RISK",
        "--by",
        "Reviewer",
        "--run",
        run.run_id,
        "--repo",
        str(repository),
        "--state-dir",
        str(state),
    ]

    assert main(arguments) == EXIT_OK
    capsys.readouterr()

    with Store(state) as store:
        moved = next(
            item for item in store.candidates_for(run.run_id) if item["id"] == candidate["id"]
        )
    assert moved["status"] == "HUMAN_ACCEPTED_RISK", (
        "a decision the engine accepted has to reach the ledger it adjudicates; leaving the row "
        "UNKNOWN means the next scan re-asks a question a human already answered"
    )
    assert moved["reason"].startswith(DECISION_REASON_PREFIX)
    assert moved["evidence_ids"] == candidate["evidence_ids"]
    assert moved["close_with"] is None
    document = yaml.safe_load((repository / ".hubbleops" / "decisions.yml").read_bytes())
    assert document["decisions"][0]["blob_hash"] in moved["reason"]


def test_replay_verifies_a_completed_runs_identity_and_artifacts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    assert run_scan(state, pack="_mock") == EXIT_OK
    capsys.readouterr()
    with Store(state) as store:
        run = store.latest_run("_mock")
        assert run is not None
    assert main(["replay", run.run_id, "--state-dir", str(state)]) == EXIT_OK
    output = capsys.readouterr().out
    assert "REPLAY VERIFIED" in output
    assert run.proof_scope_hash in output


def test_replay_fails_closed_when_an_artifact_was_changed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    assert run_scan(state, pack="_mock") == EXIT_OK
    capsys.readouterr()
    with Store(state) as store:
        run = store.latest_run("_mock")
        assert run is not None
        artifact = store.artifacts_for(run.run_id)[0]
    Path(artifact.path).write_bytes(b"tampered\n")
    assert main(["replay", run.run_id, "--state-dir", str(state)]) == EXIT_FAILED
    assert "no longer matches" in capsys.readouterr().err


def test_impact_is_a_full_rescan_report_without_repository_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "impact.json"
    assert (
        main(
            [
                "impact",
                str(FIXTURE),
                "--pack",
                "_mock",
                "--output",
                str(output),
            ]
        )
        == EXIT_OK
    )
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["full_rescan"] is True
    assert record["candidate_counts"]["unexplained"] == 0
    assert all(item["candidate_id"] for item in record["obligations"])
    assert "full clean rescan" in capsys.readouterr().out


def test_guard_cli_passes_then_rejects_a_retired_literal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    guard.write_retired(
        tmp_path,
        patterns=("retired.call",),
        provider="_mock",
        proof_scope_hash="a" * 64,
    )
    source = tmp_path / "app.py"
    source.write_text("active.call()\n", encoding="utf-8")
    arguments = ["guard", "--repo", str(tmp_path)]
    assert main(arguments) == EXIT_OK
    assert "PASS" in capsys.readouterr().out
    source.write_text("retired.call()\n", encoding="utf-8")
    assert main(arguments) == EXIT_FAILED
    assert "REINTRODUCED" in capsys.readouterr().out


def test_guard_with_no_retired_surface_loaded_is_not_a_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    absent = tmp_path / ".hubbleops" / "retired.yml"
    assert not absent.exists()

    assert main(["guard", "--repo", str(tmp_path)]) == EXIT_FAILED, (
        "a guard that loaded no pattern searched for nothing, so it proved nothing; "
        "reporting that as PASS is a green check standing for an unrun check"
    )
    captured = capsys.readouterr()
    assert "PASS" not in captured.out
    assert str(absent) in captured.out + captured.err


def test_guard_with_an_empty_retired_surface_is_not_a_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / ".hubbleops" / "retired.yml"
    empty.parent.mkdir(parents=True)
    empty.write_text(yaml.safe_dump({"schema_version": 1, "entries": []}), encoding="utf-8")

    assert main(["guard", "--repo", str(tmp_path)]) == EXIT_FAILED
    captured = capsys.readouterr()
    assert "PASS" not in captured.out
    assert str(empty) in captured.out + captured.err


def test_guard_names_the_retired_path_it_was_pointed_at_when_it_holds_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    guard.write_retired(
        tmp_path,
        patterns=("retired.call",),
        provider="_mock",
        proof_scope_hash="a" * 64,
    )
    typo = tmp_path / ".hubbleops" / "retired.yaml"

    assert main(["guard", "--repo", str(tmp_path), "--retired", str(typo)]) == EXIT_FAILED, (
        "a mistyped --retired and a checkout that never committed retired.yml are the same "
        "silence, and the guard has to say which file it read"
    )
    captured = capsys.readouterr()
    assert "PASS" not in captured.out
    assert str(typo.resolve()) in captured.out + captured.err


def test_exposure_installs_a_read_only_pull_request_workflow_without_a_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments = [
        "exposure",
        "--install-workflow",
        "--pack",
        "_mock",
        "--target",
        "v2",
        "--repo",
        str(tmp_path),
        "--state-dir",
        str(tmp_path / "state"),
    ]
    assert main(arguments) == EXIT_OK
    assert "EXPOSURE WORKFLOW" in capsys.readouterr().out
    workflow = (tmp_path / ".github" / "workflows" / "hubbleops-exposure.yml").read_text(
        encoding="utf-8"
    )
    assert "permissions:\n  contents: read\n" in workflow
    assert "hops scan . --pack _mock --target v2" in workflow
    assert 'uvx --from "hubbleops==' in workflow


def test_exposure_workflow_install_refuses_a_missing_pack(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["exposure", "--install-workflow", "--repo", str(tmp_path)]) == EXIT_FAILED
    assert "--pack" in capsys.readouterr().err
