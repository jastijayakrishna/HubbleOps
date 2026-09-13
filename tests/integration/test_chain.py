from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from hubbleops.app import registry
from hubbleops.app.cli import EXIT_OK, main, scan_repository
from hubbleops.proof import guard
from hubbleops.sandbox import DetachedWorktree
from tests.phase5_support import BASE_TREE


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _repository(root: Path) -> tuple[Path, str]:
    repository = root / "repo"
    shutil.copytree(BASE_TREE, repository)
    _git(repository, "init", "--quiet", "--initial-branch=main")
    _git(repository, "config", "user.email", "fixture@hubbleops.test")
    _git(repository, "config", "user.name", "HubbleOps Fixture")
    _git(repository, "config", "commit.gpgsign", "false")
    _git(repository, "config", "core.autocrlf", "false")
    _git(repository, "add", "--all")
    _git(repository, "commit", "--quiet", "-m", "Base tree before the migration")
    return repository, _git(repository, "rev-parse", "HEAD").strip()


def test_one_commit_scanned_two_ways_is_one_proof_scope_and_one_run(tmp_path: Path) -> None:
    repository, base_sha = _repository(tmp_path)
    pack = registry.load_pack("_mock")
    working = scan_repository(repository, pack)
    with DetachedWorktree(repository, tmp_path / "detached", base_sha) as detached:
        worktree = scan_repository(detached, pack)

    assert working.identity == worktree.identity == f"commit:{base_sha}"
    assert working.proof_scope == worktree.proof_scope
    assert working.proof_scope_hash == worktree.proof_scope_hash
    assert working.run_id == worktree.run_id


def test_migrate_verify_prepare_pr_runs_end_to_end_without_hand_editing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repository, base_sha = _repository(tmp_path)
    state = tmp_path / "state"
    obligations = state / "obligations.json"
    receipt = tmp_path / "receipt.json"

    assert (
        main(["migrate", str(repository), "--pack", "_mock", "--state-dir", str(state)]) == EXIT_OK
    )
    assert obligations.is_file()
    _git(repository, "add", "--all")
    _git(repository, "commit", "--quiet", "-m", "Candidate tree the migration produced")
    candidate_sha = _git(repository, "rev-parse", "HEAD").strip()
    assert candidate_sha != base_sha

    assert (
        main(
            [
                "verify",
                base_sha,
                candidate_sha,
                "--pack",
                "_mock",
                "--repo",
                str(repository),
                "--obligations",
                str(obligations),
                "--state-dir",
                str(state),
                "--receipt",
                str(receipt),
            ]
        )
        == EXIT_OK
    )
    verdict = capsys.readouterr().out
    assert "VERIFIED_FOR_SCOPE" in verdict

    document = json.loads(receipt.read_text(encoding="utf-8"))
    assert document["verdict"] == "VERIFIED_FOR_SCOPE"
    reconciliations = document["obligations"]
    assert reconciliations
    assert {item["status"] for item in reconciliations} == {"DISCHARGED"}

    body = repository / ".hubbleops" / "artifacts" / "hubbleops-pr-body.md"
    assert (
        main(
            [
                "prepare-pr",
                str(receipt),
                "--repo",
                str(repository),
                "--obligations",
                str(obligations),
            ]
        )
        == EXIT_OK
    )
    assert body.is_file()
    assert (repository / ".hubbleops" / "retired.yml").is_file()
    assert (repository / ".github" / "workflows" / "hubbleops-verify.yml").is_file()
    assert (repository / ".github" / "workflows" / "hubbleops-guard.yml").is_file()
    prepared_output = capsys.readouterr().out
    assert "NOT RETIRED  campaigns.legacy" in prepared_output
    assert "tests/test_reporting.py" in prepared_output.replace("\\", "/")
    retired_patterns = {
        item["pattern"] for item in guard.load(repository / ".hubbleops" / "retired.yml")
    }
    assert "campaigns.legacy" not in retired_patterns
    assert main(["guard", "--repo", str(repository)]) == EXIT_OK, (
        "the tree hops prepare-pr leaves behind must pass its own Backslide Guard"
    )
    capsys.readouterr()

    tampered = tmp_path / "tampered.json"
    forged = dict(document)
    forged["migration_audit"] = {**document["migration_audit"], "passed": False}
    tampered.write_text(json.dumps(forged), encoding="utf-8")
    assert (
        main(
            [
                "prepare-pr",
                str(tampered),
                "--repo",
                str(repository),
                "--obligations",
                str(obligations),
            ]
        )
        != EXIT_OK
    )
    assert "does not follow from the conjuncts" in capsys.readouterr().err

    _git(repository, "add", "--all")
    _git(repository, "commit", "--quiet", "--allow-empty", "-m", "A commit the receipt never saw")
    assert (
        main(
            [
                "prepare-pr",
                str(receipt),
                "--repo",
                str(repository),
                "--obligations",
                str(obligations),
            ]
        )
        != EXIT_OK
    )
    assert "a new SHA kills the old proof" in capsys.readouterr().err


def test_migrate_refuses_a_working_tree_that_differs_from_head(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repository, _ = _repository(tmp_path)
    (repository / "client.py").write_text("ENDPOINT = 'dirty'\n", encoding="utf-8")

    state = tmp_path / "state"
    assert (
        main(["migrate", str(repository), "--pack", "_mock", "--state-dir", str(state)]) != EXIT_OK
    )
    assert "client.py" in capsys.readouterr().err
    assert not (state / "obligations.json").exists()
