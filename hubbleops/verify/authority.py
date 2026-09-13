from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from hubbleops.core.verification import (
    ChangeSet,
    CheckReport,
    FalsifierView,
    ObligationView,
    OracleView,
    SuiteRun,
    VerificationResult,
)
from hubbleops.graph import ImportGraph
from hubbleops.observe import Ledger
from hubbleops.verify import audit, behavior, conserve, falsify, oracle, radius
from hubbleops.verify.gitdiff import Delta
from hubbleops.verify.verdict import Judgement, decide


@dataclass(frozen=True, slots=True)
class Evaluation:
    audit: audit.Audit
    oracle: oracle.OracleReview
    containment: radius.Containment
    blast: radius.BlastRadius
    frozen_tests: SuiteRun
    candidate_tests: SuiteRun
    differential: behavior.ShapeDifferential
    consumers: behavior.ConsumerCheck
    falsifiers: falsify.FalsifierReview
    conservation: conserve.Conservation
    candidate_counts: Mapping[str, int]
    result: VerificationResult
    judgement: Judgement

    def reports(self) -> tuple[CheckReport, ...]:
        return (
            self.audit.report(),
            self.oracle.report(),
            self.containment.report(),
            radius.frozen_report(self.frozen_tests),
            self.differential.report(),
            self.consumers.report(),
            self.falsifiers.report(),
            self.conservation.report(),
        )


@dataclass(frozen=True, slots=True)
class Inputs:
    base_ledger: Ledger
    candidate_ledger: Ledger
    base_graph: ImportGraph
    candidate_graph: ImportGraph
    delta: Delta
    changes: ChangeSet
    obligations: tuple[ObligationView, ...]
    oracle: OracleView
    falsifiers: tuple[FalsifierView, ...]
    frozen_tests: SuiteRun
    candidate_tests: SuiteRun
    candidate_root: str
    candidate_paths: tuple[str, ...] = ()
    base_captured: tuple[Mapping[str, Any], ...] = ()
    candidate_captured: tuple[Mapping[str, Any], ...] = ()
    decisions: tuple[Mapping[str, Any], ...] = ()
    uncoverable: tuple[str, ...] = ()
    supported_targets: tuple[str, ...] = ()
    captures_supplied: bool = False
    now: datetime | None = None


def evaluate(inputs: Inputs) -> Evaluation:
    audit_result = audit.run(
        inputs.candidate_ledger,
        inputs.changes,
        inputs.obligations,
        Path(inputs.candidate_root),
        inputs.candidate_paths,
        inputs.candidate_captured,
        inputs.supported_targets,
    )
    sites = oracle.request_sites(inputs.candidate_ledger, inputs.candidate_captured)
    oracle_result = oracle.review(
        inputs.oracle,
        sites.requests,
        inputs.changes.to_version,
        inputs.now,
        sites.unreachable,
        subjects_changed=bool(inputs.changes.changes),
        decided=sites.decided,
    )
    containment = radius.contain(
        inputs.delta,
        inputs.obligations,
        inputs.candidate_ledger.evidence,
        inputs.candidate_graph,
    )
    blast = radius.blast(
        inputs.delta,
        inputs.candidate_graph,
        inputs.frozen_tests,
        inputs.uncoverable,
        inputs.base_graph,
    )
    differential = _differential(inputs)
    consumers = behavior.consumers(
        inputs.candidate_graph,
        inputs.changes,
        Path(inputs.candidate_root),
        _provider_paths(inputs.candidate_ledger),
    )
    falsifiers = falsify.run(
        inputs.falsifiers,
        inputs.candidate_ledger,
        inputs.changes,
        inputs.candidate_root,
        inputs.candidate_captured,
    )
    conservation = conserve.compare(inputs.base_ledger, inputs.candidate_ledger, inputs.decisions)

    frozen = radius.frozen_report(inputs.frozen_tests)
    reports = (
        audit_result.report(),
        oracle_result.report(),
        containment.report(),
        frozen,
        differential.report(),
        consumers.report(),
        falsifiers.report(),
        conservation.report(),
    )
    result = VerificationResult(
        audit_pass=reports[0].passed,
        oracle_all_accepted=reports[1].passed,
        zero_unexplained_hunks=reports[2].passed,
        frozen_baseline_tests_pass=reports[3].passed,
        request_shape_differential_pass=reports[4].passed,
        response_consumer_check_pass=reports[5].passed,
        falsifiers_pass=reports[6].passed,
        unknown_conservation_pass=reports[7].passed,
        unknown_blast=blast.unknown_blast,
        oracle_available=oracle_result.available,
        unresolved=tuple(sorted({item for report in reports for item in report.unresolved})),
        reasons=tuple(sorted({item for report in reports for item in report.reasons})),
    )
    return Evaluation(
        audit=audit_result,
        oracle=oracle_result,
        containment=containment,
        blast=blast,
        frozen_tests=inputs.frozen_tests,
        candidate_tests=inputs.candidate_tests,
        differential=differential,
        consumers=consumers,
        falsifiers=falsifiers,
        conservation=conservation,
        candidate_counts=inputs.candidate_ledger.counts(),
        result=result,
        judgement=decide(result),
    )


def _provider_paths(ledger: Ledger) -> frozenset[str]:
    attached = {eid for candidate in ledger.candidates for eid in candidate["evidence_ids"]}
    return frozenset(
        str(record["path"]) for record in ledger.evidence if str(record["id"]) in attached
    )


def _differential(inputs: Inputs) -> behavior.ShapeDifferential:
    if inputs.captures_supplied:
        if not inputs.base_captured or not inputs.candidate_captured:
            return behavior.differential(
                (),
                (),
                inputs.obligations,
                behavior.CAPTURED_SOURCE,
                resolved=False,
                reason="both base and candidate captures are required for a dynamic differential",
            )
        base_shapes = behavior.captured_shapes(inputs.base_captured)
        candidate_shapes = behavior.captured_shapes(inputs.candidate_captured)
        if not base_shapes or not candidate_shapes:
            return behavior.differential(
                (),
                (),
                inputs.obligations,
                behavior.CAPTURED_SOURCE,
                resolved=False,
                reason="a supplied capture carries no reconstructable request body",
            )
        return behavior.differential(
            base_shapes,
            candidate_shapes,
            inputs.obligations,
            behavior.CAPTURED_SOURCE,
            inputs.changes,
        )
    base_shapes = behavior.shapes_of(inputs.base_ledger, ())
    candidate_shapes = behavior.shapes_of(inputs.candidate_ledger, ())
    if not base_shapes and not candidate_shapes:
        return behavior.differential(
            (),
            (),
            inputs.obligations,
            behavior.STATIC_SOURCE,
            resolved=False,
            reason=(
                "no capture was supplied and neither tree yields a request skeleton, "
                "so the request-shape differential has no input to compare"
            ),
        )
    return behavior.differential(
        base_shapes, candidate_shapes, inputs.obligations, behavior.STATIC_SOURCE, inputs.changes
    )


def obligations_from(records: Sequence[Mapping[str, Any]]) -> tuple[ObligationView, ...]:
    return tuple(
        sorted(
            (ObligationView.from_record(record) for record in records),
            key=lambda item: item.id,
        )
    )


__all__ = ["Evaluation", "Inputs", "evaluate", "obligations_from"]
