from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hubbleops.core.canonical import content_id
from hubbleops.core.errors import HubbleOpsError
from hubbleops.sandbox.image import ImageSpec
from hubbleops.sandbox.limits import ResourceLimits
from hubbleops.sandbox.mounts import Mount

VERIFIER_REFERENCE = (
    "docker.io/library/python@sha256:"
    "782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254"
)
CANDIDATE_TARGET = "/verify/candidate"
BASE_TARGET = "/verify/base"
OUTPUT_TARGET = "/verify/output"
FORBIDDEN_SOURCE_MARKERS = (
    "attempts",
    "agent",
    "agent-logs",
    "change-manifest",
    "change_manifest",
    "manifest",
    "repair",
    "worktrees",
)


class VerifierIsolationViolated(HubbleOpsError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            f"VERIFIER_ISOLATION_VIOLATED: {detail}. The authority reads the base tree, the "
            "candidate tree and its own output directory, and nothing the repair side produced."
        )
        self.detail = detail


@dataclass(frozen=True, slots=True)
class VerifierImage:
    reference: str = VERIFIER_REFERENCE
    user: str = "65532:65532"
    network: str = "none"
    root_read_only: bool = True

    def spec(self) -> ImageSpec:
        return ImageSpec(self.reference, self.user)

    def fingerprint(self) -> str:
        return content_id(
            {
                "reference": self.reference,
                "user": self.user,
                "network": self.network,
                "root_read_only": self.root_read_only,
                "candidate_target": CANDIDATE_TARGET,
                "base_target": BASE_TARGET,
                "output_target": OUTPUT_TARGET,
                "candidate_read_only": True,
                "base_read_only": True,
                "forbidden_markers": list(FORBIDDEN_SOURCE_MARKERS),
            }
        )

    def mounts(self, base: Path, candidate: Path, output: Path) -> tuple[Mount, ...]:
        for label, source in (("base", base), ("candidate", candidate)):
            _refuse_repair_source(label, source)
        return (
            Mount(source=base, target=BASE_TARGET, read_only=True),
            Mount(source=candidate, target=CANDIDATE_TARGET, read_only=True),
            Mount(source=output, target=OUTPUT_TARGET, read_only=False),
        )


def _refuse_repair_source(label: str, source: Path) -> None:
    parts = {part.lower() for part in source.resolve().parts}
    offending = sorted(parts & set(FORBIDDEN_SOURCE_MARKERS))
    if offending:
        raise VerifierIsolationViolated(
            f"the {label} mount source {source} lies under {offending[0]!r}"
        )


def verify_read_only(mounts: tuple[Mount, ...]) -> None:
    for mount in mounts:
        if mount.target in (BASE_TARGET, CANDIDATE_TARGET) and not mount.read_only:
            raise VerifierIsolationViolated(
                f"{mount.target} is mounted read-write; the verifier never writes what it judges"
            )


VERIFIER_IMAGE = VerifierImage()
VERIFIER_LIMITS = ResourceLimits(
    cpu_seconds=900,
    memory_bytes=2_147_483_648,
    address_space_bytes=8_589_934_592,
    wall_seconds=1_800.0,
)

__all__ = [
    "BASE_TARGET",
    "CANDIDATE_TARGET",
    "FORBIDDEN_SOURCE_MARKERS",
    "OUTPUT_TARGET",
    "VERIFIER_IMAGE",
    "VERIFIER_LIMITS",
    "VerifierImage",
    "VerifierIsolationViolated",
    "verify_read_only",
]
