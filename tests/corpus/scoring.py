from __future__ import annotations

from dataclasses import dataclass
from math import comb
from typing import Literal

from tests.corpus.matching import MatchReport
from tests.corpus.spec import (
    STAGES,
    Definitions,
    Family,
    FindingSet,
    LabelSet,
    Stage,
    normalise_path,
)

Ratio = float | None
LabelKind = Literal["natural", "seeded"]


def binomial_cdf(k: int, n: int, p: float) -> float:
    if k >= n:
        return 1.0
    if k < 0:
        return 0.0
    total = 0.0
    for index in range(k + 1):
        total += comb(n, index) * (p**index) * ((1.0 - p) ** (n - index))
    return min(1.0, max(0.0, total))


def clopper_pearson_upper(k: int, n: int, alpha: float = 0.05) -> Ratio:
    if n <= 0:
        return None
    if k >= n:
        return 1.0
    low = 0.0
    high = 1.0
    for _ in range(200):
        mid = (low + high) / 2.0
        if binomial_cdf(k, n, mid) > alpha:
            low = mid
        else:
            high = mid
    return round((low + high) / 2.0, 6)


def ratio(numerator: int, denominator: int) -> Ratio:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


@dataclass(frozen=True)
class FamilyScore:
    family_id: str
    repo: str
    language: str
    source_api_version: str
    arm: str
    outcome: str
    verdict: str
    label_kind: LabelKind
    actionable_labels: int
    matched: int
    exact_mechanism_matches: int
    missed: int
    false_positives: int
    duplicates: int
    unscored_findings: int
    unknown_on_actionable: int
    conserved_unknown_labels: int
    findings_on_unsettled: int
    unsettled_labels: int
    precision_measurable: bool
    unrepaired_matched: int
    adjudicated_files: int
    eligible_files: int
    actionable_recall: Ratio
    adjudicated_coverage: Ratio
    precision: Ratio
    precision_exact_mechanism: Ratio
    false_verification: bool
    attributed_stage: str
    runtime_seconds: float
    human_minutes: float
    cost_usd: float
    engineer_edits: int
    degraded: str
    notes: str


def unrepaired_matched_sites(
    report: MatchReport, findings: FindingSet, label_set: LabelSet
) -> tuple[str, ...]:
    by_id = {label.label_id: label for label in label_set.labels}
    repaired = {normalise_path(item) for item in findings.repaired}
    out: list[str] = []
    for entry in report.matches:
        label = by_id.get(entry.label_id)
        if label is None or label.scope == "subject":
            continue
        if label.path not in repaired:
            out.append(entry.label_id)
    return tuple(out)


def attribute_precisely(
    outcome: str,
    report: MatchReport,
    findings: FindingSet,
    label_set: LabelSet,
    false_verification: bool,
) -> str:
    del false_verification
    if outcome == "SETUP_FAILED":
        return "environment"
    by_id = {label.label_id: label for label in label_set.labels}
    if report.missed_labels:
        seen_sites: set[tuple[str, int]] = set()
        file_scoped: set[str] = set()
        for finding in findings.findings:
            if finding.start_line <= 0:
                file_scoped.add(finding.path)
                continue
            for line in range(finding.start_line, finding.end_line + 1):
                seen_sites.add((finding.path, line))
        for label_id in report.missed_labels:
            label = by_id.get(label_id)
            if label is None:
                continue
            if label.path in file_scoped:
                continue
            if (label.path, label.line) not in seen_sites:
                return "discovery"
        return "classification"
    if report.false_positives and any(label.verdict == "CLEAN" for label in label_set.labels):
        return "classification"
    if unrepaired_matched_sites(report, findings, label_set):
        return "repair"
    if outcome == "COMPLETED":
        return ""
    return "verification"


def score_family(
    definitions: Definitions,
    family: Family,
    label_set: LabelSet,
    findings: FindingSet,
    report: MatchReport,
    label_kind: LabelKind,
) -> FamilyScore:
    del definitions
    matched = report.matched
    missed = len(report.missed_labels)
    false_positives = len(report.false_positives)
    unrepaired = unrepaired_matched_sites(report, findings, label_set)
    false_verification = findings.outcome == "COMPLETED" and (missed > 0 or bool(unrepaired))
    stage = attribute_precisely(findings.outcome, report, findings, label_set, false_verification)
    if stage != "" and stage not in STAGES:
        raise ValueError(f"{family.family_id}: {stage!r} is not a failure attribution stage")
    return FamilyScore(
        family_id=family.family_id,
        repo=family.repo,
        language=family.language,
        source_api_version=family.source_api_version,
        arm=findings.arm,
        outcome=findings.outcome,
        verdict=findings.verdict,
        label_kind=label_kind,
        actionable_labels=report.actionable_labels,
        matched=matched,
        exact_mechanism_matches=report.exact_mechanism_matches,
        missed=missed,
        false_positives=false_positives,
        duplicates=len(report.duplicate_findings),
        unscored_findings=len(report.unscored_findings),
        unknown_on_actionable=len(report.unknown_on_actionable),
        conserved_unknown_labels=len(report.conserved_unknown_labels),
        findings_on_unsettled=len(report.findings_on_unsettled),
        unsettled_labels=report.unsettled_labels,
        precision_measurable=report.precision_measurable,
        unrepaired_matched=len(unrepaired),
        adjudicated_files=report.adjudicated_files,
        eligible_files=report.eligible_files,
        actionable_recall=ratio(matched, report.actionable_labels),
        adjudicated_coverage=ratio(report.adjudicated_files, report.eligible_files),
        precision=(
            ratio(matched, matched + false_positives) if report.precision_measurable else None
        ),
        precision_exact_mechanism=(
            ratio(
                report.exact_mechanism_matches,
                report.exact_mechanism_matches + false_positives,
            )
            if report.precision_measurable
            else None
        ),
        false_verification=false_verification,
        attributed_stage=stage,
        runtime_seconds=round(findings.runtime_seconds, 3),
        human_minutes=round(findings.human_minutes, 2),
        cost_usd=round(findings.cost_usd, 4),
        engineer_edits=findings.engineer_edits,
        degraded=findings.degraded,
        notes=findings.notes,
    )


