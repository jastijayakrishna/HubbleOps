from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from hubbleops.core.candidate import OPEN_STATUSES, STATUSES

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "real_repo"
FROZEN = {
    "glna_unknowns_before.json": {
        "repo_sha": "b43b322771071ed88d5a817422dd222acbaa5f33",
        "unknown_total": 558,
        "ledger_unknown": 752,
        "merged": 194,
        "proposal": "P-024",
        "runtime_only": False,
    },
    "dub_unknowns_before.json": {
        "repo_sha": "b8866f413cec065438d6e5faabbd9dac7d1ceea5",
        "unknown_total": 100,
        "ledger_unknown": 103,
        "merged": 3,
        "proposal": "identifier-boundary-keys",
        "runtime_only": False,
    },
}


@pytest.fixture(scope="module", params=sorted(FROZEN))
def baseline(request: pytest.FixtureRequest) -> Mapping[str, Any]:
    name = str(request.param)
    document = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return {**document, "expected": FROZEN[name]}


def conservation_violations(
    baseline: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]
) -> list[tuple[str, str]]:
    present = {str(candidate["id"]): str(candidate["status"]) for candidate in candidates}
    violations: list[tuple[str, str]] = []
    for entry in baseline["unknowns"]:
        identifier = str(entry["candidate_id"])
        status = present.get(identifier)
        if status is None:
            violations.append((identifier, "absent"))
        elif status not in entry["expected_future_disposition"]["permitted"]:
            violations.append((identifier, status))
    return violations


def test_the_baseline_freezes_every_unknown_of_the_recorded_run(
    baseline: Mapping[str, Any],
) -> None:
    migration = baseline["identity_migration"]
    expected = baseline["expected"]
    assert baseline["unknown_total"] == expected["unknown_total"]
    assert len(baseline["unknowns"]) == expected["unknown_total"]
    assert baseline["ledger_counts"]["unknown"] == expected["ledger_unknown"]
    assert baseline["ledger_counts"]["unexplained"] == 0
    assert baseline["unknown_total"] + migration["merged"] == baseline["ledger_counts"]["unknown"]
    assert baseline["repo_sha"] == expected["repo_sha"]


def test_every_identity_p024_retired_is_carried_by_a_surviving_candidate(
    baseline: Mapping[str, Any],
) -> None:
    migration = baseline["identity_migration"]
    retired = migration["retired_identities"]
    present = {str(entry["candidate_id"]): entry for entry in baseline["unknowns"]}
    assert migration["proposal"] == baseline["expected"]["proposal"]
    assert len(retired) == migration["merged"] == baseline["expected"]["merged"]
    assert not set(retired) & set(present)
    for old_id, new_id in retired.items():
        survivor = present[new_id]
        assert old_id in survivor["merged_from"]
        assert survivor["expected_future_disposition"]["must_never"] == "absent"
    carried = {item for entry in baseline["unknowns"] for item in entry.get("merged_from", ())}
    assert carried == set(retired)


def test_every_rekeyed_identity_survives_as_the_same_observation_under_its_new_claim(
    baseline: Mapping[str, Any],
) -> None:
    rekeyed = baseline["identity_migration"].get("rekeyed_identities", {})
    present = {str(entry["candidate_id"]): entry for entry in baseline["unknowns"]}
    assert not set(rekeyed) & set(present)
    for old_id, new_id in rekeyed.items():
        survivor = present[new_id]
        assert survivor["rekeyed_from"] == old_id
        assert survivor["expected_future_disposition"]["must_never"] == "absent"


def test_every_relabel_on_evidence_names_a_present_entry_with_the_atlas_root_cause(
    baseline: Mapping[str, Any],
) -> None:
    present = {str(entry["candidate_id"]): entry for entry in baseline["unknowns"]}
    relabels = baseline.get("relabelled_on_evidence", {})
    for day, by_cause in relabels.items():
        assert day
        for root_cause, identifiers in by_cause.items():
            assert root_cause.startswith("FA-")
            for identifier in identifiers:
                entry = present[identifier]
                assert entry["root_cause"] == root_cause
                assert entry["expected_future_disposition"]["must_never"] == "absent"
                assert (
                    "HUMAN_ACCEPTED_RISK" not in entry["expected_future_disposition"]["permitted"]
                )
    assert baseline["expected"]["runtime_only"] == (
        baseline["root_cause_totals"].get("RUNTIME_ONLY", 0) > 0
    )


