from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from hubbleops.app import registry
from hubbleops.app.cli import ScanResult, scan_repository


@pytest.fixture(scope="session")
def google_pack() -> registry.LoadedPack:
    return registry.load_pack("google_ads")


@pytest.fixture(scope="session")
def mock_pack() -> registry.LoadedPack:
    return registry.load_pack("_mock")


@pytest.fixture(scope="session")
def scan() -> Callable[[Path, registry.LoadedPack], ScanResult]:
    cache: dict[tuple[str, str], ScanResult] = {}

    def run(repo: Path, pack: registry.LoadedPack) -> ScanResult:
        key = (str(repo), pack.name)
        if key not in cache:
            cache[key] = scan_repository(repo, pack)
        return cache[key]

    return run