@dataclass(frozen=True)
class Cohort:
    name: str
    families: int
    actionable_labels: int
    matched: int
    missed: int
    false_positives: int
    unknown_on_actionable: int
    conserved_unknown_labels: int
    findings_on_unsettled: int
    unsettled_labels: int
    precision_measurable: bool
    unrepaired_matched: int
    actionable_recall: Ratio
    adjudicated_coverage: Ratio
    precision: Ratio
    precision_exact_mechanism: Ratio
    completed: int
    human_required: int
    setup_failed: int
    engine_failed: int
    completion: Ratio
    false_verifications: int
    false_verification_rate: Ratio
    false_verification_upper95: Ratio
    human_minutes: float
    runtime_seconds: float
    cost_usd: float
    stages: dict[str, int]


def build_cohort(name: str, rows: tuple[FamilyScore, ...]) -> Cohort:
    matched = sum(row.matched for row in rows)
    exact = sum(row.exact_mechanism_matches for row in rows)
    labels = sum(row.actionable_labels for row in rows)
    false_positives = sum(row.false_positives for row in rows)
    adjudicated = sum(row.adjudicated_files for row in rows)
    eligible = sum(row.eligible_files for row in rows)
    completed = sum(1 for row in rows if row.outcome == "COMPLETED")
    false_verifications = sum(1 for row in rows if row.false_verification)
    stages: dict[str, int] = {}
    for stage in STAGES:
        count = sum(1 for row in rows if row.attributed_stage == stage)
        if count:
            stages[stage] = count
    return Cohort(
        name=name,
        families=len(rows),
        actionable_labels=labels,
        matched=matched,
        missed=sum(row.missed for row in rows),
        false_positives=false_positives,
        unknown_on_actionable=sum(row.unknown_on_actionable for row in rows),
        conserved_unknown_labels=sum(row.conserved_unknown_labels for row in rows),
        findings_on_unsettled=sum(row.findings_on_unsettled for row in rows),
        unsettled_labels=sum(row.unsettled_labels for row in rows),
        precision_measurable=any(row.precision_measurable for row in rows),
        unrepaired_matched=sum(row.unrepaired_matched for row in rows),
        actionable_recall=ratio(matched, labels),
        adjudicated_coverage=ratio(adjudicated, eligible),
        precision=(
            ratio(matched, matched + false_positives)
            if any(row.precision_measurable for row in rows)
            else None
        ),
        precision_exact_mechanism=(
            ratio(exact, exact + false_positives)
            if any(row.precision_measurable for row in rows)
            else None
        ),
        completed=completed,
        human_required=sum(1 for row in rows if row.outcome == "HUMAN_REQUIRED"),
        setup_failed=sum(1 for row in rows if row.outcome == "SETUP_FAILED"),
        engine_failed=sum(1 for row in rows if row.outcome == "ENGINE_FAILED"),
        completion=ratio(completed, len(rows)),
        false_verifications=false_verifications,
        false_verification_rate=ratio(false_verifications, len(rows)),
        false_verification_upper95=clopper_pearson_upper(false_verifications, len(rows)),
        human_minutes=round(sum(row.human_minutes for row in rows), 2),
        runtime_seconds=round(sum(row.runtime_seconds for row in rows), 3),
        cost_usd=round(sum(row.cost_usd for row in rows), 4),
        stages=stages,
    )


def worst_first(cohorts: tuple[Cohort, ...]) -> tuple[Cohort, ...]:
    def key(entry: Cohort) -> tuple[float, float, str]:
        recall = 2.0 if entry.actionable_recall is None else entry.actionable_recall
        precision = 2.0 if entry.precision is None else entry.precision
        return (recall, precision, entry.name)

    return tuple(sorted(cohorts, key=key))


def cohorts_by_language(rows: tuple[FamilyScore, ...]) -> tuple[Cohort, ...]:
    languages = sorted({row.language for row in rows})
    built = tuple(
        build_cohort(language, tuple(row for row in rows if row.language == language))
        for language in languages
    )
    return worst_first(built)


def stage_totals(rows: tuple[FamilyScore, ...]) -> dict[str, int]:
    out: dict[str, int] = {}
    for stage in STAGES:
        value: Stage = stage
        count = sum(1 for row in rows if row.attributed_stage == value)
        if count:
            out[value] = count
    return out
