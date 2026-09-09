from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol, runtime_checkable

TransformResult = Literal["APPLIED", "SKIPPED", "REVERTED"]


@dataclass(frozen=True, slots=True)
class TransformInput:
    obligation_id: str
    path: str
    text: str
    current_state: str
    required_state: str
    from_version: str
    to_version: str
    claim_type: str = ""
    line: int | None = None
    subject: str | None = None
    replacement: str | None = None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "path": self.path,
            "current_state": self.current_state,
            "required_state": self.required_state,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "claim_type": self.claim_type,
            "line": self.line,
            "subject": self.subject,
            "replacement": self.replacement,
        }

    def with_text(self, text: str) -> TransformInput:
        return replace(self, text=text)


@dataclass(frozen=True, slots=True)
class TransformOutput:
    source: TransformInput
    result: TransformResult
    text: str
    reason: str
    sites: tuple[str, ...] = ()

    def changed(self) -> bool:
        return self.result == "APPLIED" and self.text != self.source.text

    def to_mapping(self) -> dict[str, Any]:
        return {
            "obligation_id": self.source.obligation_id,
            "path": self.source.path,
            "result": self.result,
            "reason": self.reason,
            "sites": list(self.sites),
        }


@runtime_checkable
class TransformView(Protocol):
    name: str
    failure_class: str

    def precondition(self, subject: TransformInput) -> bool: ...

    def apply(self, subject: TransformInput) -> TransformOutput: ...

    def postcondition(self, subject: TransformOutput) -> bool: ...


__all__ = [
    "TransformInput",
    "TransformOutput",
    "TransformResult",
    "TransformView",
]
