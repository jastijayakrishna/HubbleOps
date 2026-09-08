from __future__ import annotations

from dataclasses import dataclass

from hubbleops.core.canonical import content_id


@dataclass(frozen=True, slots=True)
class VerifierImage:
    reference: str | None = None
    network: str = "none"
    root_read_only: bool = True

    def fingerprint(self) -> str:
        return content_id(
            {
                "reference": self.reference,
                "network": self.network,
                "root_read_only": self.root_read_only,
            }
        )


VERIFIER_IMAGE = VerifierImage()

__all__ = ["VERIFIER_IMAGE", "VerifierImage"]
