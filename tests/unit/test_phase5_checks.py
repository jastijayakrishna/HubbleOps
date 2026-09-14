from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from hubbleops.app import decision, verification
from hubbleops.core.candidate import DECISION_REASON_PREFIX, candidate_identity, make_candidate
from hubbleops.core.canonical import content_id
from hubbleops.core.evidence import AI_DERIVATION, make_evidence, observation_identity
from hubbleops.core.verification import (
    ChangeSet,
    FalsifierInput,
    FalsifierOutcome,
    Hunk,
    OracleOutcome,
    SubjectChange,
    SuiteCase,
    SuiteRun,
)
from hubbleops.graph import Definition, ImportGraph, SourceRange
from hubbleops.observe.ledger import Ledger
from hubbleops.verify import (
    audit,
    behavior,
    conserve,
    coverage,
    falsify,
    gitdiff,
    oracle,
    radius,
    runners,
    suites,
)
from hubbleops.verify.suites import suite_paths

SCOPE = "0" * 64
RUN = "1" * 64
CANDIDATE_SCOPE = "2" * 64
CANDIDATE_RUN = "3" * 64


def capture_event(
    *, version: str = "v2", request_text: str | None = "SELECT campaigns.id FROM campaigns"
) -> dict[str, Any]:
    return {
        "version": version,
        "service": "MockService",
        "method": "Search",
        "request_text": request_text,
        "request_type": "rest",
        "stack": [],
        "ts": "2026-09-09T00:00:00Z",
        "mode": "hook",
    }


def evidence(
    path: str,
    claim_type: str,
    value: Any,
    derivation: str = "OBSERVED",
    source_hash: str = "a" * 64,
    run_id: str = RUN,
    proof_scope_hash: str = SCOPE,
    repo_sha: str | None = None,
) -> dict[str, Any]:
    return make_evidence(
        run_id=run_id,
        proof_scope_hash=proof_scope_hash,
        claim_type=claim_type,
        observer="text",
        repo_sha=repo_sha,
        path=path,
        line_start=1,
        line_end=1,
        source_hash=source_hash,
        value=value,
        provider_subject=None,
        dependency_context_hash=None,
        derivation=derivation,
        confidence="RAW",
    )


def ledger_of(
    records: list[dict[str, Any]],
    statuses: dict[str, str],
    key_suffix: str = "",
    run_id: str = RUN,
    proof_scope_hash: str = SCOPE,
) -> Ledger:
    candidates: list[dict[str, Any]] = []
    for key, status in sorted(statuses.items()):
        attached = [item["id"] for item in records if item["path"] == key]
        candidates.append(
            make_candidate(
                candidate_id=candidate_identity("p", "surface_reference", f"{key}{key_suffix}"),
                run_id=run_id,
                proof_scope_hash=proof_scope_hash,
                provider="p",
                evidence_ids=attached,
                status=status,
                reason="fixture",
                close_with="run the closing instruction" if status == "UNKNOWN" else None,
            )
        )
    return Ledger(
        provider="p",
        run_id=run_id,
        proof_scope_hash=proof_scope_hash,
        evidence=tuple(records),
        candidates=tuple(candidates),
    )


def test_an_unknown_closed_without_evidence_is_a_conservation_failure() -> None:
    before = evidence("a.py", "surface_reference", {"pattern": "x"})
    base = ledger_of([before], {"a.py": "UNKNOWN"})
    candidate = ledger_of([before], {"a.py": "NOT_AFFECTED_WITH_EVIDENCE"})
    result = conserve.compare(base, candidate)
    assert result.report().passed is False
    assert result.violations[0].candidate_id == candidate.candidates[0]["id"]
    assert "no new evidence" in result.violations[0].justification


def test_an_unknown_closed_without_evidence_still_fails_across_two_scan_runs() -> None:
    before = evidence("a.py", "surface_reference", {"pattern": "x"}, repo_sha="b" * 40)
    after = evidence(
        "a.py",
        "surface_reference",
        {"pattern": "x"},
        run_id=CANDIDATE_RUN,
        proof_scope_hash=CANDIDATE_SCOPE,
        repo_sha="c" * 40,
    )
    base = ledger_of([before], {"a.py": "UNKNOWN"})
    candidate = ledger_of(
        [after],
        {"a.py": "NOT_AFFECTED_WITH_EVIDENCE"},
        run_id=CANDIDATE_RUN,
        proof_scope_hash=CANDIDATE_SCOPE,
    )
    result = conserve.compare(base, candidate)
    assert result.report().passed is False, (
        "law L3: base and candidate are always separate runs, so evidence that only repeats "
        "a base observation under a new run id is not new evidence"
    )
    assert "no new evidence" in result.violations[0].justification


def test_a_repeated_observation_under_a_new_run_is_not_new_evidence() -> None:
    before = evidence("a.py", "surface_reference", {"pattern": "x"}, repo_sha="b" * 40)
    after = evidence(
        "a.py",
        "surface_reference",
        {"pattern": "x"},
        run_id=CANDIDATE_RUN,
        proof_scope_hash=CANDIDATE_SCOPE,
        repo_sha="c" * 40,
    )
    assert before["id"] != after["id"]
    assert observation_identity(before) == observation_identity(after)


