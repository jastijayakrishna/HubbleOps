from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
import sysconfig
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import hubbleops
from hubbleops.core.errors import ToolingMissing

__all__ = ["ToolBinary", "identity", "locate", "platform_tag", "vendored", "vendored_root"]


@dataclass(frozen=True, slots=True)
class ToolBinary:
    name: str
    path: str
    origin: Literal["vendored", "path"]
    sha256: str


def platform_tag() -> str | None:
    host = sysconfig.get_platform()
    machine = platform.machine().lower()
    if host == "win-amd64":
        return "win_amd64"
    if host.startswith("linux") and machine in {"x86_64", "amd64"}:
        return "manylinux_2_35_x86_64"
    if host.startswith("macosx"):
        if machine == "arm64":
            return "macosx_11_0_arm64"
        if machine == "x86_64":
            return "macosx_10_12_x86_64"
    return None


def vendored_root() -> Path:
    return Path(hubbleops.__file__).parent / "_toolchain"


def locate(name: str, requested: str | None = None) -> ToolBinary:
    if requested is not None and (
        Path(requested).is_absolute() or Path(requested).parent != Path()
    ):
        return ToolBinary(name, requested, "path", _sha256(name, Path(requested)))
    vendored = _vendored(name)
    if vendored is not None:
        return vendored
    found = _which(requested or name)
    if found is None:
        raise ToolingMissing(name, "not vendored for this platform and not found on PATH")
    return ToolBinary(name, found, "path", _sha256(name, Path(found)))


def identity(tool: ToolBinary, version_text: str) -> str:
    return f"{version_text}+sha256:{tool.sha256}"


def vendored(name: str) -> ToolBinary | None:
    return _vendored(name)


def _vendored(name: str) -> ToolBinary | None:
    tag = platform_tag()
    if tag is None:
        return None
    manifest_path = vendored_root() / tag / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ToolingMissing(
            name, f"vendored manifest {manifest_path} unreadable: {error}"
        ) from error
    entry = _field(_field(manifest, "tools"), name)
    if entry is None:
        return None
    file_name = _field(entry, "file")
    expected = _field(entry, "sha256")
    if not isinstance(file_name, str) or not isinstance(expected, str):
        raise ToolingMissing(
            name, f"vendored manifest {manifest_path} names {name} without file and sha256"
        )
    path = manifest_path.parent / file_name
    actual = _sha256(name, path, "vendored binary hash mismatch")
    if actual != expected:
        raise ToolingMissing(
            name,
            f"vendored binary hash mismatch: manifest says {expected}, {path} hashes to {actual}",
        )
    if not os.access(path, os.X_OK):
        raise ToolingMissing(name, f"vendored binary is not executable: {path}")
    return ToolBinary(name, str(path), "vendored", actual)


def _field(container: object, key: str) -> object:
    if not isinstance(container, dict):
        return None
    return cast(Mapping[str, object], container).get(key)


def _which(query: str) -> str | None:
    found = shutil.which(query)
    if found is None and sys.platform == "win32":
        found = shutil.which(query + ".exe")
    return found


def _sha256(name: str, path: Path, prefix: str = "binary unreadable") -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as error:
        raise ToolingMissing(name, f"{prefix}: {path}: {error}") from error
    return digest.hexdigest()
