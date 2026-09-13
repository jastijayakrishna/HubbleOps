from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hubbleops.app import cli
from hubbleops.app.decision import DecisionInvalid
from hubbleops.app.verification import VerificationInvalid, VerificationRun
from hubbleops.core.canonical import content_id
from hubbleops.observe.dynamic.runner import event_schema_hash
from hubbleops.proof import guard, pr_body, receipt
from hubbleops.proof.receipt import Receipt
from hubbleops.sandbox.verifier_image import VERIFIER_IMAGE
from tests.phase5_support import (
    Repository,
    build_repository,
    verify,
    write_decision,
    write_obligations,
)


@pytest.fixture(scope="module")
def repository(tmp_path_factory: pytest.TempPathFactory) -> Repository:
    root = tmp_path_factory.mktemp("phase5-good")
    return build_repository(root / "repo")


@pytest.fixture(scope="module")
def obligations(tmp_path_factory: pytest.TempPathFactory, repository: Repository) -> Path:
    return write_obligations(tmp_path_factory.mktemp("phase5-obl") / "obligations.json", repository)


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
    obligations = write_obligations(
        tmp_path / f"{name}.json", repository, (*corruption.obligation_sites,)
    )
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
    repository: Repository,
    obligations: Path,
    good_run: VerificationRun,
    tmp_path: Path,
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
    base_capture = tmp_path / "base" / "events.jsonl"
    candidate_capture = tmp_path / "candidate" / "events.jsonl"
    base_capture.parent.mkdir()
    candidate_capture.parent.mkdir()
    payload = json.dumps(event, sort_keys=True) + "\n"
    base_capture.write_text(payload, encoding="utf-8")
    candidate_capture.write_text(payload, encoding="utf-8")
    for path, sha in (
        (base_capture, repository.base_sha),
        (candidate_capture, repository.candidate_sha),
    ):
        path.with_name("execution-manifest.json").write_text(
            json.dumps(
                {
                    "event_schema_sha256": event_schema_hash(),
                    "events_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "execution_spec": "phase5-fixture",
                    "provider": "_mock",
                    "repo_sha": sha,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    run = verify(
        repository,
        obligations_path=obligations,
        base_capture_path=base_capture,
        candidate_capture_path=candidate_capture,
    )
    assert run.evaluation.judgement.verdict == "FAILED"
    assert run.evaluation.audit.residue == ("captured event 1 (MockService.Search)",)
    assert run.proof_scope_hash != good_run.proof_scope_hash


def test_the_scope_binds_the_verifier_image(good_run: VerificationRun) -> None:
    assert good_run.proof_scope["verifier_image_hash"] == VERIFIER_IMAGE.fingerprint()
    assert good_run.proof_scope["verifier_version"]
    assert len(good_run.proof_scope["verification_inputs_hash"]) == 64
    assert len(good_run.proof_scope["oracle_context_hash"]) == 64


def test_the_receipt_validates_against_the_frozen_schema(good_run: VerificationRun) -> None:
    document = _receipt(good_run)
    assert document.verdict() == "VERIFIED_FOR_SCOPE"
    assert document.record["candidates_summary"]["unexplained"] == 0
    rendered = receipt.render(document)
    assert "VERDICT   VERIFIED_FOR_SCOPE" in rendered
    assert "BLAST RADIUS" in rendered and "FALSIFIERS" in rendered
    assert json.loads(document.json_bytes())["verdict"] == "VERIFIED_FOR_SCOPE"


def test_the_receipt_states_the_packs_own_catalog_scope_and_asserts_none_of_its_own(
    good_run: VerificationRun,
) -> None:
    document = _receipt(good_run)
    assert document.record["oracle_authority"] == "CATALOG"
    rendered = receipt.render(document)
    catalog_reasons = {
        item["reason"]
        for item in document.record["oracle_results"]
        if item["code"] == "VALID" and item.get("authority") == "CATALOG"
    }
    assert catalog_reasons
    for reason in catalog_reasons:
        assert reason in rendered, (
            "the receipt must state the scope the accepting pack recorded, not a scope the "
            f"generic layer invented; {reason!r} is missing"
        )
    assert "mutate" not in rendered.lower(), (
        "proof/ must not assert what a provider's catalog authority does or does not cover; "
        "that claim drifts out of date the moment the pack learns a new check"
    )


def test_pr_materials_preserve_memory_and_bind_actions_to_the_candidate(
    good_run: VerificationRun,
    repository: Repository,
    obligations: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "candidate"
    subprocess.run(
        ["git", "clone", "--quiet", str(repository.path), str(root)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "checkout", "--quiet", good_run.candidate_sha],
        check=True,
        capture_output=True,
    )
    state = root / ".hubbleops"
    state.mkdir(parents=True)
    state.joinpath("obligations.json").write_bytes(obligations.read_bytes())
    document = receipt.build(
        evaluation=good_run.evaluation,
        proof_scope=good_run.proof_scope,
        provider="_mock",
        changes_hash=good_run.changes_hash,
        base_sha=good_run.base_sha,
        candidate_sha=good_run.candidate_sha,
        from_version=good_run.from_version,
        to_version=good_run.to_version,
        retired_patterns=("v1", "campaigns.legacy"),
    )
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_bytes(document.json_bytes())
    body = tmp_path / "pr-body.md"
    assert (
        cli.main(
            [
                "prepare-pr",
                str(receipt_path),
                "--repo",
                str(root),
                "--body",
                str(body),
            ]
        )
        == cli.EXIT_OK
    )
    prepared = capsys.readouterr().out
    assert "PR MATERIALS PREPARED" in prepared
    assert "NOT RETIRED  campaigns.legacy" in prepared
    assert body.read_text(encoding="utf-8") == pr_body.render(document)
    assert {"surface.yml", "bindings.json", "decisions.yml", "retired.yml"} <= {
        path.name for path in state.iterdir()
    }
    assert {item["pattern"] for item in guard.load(state / "retired.yml")} == {"v1"}
    assert cli.main(["guard", "--repo", str(root)]) == cli.EXIT_OK
    workflow_text = root.joinpath(".github", "workflows", "hubbleops-verify.yml").read_text(
        encoding="utf-8"
    )
    assert "GITHUB_SHA" in workflow_text
    assert "--pack _mock" in workflow_text


def test_a_forged_candidate_sha_cannot_present_a_dead_receipt_for_a_new_tree(
    good_run: VerificationRun,
    repository: Repository,
    obligations: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "forged"
    subprocess.run(
        ["git", "clone", "--quiet", str(repository.path), str(root)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "checkout", "--quiet", good_run.candidate_sha],
        check=True,
        capture_output=True,
    )
    state = root / ".hubbleops"
    state.mkdir(parents=True)
    state.joinpath("obligations.json").write_bytes(obligations.read_bytes())
    document = receipt.build(
        evaluation=good_run.evaluation,
        proof_scope=good_run.proof_scope,
        provider="_mock",
        changes_hash=good_run.changes_hash,
        base_sha=good_run.base_sha,
        candidate_sha=good_run.candidate_sha,
        from_version=good_run.from_version,
        to_version=good_run.to_version,
        retired_patterns=("v1", "campaigns.legacy"),
    )
    root.joinpath("reporting.py").write_text("MOVED = 1\n", encoding="utf-8")
    for arguments in (
        ["add", "reporting.py"],
        [
            "-c",
            "user.email=fixture@hubbleops.test",
            "-c",
            "user.name=HubbleOps Fixture",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "-m",
            "Move the tree on past the one that was verified",
        ],
    ):
        subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True)
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    forged = json.loads(document.json_bytes())
    forged["migration_audit"]["candidate_sha"] = head
    assert forged["proof_scope"]["repo_sha"] == good_run.candidate_sha != head
    receipt_path = tmp_path / "forged-receipt.json"
    receipt_path.write_text(json.dumps(forged), encoding="utf-8")
    body = tmp_path / "forged-body.md"

    assert (
        cli.main(["prepare-pr", str(receipt_path), "--repo", str(root), "--body", str(body)])
        != cli.EXIT_OK
    ), "law L4: a receipt whose ProofScope names another tree cannot prepare a pull request"
    printed = capsys.readouterr()
    assert "ProofScope" in printed.out + printed.err
    assert not body.exists()


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
    with pytest.raises(VerificationInvalid, match="not bound to the base ProofScope"):
        verify(moved, obligations_path=obligations)
    moved_obligations = write_obligations(tmp_path / "moved-obligations.json", moved)
    later = verify(moved, obligations_path=moved_obligations)
    assert later.proof_scope_hash != good_run.proof_scope_hash, (
        "law L4: a new tree is a new scope, so the previous receipt cannot be presented for it"
    )
    assert _receipt(later).record["proof_scope"] != _receipt(good_run).record["proof_scope"]


def test_a_recorded_human_decision_closes_an_unknown_through_verify(
    repository: Repository, obligations: Path, tmp_path: Path
) -> None:
    decisions, candidate_id = write_decision(
        tmp_path / "decisions.json", repository, "surface_reference", "billing.py"
    )
    run = verify(repository, obligations_path=obligations, decisions_path=decisions)
    closed = {item.candidate_id: item for item in run.evaluation.conservation.closures}
    assert candidate_id in closed, (
        "law L3: a recorded human decision is one of the two ways an UNKNOWN may close, "
        "so it has to survive the base scan it was recorded against into the candidate judgement"
    )
    assert "recorded human decision" in closed[candidate_id].justification
    assert closed[candidate_id] not in run.evaluation.conservation.violations
    assert run.evaluation.judgement.verdict == "VERIFIED_FOR_SCOPE"


def test_a_decision_dies_when_the_source_it_was_made_about_changes(
    repository: Repository, obligations: Path, tmp_path: Path
) -> None:
    decisions, _ = write_decision(
        tmp_path / "decisions.json", repository, "surface_reference", "client.py"
    )
    with pytest.raises(DecisionInvalid, match="not keyed to evidence"):
        verify(repository, obligations_path=obligations, decisions_path=decisions)


def test_a_decision_recorded_against_another_run_never_reaches_the_candidate(
    repository: Repository, obligations: Path, tmp_path: Path
) -> None:
    decisions, _ = write_decision(
        tmp_path / "decisions.json", repository, "surface_reference", "billing.py"
    )
    records: list[dict[str, Any]] = json.loads(decisions.read_text(encoding="utf-8"))
    forged = dict(records[0])
    forged["run_id"] = "0" * 64
    forged["id"] = content_id({key: forged[key] for key in forged if key != "id"})
    decisions.write_text(json.dumps([forged], indent=2, sort_keys=True), encoding="utf-8")
    with pytest.raises(DecisionInvalid, match="different ProofScope or run"):
        verify(repository, obligations_path=obligations, decisions_path=decisions)


def test_an_ambiguous_source_version_is_refused_rather_than_guessed(tmp_path: Path) -> None:
    from hubbleops.app import verification
    from tests.phase5_support import git, mock_pack

    mixed = build_repository(tmp_path / "mixed")
    (mixed.path / "legacy.py").write_text(
        'LEGACY = "https://api.mockprov.test/v1/campaigns:search"\n', encoding="utf-8"
    )
    git(mixed.path, "add", "--all")
    git(mixed.path, "commit", "--quiet", "-m", "Keep one v1 endpoint beside the v2 tree")
    mixed_sha = git(mixed.path, "rev-parse", "HEAD").strip()

    with pytest.raises(VerificationInvalid) as error:
        verification.execute(
            verification.VerificationRequest(
                repository=mixed.path,
                pack=mock_pack(),
                base=mixed_sha,
                candidate=mixed.candidate_sha,
                from_version=None,
                to_version="v2",
            )
        )
    assert "pass --from" in str(error.value)
    assert "v1, v2" in str(error.value)


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


def test_the_cli_exit_code_separates_failure_from_absence(tmp_path: Path) -> None:
    broken = build_repository(tmp_path / "broken", {"analytics.py": "STRAY = 1\n"})
    broken_obligations = write_obligations(tmp_path / "broken-obligations.json", broken)
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
            str(broken_obligations),
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