def test_a_changed_observation_under_a_new_run_is_new_evidence() -> None:
    before = evidence("a.py", "surface_reference", {"pattern": "x"}, repo_sha="b" * 40)
    after = evidence(
        "a.py",
        "surface_reference",
        {"pattern": "y"},
        run_id=CANDIDATE_RUN,
        proof_scope_hash=CANDIDATE_SCOPE,
        repo_sha="c" * 40,
    )
    assert observation_identity(before) != observation_identity(after)


def test_editing_the_enclosing_file_is_not_a_new_observation() -> None:
    before = evidence("a.py", "surface_reference", {"pattern": "x"}, source_hash="a" * 64)
    after = evidence(
        "a.py",
        "surface_reference",
        {"pattern": "x"},
        source_hash="b" * 64,
        run_id=CANDIDATE_RUN,
        proof_scope_hash=CANDIDATE_SCOPE,
    )
    assert observation_identity(before) == observation_identity(after), (
        "law L3: source_hash is the blob hash of the enclosing file, so any edit anywhere in "
        "that file would otherwise turn every observation it holds into new evidence"
    )


def test_an_unknown_closed_after_the_file_was_rewritten_is_a_conservation_failure() -> None:
    before = evidence("a.py", "surface_reference", {"pattern": "x"}, source_hash="a" * 64)
    after = evidence(
        "a.py",
        "surface_reference",
        {"pattern": "x"},
        source_hash="b" * 64,
        run_id=CANDIDATE_RUN,
        proof_scope_hash=CANDIDATE_SCOPE,
    )
    base = ledger_of([before], {"a.py": "UNKNOWN"})
    candidate = ledger_of(
        [after],
        {"a.py": "NOT_AFFECTED_WITH_EVIDENCE"},
        run_id=CANDIDATE_RUN,
        proof_scope_hash=CANDIDATE_SCOPE,
    )
    result = conserve.compare(base, candidate)
    assert result.report().passed is False, (
        "law L3: rewriting the file an observation sits in is not evidence about the "
        "observation, so an UNKNOWN cannot close on it"
    )
    assert "no new evidence" in result.violations[0].justification


def test_an_unknown_closed_on_new_evidence_conserves() -> None:
    before = evidence("a.py", "surface_reference", {"pattern": "x"})
    fresh = evidence("a.py", "surface_reference", {"pattern": "y"})
    base = ledger_of([before], {"a.py": "UNKNOWN"})
    candidate = ledger_of([before, fresh], {"a.py": "NOT_AFFECTED_WITH_EVIDENCE"})
    assert conserve.compare(base, candidate).report().passed is True


def test_ai_derived_evidence_alone_never_closes_an_unknown() -> None:
    before = evidence("a.py", "surface_reference", {"pattern": "x"})
    guess = evidence("a.py", "surface_reference", {"pattern": "y"}, derivation=AI_DERIVATION)
    base = ledger_of([before], {"a.py": "UNKNOWN"})
    candidate = ledger_of([before, guess], {"a.py": "NOT_AFFECTED_WITH_EVIDENCE"})
    result = conserve.compare(base, candidate)
    assert result.report().passed is False, (
        "law L10: AI-derived evidence cannot alone close an UNKNOWN"
    )


def test_a_recorded_human_decision_closes_an_unknown() -> None:
    before = evidence("a.py", "surface_reference", {"pattern": "x"})
    base = ledger_of([before], {"a.py": "UNKNOWN"})
    candidate = ledger_of([before], {"a.py": "NOT_AFFECTED_WITH_EVIDENCE"})
    decision = {"id": "d-1", "candidate_id": candidate.candidates[0]["id"]}
    assert conserve.compare(base, candidate, (decision,)).report().passed is True


def test_a_source_bound_decision_cannot_survive_candidate_blob_drift() -> None:
    before = evidence("a.py", "surface_reference", {"pattern": "x"})
    after = evidence(
        "a.py",
        "surface_reference",
        {"pattern": "x"},
        source_hash="b" * 64,
        run_id=CANDIDATE_RUN,
        proof_scope_hash=CANDIDATE_SCOPE,
    )
    base = ledger_of([before], {"a.py": "UNKNOWN"})
    candidate = ledger_of(
        [after], {"a.py": "UNKNOWN"}, run_id=CANDIDATE_RUN, proof_scope_hash=CANDIDATE_SCOPE
    )
    body: dict[str, Any] = {
        "run_id": RUN,
        "proof_scope_hash": SCOPE,
        "candidate_id": base.candidates[0]["id"],
        "blob_hash": "a" * 64,
        "path": "a.py",
        "line_start": 1,
        "claim_type": "surface_reference",
        "value": "HUMAN_ACCEPTED_RISK",
        "by": "Reviewer",
    }
    record = {"id": content_id(body), **body}
    with pytest.raises(decision.DecisionInvalid, match="not keyed to evidence"):
        decision.apply(candidate, (record,), decided_run_id=RUN, decided_proof_scope_hash=SCOPE)


