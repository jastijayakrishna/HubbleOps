from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.support import (
    PACKAGE_ROOT,
    PHASE_ONE_LAYERS,
    generic_layer_dirs,
    import_cycles,
    internal_imports,
    module_layer,
    python_sources,
)

VOCABULARY_LAYERS = frozenset({"core"})

TOLERATED_DEEP_IMPORTS = frozenset(
    {
        "app -> hubbleops.graph.imports",
        "app -> hubbleops.observe.deps",
        "app -> hubbleops.observe.dynamic",
        "app -> hubbleops.observe.dynamic.loaders",
        "app -> hubbleops.observe.dynamic.runner",
        "app -> hubbleops.observe.ledger",
        "app -> hubbleops.observe.telemetry",
        "app -> hubbleops.packs._protocol",
        "app -> hubbleops.sandbox.verifier_image",
        "app -> hubbleops.store.artifacts",
        "app -> hubbleops.store.sqlite",
        "observe -> hubbleops.closure.source_closure",
        "observe -> hubbleops.graph.imports",
    }
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


def forbidden_for(layer: str) -> tuple[str, ...]:
    own = (layer, f"hubbleops.{layer}")
    return tuple(prefix for prefix in FORBIDDEN_PREFIXES if prefix not in own)


def test_generic_layers_never_import_packs_or_repair() -> None:
    offences: list[str] = []
    for directory in generic_layer_dirs():
        for source in python_sources(directory):
            for module in imported_modules(source):
                for prefix in forbidden_for(directory.name):
                    if module == prefix or module.startswith(f"{prefix}."):
                        offences.append(
                            f"{source.relative_to(PACKAGE_ROOT.parent)} imports {module}"
                        )
    assert offences == [], "law L5: generic layers must receive pack parts as parameters"


def test_the_repair_layer_is_scanned_by_the_leak_tests() -> None:
    scanned = {directory.name for directory in generic_layer_dirs()}
    assert "repair" in scanned, (
        "repair/ receives pack transforms as parameters like every other generic layer, "
        "so the import and provider-name laws have to look there"
    )
    assert "hubbleops.packs" in forbidden_for("repair")
    assert "hubbleops.repair" not in forbidden_for("repair")


def test_an_injected_generic_pack_import_is_detected(tmp_path: Path) -> None:
    source = tmp_path / "leak.py"
    source.write_text("from hubbleops.packs import google_ads\n", encoding="utf-8")
    modules = imported_modules(source)
    assert any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in modules
        for prefix in FORBIDDEN_PREFIXES
    )


def test_verify_never_imports_the_systems_it_judges() -> None:
    verify_dir = PACKAGE_ROOT / "verify"
    if not verify_dir.is_dir():
        pytest.skip("verify/ ships in Phase 5; law L6 has no code to test yet")
    offences: list[str] = []
    for source in python_sources(verify_dir):
        for module in imported_modules(source):
            for prefix in VERIFY_FORBIDDEN:
                if module == prefix or module.startswith(f"{prefix}."):
                    offences.append(f"{source.name} imports {module}")
    assert offences == [], "law L6: the system that changes code never judges the change"


def test_app_is_the_only_importer_of_packs() -> None:
    assert (PACKAGE_ROOT / "packs").is_dir(), "no packs/ to import, so the law is untested"
    importers: set[str] = set()
    for source in python_sources(PACKAGE_ROOT):
        relative = source.relative_to(PACKAGE_ROOT)
        if relative.parts[0] == "packs":
            continue
        for module in imported_modules(source):
            if module == "hubbleops.packs" or module.startswith("hubbleops.packs."):
                importers.add(relative.parts[0])
    assert importers <= {"app"}, f"only app/ may import packs/, found: {sorted(importers)}"


def test_layer_imports_form_an_acyclic_graph() -> None:
    cycles = import_cycles()
    assert cycles == [], f"layers must stay acyclic, found: {[' -> '.join(c) for c in cycles]}"


def test_cross_layer_imports_address_a_layer_surface() -> None:
    offences: list[str] = []
    for origin, module, source in internal_imports():
        target = module_layer(module)
        if target is None or target in VOCABULARY_LAYERS:
            continue
        if len(module.split(".")) <= 2:
            continue
        edge = f"{origin} -> {module}"
        if edge in TOLERATED_DEEP_IMPORTS:
            continue
        offences.append(f"{source.relative_to(PACKAGE_ROOT.parent)} imports {module}")
    assert offences == [], (
        "cross-layer imports address the layer package, not its internals; "
        f"add a re-export to that layer's __init__.py instead: {offences}"
    )


def test_the_tolerated_deep_import_list_never_grows_stale() -> None:
    present = {
        f"{origin} -> {module}"
        for origin, module, _ in internal_imports()
        if module_layer(module) not in VOCABULARY_LAYERS and len(module.split(".")) > 2
    }
    stale = sorted(TOLERATED_DEEP_IMPORTS - present)
    assert stale == [], f"these deep imports are gone; delete them from the list: {stale}"


def test_generic_layers_never_name_a_pack_path() -> None:
    offences: list[str] = []
    for directory in generic_layer_dirs():
        for source in python_sources(directory):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if "packs" in node.value:
                        offences.append(
                            f"{source.relative_to(PACKAGE_ROOT.parent)}:{node.lineno} "
                            f"names {node.value!r}"
                        )
    assert offences == [], "law L5: generic layers carry no pack path, imported or written"
