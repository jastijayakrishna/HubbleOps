from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from hubbleops.core.canonical import content_id
from hubbleops.core.errors import HubbleOpsError


class MountInvalid(HubbleOpsError):
    pass


@dataclass(frozen=True, order=True, slots=True)
class Mount:
    source: Path
    target: str
    read_only: bool

    def __post_init__(self) -> None:
        resolved = self.source.resolve()
        if not resolved.exists():
            raise MountInvalid(f"mount source does not exist: {resolved}")
        if (
            re.fullmatch(r"/[A-Za-z0-9._/-]+", self.target) is None
            or ".." in Path(self.target).parts
        ):
            raise MountInvalid(f"invalid container mount target: {self.target}")
        object.__setattr__(self, "source", resolved)

    def argument(self) -> str:
        mode = "ro" if self.read_only else "rw"
        return f"{wsl_path(self.source)}:{self.target}:{mode},nosuid,nodev"

    def mapping(self) -> dict[str, object]:
        return {"source": str(self.source), "target": self.target, "read_only": self.read_only}


def validate_mounts(mounts: tuple[Mount, ...], allowed_roots: tuple[Path, ...]) -> str:
    roots = tuple(root.resolve() for root in allowed_roots)
    targets: set[str] = set()
    for mount in mounts:
        if not any(_beneath(mount.source, root) for root in roots):
            raise MountInvalid(f"mount source escapes allowed roots: {mount.source}")
        if mount.target in targets:
            raise MountInvalid(f"duplicate container mount target: {mount.target}")
        targets.add(mount.target)
    return content_id([mount.mapping() for mount in sorted(mounts)])


def wsl_path(path: Path) -> str:
    resolved = path.resolve()
    drive, tail = os.path.splitdrive(str(resolved))
    if drive:
        normalized = tail.replace("\\", "/")
        return f"/mnt/{drive[0].lower()}{normalized}"
    return resolved.as_posix()


def _beneath(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


__all__ = ["Mount", "MountInvalid", "validate_mounts", "wsl_path"]