def test_a_decision_from_a_foreign_run_never_applies_to_the_candidate() -> None:
    before = evidence("a.py", "surface_reference", {"pattern": "x"})
    base = ledger_of([before], {"a.py": "UNKNOWN"})
    body: dict[str, Any] = {
        "run_id": CANDIDATE_RUN,
        "proof_scope_hash": CANDIDATE_SCOPE,
        "candidate_id": base.candidates[0]["id"],
        "blob_hash": "a" * 64,
        "path": "a.py",
        "line_start": 1,
        "claim_type": "surface_reference",
        "value": "HUMAN_ACCEPTED_RISK",
        "by": "Reviewer",
    }
    record = {"id": content_id(body), **body}
    with pytest.raises(decision.DecisionInvalid, match="different ProofScope or run"):
        decision.apply(base, (record,), decided_run_id=RUN, decided_proof_scope_hash=SCOPE)


def test_a_new_unknown_is_preserved_not_a_violation() -> None:
    before = evidence("a.py", "surface_reference", {"pattern": "x"})
    base = ledger_of([before], {"a.py": "NOT_AFFECTED_WITH_EVIDENCE"})
    candidate = ledger_of([before], {"a.py": "UNKNOWN"})
    result = conserve.compare(base, candidate)
    assert result.report().passed is True
    assert [item["close_with"] for item in result.preserved_unknowns()] == [
        "run the closing instruction"
    ]


def test_a_request_the_oracle_could_not_read_is_unresolved_not_accepted() -> None:
    holed = evidence(
        "q.py",
        "request_text",
        {"skeleton": {"fragments": ["select "], "holes": ["$FIELD"]}},
    )
    book = ledger_of([holed], {"q.py": "UNKNOWN"})
    requests, unreachable = oracle.requests_from(book, ())
    assert requests == ()
    assert unreachable == ("q.py:1",)
    report = oracle.review(_AcceptingOracle(), requests, "v2", unreachable=unreachable).report()
    assert report.passed is True
    assert report.unresolved, (
        "a request the oracle never saw is unproven, not proven; it must reach the verdict as "
        "unresolved rather than as a silent pass"
    )


def test_a_request_site_closed_by_a_recorded_decision_is_decided_not_unreachable() -> None:
    holed = evidence(
        "q.py",
        "request_text",
        {"skeleton": {"fragments": ["select "], "holes": ["$FIELD"]}},
    )
    book = ledger_of([holed], {"q.py": "HUMAN_ACCEPTED_RISK"})
    decided_book = Ledger(
        provider=book.provider,
        run_id=book.run_id,
        proof_scope_hash=book.proof_scope_hash,
        evidence=book.evidence,
        candidates=tuple(
            {**item, "reason": f"{DECISION_REASON_PREFIX}abc by owner for source blob {'a' * 64}"}
            for item in book.candidates
        ),
    )
    sites = oracle.request_sites(decided_book, ())
    assert sites.requests == ()
    assert sites.unreachable == ()
    assert sites.decided == ("q.py:1",)
    report = oracle.review(
        _AcceptingOracle(),
        sites.requests,
        "v2",
        unreachable=sites.unreachable,
        decided=sites.decided,
    ).report()
    assert report.unresolved == ()
    assert report.detail["decided"] == ["q.py:1"]


def test_a_request_site_with_the_same_status_but_no_decision_stays_unreachable() -> None:
    holed = evidence(
        "q.py",
        "request_text",
        {"skeleton": {"fragments": ["select "], "holes": ["$FIELD"]}},
    )
    sites = oracle.request_sites(ledger_of([holed], {"q.py": "HUMAN_ACCEPTED_RISK"}), ())
    assert sites.unreachable == ("q.py:1",)
    assert sites.decided == ()


def test_a_readable_request_reaches_the_oracle() -> None:
    plain = evidence(
        "q.py",
        "request_text",
        {"skeleton": {"fragments": ["select campaigns.id"], "holes": []}},
    )
    book = ledger_of([plain], {"q.py": "AFFECTED"})
    requests, unreachable = oracle.requests_from(book, ())
    assert len(requests) == 1
    assert unreachable == ()
    review = oracle.review(_AcceptingOracle(), requests, "v2", unreachable=unreachable)
    assert review.report().passed is True
    assert review.report().unresolved == ()
    assert review.checks[0].request_hash


def test_a_phase4_capture_reaches_the_oracle_as_a_request_mapping() -> None:
    requests, unreachable = oracle.requests_from(ledger_of([], {}), (capture_event(),))
    assert unreachable == ()
    assert len(requests) == 1
    assert requests[0][1] == {
        "service": "MockService",
        "method": "Search",
        "request": {"query": "SELECT campaigns.id FROM campaigns"},
        "origin": "captured",
    }


def test_a_json_capture_preserves_the_full_request_body() -> None:
    event = capture_event(request_text=json.dumps({"operations": [{"remove": "customers/1"}]}))
    event["method"] = "Mutate"
    requests, unreachable = oracle.requests_from(ledger_of([], {}), (event,))
    assert unreachable == ()
    assert requests[0][1]["request"] == {"operations": [{"remove": "customers/1"}]}


def test_captured_request_shapes_include_the_api_version() -> None:
    before = behavior.captured_shapes((capture_event(version="v1"),))[0]
    after = behavior.captured_shapes((capture_event(version="v2"),))[0]
    assert before.identity() != after.identity()
    assert "@v1" in before.identity() and "@v2" in after.identity()


