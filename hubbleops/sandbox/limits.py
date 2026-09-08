from __future__ import annotations

from dataclasses import dataclass

from hubbleops.core.canonical import content_id
from hubbleops.core.errors import HubbleOpsError


class LimitsInvalid(HubbleOpsError):
    pass


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    cpu_seconds: int = 60
    memory_bytes: int = 536_870_912
    address_space_bytes: int = 8_589_934_592
    processes: int = 128
    open_files: int = 256
    file_bytes: int = 67_108_864
    output_bytes: int = 4_194_304
    wall_seconds: float = 120.0

    def __post_init__(self) -> None:
        values = {
            "cpu_seconds": self.cpu_seconds,
            "memory_bytes": self.memory_bytes,
            "address_space_bytes": self.address_space_bytes,
            "processes": self.processes,
            "open_files": self.open_files,
            "file_bytes": self.file_bytes,
            "output_bytes": self.output_bytes,
            "wall_seconds": self.wall_seconds,
        }
        invalid = [name for name, value in values.items() if value <= 0]
        if invalid:
            raise LimitsInvalid(f"resource limits must be positive: {', '.join(invalid)}")
        if self.memory_bytes % 1024:
            raise LimitsInvalid("memory_bytes must be divisible by 1024")
        if self.address_space_bytes % 1024:
            raise LimitsInvalid("address_space_bytes must be divisible by 1024")
        if self.address_space_bytes < self.memory_bytes:
            raise LimitsInvalid(
                "address_space_bytes below memory_bytes would silently replace the resident bound"
            )
        if self.file_bytes % 512:
            raise LimitsInvalid("file_bytes must be divisible by 512")

    def shell(self, command: str) -> str:
        values = (
            f"ulimit -t {self.cpu_seconds}",
            f"ulimit -d {self.memory_bytes // 1024}",
            f"ulimit -v {self.address_space_bytes // 1024}",
            f"ulimit -u {self.processes}",
            f"ulimit -n {self.open_files}",
            f"ulimit -f {self.file_bytes // 512}",
            f"exec sh -ceu {quote_shell(command)}",
        )
        return "; ".join(values)

    def fingerprint(self) -> str:
        return content_id(
            {
                "cpu_seconds": self.cpu_seconds,
                "memory_bytes": self.memory_bytes,
                "address_space_bytes": self.address_space_bytes,
                "processes": self.processes,
                "open_files": self.open_files,
                "file_bytes": self.file_bytes,
                "output_bytes": self.output_bytes,
                "wall_seconds": self.wall_seconds,
            }
        )


def quote_shell(value: str) -> str:
    if "\x00" in value:
        raise LimitsInvalid("command contains NUL")
    return "'" + value.replace("'", "'\"'\"'") + "'"


__all__ = ["LimitsInvalid", "ResourceLimits", "quote_shell"]
