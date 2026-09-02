from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from hubbleops.core.candidate import candidate_identity, make_candidate
from hubbleops.core.canonical import EMPTY_SHA256, content_id
from hubbleops.core.errors import (
    ProofScopeMismatch,
    ProvenanceDropped,
    StoreSchemaMismatch,
    UnknownNotConserved,
)
from hubbleops.core.evidence import make_evidence
from hubbleops.core.proof_scope import make_proof_scope, proof_scope_hash
from hubbleops.store.artifacts import write_atomic
from hubbleops.store.sqlite import Store

EXPECTED_TABLES = {"runs", "evidence", "candidates", "obligations", "checks", "artifacts"}


def seeded(directory: Path) -> tuple[Store, str, str]:
    scope = make_proof_scope(
        repo_sha=None,
        tree_hash=EMPTY_SHA256,
        dependency_resolution_hash=None,
        scanner_version="test",
    )
    scope_hash = proof_scope_hash(scope)
    run_id = content_id({"run": scope_hash})
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
    assert len(store.run(run_id).proof_scope) == 10
    store.close()


def test_the_latest_run_is_findable_by_pack(tmp_path: Path) -> None:
    store, run_id, _ = seeded(tmp_path)
    assert store.latest_run("p").run_id == run_id
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
    store.close()


def evidence_at(run_id: str, scope_hash: str, path: str) -> dict[str, Any]:
    return make_evidence(
        run_id=run_id,
        proof_scope_hash=scope_hash,
        claim_type="call_version",
        observer="text",
        repo_sha=None,
        path=path,
        line_start=1,
        line_end=1,
        source_hash=EMPTY_SHA256,
        value={"pattern": "c", "slot": "per_call"},
        provider_subject="v22",
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="RAW",
    )


def open_candidate(
    run_id: str,
    scope_hash: str,
    evidence_ids: list[str],
    status: str,
    close_with: str | None,
) -> dict[str, Any]:
    return make_candidate(
        candidate_id=candidate_identity("p", "call_version", "src/open.py:1"),
        run_id=run_id,
        proof_scope_hash=scope_hash,
        provider="p",
        evidence_ids=evidence_ids,
        status=status,
        reason="the version at this call site is not statically resolvable",
        close_with=close_with,
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


def test_an_unknown_closes_when_new_evidence_arrives(tmp_path: Path) -> None:
    store, run_id, scope_hash, first = seeded_unknown(tmp_path)
    second = evidence_at(run_id, scope_hash, "src/open_again.py")
    store.write_evidence([second])
    closing = open_candidate(run_id, scope_hash, [first["id"], second["id"]], "AFFECTED", None)
    store.write_candidates([closing])
    assert held_status(store, run_id, closing["id"]) == "AFFECTED"
    store.close()


def test_a_candidate_write_can_never_drop_evidence(tmp_path: Path) -> None:
    store, run_id, scope_hash, _ = seeded_unknown(tmp_path)
    second = evidence_at(run_id, scope_hash, "src/open_again.py")
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


def test_a_database_written_by_another_store_schema_is_refused(tmp_path: Path) -> None:
    store, _, _ = seeded(tmp_path)
    store.connection.execute("PRAGMA user_version=0")
    store.connection.commit()
    store.close()
    with pytest.raises(StoreSchemaMismatch):
        Store(tmp_path)