def test_captured_request_shapes_walk_objects_inside_arrays() -> None:
    event = capture_event(
        request_text=json.dumps(
            {"operations": [{"create": {"name": "x"}}, {"remove": "customers/1"}]}
        )
    )
    event["method"] = "Mutate"
    shape = behavior.captured_shapes((event,))[0]
    assert shape.fields == ("operations[].create.name", "operations[].remove")


class _AcceptingOracle:
    def validate(self, request: Mapping[str, Any], version: str) -> OracleOutcome:
        return OracleOutcome(code="VALID", reason="fixture accepts", authority="CATALOG")


class _LiveOracle:
    def validate(self, request: Mapping[str, Any], version: str) -> OracleOutcome:
        return OracleOutcome(code="VALID", reason="provider accepts", authority="LIVE")


class _UnattributedOracle:
    def validate(self, request: Mapping[str, Any], version: str) -> OracleOutcome:
        return OracleOutcome(code="VALID", reason="accepts without saying who says so")


class _InvalidOracle:
    def validate(self, request: Mapping[str, Any], version: str) -> Any:
        return OracleOutcome(code=cast(Any, "BROKEN"), reason="invalid")


def test_an_invalid_oracle_code_is_unknown_not_accepted() -> None:
    review = oracle.review(_InvalidOracle(), (("a" * 64, {"origin": "captured"}),), "v2")
    assert review.accepted() == ()
    assert review.report().passed is True
    assert any("no valid outcome code" in item for item in review.report().unresolved)


def test_an_acceptance_that_names_no_authority_is_refused() -> None:
    review = oracle.review(_UnattributedOracle(), (("a" * 64, {"origin": "captured"}),), "v2")
    assert review.accepted() == ()
    assert review.available is False
    assert review.authority() == "ORACLE_UNAVAILABLE"
    assert "without naming an authority" in review.checks[0].reason


def test_the_review_reports_the_weakest_authority_that_accepted_anything() -> None:
    requests = (("a" * 64, {"origin": "captured"}),)
    assert oracle.review(_LiveOracle(), requests, "v2").authority() == "LIVE"
    assert oracle.review(_AcceptingOracle(), requests, "v2").authority() == "CATALOG"


def test_an_oracle_that_saw_nothing_while_subjects_changed_is_unresolved() -> None:
    silent = oracle.review(_AcceptingOracle(), (), "v2", subjects_changed=True).report()
    assert silent.passed is True
    assert any("proves nothing" in item for item in silent.unresolved)

    quiet = oracle.review(_AcceptingOracle(), (), "v2", subjects_changed=False).report()
    assert quiet.unresolved == (), "a Change Pack that changes nothing has nothing to validate"


def test_an_empty_falsifier_set_is_unresolved_when_the_contract_changed() -> None:
    changed = ChangeSet(
        from_version="v1",
        to_version="v2",
        pair_hash="h",
        changes=(SubjectChange("campaigns.gone", "REMOVED", None, "VALID", ""),),
    )
    report = falsify.run((), ledger_of([], {}), changed, "/candidate").report()
    assert report.passed is True
    assert any("proves nothing" in item for item in report.unresolved)


class _InvalidFalsifier:
    name = "invalid"
    failure_class = "surface_reference"

    def check(self, subject: FalsifierInput) -> FalsifierOutcome:
        return cast(FalsifierOutcome, object())


def test_an_invalid_falsifier_result_is_unknown_not_a_pass() -> None:
    book = ledger_of(
        [evidence("a.py", "surface_reference", {"pattern": "x"})], {"a.py": "AFFECTED"}
    )
    review = falsify.run((_InvalidFalsifier(),), book, ChangeSet("v1", "v2", "h", ()), ".")
    assert review.report().passed is True
    assert any("no valid" in item for item in review.report().unresolved)
    changed = ChangeSet(
        from_version="v1",
        to_version="v2",
        pair_hash="h",
        changes=(SubjectChange("campaigns.gone", "REMOVED", None, "VALID", ""),),
    )
    review = falsify.run((_RequestFalsifier(),), book, changed, "/candidate")
    assert review.runs[0].result == "SKIPPED"
    assert review.executed() == ()
    report = review.report()
    assert report.passed is True
    assert any("skipped" in item for item in report.unresolved), (
        "a falsifier set that never ran cannot make falsifiers_pass mean anything"
    )


def test_a_falsifier_that_ran_leaves_no_disarmed_note() -> None:
    book = ledger_of(
        [evidence("a.py", "request_text", {"skeleton": {"fragments": ["q"]}})], {"a.py": "AFFECTED"}
    )
    changed = ChangeSet(
        from_version="v1",
        to_version="v2",
        pair_hash="h",
        changes=(SubjectChange("campaigns.gone", "REMOVED", None, "VALID", ""),),
    )
    review = falsify.run((_RequestFalsifier(),), book, changed, "/candidate")
    assert review.runs[0].result == "PASS"
    assert review.report().unresolved == ()


