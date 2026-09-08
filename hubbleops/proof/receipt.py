from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from hubbleops.core.canonical import content_id, export_bytes
from hubbleops.core.proof_scope import short_scope
from hubbleops.core.records import as_mapping, as_sequence, is_list, is_mapping
from hubbleops.core.schema import validate
from hubbleops.verify import Evaluation

RULE = "─" * 44
TIMESTAMP_KEYS = frozenset({"checked_at", "started_at", "finished_at", "duration_seconds"})


@dataclass(frozen=True, slots=True)
class Receipt:
    record: dict[str, Any]

    def body_hash(self) -> str:
        return content_id(_without_timestamps(self.record))

    def verdict(self) -> str:
        return str(self.record["verdict"])

    def json_bytes(self) -> bytes:
        return export_bytes(self.record)


def build(
    *,
    evaluation: Evaluation,
    proof_scope: Mapping[str, Any],
    provider: str,
    changes_hash: str,
    base_sha: str,
    candidate_sha: str,
    from_version: str,
    to_version: str,
    production_coverage: tuple[int, int] | None = None,
) -> Receipt:
    record: dict[str, Any] = {
        "proof_scope": dict(proof_scope),
        "candidates_summary": _counts(evaluation),
        "obligations": [item.to_mapping() for item in evaluation.audit.reconciliations],
        "changed_files": _changed_files(evaluation),
        "migration_audit": evaluation.audit.report().to_mapping(),
        "oracle_results": [item.to_mapping() for item in evaluation.oracle.checks],
        "blast_radius": {
            **evaluation.blast.to_mapping(),
            "containment": evaluation.containment.report().to_mapping()["detail"],
        },
        "request_shape_differential": evaluation.differential.report().to_mapping(),
        "response_consumer_check": evaluation.consumers.report().to_mapping(),
        "frozen_baseline_tests": evaluation.frozen_tests.to_mapping(),
        "candidate_tests": evaluation.candidate_tests.to_mapping(),
        "falsifiers": [item.to_mapping() for item in evaluation.falsifiers.runs],
        "unknowns": list(evaluation.conservation.preserved_unknowns()),
        "unknown_conservation": evaluation.conservation.report().to_mapping(),
        "verdict": evaluation.judgement.verdict,
        "reasons": list(evaluation.judgement.reasons),
    }
    record["migration_audit"].update(
        {
            "provider": provider,
            "changes_hash": changes_hash,
            "base_sha": base_sha,
            "candidate_sha": candidate_sha,
            "from_version": from_version,
            "to_version": to_version,
            "production_coverage": list(production_coverage) if production_coverage else None,
        }
    )
    return Receipt(record=validate("receipt", record))


def _counts(evaluation: Evaluation) -> dict[str, int]:
    counts = evaluation.candidate_counts
    return {
        key: int(counts.get(key, 0))
        for key in (
            "total",
            "affected",
            "not_affected_with_evidence",
            "unknown",
            "human_required",
            "excluded_with_evidence",
            "unexplained",
        )
    }


def _changed_files(evaluation: Evaluation) -> list[str]:
    return sorted({item.hunk.path for item in evaluation.containment.mappings})


def _without_timestamps(value: Any) -> Any:
    if is_mapping(value):
        mapping = as_mapping(value)
        return {
            key: _without_timestamps(mapping[key])
            for key in sorted(mapping)
            if key not in TIMESTAMP_KEYS
        }
    if is_list(value):
        return [_without_timestamps(item) for item in as_sequence(value)]
    return value


