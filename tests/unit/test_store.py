from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from hubbleops.core.candidate import (
    DECISION_REASON_PREFIX,
    candidate_identity,
    make_candidate,
)
from hubbleops.core.canonical import EMPTY_SHA256, content_id
from hubbleops.core.errors import (
    AiEvidenceAlone,
    CandidateIdentityMismatch,
    DecisionNotRecorded,
    EvidenceContextMismatch,
    EvidenceIdentityMismatch,
    EvidenceNotFound,
    ProofScopeMismatch,
    ProvenanceDropped,
    RunIdentityMismatch,
    RunNotFound,
    RunProviderMismatch,
    StoreSchemaMismatch,
    UnexplainedCandidates,
    UnknownNotConserved,
)
from hubbleops.core.evidence import evidence_identity, make_evidence
from hubbleops.core.proof_scope import make_proof_scope, proof_scope_hash, run_id_for
from hubbleops.store.artifacts import write_atomic
from hubbleops.store.sqlite import BUSY_TIMEOUT_MILLISECONDS, Store

EXPECTED_TABLES = {"runs", "evidence", "candidates", "obligations", "checks", "artifacts"}


def seeded(directory: Path) -> tuple[Store, str, str]:
    scope = make_proof_scope(
        repo_sha=None,
        tree_hash=EMPTY_SHA256,
        dependency_resolution_hash=None,
        scanner_version="test",
    )
    scope_hash = proof_scope_hash(scope)
    run_id = run_id_for(scope_hash=scope_hash, provider="p", verb="scan", target="target")
    store = Store(directory)
    store.start_run(
        run_id=run_id,
        proof_scope=scope,
        proof_scope_hash=scope_hash,
        provider="p",
        verb="scan",
        target="target",
        closure_summary={"entries": 0},
        started_at="2026-01-01T00:00:00+00:00",
    )
    record = make_evidence(
        run_id=run_id,
        proof_scope_hash=scope_hash,
        claim_type="call_version",
        observer="text",
        repo_sha=None,
        path="src/app.py",
        line_start=1,
        line_end=1,
        source_hash=EMPTY_SHA256,
        value={"pattern": "c", "slot": "per_call"},
        provider_subject="v22",
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="RAW",
    )
    store.write_evidence([record])
    store.write_candidates(
        [
            make_candidate(
                candidate_id=candidate_identity("p", "call_version", "src/app.py:1"),
                run_id=run_id,
                proof_scope_hash=scope_hash,
                provider="p",
                evidence_ids=[record["id"]],
                status="AFFECTED",
                reason="explicit literal",
                close_with=None,
            )
        ]
    )
    return store, run_id, scope_hash


def test_write_ahead_logging_and_foreign_keys_are_on(tmp_path: Path) -> None:
    store = Store(tmp_path)
    assert store.connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert store.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    store.close()


def test_a_reader_waits_for_the_writer_instead_of_failing(tmp_path: Path) -> None:
    store = Store(tmp_path)
    assert (
        store.connection.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MILLISECONDS
    )
    assert store.connection.execute("PRAGMA synchronous").fetchone()[0] == 2
    store.close()


def test_the_six_tables_exist(tmp_path: Path) -> None:
    store = Store(tmp_path)
    rows = store.connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert EXPECTED_TABLES <= {row["name"] for row in rows}
    store.close()


def test_every_row_carries_the_run_and_the_proof_scope(tmp_path: Path) -> None:
    store, run_id, scope_hash = seeded(tmp_path)
    for table in ("runs", "evidence", "candidates"):
        for row in store.connection.execute(f"SELECT * FROM {table}").fetchall():
            assert row["run_id"] == run_id
            assert row["proof_scope_hash"] == scope_hash
    store.close()


def test_records_round_trip_unchanged(tmp_path: Path) -> None:
    store, run_id, _ = seeded(tmp_path)
    assert len(store.evidence_for(run_id)) == 1
    assert len(store.candidates_for(run_id)) == 1
    assert store.candidates_for(run_id)[0]["status"] == "AFFECTED"
    store.close()


