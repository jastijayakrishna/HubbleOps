from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml

from hubbleops.app import promotion, registry
from hubbleops.app.cli import scan_repository


class FakeStore:
    def __init__(self, candidate: dict[str, Any], evidence: dict[str, Any]) -> None:
        self.candidate = candidate
        self.evidence = evidence

    def run(self, run_id: str) -> SimpleNamespace:
        return SimpleNamespace(provider="google_ads")

    def candidates_for(self, run_id: str) -> list[dict[str, Any]]:
        return [self.candidate]

    def evidence_for(self, run_id: str) -> list[dict[str, Any]]:
        return [self.evidence]


def observed(repository: Path) -> tuple[FakeStore, str]:
    source = repository / "wrapper.py"
    source.write_text(
        "def observed_wrapper(version):\n    return version\n\nobserved_wrapper('v22')\n",
        encoding="utf-8",
    )
    evidence = {
        "confidence": "PROVEN",
        "derivation": "OBSERVED",
        "id": "e" * 64,
        "observer": "dynamic",
        "path": ".",
        "source_hash": "0" * 64,
        "value": {
            "stack": [
                {
                    "function": "observed_wrapper",
                    "kind": "repository",
                    "line": 1,
                    "path": "wrapper.py",
                }
            ]
        },
    }
    candidate = {"evidence_ids": [evidence["id"]], "id": "c" * 64}
    return FakeStore(candidate, evidence), str(candidate["id"])


def test_promotion_is_idempotent_revocable_and_provenance_bound(tmp_path: Path) -> None:
    store, candidate_id = observed(tmp_path)
    typed_store = cast(Any, store)
    path = promotion.promote(tmp_path, typed_store, "r" * 64, candidate_id, revoke=False)
    first = path.read_bytes()
    promotion.promote(tmp_path, typed_store, "r" * 64, candidate_id, revoke=False)
    assert path.read_bytes() == first
    document = yaml.safe_load(first)
    entry = document["entries"][0]
    assert entry["candidate_id"] == candidate_id
    assert entry["evidence_ids"] == ["e" * 64]
    assert (
        entry["source_hash"]
        == hashlib.sha256(tmp_path.joinpath("wrapper.py").read_bytes()).hexdigest()
    )
    promotion.promote(tmp_path, typed_store, "r" * 64, candidate_id, revoke=True)
    assert yaml.safe_load(path.read_bytes())["entries"][0]["state"] == "revoked"


def test_active_promotion_is_consumed_by_the_next_scan(tmp_path: Path) -> None:
    store, candidate_id = observed(tmp_path)
    typed_store = cast(Any, store)
    promotion.promote(tmp_path, typed_store, "r" * 64, candidate_id, revoke=False)
    result = scan_repository(tmp_path, registry.load_pack("google_ads"))
    promoted = [
        record
        for record in result.ledger.evidence
        if record["claim_type"] == "call_version"
        and record["path"] == "wrapper.py"
        and record["provider_subject"] == "v22"
    ]
    assert promoted


def test_source_drift_fails_the_next_scan_closed(tmp_path: Path) -> None:
    store, candidate_id = observed(tmp_path)
    typed_store = cast(Any, store)
    promotion.promote(tmp_path, typed_store, "r" * 64, candidate_id, revoke=False)
    tmp_path.joinpath("wrapper.py").write_text("changed = True\n", encoding="utf-8")
    with pytest.raises(promotion.PromotionInvalid, match="PROMOTION_SOURCE_DRIFT"):
        scan_repository(tmp_path, registry.load_pack("google_ads"))


def test_revoked_promotion_does_not_apply_or_block_on_later_drift(tmp_path: Path) -> None:
    store, candidate_id = observed(tmp_path)
    typed_store = cast(Any, store)
    promotion.promote(tmp_path, typed_store, "r" * 64, candidate_id, revoke=False)
    promotion.promote(tmp_path, typed_store, "r" * 64, candidate_id, revoke=True)
    tmp_path.joinpath("wrapper.py").write_text("changed = True\n", encoding="utf-8")
    result = scan_repository(tmp_path, registry.load_pack("google_ads"))
    assert not any(
        record["claim_type"] == "call_version" and record["path"] == "wrapper.py"
        for record in result.ledger.evidence
    )


def test_promotion_rejects_missing_observed_repository_stack(tmp_path: Path) -> None:
    store, candidate_id = observed(tmp_path)
    store.evidence["value"] = {"stack": []}
    with pytest.raises(promotion.PromotionInvalid, match="requires observed"):
        promotion.promote(tmp_path, cast(Any, store), "r" * 64, candidate_id, revoke=False)
