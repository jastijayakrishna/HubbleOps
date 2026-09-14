from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from tests.corpus.scoring import Cohort, FamilyScore, build_cohort, cohorts_by_language, worst_first
from tests.corpus.spec import STAGES


@dataclass(frozen=True)
class ArmSection:
    arm: str
    label_kind: str
    labels_signed: bool
    degraded: str
    rows: tuple[FamilyScore, ...]
    overall: Cohort
    by_language: tuple[Cohort, ...]
    by_family: tuple[Cohort, ...]
    unscored: tuple[str, ...]

    def body(self) -> dict[str, object]:
        def paired(entry: Cohort) -> dict[str, object]:
            row = asdict(entry)
            row["recall_at_coverage"] = (
                f"{_num(entry.actionable_recall)} at coverage {_num(entry.adjudicated_coverage)}"
            )
            row["precision_at_coverage"] = (
                f"{_num(entry.precision)} at coverage {_num(entry.adjudicated_coverage)}"
            )
            return row

        def paired_family(entry: FamilyScore) -> dict[str, object]:
            row = asdict(entry)
            row["recall_at_coverage"] = (
                f"{_num(entry.actionable_recall)} at coverage {_num(entry.adjudicated_coverage)}"
            )
            row["precision_at_coverage"] = (
                f"{_num(entry.precision)} at coverage {_num(entry.adjudicated_coverage)}"
            )
            return row

        return {
            "arm": self.arm,
            "label_kind": self.label_kind,
            "labels_signed": self.labels_signed,
            "degraded": self.degraded,
            "families": len(self.rows),
            "overall": paired(self.overall),
            "worst_cohort_first_by_language": [paired(item) for item in self.by_language],
            "worst_first_by_family": [paired(item) for item in self.by_family],
            "per_family": [
                paired_family(row) for row in sorted(self.rows, key=lambda item: item.family_id)
            ],
            "failure_attribution": {
                stage: self.overall.stages[stage]
                for stage in STAGES
                if stage in self.overall.stages
            },
            "unscored_families": list(self.unscored),
            "reading_rule": (
                "Every recall and precision figure here carries its adjudicated coverage beside "
                "it. A recall of 1.000 at coverage 0.186 means the arm found everything the "
                "labels could see in 18.6 percent of the eligible files, and says nothing about "
                "the other 81.4 percent. A null precision means no label source was willing to "
                "call any site clean, so there is no denominator to divide by."
            ),
        }


def arm_section(
    arm: str,
    label_kind: str,
    rows: tuple[FamilyScore, ...],
    signed: bool,
    degraded: str,
    unscored: tuple[str, ...] = (),
) -> ArmSection:
    return ArmSection(
        arm=arm,
        label_kind=label_kind,
        labels_signed=signed,
        degraded=degraded,
        rows=rows,
        overall=build_cohort("all", rows),
        by_language=cohorts_by_language(rows),
        by_family=worst_first(tuple(build_cohort(row.family_id, (row,)) for row in rows)),
        unscored=unscored,
    )


def unavailable_arm(arm: str, reason: str, missing: tuple[str, ...]) -> dict[str, object]:
    return {
        "arm": arm,
        "status": "ARM_UNAVAILABLE",
        "scored": False,
        "reason": reason,
        "missing_preconditions": list(missing),
    }


def write_scorecard(path: Path, body: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _num(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"


HEADER = (
    f"  {'cohort':22} {'fam':>3} {'recall':>7} {'cover':>7} {'prec':>7} {'precX':>7} "
    f"{'cmpl':>4} {'humn':>4} {'setp':>4} {'engf':>4} {'fV':>3} {'ub95':>6} "
    f"{'unk':>5} {'unset':>6} {'mins':>6} {'secs':>8} {'usd':>7}"
)


def _row(entry: Cohort) -> str:
    return (
        f"  {entry.name[:22]:22} {entry.families:>3} "
        f"{_num(entry.actionable_recall):>7} {_num(entry.adjudicated_coverage):>7} "
        f"{_num(entry.precision):>7} {_num(entry.precision_exact_mechanism):>7} "
        f"{entry.completed:>4} {entry.human_required:>4} {entry.setup_failed:>4} "
        f"{entry.engine_failed:>4} {entry.false_verifications:>3} "
        f"{_num(entry.false_verification_upper95):>6} {entry.unknown_on_actionable:>5} "
        f"{entry.findings_on_unsettled:>6} {entry.human_minutes:>6.1f} "
        f"{entry.runtime_seconds:>8.1f} {entry.cost_usd:>7.3f}"
    )


def render_text(section: ArmSection) -> str:
    lines: list[str] = []
    lines.append(
        f"ARM {section.arm}  labels={section.label_kind}  signed={section.labels_signed}  "
        f"families={len(section.rows)}"
    )
    if section.degraded:
        lines.append(f"  DEGRADED: {section.degraded}")
    lines.append("  BY LANGUAGE, worst cohort first")
    lines.append(HEADER)
    for cohort in section.by_language:
        lines.append(_row(cohort))
    lines.append("  BY FAMILY, worst first")
    lines.append(HEADER)
    for cohort in section.by_family:
        lines.append(_row(cohort))
    lines.append("  OVERALL")
    lines.append(_row(section.overall))
    if section.overall.stages:
        parts = [f"{key}={value}" for key, value in sorted(section.overall.stages.items())]
        lines.append("  attribution: " + " ".join(parts))
    if section.unscored:
        lines.append(f"  unscored: {len(section.unscored)}")
        for entry in section.unscored:
            lines.append(f"    {entry}")
    return "\n".join(lines)