def test_a_merged_identity_never_widens_what_its_unknown_may_become(
    baseline: Mapping[str, Any],
) -> None:
    merged = [entry for entry in baseline["unknowns"] if entry.get("merged_from")]
    assert merged
    for entry in merged:
        permitted = set(entry["expected_future_disposition"]["permitted"])
        assert permitted <= set(STATUSES)
        assert len(entry["merged_root_causes"]) >= 1
        assert entry["root_cause"] in entry["merged_root_causes"]


def test_every_frozen_unknown_is_individually_addressable(baseline: Mapping[str, Any]) -> None:
    identifiers = [entry["candidate_id"] for entry in baseline["unknowns"]]
    assert len(set(identifiers)) == len(identifiers)
    for entry in baseline["unknowns"]:
        assert entry["path"]
        assert entry["close_with"]
        assert entry["winning_evidence"]["id"]
        assert entry["winning_evidence"]["source_hash"]


def test_every_frozen_unknown_names_a_root_cause_and_a_permitted_outcome(
    baseline: Mapping[str, Any],
) -> None:
    totals = Counter(entry["root_cause"] for entry in baseline["unknowns"])
    assert sum(totals.values()) == baseline["expected"]["unknown_total"]
    assert dict(totals) == baseline["root_cause_totals"]
    for entry in baseline["unknowns"]:
        disposition = entry["expected_future_disposition"]
        assert entry["root_cause"] != "UNTRIAGED"
        assert disposition["permitted"]
        assert disposition["resolution_requires"]
        assert disposition["must_never"] == "absent"
        assert set(disposition["permitted"]) <= set(STATUSES)


def test_a_statically_undecidable_site_may_never_be_resolved_to_anything_but_unknown(
    baseline: Mapping[str, Any],
) -> None:
    runtime_only = [e for e in baseline["unknowns"] if e["root_cause"] == "RUNTIME_ONLY"]
    assert bool(runtime_only) == baseline["expected"]["runtime_only"]
    for entry in runtime_only:
        assert entry["expected_future_disposition"]["permitted"] == ["UNKNOWN"]


def test_no_root_cause_permits_an_outcome_that_merely_renames_the_uncertainty(
    baseline: Mapping[str, Any],
) -> None:
    for entry in baseline["unknowns"]:
        permitted = set(entry["expected_future_disposition"]["permitted"])
        assert "HUMAN_ACCEPTED_RISK" not in permitted
        assert permitted - set(OPEN_STATUSES) or permitted == {"UNKNOWN"}


def test_conservation_accepts_a_rerun_that_still_preserves_every_unknown(
    baseline: Mapping[str, Any],
) -> None:
    rerun = [{"id": entry["candidate_id"], "status": "UNKNOWN"} for entry in baseline["unknowns"]]
    assert conservation_violations(baseline, rerun) == []


def test_conservation_accepts_an_unknown_resolved_to_a_permitted_status(
    baseline: Mapping[str, Any],
) -> None:
    resolvable = next(e for e in baseline["unknowns"] if e["root_cause"] == "FA-034")
    rerun = [
        {
            "id": entry["candidate_id"],
            "status": (
                "NOT_AFFECTED_WITH_EVIDENCE"
                if entry["candidate_id"] == resolvable["candidate_id"]
                else "UNKNOWN"
            ),
        }
        for entry in baseline["unknowns"]
    ]
    assert conservation_violations(baseline, rerun) == []


def test_conservation_rejects_an_unknown_that_simply_disappears(
    baseline: Mapping[str, Any],
) -> None:
    dropped = baseline["unknowns"][0]["candidate_id"]
    rerun = [
        {"id": entry["candidate_id"], "status": "UNKNOWN"}
        for entry in baseline["unknowns"]
        if entry["candidate_id"] != dropped
    ]
    assert conservation_violations(baseline, rerun) == [(dropped, "absent")]


def test_conservation_rejects_a_site_forced_to_a_status_its_root_cause_never_permits(
    baseline: Mapping[str, Any],
) -> None:
    forced = baseline["unknowns"][0]
    assert "HUMAN_ACCEPTED_RISK" not in forced["expected_future_disposition"]["permitted"]
    rerun = [
        {
            "id": entry["candidate_id"],
            "status": (
                "HUMAN_ACCEPTED_RISK"
                if entry["candidate_id"] == forced["candidate_id"]
                else "UNKNOWN"
            ),
        }
        for entry in baseline["unknowns"]
    ]
    assert conservation_violations(baseline, rerun) == [
        (forced["candidate_id"], "HUMAN_ACCEPTED_RISK")
    ]
