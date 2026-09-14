from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from tests.corpus.spec import Definitions, Finding, Label, LabelSet

Strength = Literal["exact", "windowed", "file", "subject"]
RULES: tuple[tuple[str, Strength], ...] = (
    ("M1", "exact"),
    ("M2", "windowed"),
    ("M3", "file"),
    ("M4", "subject"),
)


@dataclass(frozen=True)
class Match:
    label_id: str
    finding_id: str
    rule: str
    strength: Strength
    exact_mechanism: bool


@dataclass(frozen=True)
class MatchReport:
    family_id: str
    arm: str
    matches: tuple[Match, ...]
    missed_labels: tuple[str, ...]
    false_positives: tuple[str, ...]
    unscored_findings: tuple[str, ...]
    duplicate_findings: tuple[str, ...]
    findings_on_unsettled: tuple[str, ...]
    unknown_on_actionable: tuple[str, ...]
    conserved_unknown_labels: tuple[str, ...]
    right_site_wrong_mechanism: tuple[str, ...]
    actionable_labels: int
    clean_labels: int
    unsettled_labels: int
    precision_measurable: bool
    adjudicated_files: int
    eligible_files: int

    @property
    def matched(self) -> int:
        return len(self.matches)

    @property
    def exact_mechanism_matches(self) -> int:
        return sum(1 for entry in self.matches if entry.exact_mechanism)


def compatible(definitions: Definitions, label_mechanism: str, finding_mechanism: str) -> bool:
    if finding_mechanism in definitions.universal_mechanisms:
        return True
    allowed = definitions.compatibility.get(label_mechanism)
    if allowed is None:
        return label_mechanism == finding_mechanism
    return finding_mechanism in allowed


def line_distance(finding: Finding, label: Label) -> int:
    if label.line <= 0:
        return 0
    if finding.start_line <= label.line <= finding.end_line:
        return 0
    if label.line < finding.start_line:
        return finding.start_line - label.line
    return label.line - finding.end_line


def on_site(definitions: Definitions, finding: Finding, label: Label) -> bool:
    if label.scope == "file" or label.line <= 0:
        return finding.path == label.path
    return finding.path == label.path and line_distance(finding, label) <= definitions.window_lines


def _holds(
    rule: str,
    definitions: Definitions,
    finding: Finding,
    label: Label,
) -> bool:
    if not compatible(definitions, label.mechanism, finding.mechanism):
        return False
    if rule == "M1":
        return (
            label.scope == "line"
            and finding.path == label.path
            and finding.start_line <= label.line <= finding.end_line
        )
    if rule == "M2":
        return (
            label.scope == "line"
            and finding.path == label.path
            and line_distance(finding, label) <= definitions.window_lines
        )
    if rule == "M3":
        return (
            label.scope == "file"
            and finding.path == label.path
            and label.mechanism in definitions.file_mechanisms
        )
    if rule == "M4":
        return (
            label.scope == "subject"
            and label.subject != ""
            and finding.subject == label.subject
            and label.mechanism in definitions.subject_mechanisms
        )
    return False


def _sorted_findings(findings: tuple[Finding, ...]) -> tuple[Finding, ...]:
    return tuple(
        sorted(
            findings,
            key=lambda item: (
                item.path,
                item.start_line,
                item.end_line,
                item.mechanism,
                item.finding_id,
            ),
        )
    )


def _sorted_labels(labels: tuple[Label, ...]) -> tuple[Label, ...]:
    return tuple(
        sorted(labels, key=lambda item: (item.path, item.line, item.mechanism, item.label_id))
    )


def match(
    definitions: Definitions,
    label_set: LabelSet,
    arm: str,
    findings: tuple[Finding, ...],
) -> MatchReport:
    adjudicated = label_set.adjudicated_paths()
    candidates = _sorted_labels(
        tuple(
            label
            for label in label_set.labels
            if label.verdict == "ACTIONABLE"
            and (label.scope == "subject" or label.path in adjudicated)
        )
    )
    asserted = _sorted_findings(tuple(item for item in findings if item.asserted == "actionable"))
    unknowns = _sorted_findings(tuple(item for item in findings if item.asserted == "unknown"))

    unknown_hits: list[str] = []
    conserved: set[str] = set()
    for label in candidates:
        for finding in unknowns:
            if any(_holds(rule, definitions, finding, label) for rule, _ in RULES):
                unknown_hits.append(finding.finding_id)
                conserved.add(label.label_id)
                break
    scorable = tuple(label for label in candidates if label.label_id not in conserved)

    claimed_label: dict[str, Match] = {}
    claimed_finding: set[str] = set()
    for rule, strength in RULES:
        for label in scorable:
            if label.label_id in claimed_label:
                continue
            best: tuple[int, str, Finding] | None = None
            for finding in asserted:
                if not _holds(rule, definitions, finding, label):
                    continue
                key = (line_distance(finding, label), finding.finding_id, finding)
                if best is None or (key[0], key[1]) < (best[0], best[1]):
                    best = key
            if best is None:
                continue
            chosen = best[2]
            claimed_label[label.label_id] = Match(
                label_id=label.label_id,
                finding_id=chosen.finding_id,
                rule=rule,
                strength=strength,
                exact_mechanism=chosen.mechanism == label.mechanism,
            )
            claimed_finding.add(chosen.finding_id)

    unsettled = _sorted_labels(
        tuple(label for label in label_set.labels if label.verdict == "UNSETTLED")
    )
    duplicates: list[str] = []
    false_positives: list[str] = []
    unscored: list[str] = []
    on_unsettled: list[str] = []
    for finding in asserted:
        if finding.finding_id in claimed_finding:
            continue
        is_duplicate = False
        for label in scorable:
            if label.label_id not in claimed_label:
                continue
            if any(_holds(rule, definitions, finding, label) for rule, _ in RULES):
                is_duplicate = True
                break
        if is_duplicate:
            duplicates.append(finding.finding_id)
            continue
        if any(on_site(definitions, finding, label) for label in unsettled):
            on_unsettled.append(finding.finding_id)
        elif finding.path in adjudicated:
            false_positives.append(finding.finding_id)
        else:
            unscored.append(finding.finding_id)

    matches = tuple(sorted(claimed_label.values(), key=lambda item: item.label_id))
    return MatchReport(
        family_id=label_set.family_id,
        arm=arm,
        matches=matches,
        missed_labels=tuple(
            label.label_id for label in scorable if label.label_id not in claimed_label
        ),
        false_positives=tuple(sorted(false_positives)),
        unscored_findings=tuple(sorted(unscored)),
        duplicate_findings=tuple(sorted(duplicates)),
        findings_on_unsettled=tuple(sorted(on_unsettled)),
        unknown_on_actionable=tuple(sorted(set(unknown_hits))),
        conserved_unknown_labels=tuple(sorted(conserved)),
        right_site_wrong_mechanism=tuple(
            sorted(entry.label_id for entry in matches if not entry.exact_mechanism)
        ),
        actionable_labels=len(scorable),
        clean_labels=sum(1 for label in label_set.labels if label.verdict == "CLEAN"),
        precision_measurable=any(label.verdict == "CLEAN" for label in label_set.labels),
        unsettled_labels=sum(1 for label in label_set.labels if label.verdict == "UNSETTLED"),
        adjudicated_files=len(adjudicated),
        eligible_files=label_set.eligible_files,
    )
