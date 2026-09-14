from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

import pytest
import yaml

from tests.corpus.history import in_trailing_comment, prose_lines
from tests.corpus.matching import match
from tests.corpus.mutate import CARRIERS, SEEDED_VERSION, Mutation, apply, labels_for, plan
from tests.corpus.scoring import binomial_cdf, clopper_pearson_upper, score_family
from tests.corpus.spec import (
    Definitions,
    Family,
    FileCoverage,
    Finding,
    FindingSet,
    Label,
    LabelSet,
    load_definitions,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFINITIONS = REPO_ROOT / "dev" / "corpus" / "definitions.json"
MATCHING = REPO_ROOT / "dev" / "corpus" / "matching-rules.json"
SURFACE = REPO_ROOT / "hubbleops" / "packs" / "google_ads" / "surface.yaml"


@pytest.fixture(scope="module")
def definitions() -> Definitions:
    return load_definitions(DEFINITIONS, MATCHING)


def _declared_carriers() -> dict[str, list[str]]:
    body = cast("dict[str, object]", yaml.safe_load(SURFACE.read_text(encoding="utf-8")))
    entries = body.get("version_carriers")
    assert isinstance(entries, list)
    out: dict[str, list[str]] = {}
    for entry in cast("list[object]", entries):
        assert isinstance(entry, dict)
        record = {str(key): value for key, value in cast("dict[object, object]", entry).items()}
        name = str(record.get("name", ""))
        pattern = str(record.get("regex", ""))
        out.setdefault(name, []).append(pattern)
    return out


def test_every_seeded_template_is_found_by_the_pack_that_must_find_it() -> None:
    declared = _declared_carriers()
    unmatched: list[str] = []
    for carrier in CARRIERS:
        patterns = declared.get(carrier.name)
        assert patterns, f"{carrier.name} is not a version carrier the pack declares"
        text = carrier.template.format(version=SEEDED_VERSION, VERSION=SEEDED_VERSION.upper())
        found = None
        for pattern in patterns:
            hit = re.search(pattern, text)
            if hit is not None:
                found = hit
                break
        if found is None:
            unmatched.append(f"{carrier.name} ({','.join(carrier.languages)}): {text}")
            continue
        seen = found.group("version")
        assert seen.lower() == SEEDED_VERSION, (
            f"{carrier.name} seeded {seen}, wanted {SEEDED_VERSION}"
        )
    assert not unmatched, (
        "a seeded label the pack's own carrier regex cannot match would be scored against the "
        "engine as a miss it never had a chance to make:\n" + "\n".join(unmatched)
    )


def test_seeded_mechanisms_are_keys_of_the_fixed_compatibility_table(
    definitions: Definitions,
) -> None:
    for carrier in CARRIERS:
        assert carrier.mechanism in definitions.compatibility, (
            f"{carrier.name} labels sites as {carrier.mechanism}, which the fixed matching rules "
            "do not define"
        )


def test_seeded_plan_is_deterministic(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    for index in range(4):
        (repo / "pkg" / f"client{index}.py").write_text(
            f"import os\nGOOGLE_ADS_CLIENT = {index}\n", encoding="utf-8"
        )
    family = Family(
        family_id="fixture",
        repo="example/fixture",
        sha="0" * 40,
        clone_url="https://example.invalid/fixture.git",
        members=(),
        language="python",
        source_api_version="v22",
        sdk_kind="official-sdk",
        shape="fixture",
        documented_install=(),
        test_command="",
        independence="",
        eligible=True,
    )
    first = plan(repo, family)
    second = plan(repo, family)
    assert first == second
    assert first.mutations
    placed = apply(repo, first.mutations)
    assert len(placed) == len(first.mutations)
    for mutation in placed:
        body = (repo / mutation.path).read_text(encoding="utf-8").splitlines()
        assert body[mutation.line - 1] == mutation.text
    labels = labels_for(placed)
    assert len({label.label_id for label in labels}) == len(labels)
    assert all(label.kind == "seeded" and label.verdict == "ACTIONABLE" for label in labels)


def _label(
    label_id: str,
    path: str,
    line: int,
    mechanism: str,
    verdict: str = "ACTIONABLE",
    scope: str = "line",
    subject: str = "",
) -> Label:
    assert verdict in ("ACTIONABLE", "CLEAN", "UNSETTLED")
    assert scope in ("line", "file", "subject")
    return Label(
        label_id=label_id,
        path=path,
        line=line,
        scope=scope,
        mechanism=mechanism,
        verdict=verdict,
        kind="natural",
        sources=("S1",),
        subject=subject,
        evidence="",
    )


def _finding(
    finding_id: str,
    path: str,
    start: int,
    mechanism: str,
    asserted: str = "actionable",
    end: int | None = None,
    subject: str = "",
) -> Finding:
    assert asserted in ("actionable", "unknown", "clean")
    return Finding(
        finding_id=finding_id,
        path=path,
        start_line=start,
        end_line=start if end is None else end,
        mechanism=mechanism,
        asserted=asserted,
        subject=subject,
        reason="",
    )


def _labels(entries: tuple[Label, ...], adjudicated: tuple[str, ...]) -> LabelSet:
    return LabelSet(
        family_id="fixture",
        repo="example/fixture",
        sha="0" * 40,
        signed=True,
        signer="test",
        eligible_files=max(len(adjudicated), 1),
        coverage=tuple(
            FileCoverage(path=path, sources=("S1",), adjudicated=True, reason_if_not="")
            for path in adjudicated
        ),
        labels=entries,
    )


def test_exact_site_beats_window_and_a_label_is_claimed_once(definitions: Definitions) -> None:
    labels = _labels((_label("L1", "a.py", 10, "version_literal"),), ("a.py",))
    findings = (
        _finding("F_far", "a.py", 12, "version_literal"),
        _finding("F_exact", "a.py", 10, "version_literal"),
    )
    report = match(definitions, labels, "A", findings)
    assert report.matched == 1
    assert report.matches[0].finding_id == "F_exact"
    assert report.matches[0].rule == "M1"
    assert report.duplicate_findings == ("F_far",)
    assert report.false_positives == ()


def test_a_finding_outside_the_adjudicated_region_is_unscored_not_a_false_positive(
    definitions: Definitions,
) -> None:
    labels = _labels((_label("L1", "a.py", 10, "version_literal"),), ("a.py",))
    findings = (
        _finding("F1", "a.py", 10, "version_literal"),
        _finding("F_elsewhere", "unseen.py", 3, "version_literal"),
        _finding("F_wrong", "a.py", 900, "version_literal"),
    )
    report = match(definitions, labels, "A", findings)
    assert report.matched == 1
    assert report.unscored_findings == ("F_elsewhere",)
    assert report.false_positives == ("F_wrong",)


def test_unknown_on_an_actionable_label_is_neither_a_hit_nor_a_miss(
    definitions: Definitions,
) -> None:
    labels = _labels((_label("L1", "a.py", 10, "version_literal"),), ("a.py",))
    findings = (_finding("F_unknown", "a.py", 10, "version_literal", asserted="unknown"),)
    report = match(definitions, labels, "A", findings)
    assert report.matched == 0
    assert report.missed_labels == ()
    assert report.conserved_unknown_labels == ("L1",)
    assert report.unknown_on_actionable == ("F_unknown",)
    assert report.actionable_labels == 0


def test_an_unmappable_mechanism_matches_on_site_but_never_exactly(
    definitions: Definitions,
) -> None:
    labels = _labels((_label("L1", "a.py", 10, "namespace_segment"),), ("a.py",))
    report = match(definitions, labels, "C", (_finding("F1", "a.py", 10, "other"),))
    assert report.matched == 1
    assert report.exact_mechanism_matches == 0
    assert report.right_site_wrong_mechanism == ("L1",)


def test_an_incompatible_mechanism_on_the_right_line_does_not_match(
    definitions: Definitions,
) -> None:
    labels = _labels((_label("L1", "a.py", 10, "namespace_segment"),), ("a.py",))
    report = match(definitions, labels, "A", (_finding("F1", "a.py", 10, "request_shape"),))
    assert report.matched == 0
    assert report.false_positives == ("F1",)


def test_subject_scope_matches_anywhere_in_the_family(definitions: Definitions) -> None:
    labels = _labels(
        (
            _label(
                "L1",
                "contract",
                0,
                "removed_enum_value",
                scope="subject",
                subject="ACQUISITION",
            ),
        ),
        (),
    )
    report = match(
        definitions,
        labels,
        "A",
        (_finding("F1", "far/away.py", 7, "enum_reference", subject="ACQUISITION"),),
    )
    assert report.matched == 1
    assert report.matches[0].rule == "M4"


def test_unsettled_labels_leave_every_numerator_and_denominator_alone(
    definitions: Definitions,
) -> None:
    labels = _labels(
        (
            _label("L1", "a.py", 10, "version_literal"),
            _label("L2", "a.py", 20, "version_literal", verdict="UNSETTLED"),
        ),
        ("a.py",),
    )
    report = match(definitions, labels, "A", (_finding("F1", "a.py", 10, "version_literal"),))
    assert report.actionable_labels == 1
    assert report.unsettled_labels == 1
    assert report.matched == 1
    assert report.missed_labels == ()


def test_matching_is_a_pure_function_of_its_inputs(definitions: Definitions) -> None:
    labels = _labels(
        tuple(_label(f"L{index}", "a.py", index * 5, "version_literal") for index in range(1, 8)),
        ("a.py",),
    )
    findings = tuple(
        _finding(f"F{index}", "a.py", index * 5 + 1, "version_literal") for index in range(1, 8)
    )
    first = match(definitions, labels, "A", findings)
    second = match(definitions, labels, "A", tuple(reversed(findings)))
    assert first == second


def _findings_set(
    outcome: str, findings: tuple[Finding, ...], repaired: tuple[str, ...]
) -> FindingSet:
    assert outcome in ("COMPLETED", "HUMAN_REQUIRED", "SETUP_FAILED", "ENGINE_FAILED")
    return FindingSet(
        arm="A",
        family_id="fixture",
        sha="0" * 40,
        outcome=outcome,
        verdict="VERIFIED_FOR_SCOPE" if outcome == "COMPLETED" else "FAILED",
        runtime_seconds=1.0,
        human_minutes=0.0,
        cost_usd=0.0,
        engineer_edits=0,
        findings=findings,
        repaired=repaired,
        stages=(),
        notes="",
    )


def _family() -> Family:
    return Family(
        family_id="fixture",
        repo="example/fixture",
        sha="0" * 40,
        clone_url="https://example.invalid/fixture.git",
        members=(),
        language="python",
        source_api_version="v22",
        sdk_kind="official-sdk",
        shape="fixture",
        documented_install=(),
        test_command="",
        independence="",
        eligible=True,
    )


def test_completed_with_a_missed_actionable_label_is_a_false_verification(
    definitions: Definitions,
) -> None:
    labels = _labels((_label("L1", "a.py", 10, "version_literal"),), ("a.py",))
    findings = _findings_set("COMPLETED", (), ())
    report = match(definitions, labels, "A", findings.findings)
    row = score_family(definitions, _family(), labels, findings, report, "natural")
    assert row.false_verification is True
    assert row.attributed_stage == "discovery"


def test_completed_with_a_found_but_unrepaired_site_is_a_false_verification(
    definitions: Definitions,
) -> None:
    labels = _labels((_label("L1", "a.py", 10, "version_literal"),), ("a.py",))
    findings = _findings_set("COMPLETED", (_finding("F1", "a.py", 10, "version_literal"),), ())
    report = match(definitions, labels, "A", findings.findings)
    row = score_family(definitions, _family(), labels, findings, report, "natural")
    assert report.matched == 1
    assert row.unrepaired_matched == 1
    assert row.false_verification is True
    assert row.attributed_stage == "repair"


def test_completed_with_every_matched_site_repaired_is_not_a_false_verification(
    definitions: Definitions,
) -> None:
    labels = _labels((_label("L1", "a.py", 10, "version_literal"),), ("a.py",))
    findings = _findings_set(
        "COMPLETED", (_finding("F1", "a.py", 10, "version_literal"),), ("a.py",)
    )
    report = match(definitions, labels, "A", findings.findings)
    row = score_family(definitions, _family(), labels, findings, report, "natural")
    assert row.unrepaired_matched == 0
    assert row.false_verification is False
    assert row.attributed_stage == ""


def test_one_finding_may_satisfy_two_labels_it_really_covers(
    definitions: Definitions,
) -> None:
    labels = _labels(
        (
            _label("L1", "a.py", 12, "version_literal"),
            _label("L2", "a.py", 15, "version_literal"),
        ),
        ("a.py",),
    )
    report = match(
        definitions, labels, "A", (_finding("F1", "a.py", 10, "version_literal", end=20),)
    )
    assert report.matched == 2
    assert report.missed_labels == ()
    assert report.false_positives == ()


def test_precision_is_not_reported_when_no_source_asserted_anything_clean(
    definitions: Definitions,
) -> None:
    labels = _labels((_label("L1", "a.py", 10, "version_literal"),), ("a.py",))
    findings = _findings_set(
        "HUMAN_REQUIRED",
        (
            _finding("F1", "a.py", 10, "version_literal"),
            _finding("F2", "a.py", 900, "version_literal"),
        ),
        (),
    )
    report = match(definitions, labels, "A", findings.findings)
    row = score_family(definitions, _family(), labels, findings, report, "natural")
    assert report.precision_measurable is False
    assert row.false_positives == 1
    assert row.precision is None
    assert row.actionable_recall == 1.0


def test_a_site_never_observed_attributes_to_discovery_not_classification(
    definitions: Definitions,
) -> None:
    labels = _labels((_label("L1", "a.py", 10, "version_literal"),), ("a.py",))
    findings = _findings_set("HUMAN_REQUIRED", (), ())
    report = match(definitions, labels, "A", findings.findings)
    row = score_family(definitions, _family(), labels, findings, report, "natural")
    assert row.attributed_stage == "discovery"


def test_a_site_seen_but_called_clean_attributes_to_classification(
    definitions: Definitions,
) -> None:
    labels = _labels((_label("L1", "a.py", 10, "version_literal"),), ("a.py",))
    findings = _findings_set(
        "HUMAN_REQUIRED", (_finding("F1", "a.py", 10, "version_literal", asserted="clean"),), ()
    )
    report = match(definitions, labels, "A", findings.findings)
    row = score_family(definitions, _family(), labels, findings, report, "natural")
    assert row.attributed_stage == "classification"


def test_a_finding_elsewhere_in_the_file_is_not_having_seen_the_site(
    definitions: Definitions,
) -> None:
    labels = _labels((_label("L1", "a.py", 10, "version_literal"),), ("a.py",))
    findings = _findings_set(
        "HUMAN_REQUIRED",
        (_finding("F_far", "a.py", 400, "version_literal", asserted="clean"),),
        (),
    )
    report = match(definitions, labels, "A", findings.findings)
    row = score_family(definitions, _family(), labels, findings, report, "natural")
    assert row.attributed_stage == "discovery"


def test_a_file_scoped_finding_counts_as_having_seen_the_file(
    definitions: Definitions,
) -> None:
    labels = _labels((_label("L1", "composer.json", 12, "manifest_pin"),), ("composer.json",))
    findings = _findings_set(
        "HUMAN_REQUIRED",
        (_finding("F_file", "composer.json", 0, "manifest_pin", asserted="clean", end=0),),
        (),
    )
    report = match(definitions, labels, "A", findings.findings)
    row = score_family(definitions, _family(), labels, findings, report, "natural")
    assert row.attributed_stage == "classification"


def test_setup_failure_always_attributes_to_environment(definitions: Definitions) -> None:
    labels = _labels((_label("L1", "a.py", 10, "version_literal"),), ("a.py",))
    findings = _findings_set("SETUP_FAILED", (), ())
    report = match(definitions, labels, "A", findings.findings)
    row = score_family(definitions, _family(), labels, findings, report, "natural")
    assert row.attributed_stage == "environment"


def test_found_and_classified_but_never_edited_attributes_to_repair(
    definitions: Definitions,
) -> None:
    labels = _labels((_label("L1", "a.py", 10, "version_literal"),), ("a.py",))
    findings = _findings_set("HUMAN_REQUIRED", (_finding("F1", "a.py", 10, "version_literal"),), ())
    report = match(definitions, labels, "A", findings.findings)
    row = score_family(definitions, _family(), labels, findings, report, "natural")
    assert row.attributed_stage == "repair"


def test_clopper_pearson_upper_bound_matches_known_values() -> None:
    assert clopper_pearson_upper(0, 10) == pytest.approx(0.258866, abs=1e-5)
    assert clopper_pearson_upper(0, 1) == pytest.approx(0.95, abs=1e-5)
    assert clopper_pearson_upper(1, 10) == pytest.approx(0.394163, abs=1e-5)
    assert clopper_pearson_upper(10, 10) == 1.0
    assert clopper_pearson_upper(0, 0) is None


def test_clopper_pearson_upper_bound_solves_its_own_definition() -> None:
    for successes, trials in ((0, 10), (1, 10), (2, 7), (3, 9)):
        bound = clopper_pearson_upper(successes, trials)
        assert bound is not None
        assert binomial_cdf(successes, trials, bound) == pytest.approx(0.05, abs=1e-4)


def test_zero_actionable_labels_reports_none_not_a_perfect_score(
    definitions: Definitions,
) -> None:
    labels = _labels((_label("L1", "a.py", 10, "version_literal", verdict="CLEAN"),), ("a.py",))
    findings = _findings_set("HUMAN_REQUIRED", (), ())
    report = match(definitions, labels, "A", findings.findings)
    row = score_family(definitions, _family(), labels, findings, report, "natural")
    assert row.actionable_labels == 0
    assert row.actionable_recall is None
    assert row.precision is None


def test_the_fixed_files_are_internally_consistent(definitions: Definitions) -> None:
    assert definitions.target_version == "v25"
    assert definitions.budget_minutes == 30
    assert definitions.engineer_edits_allowed == 0
    assert "v19" in definitions.source_versions and "v24" in definitions.source_versions
    assert "v25" not in definitions.source_versions
    for mechanism, allowed in definitions.compatibility.items():
        assert mechanism in allowed, f"{mechanism} must be compatible with itself"
    for mechanism in definitions.file_mechanisms + definitions.subject_mechanisms:
        assert mechanism in definitions.compatibility, f"{mechanism} has no compatibility row"
    body = cast("dict[str, object]", json.loads(MATCHING.read_text(encoding="utf-8")))
    assert body.get("version") == definitions.matching_version


def test_seeded_mutations_do_not_collide_on_one_line(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "only.py").write_text("GOOGLE_ADS = 1\n", encoding="utf-8")
    family = Family(
        family_id="single",
        repo="example/single",
        sha="0" * 40,
        clone_url="https://example.invalid/single.git",
        members=(),
        language="python",
        source_api_version="v20",
        sdk_kind="rest",
        shape="fixture",
        documented_install=(),
        test_command="",
        independence="",
        eligible=True,
    )
    placed = apply(repo, plan(repo, family).mutations)
    sites = {(mutation.path, mutation.line) for mutation in placed}
    assert len(sites) == len(placed)
    text = (repo / "only.py").read_text(encoding="utf-8").splitlines()
    for mutation in placed:
        assert text[mutation.line - 1] == mutation.text


@pytest.mark.parametrize(
    ("suffix", "body", "prose_line"),
    [
        (".py", 'X = 1\n# v24 dropped click_through_rate\nURL = "v24"\n', 2),
        (".py", 'def f():\n    """Since Ads API v24 the forecast changed."""\n    return 1\n', 2),
        (".ts", 'const a = 1;\n// see rest/v18 for the old shape\nconst v = "v22";\n', 2),
        (".ts", '/*\n * v20 used to allow this\n */\nconst v = "v22";\n', 2),
        (".php", "<?php\n// v21 is gone\n$v = 'v23';\n", 2),
    ],
)
def test_prose_lines_finds_comments_and_docstrings(suffix: str, body: str, prose_line: int) -> None:
    found = prose_lines(body, suffix)
    assert prose_line in found, f"{suffix}: line {prose_line} should read as prose, got {found}"


def test_a_version_in_code_is_not_prose() -> None:
    body = 'X = 1\n# a comment\nURL = "https://googleads.googleapis.com/v24/customers/"\n'
    assert 3 not in prose_lines(body, ".py")


def test_a_trailing_comment_after_code_does_not_make_the_line_prose() -> None:
    line = 'URL = "v24"  # v23 was the old one'
    assert in_trailing_comment(line, line.index("v24") - 1, ".py") is False
    assert in_trailing_comment(line, line.rindex("v23"), ".py") is True


def test_php_seed_lands_inside_the_php_block(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "Client.php"
    target.write_text("<?php\n$googleads = 1;\n?>\n", encoding="utf-8")
    placed = apply(
        repo,
        (
            Mutation(
                carrier="rest_path_version",
                mechanism="endpoint_path",
                path="Client.php",
                line=0,
                text="$hopsSeededRestPath = '/v21/customers/';",
                version="v21",
            ),
        ),
    )
    assert len(placed) == 1
    lines = target.read_text(encoding="utf-8").splitlines()
    assert lines[placed[0].line - 1] == "$hopsSeededRestPath = '/v21/customers/';"
    assert lines.index("?>") > placed[0].line - 1
