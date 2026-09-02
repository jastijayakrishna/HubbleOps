from __future__ import annotations

from pathlib import Path
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hubbleops.closure.source_closure import Classification, ClosureEntry, SourceClosure
from hubbleops.core.candidate import OPEN_STATUSES, STATUSES
from hubbleops.core.canonical import EMPTY_SHA256, content_id
from hubbleops.core.evidence import make_evidence
from hubbleops.observe import ledger

RUN_ID = content_id({"run": 1})
SCOPE_HASH = content_id({"scope": 1})
PATHS = ("a.py", "b/c.js", "d/e/f.php", "requirements.txt")

SCANNED_CLOSURE = SourceClosure(
    root=Path("."),
    repo_sha=None,
    entries=tuple(
        ClosureEntry(
            path=path,
            classification=Classification.INSIDE,
            reason="first-party source",
            blob_sha=EMPTY_SHA256,
            size=0,
        )
        for path in PATHS
    ),
    control_directories=(),
)
SUBJECTS = ("v22", "v23", "sdk-name", "API_VERSION")
ECOSYSTEMS = ("python", "javascript", "php")

SETTINGS = settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


@st.composite
def evidence_record(draw: st.DrawFn) -> dict[str, Any]:
    claim_type = draw(
        st.sampled_from(
            (
                "call_version",
                "config_reference",
                "dependency_state",
                "endpoint_reference",
                "external_boundary",
                "file_unscanned",
                "package_reference",
                "request_text",
                "sdk_installed",
                "surface_reference",
            )
        )
    )
    path = draw(st.sampled_from(PATHS))
    line: int | None = draw(st.integers(min_value=1, max_value=40))
    subject: str | None = draw(st.sampled_from(SUBJECTS))
    observer = "deps" if claim_type in ("sdk_installed", "dependency_state") else "text"
    value: Any

    if claim_type == "call_version":
        value = {
            "pattern": "carrier",
            "kind": "version_carrier",
            "slot": draw(st.sampled_from(("per_call", "client_init", "sdk_default"))),
        }
        subject = draw(st.one_of(st.none(), st.sampled_from(("v22", "v23"))))
    elif claim_type == "sdk_installed":
        ecosystem = draw(st.sampled_from(ECOSYSTEMS))
        if draw(st.booleans()):
            value = {
                "state": "PRESENT",
                "ecosystem": ecosystem,
                "package": draw(st.sampled_from(("sdk-name", "other-pkg"))),
                "version": draw(st.one_of(st.none(), st.just("22.1.0"))),
                "spec": "^22.0",
                "source_kind": draw(st.sampled_from(("lock", "manifest"))),
            }
        else:
            value = {
                "state": "ABSENT",
                "ecosystem": ecosystem,
                "source_kind": "lock",
                "locks": ["lock.file"],
            }
            subject = None
            line = None
    elif claim_type == "dependency_state":
        state = draw(st.sampled_from(("NO_MANIFEST", "UNRESOLVED_ECOSYSTEM", "UNPARSABLE")))
        value = {
            "state": state,
            "ecosystem": draw(st.sampled_from(ECOSYSTEMS)),
            "manifests": ["manifest.file"],
            "detail": "parse failure",
        }
        subject = None
        line = None
    elif claim_type in ("file_unscanned", "external_boundary"):
        value = {"reason": draw(st.sampled_from(("binary_opaque", "io_error", "symlink")))}
        subject = None
        line = None
    else:
        value = {"pattern": "literal", "kind": "identifier"}

    return make_evidence(
        run_id=RUN_ID,
        proof_scope_hash=SCOPE_HASH,
        claim_type=claim_type,
        observer=observer,
        repo_sha=None,
        path=path,
        line_start=line,
        line_end=line,
        source_hash=EMPTY_SHA256,
        value=value,
        provider_subject=subject,
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="RAW",
    )


def build(records: list[dict[str, Any]]) -> ledger.Ledger:
    return ledger.build(
        provider="p",
        run_id=RUN_ID,
        proof_scope_hash=SCOPE_HASH,
        evidence=records,
        closure=SCANNED_CLOSURE,
    )


@SETTINGS
@given(st.lists(evidence_record(), min_size=1, max_size=25))
def test_the_ledger_never_persists_an_unexplained_candidate(records: list[dict[str, Any]]) -> None:
    book = build(records)
    assert book.unexplained() == 0
    assert book.counts()["unexplained"] == 0


@SETTINGS
@given(st.lists(evidence_record(), min_size=1, max_size=25))
def test_every_candidate_has_a_status_from_the_frozen_set(records: list[dict[str, Any]]) -> None:
    for candidate in build(records).candidates:
        assert candidate["status"] in STATUSES


@SETTINGS
@given(st.lists(evidence_record(), min_size=1, max_size=25))
def test_every_open_candidate_carries_a_closing_instruction(
    records: list[dict[str, Any]],
) -> None:
    for candidate in build(records).candidates:
        if candidate["status"] in OPEN_STATUSES:
            assert candidate["close_with"]


@SETTINGS
@given(st.lists(evidence_record(), min_size=1, max_size=25))
def test_every_observation_is_attached_to_exactly_one_candidate(
    records: list[dict[str, Any]],
) -> None:
    book = build(records)
    attachments: dict[str, int] = {}
    for candidate in book.candidates:
        for evidence_id in candidate["evidence_ids"]:
            attachments[evidence_id] = attachments.get(evidence_id, 0) + 1
    for record in records:
        assert attachments.get(record["id"]) == 1


@SETTINGS
@given(
    st.lists(evidence_record(), min_size=1, max_size=15),
    st.lists(evidence_record(), min_size=1, max_size=15),
)
def test_a_candidate_never_disappears_when_evidence_arrives(
    first: list[dict[str, Any]], second: list[dict[str, Any]]
) -> None:
    before = {candidate["id"] for candidate in build(first).candidates}
    after = {candidate["id"] for candidate in build(first + second).candidates}
    assert before <= after


@SETTINGS
@given(st.lists(evidence_record(), min_size=1, max_size=20))
def test_the_ledger_is_order_independent(records: list[dict[str, Any]]) -> None:
    forward = build(records).export()
    backward = build(list(reversed(records))).export()
    assert forward == backward
