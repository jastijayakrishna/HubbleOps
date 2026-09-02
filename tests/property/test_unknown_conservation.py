from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hubbleops.core.candidate import (
    OPEN_STATUSES,
    STATUSES,
    candidate_identity,
    make_candidate,
)
from hubbleops.core.canonical import EMPTY_SHA256
from hubbleops.core.errors import UnknownNotConserved
from hubbleops.core.evidence import make_evidence
from hubbleops.core.proof_scope import make_proof_scope, proof_scope_hash
from hubbleops.store.sqlite import Store

SETTINGS = settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

CLOSE_WITH = "capture the executed call, or record it with `hops decide`"


def seed(directory: Path) -> tuple[Store, str, str]:
    scope = make_proof_scope(
        repo_sha=None,
        tree_hash=EMPTY_SHA256,
        dependency_resolution_hash=None,
        scanner_version="test",
    )
    scope_hash = proof_scope_hash(scope)
    run_id = scope_hash
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
    return store, run_id, scope_hash


def observation(run_id: str, scope_hash: str, path: str) -> dict[str, Any]:
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
        value={"pattern": "carrier", "slot": "per_call"},
        provider_subject=None,
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="RAW",
    )


def candidate(run_id: str, scope_hash: str, evidence_ids: list[str], status: str) -> dict[str, Any]:
    return make_candidate(
        candidate_id=candidate_identity("p", "call_version", "src/app.py:1"),
        run_id=run_id,
        proof_scope_hash=scope_hash,
        provider="p",
        evidence_ids=evidence_ids,
        status=status,
        reason="the version at this call site",
        close_with=CLOSE_WITH if status in OPEN_STATUSES else None,
    )


def status_of(store: Store, run_id: str, candidate_id: str) -> str:
    held = [item for item in store.candidates_for(run_id) if item["id"] == candidate_id]
    return str(held[0]["status"])


@given(
    was=st.sampled_from(STATUSES),
    now=st.sampled_from(STATUSES),
    evidence_arrives=st.booleans(),
)
@SETTINGS
def test_a_status_changes_away_from_open_only_when_evidence_arrives(
    was: str, now: str, evidence_arrives: bool
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        store, run_id, scope_hash = seed(Path(directory))
        try:
            first = observation(run_id, scope_hash, "src/app.py")
            store.write_evidence([first])
            attached = [first["id"]]
            store.write_candidates([candidate(run_id, scope_hash, attached, was)])

            if evidence_arrives:
                second = observation(run_id, scope_hash, "src/other.py")
                store.write_evidence([second])
                attached = sorted([first["id"], second["id"]])

            offered = candidate(run_id, scope_hash, attached, now)
            conserved = was not in OPEN_STATUSES or now in OPEN_STATUSES or evidence_arrives

            if conserved:
                store.write_candidates([offered])
                assert status_of(store, run_id, offered["id"]) == now
            else:
                with pytest.raises(UnknownNotConserved):
                    store.write_candidates([offered])
                assert status_of(store, run_id, offered["id"]) == was
        finally:
            store.close()