def test_an_unknown_that_vanishes_entirely_is_a_conservation_failure() -> None:
    before = evidence("a.py", "surface_reference", {"pattern": "x"})
    after = evidence("a.py", "surface_reference", {"pattern": "y"})
    base = ledger_of([before], {"a.py": "UNKNOWN"})
    candidate = ledger_of([after], {"a.py": "NOT_AFFECTED_WITH_EVIDENCE"}, key_suffix=":moved")
    result = conserve.compare(base, candidate)
    assert result.report().passed is False, (
        "an UNKNOWN whose candidate id is simply gone was dropped, not closed; silently skipping "
        "it is how a conservation check passes without checking anything"
    )
    assert any("dropped rather than closed" in item.justification for item in result.violations)


def test_an_unknown_that_moved_but_stayed_open_is_not_a_violation() -> None:
    before = evidence("a.py", "surface_reference", {"pattern": "x"})
    after = evidence("a.py", "surface_reference", {"pattern": "y"})
    base = ledger_of([before], {"a.py": "UNKNOWN"})
    candidate = ledger_of([after], {"a.py": "UNKNOWN"}, key_suffix=":moved")
    assert conserve.compare(base, candidate).report().passed is True


def test_an_unknown_on_a_path_the_candidate_no_longer_observes_is_not_a_violation() -> None:
    before = evidence("gone.py", "surface_reference", {"pattern": "x"})
    elsewhere = evidence("b.py", "surface_reference", {"pattern": "y"})
    base = ledger_of([before], {"gone.py": "UNKNOWN"})
    candidate = ledger_of([elsewhere], {"b.py": "AFFECTED"})
    assert conserve.compare(base, candidate).report().passed is True


class _RequestFalsifier:
    name = "request_only"
    failure_class = "request_text"

    def check(self, subject: object) -> FalsifierOutcome:
        return FalsifierOutcome(result="PASS", reason="fixture")


def test_a_hunk_parses_into_line_spans() -> None:
    unified = (
        "diff --git a/one.py b/one.py\n"
        "--- a/one.py\n"
        "+++ b/one.py\n"
        "@@ -4,2 +4,3 @@\n"
        "-old\n"
        "-older\n"
        "+new\n"
        "+newer\n"
        "+newest\n"
    )
    hunks = gitdiff.parse_hunks(unified)
    assert len(hunks) == 1
    hunk = hunks[0]
    assert (hunk.path, hunk.new_start, hunk.new_lines) == ("one.py", 4, 3)
    assert hunk.added == ("new", "newer", "newest")
    assert hunk.removed == ("old", "older")
    assert hunk.spans(4) and hunk.spans(6) and not hunk.spans(7)


def test_a_pure_deletion_hunk_spans_its_anchor() -> None:
    unified = "diff --git a/one.py b/one.py\n@@ -4,2 +3,0 @@\n-gone\n-also gone\n"
    hunk = gitdiff.parse_hunks(unified)[0]
    assert (hunk.new_start, hunk.new_lines) == (3, 0)
    assert hunk.spans(3) and not hunk.spans(4)


def _empty_graph() -> ImportGraph:
    return ImportGraph((), (), (), (), (), (), (), (), (), (), (), ())


def test_a_binary_change_is_never_absent_from_containment() -> None:
    delta = gitdiff.Delta("a" * 40, "b" * 40, (), ("asset.bin",), ("asset.bin",))
    result = radius.contain(delta, (), (), _empty_graph())
    assert len(result.mappings) == 1
    assert result.unexplained()[0].hunk.path == "asset.bin"


def test_git_diff_materializes_a_binary_change_as_a_containment_hunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_git(repository: Path, arguments: tuple[str, ...], executable: str) -> str:
        if "--numstat" in arguments:
            return "-\t-\tasset.bin\n"
        return "diff --git a/asset.bin b/asset.bin\nBinary files differ\n"

    monkeypatch.setattr(gitdiff, "_git", fake_git)
    delta = gitdiff.read(tmp_path, "a" * 40, "b" * 40)
    assert delta.binary_paths == ("asset.bin",)
    assert [hunk.path for hunk in delta.all_hunks()] == ["asset.bin"]


def test_a_lockfile_is_not_collateral_without_a_dependency_obligation() -> None:
    delta = gitdiff.Delta(
        "a" * 40,
        "b" * 40,
        (Hunk("uv.lock", 1, 1, 1, 1, ("new package",), ("old package",)),),
        ("uv.lock",),
    )
    assert radius.contain(delta, (), (), _empty_graph()).unexplained()


def test_a_lockfile_is_collateral_only_to_a_dependency_obligation() -> None:
    from hubbleops.core.verification import ObligationView

    delta = gitdiff.Delta(
        "a" * 40,
        "b" * 40,
        (Hunk("uv.lock", 1, 1, 1, 1, ("new package",), ("old package",)),),
        ("uv.lock",),
    )
    obligation = ObligationView(
        id="o",
        provider_change_id="sdk",
        evidence_ids=(),
        current_state="pyproject.toml:1 pins the old SDK",
        required_state="pyproject.toml:1 pins the new SDK",
        repair_class="DETERMINISTIC",
        verification_method="version:v2",
        status="OPEN",
    )
    result = radius.contain(delta, (obligation,), (), _empty_graph())
    assert result.collateral()[0].reason.endswith("dependency obligation")


