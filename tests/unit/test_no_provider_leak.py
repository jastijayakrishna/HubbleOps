from __future__ import annotations

from pathlib import Path

from tests.support import PACKAGE_ROOT, generic_layer_dirs

NAMES_FILE = Path(__file__).parent / "provider_names.txt"
TEXT_SUFFIXES = frozenset({".py", ".json", ".yaml", ".yml", ".toml", ".txt", ".sql", ".md"})


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
    names = [name.lower() for name in provider_names()]
    offences: list[str] = []
    for path in scanned_files():
        content = path.read_text(encoding="utf-8", errors="replace").lower()
        for number, line in enumerate(content.splitlines(), start=1):
            for name in names:
                if name in line:
                    offences.append(
                        f"{path.relative_to(PACKAGE_ROOT.parent)}:{number} contains {name!r}"
                    )
    assert offences == [], (
        "law L5: generic layers carry no provider name, hostname, package name or pack path"
    )
