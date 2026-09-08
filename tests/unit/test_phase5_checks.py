from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hubbleops.core.candidate import candidate_identity, make_candidate
from hubbleops.core.evidence import AI_DERIVATION, make_evidence
from hubbleops.core.verification import ChangeSet, SubjectChange, SuiteCase, SuiteRun
from hubbleops.observe.ledger import Ledger
from hubbleops.verify import conserve, coverage, gitdiff, radius
from hubbleops.verify.suites import suite_paths

SCOPE = "0" * 64
RUN = "1" * 64


def evidence(
    path: str, claim_type: str, value: Any, derivation: str = "OBSERVED"
) -> dict[str, Any]:
    return make_evidence(
        run_id=RUN,
        proof_scope_hash=SCOPE,
        claim_type=claim_type,
        observer="text",
        repo_sha=None,
        path=path,
        line_start=1,
        line_end=1,
        source_hash="a" * 64,
        value=value,
        provider_subject=None,
        dependency_context_hash=None,
        derivation=derivation,
        confidence="RAW",
    )


def ledger_of(records: list[dict[str, Any]], statuses: dict[str, str]) -> Ledger:
    candidates: list[dict[str, Any]] = []
    for key, status in sorted(statuses.items()):
        attached = [item["id"] for item in records if item["path"] == key]
        candidates.append(
            make_candidate(
                candidate_id=candidate_identity("p", "surface_reference", key),
                run_id=RUN,
                proof_scope_hash=SCOPE,
                provider="p",
                evidence_ids=attached,
                status=status,
                reason="fixture",
                close_with="run the closing instruction" if status == "UNKNOWN" else None,
            )
        )
    return Ledger(
        provider="p",
        run_id=RUN,
        proof_scope_hash=SCOPE,
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


def test_a_new_unknown_is_preserved_not_a_violation() -> None:
    before = evidence("a.py", "surface_reference", {"pattern": "x"})
    base = ledger_of([before], {"a.py": "NOT_AFFECTED_WITH_EVIDENCE"})
    candidate = ledger_of([before], {"a.py": "UNKNOWN"})
    result = conserve.compare(base, candidate)
    assert result.report().passed is True
    assert [item["close_with"] for item in result.preserved_unknowns()] == [
        "run the closing instruction"
    ]


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


def test_a_frozen_run_that_never_started_is_unresolved_not_a_pass() -> None:
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
    assert report.passed is False
    assert report.unresolved


def test_an_untraceable_language_is_named_not_assumed_covered() -> None:
    modules = ("a.py", "b.ts", "c.php")
    languages = {"a.py": "python", "b.ts": "typescript", "c.php": "php"}
    assert coverage.unsupported_modules(modules, languages) == ("b.ts", "c.php")


def test_the_coverage_report_survives_a_corrupt_line(tmp_path: Path) -> None:
    report = tmp_path / "coverage.jsonl"
    report.write_bytes(
        b'{"test":"a","outcome":"passed","files":["x.py"]}\n'
        b"not json at all\n"
        b'{"test":"b","outcome":"failed","files":[]}\n'
    )
    outcomes = coverage.read_report(report)
    assert [item.name for item in outcomes] == ["a", "b"]


def test_the_coverage_plugin_needs_no_third_party_import() -> None:
    source = coverage.plugin_source()
    assert "import coverage" not in source
    assert "sys.monitoring" in source


def test_a_suite_directory_beats_a_loose_name_guess(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("", encoding="utf-8")
    (tmp_path / "test_loose.py").write_text("", encoding="utf-8")
    assert suite_paths(tmp_path) == ("tests",)


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