def test_a_top_level_source_change_enters_unknown_blast_without_coverage() -> None:
    delta = gitdiff.Delta(
        "a" * 40,
        "b" * 40,
        (Hunk("app.py", 1, 1, 1, 1, ("VALUE = 2",), ("VALUE = 1",)),),
        ("app.py",),
    )
    frozen = SuiteRun("FROZEN_BASELINE", True, 1, 0, 0, "COMPLETED", "", ())
    assert radius.blast(delta, _empty_graph(), frozen).unknown_blast == ("app.py",)


def test_a_definition_deleted_from_the_base_graph_enters_unknown_blast() -> None:
    delta = gitdiff.Delta(
        "a" * 40,
        "b" * 40,
        (Hunk("old.py", 1, 3, 0, 0, (), ("def old():", "    return 1", "")),),
        ("old.py",),
    )
    old = Definition(
        id="old-definition",
        path="old.py",
        language="python",
        name="old",
        parameters=(),
        range=SourceRange(0, 24, 1, 3),
        asynchronous=False,
    )
    baseline = ImportGraph((old,), (), (), (), (), (), (), (), (), (), (), ())
    frozen = SuiteRun("FROZEN_BASELINE", True, 1, 0, 0, "COMPLETED", "", ())
    result = radius.blast(delta, _empty_graph(), frozen, base_graph=baseline)
    assert result.changed_definitions == ("old-definition",)
    assert result.unknown_blast == ("old.py",)


def test_the_reach_bound_is_reported_not_silently_truncated() -> None:
    assert radius.MAX_REACH_HOPS == 5


def test_coverage_reads_only_what_a_passing_test_executed() -> None:
    run = SuiteRun(
        source="FROZEN_BASELINE",
        executed=True,
        passed=1,
        failed=1,
        skipped=0,
        outcome="COMPLETED",
        reason="",
        tests=(
            SuiteCase("t_ok", "passed", ("a.py",)),
            SuiteCase("t_bad", "failed", ("b.py",)),
        ),
    )
    assert run.covered_files() == frozenset({"a.py"})
    assert run.all_passed() is False


def test_a_failed_frozen_run_covers_nothing() -> None:
    run = SuiteRun(
        source="FROZEN_BASELINE",
        executed=True,
        passed=0,
        failed=1,
        skipped=0,
        outcome="COMPLETED",
        reason="",
        tests=(SuiteCase("t", "passed", ("a.py",)),),
    )
    report = radius.frozen_report(run)
    assert report.passed is False


def test_a_frozen_run_that_never_started_is_unresolved_not_a_failure() -> None:
    run = SuiteRun(
        source="FROZEN_BASELINE",
        executed=False,
        passed=0,
        failed=0,
        skipped=0,
        outcome="NO_FROZEN_TESTS",
        reason="the base SHA carries no test directory",
    )
    report = radius.frozen_report(run)
    assert report.passed is True
    assert report.unresolved


def test_source_extinction_independently_catches_a_split_removed_subject(tmp_path: Path) -> None:
    source = tmp_path / "reader.py"
    source.write_text('key = "campaigns." + "legacy"\nvalue = row[key]\n', encoding="utf-8")
    changes = ChangeSet(
        from_version="v1",
        to_version="v2",
        pair_hash="h",
        changes=(SubjectChange("campaigns.legacy", "REMOVED", "campaigns.name", "VALID", ""),),
    )
    result = audit.run(ledger_of([], {}), changes, (), tmp_path, ("reader.py",))
    assert result.reintroduced == (
        "campaigns.legacy at reader.py:1 (independent source extinction)",
    )
    assert result.report().passed is False


def test_source_extinction_handles_multiline_splits_without_matching_superstrings(
    tmp_path: Path,
) -> None:
    source = tmp_path / "reader.py"
    source.write_text(
        'key = ("campaigns."\\\n    + "legacy")\nother = "campaigns.legacy_value"\n',
        encoding="utf-8",
    )
    changes = ChangeSet(
        from_version="v1",
        to_version="v2",
        pair_hash="h",
        changes=(SubjectChange("campaigns.legacy", "REMOVED", "campaigns.name", "VALID", ""),),
    )
    result = audit.run(ledger_of([], {}), changes, (), tmp_path, ("reader.py",))
    assert result.reintroduced == (
        "campaigns.legacy at reader.py:1 (independent source extinction)",
    )


def test_an_all_skipped_frozen_suite_is_unresolved_not_a_failure() -> None:
    run = SuiteRun(
        source="FROZEN_BASELINE",
        executed=True,
        passed=0,
        failed=0,
        skipped=3,
        outcome="COMPLETED",
        reason="",
    )
    report = radius.frozen_report(run)
    assert report.passed is True
    assert report.unresolved == ("frozen_baseline_tests: NO_PASSING_TESTS",)


