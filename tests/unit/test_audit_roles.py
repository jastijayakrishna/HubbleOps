from __future__ import annotations

from typing import Any

import pytest

from hubbleops.core.canonical import content_id
from hubbleops.core.evidence import make_evidence
from hubbleops.core.verification import ChangeSet, ObligationView, method
from hubbleops.observe import Ledger
from hubbleops.verify import audit

RUN = content_id({"run": "audit-roles"})
SCOPE = content_id({"scope": "audit-roles"})
SOURCE_SITE = ("src/client.py", 3, 'API_VERSION = "v24"')
DOCUMENTATION_SITE = ("CHANGELOG.md", 7, "- Upgrade the Google Ads client to v24")


def _record(path: str, line: int, text: str) -> dict[str, Any]:
    return make_evidence(
        run_id=RUN,
        proof_scope_hash=SCOPE,
        claim_type="call_version",
        observer="text",
        repo_sha=None,
        path=path,
        line_start=line,
        line_end=line,
        source_hash="b" * 64,
        value={"line": text},
        provider_subject="v24",
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="RAW",
    )


def _ledger(sites: dict[tuple[str, int, str], str]) -> Ledger:
    evidence: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for (path, line, text), status in sorted(sites.items()):
        record = _record(path, line, text)
        evidence.append(record)
        candidates.append(
            {
                "id": content_id({"candidate": f"{path}:{line}"}),
                "provider": "mock",
                "claim_type": "call_version",
                "claim_key": f"{path}:{line}",
                "status": status,
                "reason": f"{status} by the scan",
                "close_with": "decide the version" if status == "UNKNOWN" else None,
                "evidence_ids": [record["id"]],
                "run_id": RUN,
                "proof_scope_hash": SCOPE,
            }
        )
    return Ledger(
        provider="mock",
        run_id=RUN,
        proof_scope_hash=SCOPE,
        evidence=tuple(evidence),
        candidates=tuple(candidates),
    )


def _absent_v24() -> ObligationView:
    return ObligationView(
        id=content_id({"obligation": "absent-v24"}),
        provider_change_id="v24",
        evidence_ids=(),
        current_state="src/client.py:3 pins v24",
        required_state="src/client.py:3 pins v25",
        repair_class="DETERMINISTIC",
        verification_method=method("absent", "v24"),
        status="OPEN",
    )


def _changes() -> ChangeSet:
    return ChangeSet(from_version="v24", to_version="v25", pair_hash="c" * 64, changes=())


def _reconcile(ledger: Ledger) -> audit.Reconciliation:
    result = audit.run(ledger, _changes(), [_absent_v24()])
    assert len(result.reconciliations) == 1
    return result.reconciliations[0]


def test_a_live_source_site_keeps_the_absence_obligation_open() -> None:
    ledger = _ledger({SOURCE_SITE: "AFFECTED", DOCUMENTATION_SITE: "NOT_AFFECTED_WITH_EVIDENCE"})
    item = _reconcile(ledger)
    assert item.status == audit.OPEN_STATUS
    assert item.reason == "v24 is still present at src/client.py:3"
    assert item.evidence_ids == (ledger.evidence[1]["id"],)


def test_a_documentation_site_the_scan_explained_lets_absence_discharge() -> None:
    ledger = _ledger({DOCUMENTATION_SITE: "NOT_AFFECTED_WITH_EVIDENCE"})
    item = _reconcile(ledger)
    assert item.status == audit.DISCHARGED_STATUS
    assert item.evidence_ids == ()


@pytest.mark.parametrize(
    "status", ["NOT_AFFECTED_WITH_EVIDENCE", "EXCLUDED_WITH_EVIDENCE", "PROVIDER_REFERENCE_DATA"]
)
def test_every_status_the_scan_resolves_away_is_not_a_surviving_site(status: str) -> None:
    item = _reconcile(_ledger({DOCUMENTATION_SITE: status}))
    assert item.status == audit.DISCHARGED_STATUS


@pytest.mark.parametrize("status", ["UNKNOWN", "HUMAN_REQUIRED", "UNSCANNED", "UNSUPPORTED"])
def test_an_unadjudicated_site_never_closes_on_absence(status: str) -> None:
    ledger = _ledger({SOURCE_SITE: status, DOCUMENTATION_SITE: "NOT_AFFECTED_WITH_EVIDENCE"})
    item = _reconcile(ledger)
    assert item.status == audit.OPEN_STATUS
    assert item.reason == "v24 is still present at src/client.py:3"


def test_the_reason_lists_every_surviving_site_in_order() -> None:
    later = ("src/report.py", 9, 'client = Client(version="v24")')
    ledger = _ledger({later: "AFFECTED", SOURCE_SITE: "UNKNOWN"})
    item = _reconcile(ledger)
    assert item.status == audit.OPEN_STATUS
    assert item.reason == "v24 is still present at src/client.py:3, src/report.py:9"
    assert len(item.evidence_ids) == 2


def test_a_site_carried_by_a_live_and_an_explained_candidate_stays_live() -> None:
    ledger = _ledger({SOURCE_SITE: "AFFECTED"})
    record = ledger.evidence[0]
    explained: dict[str, Any] = {
        **ledger.candidates[0],
        "id": content_id({"candidate": "explained-twin"}),
        "claim_key": "src/client.py:3#twin",
        "status": "NOT_AFFECTED_WITH_EVIDENCE",
        "close_with": None,
    }
    twin = Ledger(
        provider="mock",
        run_id=RUN,
        proof_scope_hash=SCOPE,
        evidence=(record,),
        candidates=(ledger.candidates[0], explained),
    )
    assert _reconcile(twin).status == audit.OPEN_STATUS


def test_the_present_and_version_checks_are_untouched_by_the_role_filter() -> None:
    ledger = _ledger({DOCUMENTATION_SITE: "NOT_AFFECTED_WITH_EVIDENCE"})
    obligation = ObligationView(
        id=content_id({"obligation": "present-v24"}),
        provider_change_id="v24",
        evidence_ids=(),
        current_state="",
        required_state="",
        repair_class="DETERMINISTIC",
        verification_method=method("present", "v24"),
        status="OPEN",
    )
    result = audit.run(ledger, _changes(), [obligation])
    assert result.reconciliations[0].status == audit.DISCHARGED_STATUS
