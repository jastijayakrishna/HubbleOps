from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from hubbleops.core.repair import TransformInput, TransformView

APPLIED = "APPLIED"
NO_TRANSFORM = "NO_TRANSFORM"
PRECONDITION_FAILED = "PRECONDITION_FAILED"
POSTCONDITION_REVERTED = "POSTCONDITION_REVERTED"
TRANSFORM_FAILED = "TRANSFORM_FAILED"
UNCHANGED = "UNCHANGED"

TERMINAL_RESULTS = (
    NO_TRANSFORM,
    PRECONDITION_FAILED,
    POSTCONDITION_REVERTED,
    TRANSFORM_FAILED,
    UNCHANGED,
)


@dataclass(frozen=True, slots=True)
class RepairOutcome:
    obligation_id: str
    path: str
    transform: str
    result: str
    reason: str

    def discharged(self) -> bool:
        return self.result == APPLIED

    def to_mapping(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "path": self.path,
            "transform": self.transform,
            "result": self.result,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class RepairReport:
    outcomes: tuple[RepairOutcome, ...]
    texts: Mapping[str, str]

    def discharged(self) -> tuple[str, ...]:
        return tuple(sorted({item.obligation_id for item in self.outcomes if item.discharged()}))

    def undischarged(self) -> tuple[RepairOutcome, ...]:
        return tuple(item for item in self.outcomes if not item.discharged())

    def to_mapping(self) -> dict[str, Any]:
        return {
            "outcomes": [item.to_mapping() for item in self.outcomes],
            "paths_changed": sorted(self.texts),
        }


def run(*, transforms: Sequence[TransformView], requests: Sequence[TransformInput]) -> RepairReport:
    ordered = sorted(transforms, key=lambda item: item.name)
    texts: dict[str, str] = {}
    outcomes: list[RepairOutcome] = []
    for request in sorted(requests, key=lambda item: (item.path, item.obligation_id)):
        current = request.with_text(texts.get(request.path, request.text))
        outcomes.append(_apply_one(ordered, current, texts))
    return RepairReport(outcomes=tuple(outcomes), texts=dict(sorted(texts.items())))


def _apply_one(
    transforms: Sequence[TransformView], request: TransformInput, texts: dict[str, str]
) -> RepairOutcome:
    for transform in transforms:
        try:
            eligible = transform.precondition(request)
        except Exception as error:
            return _outcome(request, transform.name, TRANSFORM_FAILED, f"precondition: {error}")
        if not eligible:
            continue
        try:
            produced = transform.apply(request)
        except Exception as error:
            return _outcome(request, transform.name, TRANSFORM_FAILED, f"apply: {error}")
        if not produced.changed():
            return _outcome(
                request,
                transform.name,
                UNCHANGED,
                "the transform applied cleanly but changed nothing at this site",
            )
        try:
            held = transform.postcondition(produced)
        except Exception as error:
            return _outcome(request, transform.name, TRANSFORM_FAILED, f"postcondition: {error}")
        if not held:
            return _outcome(
                request,
                transform.name,
                POSTCONDITION_REVERTED,
                produced.reason or "the post-check refused the result, so nothing was written",
            )
        texts[request.path] = produced.text
        return _outcome(request, transform.name, APPLIED, produced.reason)
    return _outcome(
        request,
        "",
        NO_TRANSFORM,
        "no deterministic transform claims this obligation; it stays open for a human",
    )


def _outcome(request: TransformInput, transform: str, result: str, reason: str) -> RepairOutcome:
    return RepairOutcome(
        obligation_id=request.obligation_id,
        path=request.path,
        transform=transform,
        result=result,
        reason=reason,
    )


__all__ = [
    "APPLIED",
    "NO_TRANSFORM",
    "POSTCONDITION_REVERTED",
    "PRECONDITION_FAILED",
    "TERMINAL_RESULTS",
    "TRANSFORM_FAILED",
    "UNCHANGED",
    "RepairOutcome",
    "RepairReport",
    "run",
]
