from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, NamedTuple

from hatchling.builders.config import BuilderConfig
from hatchling.builders.hooks.plugin.interface import BuildHookInterface

PLATFORM_ENV = "HUBBLEOPS_WHEEL_PLATFORM"
CACHE_ENV = "HUBBLEOPS_TOOLCHAIN_CACHE"
PINS_FILE = "toolchain.json"


class Pin(NamedTuple):
    url: str
    archive_sha256: str
    member: str
    binary_sha256: str


class Tool(NamedTuple):
    version: str
    platforms: dict[str, Pin]


class CustomBuildHook(BuildHookInterface[BuilderConfig]):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        tag = os.environ.get(PLATFORM_ENV)
        if not tag:
            return
        root = Path(self.root)
        tools = _load_pins(root / PINS_FILE)
        known = sorted({platform for tool in tools.values() for platform in tool.platforms})
        if tag not in known:
            raise ValueError(f"{PLATFORM_ENV}={tag!r} is not pinned in {PINS_FILE}; known: {known}")
        cache = Path(os.environ.get(CACHE_ENV) or root / ".hubbleops" / "toolchain-cache")
        cache.mkdir(parents=True, exist_ok=True)
        staging = _fresh_staging(root / "build" / "toolchain", tag)
        manifest = {"platform": tag, "tools": _stage(tools, tag, cache, staging)}
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        build_data["force_include"][str(staging)] = f"hubbleops/_toolchain/{tag}"
        build_data["pure_python"] = False
        build_data["tag"] = f"py3-none-{tag}"


def _fresh_staging(parent: Path, tag: str) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / tag
    if staging.exists():
        try:
            shutil.rmtree(staging)
        except OSError:
            staging = Path(tempfile.mkdtemp(prefix=f"{tag}-", dir=parent))
            return staging
    staging.mkdir(parents=True)
    return staging


def _load_pins(path: Path) -> dict[str, Tool]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    tools: dict[str, Tool] = {}
    for name, entry in raw["tools"].items():
        platforms = {tag: Pin(**pin) for tag, pin in entry["platforms"].items()}
        tools[name] = Tool(version=entry["version"], platforms=platforms)
    return tools


def _stage(
    tools: dict[str, Tool], tag: str, cache: Path, staging: Path
) -> dict[str, dict[str, str]]:
    staged: dict[str, dict[str, str]] = {}
    for name, tool in sorted(tools.items()):
        pin = tool.platforms[tag]
        archive = _fetch(pin.url, pin.archive_sha256, cache)
        data = _read_member(archive, pin.member)
        actual = hashlib.sha256(data).hexdigest()
        if actual != pin.binary_sha256:
            raise RuntimeError(
                f"{name} member {pin.member} in {archive.name} hashes to {actual}, "
                f"pinned {pin.binary_sha256}"
            )
        file_name = name + Path(pin.member).suffix
        target = staging / file_name
        target.write_bytes(data)
        if os.name == "posix":
            target.chmod(0o755)
        staged[name] = {"file": file_name, "version": tool.version, "sha256": actual}
    return staged


def _fetch(url: str, expected: str, cache: Path) -> Path:
    dest = cache / url.rsplit("/", 1)[1]
    if not dest.is_file():
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as partial:
            with urllib.request.urlopen(url) as response:
                shutil.copyfileobj(response, partial)
        os.replace(partial.name, dest)
    actual = _sha256_file(dest)
    if actual != expected:
        raise RuntimeError(
            f"{dest} hashes to {actual}, pinned {expected}; delete it to fetch again"
        )
    return dest


def _read_member(archive: Path, member: str) -> bytes:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            return bundle.read(member)
    with tarfile.open(archive, "r:gz") as bundle:
        stream = bundle.extractfile(member)
        if stream is None:
            raise RuntimeError(f"{member} in {archive.name} is not a regular file")
        with stream:
            return stream.read()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
