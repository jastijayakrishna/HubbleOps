from __future__ import annotations

from pathlib import Path

from tests.support import PACKAGE_ROOT, generic_layer_dirs

NAMES_FILE = Path(__file__).parent / "provider_names.txt"
TEXT_SUFFIXES = frozenset(
    {
        ".cjs",
        ".ini",
        ".js",
        ".json",
        ".md",
        ".mjs",
        ".php",
        ".py",
        ".sql",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)


def provider_names() -> list[str]:
    return [
        line.strip()
        for line in NAMES_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def scanned_files() -> list[Path]:
    files: list[Path] = []
    for directory in generic_layer_dirs():
        files.extend(
            sorted(
                path
                for path in directory.rglob("*")
                if path.is_file()
                and path.suffix in TEXT_SUFFIXES
                and "__pycache__" not in path.parts
            )
        )
    return files


def provider_offences(paths: list[Path], root: Path) -> list[str]:
    names = [name.lower() for name in provider_names()]
    offences: list[str] = []
    for path in paths:
        content = path.read_text(encoding="utf-8", errors="replace").lower()
        for number, line in enumerate(content.splitlines(), start=1):
            for name in names:
                if name in line:
                    offences.append(f"{path.relative_to(root)}:{number} contains {name!r}")
    return offences


def test_provider_name_list_is_populated() -> None:
    assert provider_names(), "the leak test proves nothing without names to look for"


def test_the_scan_reads_the_generic_layer_sources() -> None:
    scanned = scanned_files()
    assert scanned, "the leak test proves nothing with no files to read"
    assert all(path.suffix in TEXT_SUFFIXES for path in scanned)
    assert not any("__pycache__" in path.parts for path in scanned), (
        "compiled bytecode is not a source of truth; a stale .pyc would decide this law"
    )


def test_generic_layers_contain_no_provider_name() -> None:
    offences = provider_offences(scanned_files(), PACKAGE_ROOT.parent)
    assert offences == [], (
        "law L5: generic layers carry no provider name, hostname, package name or pack path"
    )


CATALOG_SCOPE_CLAIMS = ("selectability", "filterability", "resource pairing", "mutate shape")


def catalog_scope_offences(paths: list[Path], root: Path) -> list[str]:
    offences: list[str] = []
    for path in paths:
        content = path.read_text(encoding="utf-8", errors="replace").lower()
        for number, line in enumerate(content.splitlines(), start=1):
            for claim in CATALOG_SCOPE_CLAIMS:
                if claim in line:
                    offences.append(f"{path.relative_to(root)}:{number} claims {claim!r}")
    return offences


def test_no_generic_surface_states_what_a_providers_catalog_authority_covers() -> None:
    offences = catalog_scope_offences(scanned_files(), PACKAGE_ROOT.parent)
    assert offences == [], (
        "the scope of a CATALOG acceptance is the accepting pack's to state and the Receipt's to "
        "quote; a generic layer that hardcodes it becomes false the moment a pack learns a new "
        f"check: {offences}"
    )


def test_the_catalog_scope_scan_detects_an_injected_claim(tmp_path: Path) -> None:
    root = tmp_path / "hubbleops"
    proof = root / "proof"
    proof.mkdir(parents=True)
    claim = proof / "claim.py"
    claim.write_text("SCOPE = 'proves nothing about any mutate shape'\n", encoding="utf-8")
    assert catalog_scope_offences([claim], tmp_path)


def test_an_injected_observe_to_pack_import_is_detected(tmp_path: Path) -> None:
    root = tmp_path / "hubbleops"
    observe = root / "observe"
    observe.mkdir(parents=True)
    leak = observe / "leak.py"
    leak.write_text("from hubbleops.packs.google_ads import PACK\n", encoding="utf-8")
    offences = provider_offences([leak], tmp_path)
    assert any("google" in offence for offence in offences)