def test_rerunning_an_identical_scan_is_idempotent(tmp_path: Path) -> None:
    store, run_id, _ = seeded(tmp_path)
    store.close()
    store, same_run, _ = seeded(tmp_path)
    assert same_run == run_id
    assert len(store.evidence_for(run_id)) == 1
    stored = store.run(run_id)
    assert stored is not None
    assert len(stored.proof_scope) == 12
    store.close()


def test_the_latest_run_is_findable_by_pack(tmp_path: Path) -> None:
    store, run_id, _ = seeded(tmp_path)
    latest = store.latest_run("p")
    assert latest is not None
    assert latest.run_id == run_id
    assert store.latest_run("other") is None
    store.close()


def test_artifacts_are_written_atomically(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "ledger.json"
    digest = write_atomic(target, b"{}\n")
    assert target.read_bytes() == b"{}\n"
    assert len(digest) == 64
    assert list(tmp_path.rglob("*.tmp")) == []


def test_an_artifact_row_records_its_digest(tmp_path: Path) -> None:
    store, run_id, scope_hash = seeded(tmp_path)
    store.write_artifact(
        run_id=run_id,
        proof_scope_hash=scope_hash,
        kind="ledger",
        path="ledger.json",
        sha256=EMPTY_SHA256,
        size=0,
    )
    row = store.connection.execute("SELECT * FROM artifacts").fetchone()
    assert row["sha256"] == EMPTY_SHA256
    assert row["run_id"] == run_id
    artifacts = store.artifacts_for(run_id)
    assert len(artifacts) == 1
    assert artifacts[0].proof_scope_hash == scope_hash
    store.close()


def evidence_at(
    run_id: str,
    scope_hash: str,
    path: str,
    derivation: str = "OBSERVED",
    observer: str = "text",
) -> dict[str, Any]:
    return make_evidence(
        run_id=run_id,
        proof_scope_hash=scope_hash,
        claim_type="call_version",
        observer=observer,
        repo_sha=None,
        path=path,
        line_start=1,
        line_end=1,
        source_hash=EMPTY_SHA256,
        value={"pattern": "c", "slot": "per_call"},
        provider_subject="v22",
        dependency_context_hash=None,
        derivation=derivation,
        confidence="RAW",
    )


def open_candidate(
    run_id: str,
    scope_hash: str,
    evidence_ids: list[str],
    status: str,
    close_with: str | None,
    reason: str = "the version at this call site is not statically resolvable",
) -> dict[str, Any]:
    return make_candidate(
        candidate_id=candidate_identity("p", "call_version", "src/open.py:1"),
        run_id=run_id,
        proof_scope_hash=scope_hash,
        provider="p",
        evidence_ids=evidence_ids,
        status=status,
        reason=reason,
        close_with=close_with,
    )


def decision_for(
    run_id: str,
    scope_hash: str,
    value: str = "HUMAN_ACCEPTED_RISK",
    blob_hash: str = EMPTY_SHA256,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "run_id": run_id,
        "proof_scope_hash": scope_hash,
        "candidate_id": candidate_identity("p", "call_version", "src/open.py:1"),
        "blob_hash": blob_hash,
        "path": "src/open.py",
        "line_start": 1,
        "claim_type": "call_version",
        "value": value,
        "by": "Reviewer",
    }
    return {"id": content_id(body), **body}


def decided_candidate(
    run_id: str,
    scope_hash: str,
    evidence_ids: list[str],
    item: dict[str, Any],
) -> dict[str, Any]:
    return open_candidate(
        run_id,
        scope_hash,
        evidence_ids,
        str(item["value"]),
        None,
        reason=(
            f"{DECISION_REASON_PREFIX}{item['id']} by {item['by']} "
            f"for source blob {item['blob_hash']}"
        ),
    )


def held_status(store: Store, run_id: str, candidate_id: str) -> str:
    found = [item for item in store.candidates_for(run_id) if item["id"] == candidate_id]
    return str(found[0]["status"])


def seeded_unknown(tmp_path: Path) -> tuple[Store, str, str, dict[str, Any]]:
    store, run_id, scope_hash = seeded(tmp_path)
    first = evidence_at(run_id, scope_hash, "src/open.py")
    store.write_evidence([first])
    store.write_candidates(
        [open_candidate(run_id, scope_hash, [first["id"]], "UNKNOWN", "capture the call")]
    )
    return store, run_id, scope_hash, first


def test_an_unknown_does_not_close_without_new_evidence(tmp_path: Path) -> None:
    store, run_id, scope_hash, first = seeded_unknown(tmp_path)
    closing = open_candidate(run_id, scope_hash, [first["id"]], "NOT_AFFECTED_WITH_EVIDENCE", None)
    with pytest.raises(UnknownNotConserved):
        store.write_candidates([closing])
    assert held_status(store, run_id, closing["id"]) == "UNKNOWN"
    store.close()


def test_an_unknown_does_not_close_by_being_called_affected(tmp_path: Path) -> None:
    store, run_id, scope_hash, first = seeded_unknown(tmp_path)
    with pytest.raises(UnknownNotConserved):
        store.write_candidates(
            [open_candidate(run_id, scope_hash, [first["id"]], "AFFECTED", None)]
        )
    store.close()


def test_ai_evidence_alone_does_not_close_an_unknown(tmp_path: Path) -> None:
    store, run_id, scope_hash, first = seeded_unknown(tmp_path)
    derived = evidence_at(run_id, scope_hash, "src/open.py", derivation="DERIVED_AI_EVIDENCE")
    store.write_evidence([derived])
    closing = open_candidate(run_id, scope_hash, [first["id"], derived["id"]], "AFFECTED", None)

    with pytest.raises(AiEvidenceAlone):
        store.write_candidates([closing])
    assert held_status(store, run_id, closing["id"]) == "UNKNOWN"
    store.close()


def test_evidence_whose_id_is_not_its_content_is_refused(tmp_path: Path) -> None:
    store, run_id, scope_hash = seeded(tmp_path)
    forged = {**evidence_at(run_id, scope_hash, "src/forged.py"), "id": EMPTY_SHA256}

    with pytest.raises(EvidenceIdentityMismatch):
        store.write_evidence([forged])
    assert [item for item in store.evidence_for(run_id) if item["id"] == EMPTY_SHA256] == []
    store.close()


def test_stored_evidence_cannot_be_relabelled_under_its_own_id(tmp_path: Path) -> None:
    store, run_id, scope_hash, _ = seeded_unknown(tmp_path)
    derived = evidence_at(run_id, scope_hash, "src/guess.py", derivation="DERIVED_AI_EVIDENCE")
    store.write_evidence([derived])
    relabelled = {**derived, "derivation": "OBSERVED"}

    with pytest.raises(EvidenceIdentityMismatch):
        store.write_evidence([relabelled])
    held = [item for item in store.evidence_for(run_id) if item["id"] == derived["id"]]
    assert held == []
    store.close()


def test_ai_evidence_beside_an_observation_closes_an_unknown(tmp_path: Path) -> None:
    store, run_id, scope_hash, first = seeded_unknown(tmp_path)
    derived = evidence_at(run_id, scope_hash, "src/open.py", derivation="DERIVED_AI_EVIDENCE")
    observed = evidence_at(run_id, scope_hash, "src/open.py", observer="dynamic")
    store.write_evidence([derived, observed])
    closing = open_candidate(
        run_id, scope_hash, [first["id"], derived["id"], observed["id"]], "AFFECTED", None
    )

    store.write_candidates([closing])
    assert held_status(store, run_id, closing["id"]) == "AFFECTED"
    store.close()


def test_an_unknown_does_not_close_inside_the_batch_that_opens_it(tmp_path: Path) -> None:
    store, run_id, scope_hash = seeded(tmp_path)
    first = evidence_at(run_id, scope_hash, "src/open.py")
    store.write_evidence([first])
    opening = open_candidate(run_id, scope_hash, [first["id"]], "UNKNOWN", "capture the call")
    closing = open_candidate(run_id, scope_hash, [first["id"]], "AFFECTED", None)

    with pytest.raises(UnknownNotConserved):
        store.write_candidates([opening, closing])
    assert [item for item in store.candidates_for(run_id) if item["id"] == opening["id"]] == []
    store.close()


def test_an_unknown_closes_when_new_evidence_arrives(tmp_path: Path) -> None:
    store, run_id, scope_hash, first = seeded_unknown(tmp_path)
    second = evidence_at(run_id, scope_hash, "src/open.py", observer="dynamic")
    store.write_evidence([second])
    closing = open_candidate(run_id, scope_hash, [first["id"], second["id"]], "AFFECTED", None)
    store.write_candidates([closing])
    assert held_status(store, run_id, closing["id"]) == "AFFECTED"
    store.close()


def test_a_recorded_human_decision_moves_the_stored_candidate_row(tmp_path: Path) -> None:
    store, run_id, scope_hash, first = seeded_unknown(tmp_path)
    item = decision_for(run_id, scope_hash)
    closing = decided_candidate(run_id, scope_hash, [first["id"]], item)

    store.write_candidates([closing], decisions=(item,))

    assert held_status(store, run_id, closing["id"]) == "HUMAN_ACCEPTED_RISK", (
        "law L3: a recorded human decision is one of the two ways an UNKNOWN closes, so the "
        "persisted row moves rather than staying open behind a decisions file"
    )
    store.close()


def test_a_decided_status_without_its_adjudication_record_never_closes(tmp_path: Path) -> None:
    store, run_id, scope_hash, first = seeded_unknown(tmp_path)
    item = decision_for(run_id, scope_hash)
    forged = decided_candidate(run_id, scope_hash, [first["id"]], item)

    with pytest.raises(UnknownNotConserved):
        store.write_candidates([forged])

    assert held_status(store, run_id, forged["id"]) == "UNKNOWN", (
        "law L3: a reason that claims a decision is not a decision; only the adjudication "
        "record handed to the store closes an UNKNOWN"
    )
    store.close()


def test_a_decision_bound_to_another_blob_never_closes_the_candidate(tmp_path: Path) -> None:
    store, run_id, scope_hash, first = seeded_unknown(tmp_path)
    drifted = decision_for(run_id, scope_hash, blob_hash="b" * 64)
    closing = decided_candidate(run_id, scope_hash, [first["id"]], drifted)

    with pytest.raises(DecisionNotRecorded):
        store.write_candidates([closing], decisions=(drifted,))

    assert held_status(store, run_id, closing["id"]) == "UNKNOWN", (
        "FA-031: a decision made about one source blob cannot close a candidate whose evidence "
        "carries a different one"
    )
    store.close()


def test_a_decision_disagreeing_with_the_status_it_licenses_never_closes(tmp_path: Path) -> None:
    store, run_id, scope_hash, first = seeded_unknown(tmp_path)
    item = decision_for(run_id, scope_hash, value="AFFECTED")
    mismatched = open_candidate(
        run_id,
        scope_hash,
        [first["id"]],
        "HUMAN_ACCEPTED_RISK",
        None,
        reason=(
            f"{DECISION_REASON_PREFIX}{item['id']} by {item['by']} "
            f"for source blob {item['blob_hash']}"
        ),
    )

    with pytest.raises(DecisionNotRecorded):
        store.write_candidates([mismatched], decisions=(item,))

    assert held_status(store, run_id, mismatched["id"]) == "UNKNOWN"
    store.close()


def test_a_candidate_write_can_never_drop_evidence(tmp_path: Path) -> None:
    store, run_id, scope_hash, _ = seeded_unknown(tmp_path)
    second = evidence_at(run_id, scope_hash, "src/open.py", observer="dynamic")
    store.write_evidence([second])
    with pytest.raises(ProvenanceDropped):
        store.write_candidates(
            [open_candidate(run_id, scope_hash, [second["id"]], "UNKNOWN", "capture the call")]
        )
    store.close()


def test_the_status_column_refuses_a_status_outside_the_frozen_set(tmp_path: Path) -> None:
    store, run_id, scope_hash = seeded(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute(
            "INSERT INTO candidates (id, run_id, proof_scope_hash, provider, status, "
            "record_json) VALUES (?, ?, ?, ?, ?, ?)",
            (content_id({"raw": 1}), run_id, scope_hash, "p", "UNEXPLAINED", "{}"),
        )
    store.close()


def test_a_record_from_another_proof_scope_is_refused(tmp_path: Path) -> None:
    store, run_id, _ = seeded(tmp_path)
    other = content_id({"scope": "other"})
    with pytest.raises(ProofScopeMismatch):
        store.write_evidence([evidence_at(run_id, other, "src/elsewhere.py")])
    with pytest.raises(ProofScopeMismatch):
        store.write_candidates(
            [open_candidate(run_id, other, [EMPTY_SHA256], "UNKNOWN", "capture the call")]
        )
    with pytest.raises(ProofScopeMismatch):
        store.write_artifact(
            run_id=run_id,
            proof_scope_hash=other,
            kind="ledger",
            path="ledger.json",
            sha256=EMPTY_SHA256,
            size=0,
        )
    store.close()


def test_start_run_refuses_a_hash_that_is_not_the_offered_proof_scope(tmp_path: Path) -> None:
    scope = make_proof_scope(
        repo_sha="1" * 40,
        tree_hash=EMPTY_SHA256,
        dependency_resolution_hash=None,
        scanner_version="test",
    )
    derived = proof_scope_hash(scope)
    store = Store(tmp_path)
    with pytest.raises(ProofScopeMismatch):
        store.start_run(
            run_id=content_id({"run": derived}),
            proof_scope=scope,
            proof_scope_hash="f" * 64,
            provider="p",
            verb="scan",
            target="target",
            closure_summary={"entries": 0},
            started_at="2026-01-01T00:00:00+00:00",
        )
    assert store.latest_run() is None
    store.close()


def test_start_run_refuses_a_caller_selected_run_id(tmp_path: Path) -> None:
    scope = make_proof_scope(
        repo_sha=None,
        tree_hash=EMPTY_SHA256,
        dependency_resolution_hash=None,
        scanner_version="test",
    )
    scope_hash = proof_scope_hash(scope)
    store = Store(tmp_path)
    with pytest.raises(RunIdentityMismatch):
        store.start_run(
            run_id=content_id({"not": "the run identity"}),
            proof_scope=scope,
            proof_scope_hash=scope_hash,
            provider="p",
            verb="scan",
            target="target",
            closure_summary={"entries": 0},
            started_at="2026-01-01T00:00:00+00:00",
        )
    assert store.latest_run() is None
    store.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repo_sha", "1" * 40),
        ("dependency_context_hash", "2" * 64),
    ],
)
def test_evidence_context_must_match_its_run_proof_scope(
    tmp_path: Path, field: str, value: str
) -> None:
    store, run_id, scope_hash = seeded(tmp_path)
    offered = {**evidence_at(run_id, scope_hash, "src/context.py"), field: value}
    offered = {**offered, "id": evidence_identity(offered)}
    with pytest.raises(EvidenceContextMismatch):
        store.write_evidence([offered])
    store.close()


