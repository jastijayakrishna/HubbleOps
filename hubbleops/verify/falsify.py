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

SKIPPED = "SKIPPED"


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

    def failures(self) -> tuple[FalsifierRun, ...]:
        return tuple(item for item in self.runs if item.result == "FAIL")

    def undecided(self) -> tuple[FalsifierRun, ...]:
        return tuple(item for item in self.runs if item.result == "UNKNOWN")

    def report(self) -> CheckReport:
        failures = self.failures()
        return CheckReport(
            name="falsifiers",
            passed=not failures,
            reasons=tuple(f"falsifier {item.name} FAILED: {item.reason}" for item in failures),
            unresolved=tuple(
                sorted(
                    f"falsifier {item.name} undecided: {item.reason}" for item in self.undecided()
                )
            ),
            detail={
                "total": len(self.runs),
                "passed": sum(1 for item in self.runs if item.result == "PASS"),
                "failed": len(failures),
                "skipped": sum(1 for item in self.runs if item.result == SKIPPED),
                "runs": [item.to_mapping() for item in self.runs],
            },
        )


def detected_classes(ledger: Ledger) -> frozenset[str]:
    classes: set[str] = set()
    evidence = ledger.evidence_by_id()
    attached = {eid for candidate in ledger.candidates for eid in candidate["evidence_ids"]}
    for eid in attached:
        record = evidence.get(eid)
        if record is not None:
            classes.add(str(record["claim_type"]))
    for candidate in ledger.candidates:
        classes.add(str(candidate["status"]))
    return frozenset(classes)


def run(
    falsifiers: Sequence[FalsifierView],
    ledger: Ledger,
    changes: ChangeSet,
    candidate_root: str,
    captured: Sequence[Mapping[str, Any]] = (),
) -> FalsifierReview:
    classes = detected_classes(ledger)
    subject = FalsifierInput(
        changes=changes,
        candidate_root=candidate_root,
        evidence=tuple(ledger.evidence),
        candidates=tuple(ledger.candidates),
        captured_requests=tuple(captured),
    )
    runs: list[FalsifierRun] = []
    for falsifier in sorted(falsifiers, key=lambda item: item.name):
        if falsifier.failure_class not in classes:
            runs.append(
                FalsifierRun(
                    name=falsifier.name,
                    failure_class=falsifier.failure_class,
                    result=SKIPPED,
                    reason=f"no {falsifier.failure_class} evidence in this run",
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
    return FalsifierReview(runs=tuple(runs))


def _check(falsifier: FalsifierView, subject: FalsifierInput) -> FalsifierOutcome:
    try:
        return falsifier.check(subject)
    except (OSError, ValueError, KeyError) as error:
        return FalsifierOutcome(
            result="UNKNOWN", reason=f"{type(error).__name__}: {error}", sites=()
        )


__all__ = ["FalsifierReview", "FalsifierRun", "detected_classes", "run"]
