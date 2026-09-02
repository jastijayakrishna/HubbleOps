from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "hubbleops"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "phase1"

GENERIC_LAYERS = (
    "core",
    "closure",
    "observe",
    "graph",
    "obligations",
    "verify",
    "proof",
    "store",
    "sandbox",
)

PHASE_ONE_LAYERS = ("core", "closure", "observe", "store")


def fixture_repos() -> list[Path]:
    return sorted(path for path in FIXTURE_ROOT.iterdir() if (path / "repo").is_dir())


def generic_layer_dirs() -> list[Path]:
    return [PACKAGE_ROOT / name for name in GENERIC_LAYERS if (PACKAGE_ROOT / name).is_dir()]


def python_sources(directory: Path) -> list[Path]:
    return sorted(directory.rglob("*.py"))
