from __future__ import annotations

from pathlib import Path

from tests.support import PACKAGE_ROOT, generic_layer_dirs

NAMES_FILE = Path(__file__).parent / "provider_names.txt"


def provider_names() -> list[str]:
    return [
        line.strip()
        for line in NAMES_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def scanned_files() -> list[Path]:
    files: list[Path] = []
    for directory in generic_layer_dirs():
        files.extend(sorted(path for path in directory.rglob("*") if path.is_file()))
    return files


def test_provider_name_list_is_populated() -> None:
    assert provider_names(), "the leak test proves nothing without names to look for"


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
