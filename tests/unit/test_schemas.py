from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hubbleops.core.candidate import candidate_identity, make_candidate
from hubbleops.core.canonical import EMPTY_SHA256, content_id
from hubbleops.core.errors import SchemaViolation
from hubbleops.core.evidence import make_evidence
from hubbleops.core.proof_scope import make_proof_scope, proof_scope_hash, scanner_fingerprint
from hubbleops.core.schema import SCHEMA_DIR, schema_names, validate

RUN_ID = content_id({"run": 1})
SCOPE_HASH = content_id({"scope": 1})

REQUIRED_SCHEMAS = ("evidence", "candidate", "obligation", "proof_scope", "receipt")


def sample_evidence() -> dict[str, Any]:
    return make_evidence(
        run_id=RUN_ID,
        proof_scope_hash=SCOPE_HASH,
        claim_type="call_version",
        observer="text",
        repo_sha=None,
        path="src/app.py",
        line_start=3,
        line_end=3,
        source_hash=EMPTY_SHA256,
        value={"pattern": "carrier", "slot": "per_call"},
        provider_subject="v22",
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="RAW",
    )


def test_the_five_frozen_schemas_exist() -> None:
    assert set(REQUIRED_SCHEMAS) <= set(schema_names())


def test_every_schema_declares_an_id_and_is_draft_2020_12() -> None:
    for name in schema_names():
        document = json.loads((SCHEMA_DIR / f"{name}.json").read_text(encoding="utf-8"))
        assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert document["$id"].endswith(f"{name}.json")


def test_evidence_id_is_the_sha256_of_its_canonical_content() -> None:
    record = sample_evidence()
    body = {key: value for key, value in record.items() if key != "id"}
    assert record["id"] == content_id(body)


def test_evidence_id_changes_when_any_field_changes() -> None:
    first = sample_evidence()
    second = make_evidence(
        run_id=RUN_ID,
        proof_scope_hash=SCOPE_HASH,
        claim_type="call_version",
        observer="text",
        repo_sha=None,
        path="src/app.py",
        line_start=4,
        line_end=4,
        source_hash=EMPTY_SHA256,
        value={"pattern": "carrier", "slot": "per_call"},
        provider_subject="v22",
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="RAW",
    )
    assert first["id"] != second["id"]


def test_evidence_rejects_an_observer_outside_the_frozen_channels() -> None:
    with pytest.raises(SchemaViolation):
        make_evidence(
            run_id=RUN_ID,
            proof_scope_hash=SCOPE_HASH,
            claim_type="call_version",
            observer="closure",
            repo_sha=None,
            path="src/app.py",
            line_start=1,
            line_end=1,
            source_hash=EMPTY_SHA256,
            value=None,
            provider_subject=None,
            dependency_context_hash=None,
            derivation="OBSERVED",
            confidence="RAW",
        )


def test_a_candidate_without_a_status_cannot_be_persisted() -> None:
    record = {
        "id": candidate_identity("p", "call_version", "src/app.py:1"),
        "run_id": RUN_ID,
        "proof_scope_hash": SCOPE_HASH,
        "provider": "p",
        "evidence_ids": [sample_evidence()["id"]],
        "reason": "no status on this record",
        "close_with": None,
    }
    with pytest.raises(SchemaViolation):
        validate("candidate", record)


def test_an_unknown_candidate_must_carry_a_closing_instruction() -> None:
    with pytest.raises(SchemaViolation):
        make_candidate(
            candidate_id=candidate_identity("p", "call_version", "src/app.py:1"),
            run_id=RUN_ID,
            proof_scope_hash=SCOPE_HASH,
            provider="p",
            evidence_ids=[sample_evidence()["id"]],
            status="UNKNOWN",
            reason="undecidable",
            close_with=None,
        )


def test_a_candidate_must_carry_at_least_one_piece_of_evidence() -> None:
    with pytest.raises(SchemaViolation):
        make_candidate(
            candidate_id=candidate_identity("p", "call_version", "src/app.py:1"),
            run_id=RUN_ID,
            proof_scope_hash=SCOPE_HASH,
            provider="p",
            evidence_ids=[],
            status="AFFECTED",
            reason="claimed without evidence",
            close_with=None,
        )


