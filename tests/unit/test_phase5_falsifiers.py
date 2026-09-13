from __future__ import annotations

from typing import Any

import pytest

from hubbleops.core.verification import ChangeSet, FalsifierInput, FalsifierView, SubjectChange
from hubbleops.packs.google_ads.falsifiers import FALSIFIERS

TARGET = "v25"
SOURCE = "v22"


def changes() -> ChangeSet:
    return ChangeSet(
        from_version=SOURCE,
        to_version=TARGET,
        pair_hash="h",
        changes=(
            SubjectChange("campaign.gone", "REMOVED", None, "VALID", ""),
            SubjectChange("campaign.old", "REMOVED", "campaign.new", "VALID", ""),
        ),
    )


def record(claim_type: str, value: dict[str, Any], subject: str | None = None) -> dict[str, Any]:
    return {
        "claim_type": claim_type,
        "path": "src/client.py",
        "line_start": 7,
        "provider_subject": subject,
        "value": value,
    }


def subject_with(*records: dict[str, Any]) -> FalsifierInput:
    return FalsifierInput(
        changes=changes(),
        candidate_root="/candidate",
        evidence=tuple(records),
        candidates=(),
        captured_requests=(),
    )


CLEAN: dict[str, dict[str, Any]] = {
    "call_version": {"version": TARGET},
    "endpoint_reference": {"version": TARGET},
    "config_reference": {"version": TARGET},
    "package_reference": {"package": f"google.ads.googleads.{TARGET}"},
    "sdk_installed": {"state": "PRESENT", "version": "27.0.0"},
    "request_text": {"skeleton": {"fragments": ["SELECT campaign.new FROM campaign"], "holes": []}},
    "production_version": {"service": "GoogleAdsService", "method": "Search", "version": TARGET},
}

DIRTY: dict[str, dict[str, Any]] = {
    "call_version": {"version": SOURCE},
    "endpoint_reference": {"version": SOURCE},
    "config_reference": {"version": SOURCE},
    "package_reference": {"package": f"google.ads.googleads.{SOURCE}"},
    "request_text": {
        "skeleton": {"fragments": ["SELECT campaign.gone, campaign.old FROM campaign"], "holes": []}
    },
    "production_version": {"service": "GoogleAdsService", "method": "Search", "version": SOURCE},
}


def test_every_falsifier_has_a_distinct_name_and_failure_class() -> None:
    assert len({item.name for item in FALSIFIERS}) == len(FALSIFIERS)
    assert all(item.failure_class for item in FALSIFIERS)
    assert len(FALSIFIERS) == 8


def test_every_falsifier_conforms_to_the_injected_view() -> None:
    assert all(isinstance(item, FalsifierView) for item in FALSIFIERS)


@pytest.mark.parametrize("falsifier", FALSIFIERS, ids=lambda item: item.name)
def test_a_clean_candidate_passes_every_falsifier(falsifier: FalsifierView) -> None:
    payload = CLEAN[falsifier.failure_class]
    outcome = falsifier.check(subject_with(record(falsifier.failure_class, payload)))
    assert outcome.result == "PASS", f"{falsifier.name}: {outcome.reason} {outcome.sites}"


@pytest.mark.parametrize(
    "falsifier",
    [item for item in FALSIFIERS if item.failure_class in DIRTY],
    ids=lambda item: item.name,
)
def test_a_dirty_candidate_fails_the_falsifier_that_hunts_it(falsifier: FalsifierView) -> None:
    payload = DIRTY[falsifier.failure_class]
    outcome = falsifier.check(subject_with(record(falsifier.failure_class, payload)))
    assert outcome.result == "FAIL", (
        f"{falsifier.name} found nothing in evidence written to trip it: {outcome.reason}"
    )
    assert outcome.sites, "a failing falsifier names where it failed"


def test_the_renamed_subject_falsifier_names_the_replacement() -> None:
    falsifier = next(item for item in FALSIFIERS if item.name == "renamed_subject_in_request")
    outcome = falsifier.check(
        subject_with(
            record(
                "request_text",
                {"skeleton": {"fragments": ["SELECT campaign.old FROM campaign"], "holes": []}},
            )
        )
    )
    assert outcome.result == "FAIL"
    assert any("campaign.new" in site for site in outcome.sites)


def test_an_unresolvable_client_pin_is_unknown_not_a_pass() -> None:
    falsifier = next(item for item in FALSIFIERS if item.name == "sdk_constraint")
    outcome = falsifier.check(subject_with(record("sdk_installed", {"state": "PRESENT"})))
    assert outcome.result == "UNKNOWN"


def test_an_absent_client_is_not_an_unresolvable_one() -> None:
    falsifier = next(item for item in FALSIFIERS if item.name == "sdk_constraint")
    outcome = falsifier.check(subject_with(record("sdk_installed", {"state": "ABSENT"})))
    assert outcome.result == "PASS"


def test_a_falsifier_with_no_matching_evidence_passes_rather_than_inventing_a_failure() -> None:
    for falsifier in FALSIFIERS:
        outcome = falsifier.check(subject_with())
        assert outcome.result == "PASS", f"{falsifier.name} invented {outcome.reason} from nothing"