def test_candidate_provider_must_match_its_run(tmp_path: Path) -> None:
    store, run_id, scope_hash = seeded(tmp_path)
    evidence = evidence_at(run_id, scope_hash, "src/provider.py")
    store.write_evidence([evidence])
    offered = make_candidate(
        candidate_id=candidate_identity("other", "call_version", "src/provider.py:1"),
        run_id=run_id,
        proof_scope_hash=scope_hash,
        provider="other",
        evidence_ids=[evidence["id"]],
        status="AFFECTED",
        reason="wrong provider",
        close_with=None,
    )
    with pytest.raises(RunProviderMismatch):
        store.write_candidates([offered])
    store.close()


def test_evidence_cannot_be_staged_before_its_run_exists(tmp_path: Path) -> None:
    scope = make_proof_scope(
        repo_sha=None,
        tree_hash=EMPTY_SHA256,
        dependency_resolution_hash=None,
        scanner_version="test",
    )
    scope_hash = proof_scope_hash(scope)
    run_id = run_id_for(scope_hash=scope_hash, provider="p", verb="scan", target="target")
    store = Store(tmp_path)
    with pytest.raises(RunNotFound):
        store.write_evidence([evidence_at(run_id, scope_hash, "src/early.py")])
    store.start_run(
        run_id=run_id,
        proof_scope=scope,
        proof_scope_hash=scope_hash,
        provider="p",
        verb="scan",
        target="target",
        closure_summary={"entries": 0},
        started_at="2026-01-01T00:00:00+00:00",
    )
    assert store.evidence_for(run_id) == ()
    store.close()