def test_candidate_identity_ignores_attached_evidence() -> None:
    first = make_candidate(
        candidate_id=candidate_identity("p", "call_version", "src/app.py:1"),
        run_id=RUN_ID,
        proof_scope_hash=SCOPE_HASH,
        provider="p",
        evidence_ids=[sample_evidence()["id"]],
        status="AFFECTED",
        reason="one observation",
        close_with=None,
    )
    second = make_candidate(
        candidate_id=candidate_identity("p", "call_version", "src/app.py:1"),
        run_id=RUN_ID,
        proof_scope_hash=SCOPE_HASH,
        provider="p",
        evidence_ids=[sample_evidence()["id"], content_id({"other": 1})],
        status="AFFECTED",
        reason="two observations",
        close_with=None,
    )
    assert first["id"] == second["id"]


def test_a_receipt_cannot_record_a_non_zero_unexplained_count() -> None:
    scope = make_proof_scope(
        repo_sha=None,
        tree_hash=EMPTY_SHA256,
        dependency_resolution_hash=None,
        scanner_version="test",
    )
    receipt: dict[str, Any] = {
        "proof_scope": scope,
        "candidates_summary": {
            "total": 1,
            "affected": 0,
            "not_affected_with_evidence": 0,
            "unknown": 0,
            "human_required": 0,
            "excluded_with_evidence": 0,
            "unexplained": 1,
        },
        "obligations": [],
        "changed_files": [],
        "migration_audit": {},
        "oracle_results": [],
        "blast_radius": {},
        "request_shape_differential": {},
        "response_consumer_check": {},
        "frozen_baseline_tests": {},
        "candidate_tests": {},
        "falsifiers": [],
        "unknowns": [],
        "unknown_conservation": {},
        "verdict": "VERIFIED_FOR_SCOPE",
        "reasons": [],
    }
    with pytest.raises(SchemaViolation):
        validate("receipt", receipt)


def test_a_verdict_of_safe_is_not_representable() -> None:
    with pytest.raises(SchemaViolation):
        validate("receipt", {"verdict": "SAFE"})


def test_proof_scope_hash_is_stable_and_content_addressed() -> None:
    scope = make_proof_scope(
        repo_sha=None,
        tree_hash=EMPTY_SHA256,
        dependency_resolution_hash=None,
        scanner_version="test",
    )
    other = make_proof_scope(
        repo_sha=None,
        tree_hash=EMPTY_SHA256,
        dependency_resolution_hash=None,
        scanner_version="test-2",
    )
    assert proof_scope_hash(scope) == proof_scope_hash(dict(scope))
    assert proof_scope_hash(scope) != proof_scope_hash(other)


def test_proof_scope_requires_every_frozen_field() -> None:
    scope = make_proof_scope(
        repo_sha=None,
        tree_hash=EMPTY_SHA256,
        dependency_resolution_hash=None,
        scanner_version="test",
    )
    for field in scope:
        partial = {key: value for key, value in scope.items() if key != field}
        with pytest.raises(SchemaViolation):
            validate("proof_scope", partial)


def test_the_scanner_fingerprint_moves_when_a_source_it_covers_changes(tmp_path: Path) -> None:
    first = tmp_path / "closure.py"
    second = tmp_path / "observer.py"
    first.write_text("BINARY_SNIFF = 8192\n", encoding="utf-8")
    second.write_text("value = 1\n", encoding="utf-8")
    before = scanner_fingerprint([first, second])

    assert scanner_fingerprint([second, first]) == before
    first.write_text("BINARY_SNIFF = 4096\n", encoding="utf-8")
    assert scanner_fingerprint([first, second]) != before


def test_the_scanner_fingerprint_refuses_a_source_it_cannot_read(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        scanner_fingerprint([tmp_path / "absent.py"])
