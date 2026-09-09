from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hubbleops.app import cli
from hubbleops.app.verification import VerificationInvalid, VerificationRun
from hubbleops.proof import receipt
from hubbleops.proof.receipt import Receipt
from hubbleops.sandbox.verifier_image import VERIFIER_IMAGE
from tests.phase5_support import Repository, build_repository, verify, write_obligations


@pytest.fixture(scope="module")
def repository(tmp_path_factory: pytest.TempPathFactory) -> Repository:
    root = tmp_path_factory.mktemp("phase5-good")
    return build_repository(root / "repo")


@pytest.fixture(scope="module")
def obligations(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_obligations(tmp_path_factory.mktemp("phase5-obl") / "obligations.json")


@pytest.fixture(scope="module")
def good_run(repository: Repository, obligations: Path) -> VerificationRun:
    return verify(repository, obligations_path=obligations)


def test_a_correct_candidate_is_verified_for_its_scope(good_run: VerificationRun) -> None:
    assert good_run.evaluation.judgement.verdict == "VERIFIED_FOR_SCOPE"
    assert good_run.evaluation.judgement.reasons == ()
    assert good_run.evaluation.result.unresolved == ()


def test_the_frozen_baseline_ran_and_is_the_proof(good_run: VerificationRun) -> None:
    frozen = good_run.evaluation.frozen_tests
    assert frozen.source == "FROZEN_BASELINE"
    assert frozen.executed and frozen.failed == 0 and frozen.passed >= 5
    assert good_run.evaluation.candidate_tests.source == "CANDIDATE"


def test_coverage_is_execution_derived_not_name_derived(good_run: VerificationRun) -> None:
    covered = good_run.evaluation.frozen_tests.covered_files()
    assert {"reporting.py", "client.py", "billing.py"} <= covered
    per_test = {item.name: item.files for item in good_run.evaluation.frozen_tests.tests}
    spend = next(name for name in per_test if "spend_multiplies" in name)
    assert "billing.py" in per_test[spend], (
        "coverage must come from what the test executed, never from its name"
    )
    labels = next(name for name in per_test if "labels_come_back" in name)
    assert "billing.py" not in per_test[labels]


def test_every_hunk_is_accounted_for(good_run: VerificationRun) -> None:
    containment = good_run.evaluation.containment
    assert containment.mappings
    assert containment.unexplained() == ()
    assert {item.disposition for item in containment.mappings} <= {"OBLIGATION", "COLLATERAL"}


@pytest.mark.parametrize("name", ["renamed_field_still_read", "split_literal_response_read"])
def test_removed_response_reads_have_independent_defences(name: str, tmp_path: Path) -> None:
    from tests.adversarial.test_adversarial_candidates import CORRUPTIONS

    corruption = next(item for item in CORRUPTIONS if item.name == name)
    repository = build_repository(tmp_path / name, corruption.edits)
    obligations = write_obligations(tmp_path / f"{name}.json", (*corruption.obligation_sites,))
    run = verify(repository, obligations_path=obligations)
    assert run.evaluation.audit.reintroduced
    assert run.evaluation.consumers.hits


def test_the_oracle_carries_a_request_hash_and_a_timestamp(good_run: VerificationRun) -> None:
    checks = good_run.evaluation.oracle.checks
    assert checks, "a tree with a literal query must reach the oracle"
    for check in checks:
        assert len(check.request_hash) == 64
        assert check.checked_at.endswith("+00:00") or "T" in check.checked_at
        assert check.code == "VALID"


def test_an_old_version_dynamic_capture_fails_the_migration_audit(
    repository: Repository, obligations: Path, tmp_path: Path
) -> None:
    event: dict[str, Any] = {
        "version": "v1",
        "service": "MockService",
        "method": "Search",
        "request_text": "SELECT campaigns.id FROM campaigns",
        "request_type": "rest",
        "stack": [],
        "ts": "2026-09-09T00:00:00Z",
        "mode": "hook",
    }
    base_capture = tmp_path / "base.jsonl"
    candidate_capture = tmp_path / "candidate.jsonl"
    payload = json.dumps(event, sort_keys=True) + "\n"
    base_capture.write_text(payload, encoding="utf-8")
    candidate_capture.write_text(payload, encoding="utf-8")
    run = verify(
        repository,
        obligations_path=obligations,
        base_capture_path=base_capture,
        candidate_capture_path=candidate_capture,
    )
    assert run.evaluation.judgement.verdict == "FAILED"
    assert run.evaluation.audit.residue == ("captured event 1 (MockService.Search)",)


def test_the_scope_binds_the_verifier_image(good_run: VerificationRun) -> None:
    assert good_run.proof_scope["verifier_image_hash"] == VERIFIER_IMAGE.fingerprint()
    assert good_run.proof_scope["verifier_version"]


def test_the_receipt_validates_against_the_frozen_schema(good_run: VerificationRun) -> None:
    document = _receipt(good_run)
    assert document.verdict() == "VERIFIED_FOR_SCOPE"
    assert document.record["candidates_summary"]["unexplained"] == 0
    rendered = receipt.render(document)
    assert "VERDICT   VERIFIED_FOR_SCOPE" in rendered
    assert "BLAST RADIUS" in rendered and "FALSIFIERS" in rendered
    assert json.loads(document.json_bytes())["verdict"] == "VERIFIED_FOR_SCOPE"


def test_the_receipt_body_ignores_timestamps(
    good_run: VerificationRun, repository: Repository, obligations: Path
) -> None:
    again = verify(repository, obligations_path=obligations)
    first = _receipt(good_run)
    second = _receipt(again)
    assert first.body_hash() == second.body_hash(), (
        "two runs of one scope must agree on everything but the clock"
    )
    assert again.proof_scope_hash == good_run.proof_scope_hash


def test_a_receipt_is_bound_to_one_scope_and_dies_with_it(
    good_run: VerificationRun, tmp_path: Path, obligations: Path
) -> None:
    moved = build_repository(tmp_path / "moved", {"reporting.py": "MOVED = 1\n"})
    later = verify(moved, obligations_path=obligations)
    assert later.proof_scope_hash != good_run.proof_scope_hash, (
        "law L4: a new tree is a new scope, so the previous receipt cannot be presented for it"
    )
    assert _receipt(later).record["proof_scope"] != _receipt(good_run).record["proof_scope"]


def test_an_ambiguous_source_version_is_refused_rather_than_guessed(
    repository: Repository,
) -> None:
    from hubbleops.app import verification
    from tests.phase5_support import mock_pack

    with pytest.raises(VerificationInvalid) as error:
        verification.execute(
            verification.VerificationRequest(
                repository=repository.path,
                pack=mock_pack(),
                base=repository.base_sha,
                candidate=repository.candidate_sha,
                from_version=None,
                to_version="v2",
            )
        )
    assert "pass --from" in str(error.value)


def test_verifying_a_commit_against_itself_is_refused(repository: Repository) -> None:
    from hubbleops.app import verification
    from tests.phase5_support import mock_pack

    with pytest.raises(VerificationInvalid):
        verification.execute(
            verification.VerificationRequest(
                repository=repository.path,
                pack=mock_pack(),
                base=repository.candidate_sha,
                candidate=repository.candidate_sha,
                from_version="v1",
                to_version="v2",
            )
        )


def test_the_cli_writes_both_receipts_and_exits_zero(
    repository: Repository, obligations: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    code = cli.main(
        [
            "verify",
            repository.base_sha,
            repository.candidate_sha,
            "--pack",
            "_mock",
            "--repo",
            str(repository.path),
            "--from",
            "v1",
            "--to",
            "v2",
            "--obligations",
            str(obligations),
            "--state-dir",
            str(state),
        ]
    )
    assert code == 0
    output = capsys.readouterr().out
    assert "VERDICT   VERIFIED_FOR_SCOPE" in output
    written = sorted(path.name for path in state.rglob("receipt.*"))
    assert written == ["receipt.json", "receipt.md"]


def test_the_cli_exit_code_separates_failure_from_absence(
    tmp_path: Path, obligations: Path
) -> None:
    broken = build_repository(tmp_path / "broken", {"analytics.py": "STRAY = 1\n"})
    state = tmp_path / "state"
    code = cli.main(
        [
            "verify",
            broken.base_sha,
            broken.candidate_sha,
            "--pack",
            "_mock",
            "--repo",
            str(broken.path),
            "--from",
            "v1",
            "--to",
            "v2",
            "--obligations",
            str(obligations),
            "--state-dir",
            str(state),
        ]
    )
    assert code == cli.EXIT_FAILED
    failed_json = json.loads(next(state.rglob("receipt.json")).read_text(encoding="utf-8"))
    failed_markdown = next(state.rglob("receipt.md")).read_text(encoding="utf-8")
    assert failed_json["verdict"] == "FAILED"
    assert "VERDICT   FAILED" in failed_markdown
    assert "zero_unexplained_hunks is FAIL" in failed_markdown


def _receipt(run: VerificationRun) -> Receipt:
    return receipt.build(
        evaluation=run.evaluation,
        proof_scope=run.proof_scope,
        provider="_mock",
        changes_hash=run.changes_hash,
        base_sha=run.base_sha,
        candidate_sha=run.candidate_sha,
        from_version=run.from_version,
        to_version=run.to_version,
    )
