from __future__ import annotations

from pathlib import Path

from hubbleops.core.candidate import candidate_identity, make_candidate
from hubbleops.core.canonical import EMPTY_SHA256, content_id
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
