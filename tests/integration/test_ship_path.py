from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hubbleops.core import toolchain

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "phase1" / "monorepo_workspace" / "repo"
CACHE = REPO / ".hubbleops" / "toolchain-cache"
BUDGET_SECONDS = 300.0
STEP_TIMEOUT = 600


def _run(
    argv: list[str], cwd: Path, env: dict[str, str]
) -> tuple[float, subprocess.CompletedProcess[str]]:
    started = time.monotonic()
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=STEP_TIMEOUT,
        check=False,
    )
    return time.monotonic() - started, completed


def _path_without(tools: tuple[str, ...], original: str) -> str:
    kept: list[str] = []
    for entry in original.split(os.pathsep):
        if not entry:
            continue
        if any(shutil.which(tool, path=entry) is not None for tool in tools):
            continue
        kept.append(entry)
    return os.pathsep.join(kept)


def test_cold_clone_to_exposure_map_needs_only_uv_and_finishes_inside_the_budget(
    tmp_path: Path,
) -> None:
    tag = toolchain.platform_tag()
    if tag is None:
        pytest.skip("no vendored toolchain wheel is defined for this platform")
    uv = shutil.which("uv")
    assert uv is not None
    build_env = {
        **os.environ,
        "HUBBLEOPS_WHEEL_PLATFORM": tag,
        "HUBBLEOPS_TOOLCHAIN_CACHE": str(CACHE),
    }
    stripped = _path_without(("rg", "ast-grep", "scip-typescript"), os.environ.get("PATH", ""))
    assert shutil.which("rg", path=stripped) is None
    assert shutil.which("ast-grep", path=stripped) is None
    tools = tmp_path / "tools"
    bin_dir = tmp_path / "bin"
    run_env = {
        **os.environ,
        "PATH": stripped,
        "UV_TOOL_DIR": str(tools),
        "UV_TOOL_BIN_DIR": str(bin_dir),
        "UV_LINK_MODE": "copy",
        "HUBBLEOPS_TOOLCHAIN_CACHE": str(CACHE),
    }
    elapsed = 0.0

    seconds, built = _run(
        [uv, "build", "--wheel", "--out-dir", str(tmp_path / "dist")], REPO, build_env
    )
    elapsed += seconds
    assert built.returncode == 0, built.stderr
    wheels = sorted((tmp_path / "dist").glob(f"hubbleops-*-{tag}.whl"))
    assert len(wheels) == 1, built.stderr

    seconds, installed = _run([uv, "tool", "install", "--force", str(wheels[0])], tmp_path, run_env)
    elapsed += seconds
    assert installed.returncode == 0, installed.stderr
    hops = shutil.which("hops", path=str(bin_dir))
    assert hops is not None, sorted(path.name for path in bin_dir.iterdir())

    state = tmp_path / "state"
    seconds, scanned = _run(
        [
            hops,
            "scan",
            str(FIXTURE),
            "--pack",
            "google_ads",
            "--target",
            "v25",
            "--state-dir",
            str(state),
        ],
        tmp_path,
        run_env,
    )
    elapsed += seconds
    assert scanned.returncode == 0, scanned.stderr
    scanner_line = next(line for line in scanned.stdout.splitlines() if "Scanner" in line)
    assert scanner_line.count("+sha256:") == 2, scanner_line
    assert "rg=ripgrep 14.1.1" in scanner_line
    assert "ast-grep=0.45.0" in scanner_line
    assert "QUERIES" in scanned.stdout

    seconds, shown = _run(
        [hops, "exposure", "--target", "v25", "--state-dir", str(state)], tmp_path, run_env
    )
    elapsed += seconds
    assert shown.returncode == 0, shown.stderr
    assert "MIGRATION FINDINGS  → v25" in shown.stdout
    assert "QUERIES  → v25" in shown.stdout
    assert elapsed < BUDGET_SECONDS, f"{elapsed:.1f}s from wheel build to exposure map"
    sys.stdout.write(f"ship path {elapsed:.1f}s on {tag}\n")
