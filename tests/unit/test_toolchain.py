from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from hubbleops.core import toolchain
from hubbleops.core.errors import ToolingMissing


@pytest.mark.parametrize(
    ("host", "machine", "expected"),
    [
        ("win-amd64", "AMD64", "win_amd64"),
        ("linux-x86_64", "x86_64", "manylinux_2_35_x86_64"),
        ("macosx-14.0-arm64", "arm64", "macosx_11_0_arm64"),
        ("macosx-10.12-x86_64", "x86_64", "macosx_10_12_x86_64"),
        ("win-arm64", "ARM64", None),
        ("linux-aarch64", "aarch64", None),
        ("freebsd-14.0-amd64", "amd64", None),
    ],
)
def test_platform_tag_maps_host_and_machine(
    monkeypatch: pytest.MonkeyPatch, host: str, machine: str, expected: str | None
) -> None:
    monkeypatch.setattr(toolchain.sysconfig, "get_platform", lambda: host)
    monkeypatch.setattr(toolchain.platform, "machine", lambda: machine)
    assert toolchain.platform_tag() == expected


def _never_on_path(_: str) -> str | None:
    raise AssertionError("PATH lookup must not happen")


def _only_rg_at(location: Path) -> Callable[[str], str | None]:
    def which(query: str) -> str | None:
        return str(location) if query == "rg" else None

    return which


def _nothing_on_path(_: str) -> str | None:
    return None


def _vendor(root: Path, tag: str, name: str, payload: bytes, sha256: str | None = None) -> Path:
    directory = root / tag
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / name
    binary.write_bytes(payload)
    manifest = {
        "platform": tag,
        "tools": {name: {"file": name, "version": "1.0.0", "sha256": sha256 or _digest(payload)}},
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return binary


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def test_locate_prefers_vendored_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"vendored rg bytes"
    binary = _vendor(tmp_path, "win_amd64", "rg", payload)
    monkeypatch.setattr(toolchain, "vendored_root", lambda: tmp_path)
    monkeypatch.setattr(toolchain, "platform_tag", lambda: "win_amd64")
    monkeypatch.setattr(shutil, "which", _never_on_path)

    found = toolchain.locate("rg")

    assert found == toolchain.ToolBinary("rg", str(binary), "vendored", _digest(payload))


def test_vendored_hash_mismatch_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _vendor(tmp_path, "win_amd64", "rg", b"tampered", sha256="0" * 64)
    monkeypatch.setattr(toolchain, "vendored_root", lambda: tmp_path)
    monkeypatch.setattr(toolchain, "platform_tag", lambda: "win_amd64")
    monkeypatch.setattr(shutil, "which", _never_on_path)

    with pytest.raises(ToolingMissing, match="vendored binary hash mismatch") as raised:
        toolchain.locate("rg")
    assert raised.value.tool == "rg"


def test_vendored_binary_missing_from_disk_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = _vendor(tmp_path, "win_amd64", "rg", b"bytes")
    binary.unlink()
    monkeypatch.setattr(toolchain, "vendored_root", lambda: tmp_path)
    monkeypatch.setattr(toolchain, "platform_tag", lambda: "win_amd64")
    monkeypatch.setattr(shutil, "which", _never_on_path)

    with pytest.raises(ToolingMissing, match="vendored binary hash mismatch"):
        toolchain.locate("rg")


def test_vendored_binary_without_exec_bit_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = _vendor(tmp_path, "manylinux_2_35_x86_64", "rg", b"bytes")
    monkeypatch.setattr(toolchain, "vendored_root", lambda: tmp_path)
    monkeypatch.setattr(toolchain, "platform_tag", lambda: "manylinux_2_35_x86_64")
    monkeypatch.setattr(shutil, "which", _never_on_path)

    def not_executable(path: str | Path, mode: int) -> bool:
        return not (Path(path) == binary and mode == os.X_OK)

    monkeypatch.setattr(os, "access", not_executable)

    with pytest.raises(ToolingMissing, match="not executable"):
        toolchain.locate("rg")


def test_locate_falls_back_to_path_when_nothing_is_vendored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"path rg bytes"
    on_path = tmp_path / "bin" / "rg"
    on_path.parent.mkdir()
    on_path.write_bytes(payload)
    monkeypatch.setattr(toolchain, "vendored_root", lambda: tmp_path / "absent")
    monkeypatch.setattr(toolchain, "platform_tag", lambda: "win_amd64")
    monkeypatch.setattr(shutil, "which", _only_rg_at(on_path))

    found = toolchain.locate("rg")

    assert found == toolchain.ToolBinary("rg", str(on_path), "path", _digest(payload))


def test_manifest_without_the_tool_falls_back_to_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _vendor(tmp_path, "win_amd64", "ast-grep", b"other tool")
    on_path = tmp_path / "rg"
    on_path.write_bytes(b"rg")
    monkeypatch.setattr(toolchain, "vendored_root", lambda: tmp_path)
    monkeypatch.setattr(toolchain, "platform_tag", lambda: "win_amd64")
    monkeypatch.setattr(shutil, "which", _only_rg_at(on_path))

    assert toolchain.locate("rg").origin == "path"


def test_nothing_found_raises_tooling_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(toolchain, "vendored_root", lambda: tmp_path / "absent")
    monkeypatch.setattr(toolchain, "platform_tag", lambda: None)
    monkeypatch.setattr(shutil, "which", _nothing_on_path)

    with pytest.raises(ToolingMissing, match="TOOLING_MISSING: rg"):
        toolchain.locate("rg")


def test_explicit_path_is_used_as_given(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _vendor(tmp_path, "win_amd64", "rg", b"vendored", sha256="0" * 64)
    payload = b"explicit rg bytes"
    explicit = tmp_path / "custom" / "rg-custom"
    explicit.parent.mkdir()
    explicit.write_bytes(payload)
    monkeypatch.setattr(toolchain, "vendored_root", lambda: tmp_path)
    monkeypatch.setattr(toolchain, "platform_tag", lambda: "win_amd64")
    monkeypatch.setattr(shutil, "which", _never_on_path)

    found = toolchain.locate("rg", str(explicit))

    assert found == toolchain.ToolBinary("rg", str(explicit), "path", _digest(payload))


def test_explicit_path_that_does_not_exist_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", _never_on_path)

    with pytest.raises(ToolingMissing, match="binary unreadable"):
        toolchain.locate("rg", str(tmp_path / "missing" / "rg"))


def test_identity_binds_version_text_to_sha256() -> None:
    tool = toolchain.ToolBinary("rg", "/opt/rg", "path", "ab" * 32)

    assert toolchain.identity(tool, "14.1.1") == "14.1.1+sha256:" + "ab" * 32
