from __future__ import annotations

from pathlib import Path

import pytest

from hubbleops.app import registry
from hubbleops.app.cli import EXIT_TOOLING_MISSING, main, scan_repository
from hubbleops.core.errors import ToolingMissing
from hubbleops.observe import text
from tests.support import fixture_repos


def hide_ripgrep(monkeypatch: pytest.MonkeyPatch, empty_dir: Path) -> None:
    monkeypatch.setenv("PATH", str(empty_dir))
    monkeypatch.setenv("PATHEXT", ".NOPE")


def test_a_missing_scanner_raises_rather_than_returning_no_findings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    hide_ripgrep(monkeypatch, tmp_path)
    with pytest.raises(ToolingMissing) as raised:
        scan_repository(fixture_repos()[0] / "repo", registry.load_pack("google_ads"))
    assert "TOOLING_MISSING" in str(raised.value)


def test_the_cli_reports_tooling_missing_and_writes_no_ledger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    empty = tmp_path / "empty"
    empty.mkdir()
    hide_ripgrep(monkeypatch, empty)
    code = main(
        [
            "scan",
            str(fixture_repos()[0] / "repo"),
            "--pack",
            "google_ads",
            "--state-dir",
            str(state),
        ]
    )
    assert code == EXIT_TOOLING_MISSING
    assert "TOOLING_MISSING" in capsys.readouterr().err
    assert not (state / "artifacts").exists()
    assert not (state / "hubbleops.sqlite").exists()


def test_the_scanner_version_is_bound_into_the_proof_scope() -> None:
    result = scan_repository(fixture_repos()[0] / "repo", registry.load_pack("google_ads"))
    assert text.RIPGREP in result.proof_scope["scanner_version"]
    assert "hubbleops=" in result.proof_scope["scanner_version"]
