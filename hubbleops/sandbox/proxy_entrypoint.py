from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Protocol, cast


class ResourceApi(Protocol):
    RLIMIT_CPU: int
    RLIMIT_DATA: int
    RLIMIT_AS: int
    RLIMIT_NPROC: int
    RLIMIT_NOFILE: int
    RLIMIT_FSIZE: int

    def setrlimit(self, resource: int, limits: tuple[int, int]) -> None: ...

    def getrlimit(self, resource: int) -> tuple[int, int]: ...


RESOURCE = cast(ResourceApi, importlib.import_module("resource"))

NAMES = (
    ("cpu", RESOURCE.RLIMIT_CPU),
    ("data", RESOURCE.RLIMIT_DATA),
    ("address_space", RESOURCE.RLIMIT_AS),
    ("processes", RESOURCE.RLIMIT_NPROC),
    ("open_files", RESOURCE.RLIMIT_NOFILE),
    ("file_size", RESOURCE.RLIMIT_FSIZE),
)


def main(argv: list[str]) -> None:
    if len(argv) < 9 or argv[7] != "--":
        raise SystemExit("proxy entrypoint requires six limits, --, and an executable")
    values = [int(value) for value in argv[1:7]]
    if any(value <= 0 for value in values):
        raise SystemExit("proxy resource limits must be positive")
    for (_, kind), value in zip(NAMES, values, strict=True):
        RESOURCE.setrlimit(kind, (value, value))
    observed = {name: list(RESOURCE.getrlimit(kind)) for name, kind in NAMES}
    payload = json.dumps(observed, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    destination = Path("/hops/output/limits.json")
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.execv(argv[8], argv[8:])


if __name__ == "__main__":
    main(sys.argv)
