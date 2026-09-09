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
    "46ee549c88617e9bc8acb843a326f1a5c0fa5608d7f9703509efe6d53b55f318"
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
    offending = forbidden_source_marker(source)
    if offending is not None:
        raise VerifierIsolationViolated(
            f"the {label} mount source {source} lies under {offending!r}"
        )


def forbidden_source_marker(source: Path) -> str | None:
    for part in source.resolve().parts:
        lowered = part.lower()
        stem = Path(lowered).stem
        for marker in FORBIDDEN_SOURCE_MARKERS:
            if (
                lowered == marker
                or stem == marker
                or stem.startswith(f"{marker}-")
                or stem.startswith(f"{marker}_")
            ):
                return marker
    return None


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
    "forbidden_source_marker",
    "verify_read_only",
]