def test_an_abnormal_pytest_exit_never_becomes_a_completed_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan = suites.FrozenSuitePlan(
        workspace=workspace,
        report=workspace / "coverage.jsonl",
        plugin=workspace / "plugin.py",
        paths=("tests",),
        layout=runners.SuiteLayout(
            runner="pytest", languages=("python",), paths=("tests",), project="."
        ),
    )

    def abnormal_pytest(
        argv: tuple[str, ...],
        wall_seconds: float,
        output_bytes: int,
        name: str,
        environment: dict[str, str],
        working_directory: str,
    ) -> tuple[str, int | None, bytes, bytes]:
        return "COMPLETED", 3, b"1 passed", b"internal error"

    def passing_report(path: Path) -> tuple[SuiteCase, ...]:
        return (SuiteCase("test_ok", "passed", ("app.py",)),)

    monkeypatch.setattr(suites, "bounded_process", abnormal_pytest)
    monkeypatch.setattr(coverage, "read_report", passing_report)
    result = suites.run_frozen(plan, "python", 1)
    assert result.outcome == "EXECUTION_FAILED"
    assert result.all_passed() is False


def test_an_untraceable_language_is_named_not_assumed_covered() -> None:
    modules = ("a.py", "b.ts", "c.php")
    languages = {"a.py": "python", "b.ts": "typescript", "c.php": "php"}
    assert coverage.unsupported_modules(modules, languages) == ("b.ts", "c.php")


def test_the_coverage_report_names_a_corrupt_line_as_a_failed_case(tmp_path: Path) -> None:
    report = tmp_path / "coverage.jsonl"
    report.write_bytes(
        b'{"test":"a","outcome":"passed","files":["x.py"]}\n'
        b"not json at all\n"
        b'{"test":"b","outcome":"failed","files":[]}\n'
    )
    outcomes = coverage.read_report(report)
    assert [item.name for item in outcomes] == ["<unparseable report line 2>", "a", "b"]
    assert outcomes[0].outcome == "error"


def test_the_coverage_report_names_a_non_record_line_and_a_truncation_as_failed_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = tmp_path / "coverage.jsonl"
    report.write_bytes(b'[]\n{"test":"a","outcome":"passed","files":[]}\n"x"\n')
    outcomes = coverage.read_report(report)
    assert [(item.name, item.outcome) for item in outcomes] == [
        ("<non-record report line 1>", "error"),
        ("<non-record report line 3>", "error"),
        ("a", "passed"),
    ]
    monkeypatch.setattr(coverage, "MAX_REPORT_BYTES", 8)
    truncated = coverage.read_report(report)
    assert ("<report truncated at 8 bytes>", "error") in [
        (item.name, item.outcome) for item in truncated
    ]