def render(receipt: Receipt) -> str:
    record = receipt.record
    scope = record["proof_scope"]
    audit = record["migration_audit"]
    blast = record["blast_radius"]
    containment = blast.get("containment", {})
    frozen = record["frozen_baseline_tests"]
    candidate = record["candidate_tests"]
    counts = record["candidates_summary"]
    oracle = record["oracle_results"]
    accepted = sum(1 for item in oracle if item["code"] == "VALID")

    lines = [
        f"HubbleOps PROOF PACK — {audit['provider']} migration "
        f"{audit.get('from_version', '?')} → {audit.get('to_version', '?')}",
        "",
        f"Base       {audit['base_sha'][:7]}          Candidate   {audit['candidate_sha'][:7]}",
        f"ProofScope {short_scope(content_id(scope))}         "
        f"Verifier image  {scope.get('verifier_image_hash') or 'not recorded'}",
        f"Contract   {audit['provider']}@{audit['changes_hash'][:12]}",
        "",
        RULE,
        "DISCOVERY",
        f"  candidates {counts['total']} · affected {counts['affected']} · "
        f"not affected (evidence) {counts['not_affected_with_evidence']} · "
        f"UNKNOWN {counts['unknown']} · unexplained {counts['unexplained']}",
        f"  production services accounted for {_coverage(audit)}",
        "",
        "OBLIGATIONS",
        f"  {len(record['obligations'])} total · "
        f"{sum(1 for item in record['obligations'] if item['status'] == 'DISCHARGED')} resolved · "
        f"{sum(1 for item in record['obligations'] if item['status'] == 'OPEN')} open · "
        f"{sum(1 for item in record['obligations'] if item['status'] == 'UNRECONCILABLE')} "
        "unreconcilable",
        "",
        f"MIGRATION AUDIT                              {_mark(audit['passed'])}",
        f"  old-version residue {_none_or(audit['detail']['old_version_residue'])} · "
        f"removed subjects present {_none_or(audit['detail']['removed_subjects_present'])}",
        "",
        f"CONTRACT ORACLE ({audit.get('to_version', 'target')}, validate_only)",
        f"  {accepted} / {len(oracle)} requests ACCEPTED   [request hashes attached]",
        "",
        "BLAST RADIUS",
        f"  changed symbols {len(blast['changed_definitions'])} · "
        f"reachable modules {len(blast['reachable_modules'])} · "
        f"covered by frozen tests {len(blast['covered_modules'])} · "
        f"UNKNOWN_BLAST {_none_or(blast['unknown_blast'])}",
        f"  diff containment: {containment.get('hunks', 0)} hunks → "
        f"{containment.get('obligations', 0)} obligations · "
        f"{containment.get('collateral', 0)} collateral · "
        f"{containment.get('unexplained', 0)} unexplained",
        f"  request-shape differential {_mark(record['request_shape_differential']['passed'])}"
        f" ({record['request_shape_differential']['detail'].get('source', 'none')})"
        f" · response-consumer check {_mark(record['response_consumer_check']['passed'])}",
        "",
        "TESTS",
        f"  frozen baseline {frozen['passed']} PASS / {frozen['failed']} FAIL / "
        f"{frozen['skipped']} SKIP (proof)",
        f"  candidate {candidate['passed']} PASS / {candidate['failed']} FAIL / "
        f"{candidate['skipped']} SKIP (evidence)",
        "",
        f"FALSIFIERS  {sum(1 for item in record['falsifiers'] if item['result'] == 'PASS')}"
        f" / {len(record['falsifiers'])} PASS"
        f" ({sum(1 for item in record['falsifiers'] if item['result'] == 'SKIPPED')} skipped)",
        "",
        "UNKNOWN",
    ]
    if record["unknowns"]:
        lines.append(f"  {len(record['unknowns'])} preserved, each with a closing instruction")
        for unknown in record["unknowns"]:
            lines.append(f"    {unknown['candidate_id'][:12]}  {unknown['close_with']}")
    else:
        lines.append("  none preserved")
    lines.append(f"  conservation {_mark(record['unknown_conservation']['passed'])}")
    lines.append("")
    lines.append(f"VERDICT   {record['verdict']}")
    if record["reasons"]:
        lines.append("")
        lines.append("REASONS")
        lines.extend(f"  {reason}" for reason in record["reasons"])
    return "\n".join(lines) + "\n"


def _mark(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def _none_or(values: list[Any]) -> str:
    return "none" if not values else ", ".join(str(item) for item in values)


def _coverage(audit: Mapping[str, Any]) -> str:
    coverage = audit.get("production_coverage")
    if not coverage:
        return "no telemetry or sentinel observer in this ProofScope"
    return f"{coverage[0]} / {coverage[1]}"


__all__ = ["Receipt", "build", "render"]
