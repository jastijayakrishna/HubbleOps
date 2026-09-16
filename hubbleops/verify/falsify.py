from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from hubbleops.core.verification import (
    ChangeSet,
    CheckReport,
    FalsifierInput,
    FalsifierOutcome,
    FalsifierView,
)
from hubbleops.observe import Ledger

NOT_APPLICABLE = "NOT_APPLICABLE"
NOT_RUN = "NOT_RUN"
UNEXECUTED = (NOT_APPLICABLE, NOT_RUN)


@dataclass(frozen=True, slots=True)
class FalsifierRun:
    name: str
    failure_class: str
    result: str
    reason: str
    sites: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "failure_class": self.failure_class,
            "result": self.result,
            "reason": self.reason,
            "sites": list(self.sites),
        }


@dataclass(frozen=True, slots=True)
class FalsifierReview:
    runs: tuple[FalsifierRun, ...]
    disarmed: bool = False

    def failures(self) -> tuple[FalsifierRun, ...]:
        return tuple(item for item in self.runs if item.result == "FAIL")

    def undecided(self) -> tuple[FalsifierRun, ...]:
        return tuple(item for item in self.runs if item.result == "UNKNOWN")

    def executed(self) -> tuple[FalsifierRun, ...]:
        return tuple(item for item in self.runs if item.result not in UNEXECUTED)

    def not_run(self) -> tuple[FalsifierRun, ...]:
        return tuple(item for item in self.runs if item.result == NOT_RUN)

    def report(self) -> CheckReport:
        failures = self.failures()
        disarmed = (
            (
                "every falsifier was skipped because this run detected none of their failure "
                "classes, so falsifiers_pass proves nothing about a Change Pack that does "
                "change subjects",
            )
            if self.disarmed
            else ()
        )
        return CheckReport(
            name="falsifiers",
            passed=not failures,
            reasons=tuple(f"falsifier {item.name} FAILED: {item.reason}" for item in failures),
            unresolved=tuple(
                sorted(
                    [f"falsifier {item.name} undecided: {item.reason}" for item in self.undecided()]
                    + [
                        f"falsifier {item.name} did not run: {item.reason}"
                        for item in self.not_run()
                    ]
                    + list(disarmed)
                )
            ),
            detail={
                "total": len(self.runs),
                "passed": sum(1 for item in self.runs if item.result == "PASS"),
                "failed": len(failures),
                "not_applicable": sum(1 for item in self.runs if item.result == NOT_APPLICABLE),
                "not_run": len(self.not_run()),
                "runs": [item.to_mapping() for item in self.runs],
            },
        )


def detected_classes(ledger: Ledger, captured: Sequence[Mapping[str, Any]] = ()) -> frozenset[str]:
    classes: set[str] = set()
    evidence = ledger.evidence_by_id()
    attached = {eid for candidate in ledger.candidates for eid in candidate["evidence_ids"]}
    for eid in attached:
        record = evidence.get(eid)
        if record is not None:
            classes.add(str(record["claim_type"]))
    for candidate in ledger.candidates:
        classes.add(str(candidate["status"]))
    if captured:
        classes.add("production_version")
    if any(item.get("request_text") is not None for item in captured):
        classes.add("request_text")
    return frozenset(classes)


def run(
    falsifiers: Sequence[FalsifierView],
    ledger: Ledger,
    changes: ChangeSet,
    candidate_root: str,
    captured: Sequence[Mapping[str, Any]] = (),
) -> FalsifierReview:
    classes = detected_classes(ledger, captured)
    subject = FalsifierInput(
        changes=changes,
        candidate_root=candidate_root,
        evidence=tuple(ledger.evidence),
        candidates=tuple(ledger.candidates),
        captured_requests=tuple(captured),
    )
    inert = not changes.changes and changes.from_version == changes.to_version
    runs: list[FalsifierRun] = []
    for falsifier in sorted(falsifiers, key=lambda item: item.name):
        if falsifier.failure_class not in classes:
            runs.append(
                FalsifierRun(
                    name=falsifier.name,
                    failure_class=falsifier.failure_class,
                    result=NOT_APPLICABLE if inert else NOT_RUN,
                    reason=_unexecuted_reason(falsifier.failure_class, changes, inert),
                    sites=(),
                )
            )
            continue
        outcome = _check(falsifier, subject)
        runs.append(
            FalsifierRun(
                name=falsifier.name,
                failure_class=falsifier.failure_class,
                result=outcome.result,
                reason=outcome.reason,
                sites=outcome.sites,
            )
        )
    executed = [item for item in runs if item.result not in UNEXECUTED]
    return FalsifierReview(
        runs=tuple(runs),
        disarmed=not executed and bool(changes.changes),
    )


def _unexecuted_reason(failure_class: str, changes: ChangeSet, inert: bool) -> str:
    if inert:
        return (
            f"no {failure_class} evidence in this run and this Change Pack changes no subject "
            f"and no version, so there is nothing of this class to leave behind"
        )
    return (
        f"no {failure_class} evidence in this run, but this Change Pack carries "
        f"{len(changes.changes)} subject change(s) from {changes.from_version} to "
        f"{changes.to_version}, so nothing checked whether the migration left one behind"
    )


def _check(falsifier: FalsifierView, subject: FalsifierInput) -> FalsifierOutcome:
    try:
        outcome: Any = falsifier.check(subject)
    except Exception as error:
        return FalsifierOutcome(
            result="UNKNOWN", reason=f"{type(error).__name__}: {error}", sites=()
        )
    if not isinstance(outcome, FalsifierOutcome) or outcome.result not in (
        "PASS",
        "FAIL",
        "UNKNOWN",
    ):
        return FalsifierOutcome(
            result="UNKNOWN",
            reason=f"{falsifier.name} returned no valid falsifier outcome",
            sites=(),
        )
    return outcome


__all__ = [
    "NOT_APPLICABLE",
    "NOT_RUN",
    "FalsifierReview",
    "FalsifierRun",
    "detected_classes",
    "run",
]