def test_candidate_id_must_match_its_attached_evidence_identity(tmp_path: Path) -> None:
    store, run_id, scope_hash = seeded(tmp_path)
    evidence = evidence_at(run_id, scope_hash, "src/forged.py")
    store.write_evidence([evidence])
    forged = make_candidate(
        candidate_id="f" * 64,
        run_id=run_id,
        proof_scope_hash=scope_hash,
        provider="p",
        evidence_ids=[evidence["id"]],
        status="AFFECTED",
        reason="forged identity",
        close_with=None,
    )
    with pytest.raises(CandidateIdentityMismatch):
        store.write_candidates([forged])
    assert all(item["id"] != forged["id"] for item in store.candidates_for(run_id))
    store.close()


def test_a_database_written_by_another_store_schema_is_refused(tmp_path: Path) -> None:
    store, _, _ = seeded(tmp_path)
    store.connection.execute("PRAGMA user_version=0")
    store.connection.commit()
    store.close()
    with pytest.raises(StoreSchemaMismatch):
        Store(tmp_path)


def test_an_unknown_never_closes_on_an_evidence_id_the_run_never_wrote(tmp_path: Path) -> None:
    store, run_id, scope_hash, first = seeded_unknown(tmp_path)
    held = first["id"]

    closing = make_candidate(
        candidate_id=candidate_identity("p", "call_version", "src/open.py:1"),
        run_id=run_id,
        proof_scope_hash=scope_hash,
        provider="p",
        evidence_ids=[held, "f" * 64],
        status="AFFECTED",
        reason="closed by an id that references nothing",
        close_with=None,
    )
    with pytest.raises(EvidenceNotFound):
        store.write_candidates([closing])
    assert held_status(store, run_id, candidate_identity("p", "call_version", "src/open.py:1")) == (
        "UNKNOWN"
    )
    store.close()


def test_a_run_cannot_finish_while_evidence_is_attached_to_no_candidate(tmp_path: Path) -> None:
    store, run_id, scope_hash = seeded(tmp_path)
    orphan = make_evidence(
        run_id=run_id,
        proof_scope_hash=scope_hash,
        claim_type="file_unscanned",
        observer="text",
        repo_sha=None,
        path="ghost.bin",
        line_start=None,
        line_end=None,
        source_hash=EMPTY_SHA256,
        value={"reason": "binary_opaque"},
        provider_subject=None,
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="RAW",
    )
    store.write_evidence([orphan])
    assert store.connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 1
    with pytest.raises(UnexplainedCandidates):
        store.finish_run(run_id, "2026-01-01T00:01:00+00:00")
    store.close()

    reopened = Store(tmp_path)
    assert reopened.connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 1
    reopened.close()