def test_the_coverage_report_keeps_a_record_whose_name_carries_a_line_separator(
    tmp_path: Path,
) -> None:
    report = tmp_path / "coverage.jsonl"
    name = f"test_a{chr(0x2028)}b"
    report.write_text(
        json.dumps({"test": name, "outcome": "passed", "files": ["x.py"]}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    outcomes = coverage.read_report(report)
    assert [(item.name, item.outcome) for item in outcomes] == [(name, "passed")]


def test_the_coverage_plugin_needs_no_third_party_import() -> None:
    source = coverage.plugin_source()
    assert "import coverage" not in source
    assert "sys.monitoring" in source


def test_a_suite_directory_beats_a_loose_name_guess(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("", encoding="utf-8")
    (tmp_path / "test_loose.py").write_text("", encoding="utf-8")
    assert suite_paths(tmp_path) == ("tests",)


def test_a_malformed_capture_is_refused_instead_of_dropped(tmp_path: Path) -> None:
    from hubbleops.app.verification import VerificationInvalid, read_capture

    path = tmp_path / "capture.jsonl"
    path.write_text('{"service":"MockService"}\nnot json\n', encoding="utf-8")
    with pytest.raises(VerificationInvalid, match=r"capture\.jsonl:2"):
        read_capture(path)


def test_a_schema_invalid_capture_is_refused(tmp_path: Path) -> None:
    from hubbleops.app.verification import VerificationInvalid, read_capture

    path = tmp_path / "capture.jsonl"
    path.write_text('{"service":"MockService"}\n', encoding="utf-8")
    with pytest.raises(VerificationInvalid, match=r"capture\.jsonl:1 EVENT_INVALID"):
        read_capture(path)


def test_a_capture_participates_in_extinction_and_falsifier_detection() -> None:
    changes = ChangeSet(
        from_version="v1",
        to_version="v2",
        pair_hash="h",
        changes=(SubjectChange("campaigns.legacy", "REMOVED", None, "VALID", ""),),
    )
    captured = (capture_event(version="v1", request_text="SELECT campaigns.legacy"),)
    result = audit.run(ledger_of([], {}), changes, (), captured=captured)
    assert result.residue == ("captured event 1 (MockService.Search)",)
    assert result.reintroduced == ("campaigns.legacy at captured event 1",)
    assert {"production_version", "request_text"} <= falsify.detected_classes(
        ledger_of([], {}), captured
    )


@pytest.mark.parametrize("reader", ["obligations", "decisions"])
def test_a_scalar_verification_input_is_refused(tmp_path: Path, reader: str) -> None:
    from hubbleops.app import verification

    path = tmp_path / f"{reader}.json"
    path.write_text('"not a list"', encoding="utf-8")
    selected = (
        verification.read_obligations if reader == "obligations" else verification.read_decisions
    )
    with pytest.raises(verification.VerificationInvalid, match="must contain"):
        selected(path)


def test_a_change_set_separates_removal_from_rename() -> None:
    changes = ChangeSet(
        from_version="v1",
        to_version="v2",
        pair_hash="h",
        changes=(
            SubjectChange("a.gone", "REMOVED", None, "VALID", ""),
            SubjectChange("a.old", "REMOVED", "a.new", "VALID", ""),
            SubjectChange("a.odd", "CHANGED", None, "UNKNOWN_PROVIDER_CONTRACT", ""),
        ),
    )
    assert [item.subject for item in changes.removed()] == ["a.gone", "a.old"]
    assert [item.subject for item in changes.renamed()] == ["a.old"]
    assert [item.subject for item in changes.unresolved()] == ["a.odd"]


@pytest.mark.parametrize(
    "path",
    ["hubbleops/verify/verdict.py", "hubbleops/proof/receipt.py", ".hubbleops/decisions.yml"],
)
def test_an_authority_path_is_never_collateral(path: str) -> None:
    assert radius.is_authority_path(path) is True


@pytest.mark.parametrize("path", ["src/app.py", "uv.lock", "tests/test_a.py"])
def test_an_ordinary_path_is_not_an_authority_path(path: str) -> None:
    assert radius.is_authority_path(path) is False


def test_every_verdict_affecting_input_moves_the_verification_manifest_hash() -> None:
    capture = verification.CaptureArtifact(
        (capture_event(),),
        {"repo_sha": "a" * 40, "execution_spec": "one"},
        "b" * 64,
    )
    inputs: dict[str, object] = {
        "base_sha": "a" * 40,
        "candidate_sha": "b" * 40,
        "from_version": "v1",
        "to_version": "v2",
        "obligation_records": ({"id": "o1", "status": "OPEN"},),
        "decisions": ({"id": "d1", "value": "accept"},),
        "base_capture": capture,
        "candidate_capture": capture,
        "frozen_suite": {"paths": ["tests"], "files": {"tests/a.py": "c" * 64}},
    }
    original = verification_input_hash(inputs)
    variants: tuple[Mapping[str, object], ...] = (
        {**inputs, "base_sha": "c" * 40},
        {**inputs, "candidate_sha": "d" * 40},
        {**inputs, "from_version": "v0"},
        {**inputs, "to_version": "v3"},
        {**inputs, "obligation_records": ({"id": "o2", "status": "OPEN"},)},
        {**inputs, "decisions": ({"id": "d2", "value": "reject"},)},
        {**inputs, "base_capture": None},
        {**inputs, "candidate_capture": None},
        {**inputs, "frozen_suite": {"paths": ["spec"], "files": {}}},
    )
    assert all(verification_input_hash(variant) != original for variant in variants)


def verification_input_hash(inputs: Mapping[str, object]) -> str:
    return verification.verification_inputs_hash(
        base_sha=cast(str, inputs["base_sha"]),
        candidate_sha=cast(str, inputs["candidate_sha"]),
        from_version=cast(str, inputs["from_version"]),
        to_version=cast(str, inputs["to_version"]),
        obligation_records=cast(tuple[Mapping[str, Any], ...], inputs["obligation_records"]),
        decisions=cast(tuple[Mapping[str, Any], ...], inputs["decisions"]),
        base_capture=cast(verification.CaptureArtifact | None, inputs["base_capture"]),
        candidate_capture=cast(verification.CaptureArtifact | None, inputs["candidate_capture"]),
        frozen_suite=cast(Mapping[str, Any], inputs["frozen_suite"]),
    )


def test_a_capture_without_an_execution_manifest_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps(capture_event()) + "\n", encoding="utf-8")
    with pytest.raises(verification.VerificationInvalid, match="cannot be read"):
        verification.read_scoped_capture(path, None, "a" * 40, "_mock", "base")


def test_an_input_record_from_another_scope_is_refused(tmp_path: Path) -> None:
    record = {
        "id": "1" * 64,
        "run_id": "2" * 64,
        "proof_scope_hash": "3" * 64,
        "provider_change_id": "change",
        "evidence_ids": ["4" * 64],
        "current_state": "old",
        "required_state": "new",
        "repair_class": "DETERMINISTIC",
        "verification_method": "check",
        "status": "OPEN",
    }
    path = tmp_path / "obligations.json"
    path.write_text(json.dumps([record]), encoding="utf-8")
    with pytest.raises(verification.VerificationInvalid, match="not bound to the base ProofScope"):
        verification.read_obligation_records(path, "5" * 64, "2" * 64)


class _ChangingContextOracle:
    def __init__(self) -> None:
        self.calls = 0

    def context_hash(self) -> str:
        return str(self.calls).zfill(64)

    def validate(self, request: Mapping[str, Any], version: str) -> OracleOutcome:
        self.calls += 1
        return OracleOutcome(code="VALID", reason="changed")


def test_an_oracle_result_is_rejected_if_its_bound_context_moves() -> None:
    contract = _ChangingContextOracle()
    bound = verification.InjectedOracle(cast(Any, contract), contract.context_hash())
    outcome = bound.validate({}, "v2")
    assert outcome.code == "ORACLE_UNAVAILABLE"
    assert "changed while validating" in outcome.reason
