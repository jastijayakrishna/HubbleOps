from __future__ import annotations

import ast
from pathlib import Path

from tests.support import (
    PACKAGE_ROOT,
    PHASE_ONE_LAYERS,
    generic_layer_dirs,
    python_sources,
)

FORBIDDEN_PREFIXES = ("hubbleops.packs", "packs", "hubbleops.repair", "repair")
VERIFY_FORBIDDEN = (
    "hubbleops.sandbox.runner",
    "hubbleops.store.facts",
    "hubbleops.store.bindings",
)


def imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.append(node.module)
    return modules


def test_phase_one_layers_exist() -> None:
    missing = [name for name in PHASE_ONE_LAYERS if not (PACKAGE_ROOT / name).is_dir()]
    assert missing == [], f"generic layers missing, so the import law is untested: {missing}"


def test_generic_layers_never_import_packs_or_repair() -> None:
    offences: list[str] = []
    for directory in generic_layer_dirs():
        for source in python_sources(directory):
            for module in imported_modules(source):
                for prefix in FORBIDDEN_PREFIXES:
                    if module == prefix or module.startswith(f"{prefix}."):
                        offences.append(f"{source.relative_to(PACKAGE_ROOT.parent)} imports {module}")
    assert offences == [], "law L5: generic layers must receive pack parts as parameters"


def test_verify_never_imports_the_systems_it_judges() -> None:
    verify_dir = PACKAGE_ROOT / "verify"
    if not verify_dir.is_dir():
        return
    offences: list[str] = []
    for source in python_sources(verify_dir):
        for module in imported_modules(source):
            for prefix in VERIFY_FORBIDDEN:
                if module == prefix or module.startswith(f"{prefix}."):
                    offences.append(f"{source.name} imports {module}")
    assert offences == [], "law L6: the system that changes code never judges the change"


def test_app_is_the_only_importer_of_packs() -> None:
    importers: set[str] = set()
    for source in python_sources(PACKAGE_ROOT):
        relative = source.relative_to(PACKAGE_ROOT)
        if relative.parts[0] == "packs":
            continue
        for module in imported_modules(source):
            if module == "hubbleops.packs" or module.startswith("hubbleops.packs."):
                importers.add(relative.parts[0])
    assert importers <= {"app"}, f"only app/ may import packs/, found: {sorted(importers)}"
