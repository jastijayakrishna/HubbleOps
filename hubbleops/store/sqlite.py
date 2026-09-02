from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from hubbleops.core.candidate import OPEN_STATUSES, STATUSES
from hubbleops.core.canonical import canonical_text
from hubbleops.core.errors import (
    EvidenceNotFound,
    ProofScopeMismatch,
    ProvenanceDropped,
    StoreSchemaMismatch,
    UnexplainedCandidates,
    UnknownNotConserved,
)
from hubbleops.core.schema import validate

DATABASE_FILENAME = "hubbleops.sqlite"
SCHEMA_VERSION = 1

TABLE_NAMES = frozenset({"runs", "evidence", "candidates", "obligations", "checks", "artifacts"})

STATUS_VALUES = ", ".join(f"'{status}'" for status in STATUSES)

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        proof_scope_hash TEXT NOT NULL,
        provider TEXT NOT NULL,
        verb TEXT NOT NULL,
        target TEXT NOT NULL,
        repo_sha TEXT,
        scanner_version TEXT NOT NULL,
        proof_scope_json TEXT NOT NULL,
        closure_json TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence (
        id TEXT NOT NULL,
        run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        proof_scope_hash TEXT NOT NULL,
        claim_type TEXT NOT NULL,
        observer TEXT NOT NULL,
        path TEXT NOT NULL,
        line_start INTEGER,
        line_end INTEGER,
        record_json TEXT NOT NULL,
        PRIMARY KEY (run_id, id)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS candidates (
        id TEXT NOT NULL,
        run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        proof_scope_hash TEXT NOT NULL,
        provider TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ({STATUS_VALUES})),
        record_json TEXT NOT NULL,
        PRIMARY KEY (run_id, id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS obligations (
        id TEXT NOT NULL,
        run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        proof_scope_hash TEXT NOT NULL,
        provider_change_id TEXT NOT NULL,
        repair_class TEXT NOT NULL,
        status TEXT NOT NULL,
        record_json TEXT NOT NULL,
        PRIMARY KEY (run_id, id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS checks (
        id TEXT NOT NULL,
        run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        proof_scope_hash TEXT NOT NULL,
        name TEXT NOT NULL,
        outcome TEXT NOT NULL,
        detail_json TEXT NOT NULL,
        PRIMARY KEY (run_id, id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        id TEXT NOT NULL,
        run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        proof_scope_hash TEXT NOT NULL,
        kind TEXT NOT NULL,
        path TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        size INTEGER NOT NULL,
        PRIMARY KEY (run_id, id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS evidence_by_run ON evidence(run_id, path, line_start)",
    "CREATE INDEX IF NOT EXISTS candidates_by_run ON candidates(run_id, status)",
    "CREATE INDEX IF NOT EXISTS runs_by_started ON runs(started_at DESC)",
)


@dataclass(frozen=True, slots=True)
class RunRow:
    run_id: str
    proof_scope_hash: str
    provider: str
    verb: str
    target: str
    repo_sha: str | None
    scanner_version: str
    proof_scope: dict[str, Any]
    closure: dict[str, Any]
    started_at: str
    finished_at: str | None


class Store:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / DATABASE_FILENAME
        self._scopes: dict[str, str] = {}
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA synchronous=FULL")
        self._guard_schema_version()
        for statement in SCHEMA_STATEMENTS:
            self.connection.execute(statement)
        self.connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self.connection.commit()

    def _guard_schema_version(self) -> None:
        found = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if found == SCHEMA_VERSION:
            return
        cursor = self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if TABLE_NAMES & {str(row[0]) for row in cursor.fetchall()}:
            self.connection.close()
            raise StoreSchemaMismatch(str(self.path), found, SCHEMA_VERSION)

    def __enter__(self) -> Store:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def start_run(
        self,
        *,
        run_id: str,
        proof_scope: dict[str, Any],
        proof_scope_hash: str,
        provider: str,
        verb: str,
        target: str,
        closure_summary: dict[str, Any],
        started_at: str,
    ) -> None:
        validate("proof_scope", proof_scope)
        self.connection.execute(
            """
            INSERT INTO runs (
                run_id, proof_scope_hash, provider, verb, target, repo_sha,
                scanner_version, proof_scope_json, closure_json, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(run_id) DO UPDATE SET
                started_at = excluded.started_at,
                closure_json = excluded.closure_json,
                finished_at = NULL
            """,
            (
                run_id,
                proof_scope_hash,
                provider,
                verb,
                target,
                proof_scope["repo_sha"],
                proof_scope["scanner_version"],
                canonical_text(proof_scope),
                canonical_text(closure_summary),
                started_at,
            ),
        )
        self.connection.commit()

    def finish_run(self, run_id: str, finished_at: str) -> None:
        self._guard_every_evidence_is_explained(run_id)
        self.connection.execute(
            "UPDATE runs SET finished_at = ? WHERE run_id = ?", (finished_at, run_id)
        )
        self.connection.commit()

    def _run_scope(self, run_id: str) -> str | None:
        held = self._scopes.get(run_id)
        if held is not None:
            return held
        cursor = self.connection.execute(
            "SELECT proof_scope_hash FROM runs WHERE run_id = ?", (run_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        scope = str(row["proof_scope_hash"])
        self._scopes[run_id] = scope
        return scope

    def _bind_to_run(self, kind: str, record_id: str, run_id: str, scope: str) -> None:
        run_scope = self._run_scope(run_id)
        if run_scope is not None and run_scope != scope:
            raise ProofScopeMismatch(kind, record_id, run_scope, scope)

    def _guard_evidence_exists(self, record: dict[str, Any]) -> None:
        cited = sorted({str(eid) for eid in record["evidence_ids"]})
        if not cited:
            return
        placeholders = ", ".join("?" for _ in cited)
        cursor = self.connection.execute(
            f"SELECT id FROM evidence WHERE run_id = ? AND id IN ({placeholders})",
            (record["run_id"], *cited),
        )
        stored = {str(row["id"]) for row in cursor.fetchall()}
        missing = tuple(eid for eid in cited if eid not in stored)
        if missing:
            raise EvidenceNotFound(str(record["id"]), missing)

    def _guard_candidate_transition(self, record: dict[str, Any]) -> None:
        cursor = self.connection.execute(
            "SELECT record_json FROM candidates WHERE run_id = ? AND id = ?",
            (record["run_id"], record["id"]),
        )
        row = cursor.fetchone()
        if row is None:
            return
        stored: dict[str, Any] = json.loads(row["record_json"])
        held = set(stored["evidence_ids"])
        offered = set(record["evidence_ids"])
        was = str(stored["status"])
        now = str(record["status"])
        if not held <= offered:
            raise ProvenanceDropped(str(record["id"]), tuple(sorted(held - offered)))
        if was in OPEN_STATUSES and now not in OPEN_STATUSES and held == offered:
            raise UnknownNotConserved(str(record["id"]), was, now)

    def _guard_every_evidence_is_explained(self, run_id: str) -> None:
        cursor = self.connection.execute(
            """
            SELECT evidence.id FROM evidence
            WHERE evidence.run_id = ?
              AND NOT EXISTS (
                SELECT 1 FROM candidates, json_each(candidates.record_json, '$.evidence_ids')
                WHERE candidates.run_id = evidence.run_id
                  AND json_each.value = evidence.id
              )
            ORDER BY evidence.id
            """,
            (run_id,),
        )
        orphaned = [str(row["id"]) for row in cursor.fetchall()]
        if orphaned:
            raise UnexplainedCandidates(
                len(orphaned),
                f"run {run_id} persisted evidence attached to no candidate: "
                f"{', '.join(orphaned[:5])}",
            )

    def write_evidence(self, records: Sequence[dict[str, Any]]) -> None:
        rows: list[tuple[Any, ...]] = []
        for record in records:
            validate("evidence", record)
            self._bind_to_run(
                "evidence",
                str(record["id"]),
                str(record["run_id"]),
                str(record["proof_scope_hash"]),
            )
            rows.append(
                (
                    record["id"],
                    record["run_id"],
                    record["proof_scope_hash"],
                    record["claim_type"],
                    record["observer"],
                    record["path"],
                    record["line_start"],
                    record["line_end"],
                    canonical_text(record),
                )
            )
        self.connection.executemany(
            """
            INSERT INTO evidence (
                id, run_id, proof_scope_hash, claim_type, observer, path,
                line_start, line_end, record_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, id) DO UPDATE SET record_json = excluded.record_json
            """,
            rows,
        )
        self.connection.commit()

    def write_candidates(self, records: Sequence[dict[str, Any]]) -> None:
        rows: list[tuple[Any, ...]] = []
        for record in records:
            validate("candidate", record)
            self._bind_to_run(
                "candidate",
                str(record["id"]),
                str(record["run_id"]),
                str(record["proof_scope_hash"]),
            )
            self._guard_candidate_transition(record)
            self._guard_evidence_exists(record)
            rows.append(
                (
                    record["id"],
                    record["run_id"],
                    record["proof_scope_hash"],
                    record["provider"],
                    record["status"],
                    canonical_text(record),
                )
            )
        self.connection.executemany(
            """
            INSERT INTO candidates (
                id, run_id, proof_scope_hash, provider, status, record_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, id) DO UPDATE SET
                status = excluded.status,
                record_json = excluded.record_json
            """,
            rows,
        )
        self.connection.commit()

    def write_artifact(
        self,
        *,
        run_id: str,
        proof_scope_hash: str,
        kind: str,
        path: str,
        sha256: str,
        size: int,
    ) -> None:
        self._bind_to_run("artifact", sha256, run_id, proof_scope_hash)
        self.connection.execute(
            """
            INSERT INTO artifacts (id, run_id, proof_scope_hash, kind, path, sha256, size)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, id) DO UPDATE SET
                path = excluded.path,
                sha256 = excluded.sha256,
                size = excluded.size
            """,
            (sha256, run_id, proof_scope_hash, kind, path, sha256, size),
        )
        self.connection.commit()

    def latest_run(self, provider: str | None = None) -> RunRow | None:
        if provider is None:
            cursor = self.connection.execute(
                "SELECT * FROM runs ORDER BY started_at DESC, run_id ASC LIMIT 1"
            )
        else:
            cursor = self.connection.execute(
                "SELECT * FROM runs WHERE provider = ? ORDER BY started_at DESC, run_id ASC "
                "LIMIT 1",
                (provider,),
            )
        row = cursor.fetchone()
        return _run_row(row) if row is not None else None

    def run(self, run_id: str) -> RunRow | None:
        cursor = self.connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        row = cursor.fetchone()
        return _run_row(row) if row is not None else None

    def evidence_for(self, run_id: str) -> tuple[dict[str, Any], ...]:
        cursor = self.connection.execute(
            "SELECT record_json FROM evidence WHERE run_id = ? ORDER BY path, line_start, id",
            (run_id,),
        )
        return tuple(json.loads(row["record_json"]) for row in cursor.fetchall())

    def candidates_for(self, run_id: str) -> tuple[dict[str, Any], ...]:
        cursor = self.connection.execute(
            "SELECT record_json FROM candidates WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        return tuple(json.loads(row["record_json"]) for row in cursor.fetchall())


def _run_row(row: sqlite3.Row) -> RunRow:
    return RunRow(
        run_id=row["run_id"],
        proof_scope_hash=row["proof_scope_hash"],
        provider=row["provider"],
        verb=row["verb"],
        target=row["target"],
        repo_sha=row["repo_sha"],
        scanner_version=row["scanner_version"],
        proof_scope=json.loads(row["proof_scope_json"]),
        closure=json.loads(row["closure_json"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )
