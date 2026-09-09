from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hubbleops.core.verification import Verdict, VerificationResult


@dataclass(frozen=True, slots=True)
class Judgement:
    verdict: Verdict
    reasons: tuple[str, ...]
    unknown_blast: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reasons": list(self.reasons),
            "unknown_blast": list(self.unknown_blast),
        }


def decide(result: VerificationResult) -> Judgement:
    false_flags = tuple(sorted(name for name, value in result.flags().items() if not value))
    blast = tuple(sorted(set(result.unknown_blast)))
    unresolved = tuple(sorted(set(result.unresolved)))
    reasons = tuple(result.reasons)

    if false_flags:
        return Judgement(
            verdict="FAILED",
            reasons=tuple(sorted({*reasons, *(f"{name} is FAIL" for name in false_flags)})),
            unknown_blast=blast,
        )
    if not result.oracle_available:
        return Judgement(
            verdict="UNKNOWN",
            reasons=tuple(sorted({*reasons, "ORACLE_UNAVAILABLE"})),
            unknown_blast=blast,
        )
    if unresolved:
        return Judgement(
            verdict="UNKNOWN",
            reasons=tuple(sorted({*reasons, *(f"UNRESOLVED: {item}" for item in unresolved)})),
            unknown_blast=blast,
        )
    if blast:
        return Judgement(
            verdict="HUMAN_REQUIRED",
            reasons=tuple(
                sorted({*reasons, *(f"UNKNOWN_BLAST module {module}" for module in blast)})
            ),
            unknown_blast=blast,
        )
    return Judgement(verdict="VERIFIED_FOR_SCOPE", reasons=reasons, unknown_blast=())


__all__ = ["Judgement", "decide"]
