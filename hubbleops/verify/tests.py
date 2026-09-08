from __future__ import annotations

import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from hubbleops.core.errors import ToolingMissing
from hubbleops.core.process import bounded_process
from hubbleops.core.verification import TestRun
from hubbleops.verify import coverage

TEST_DIRECTORY_NAMES = ("tests", "test", "spec", "__tests__")
TEST_FILE = re.compile(r"^(test_.*|.*_test|.*\.test|.*\.spec)\.py$")
SUMMARY = re.compile(r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed)")
DEFAULT_WALL_SECONDS = 900.0
DEFAULT_OUTPUT_BYTES = 8_388_608


@dataclass(frozen=True, slots=True)
class FrozenTestPlan:
    workspace: Path
    report: Path
    plugin: Path
    test_paths: tuple[str, ...]


def test_directories(tree: Path) -> tuple[str, ...]:
    found: list[str] = []
    for child in sorted(tree.iterdir()):
        if child.is_dir() and child.name in TEST_DIRECTORY_NAMES:
            found.append(child.name)
    if found:
        return tuple(found)
    return tuple(
        sorted(
            path.relative_to(tree).as_posix()
            for path in tree.rglob("*.py")
            if TEST_FILE.match(path.name) and ".git" not in path.parts
        )
    )


def stage_frozen(base: Path, candidate: Path, workspace: Path) -> FrozenTestPlan:
    if workspace.exists():
        shutil.rmtree(workspace)
    shutil.copytree(candidate, workspace, ignore=shutil.ignore_patterns(".git"))
    frozen = test_directories(base)
    for name in frozen:
        source = base / name
        target = workspace / name
        if target.exists():
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        if source.is_dir():
            shutil.copytree(source, target)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    for name in set(test_directories(candidate)) - set(frozen):
        stale = workspace / name
        if stale.is_dir():
            shutil.rmtree(stale)
        elif stale.is_file():
            stale.unlink()
    plugin = coverage.install(workspace)
    return FrozenTestPlan(
        workspace=workspace,
        report=workspace / coverage.REPORT_FILENAME,
        plugin=plugin,
        test_paths=frozen,
    )


def run_frozen(
    plan: FrozenTestPlan,
    python_executable: str,
    wall_seconds: float = DEFAULT_WALL_SECONDS,
) -> TestRun:
    if not plan.test_paths:
        return TestRun(
            source="FROZEN_BASELINE",
            executed=False,
            passed=0,
            failed=0,
            skipped=0,
            outcome="NO_FROZEN_TESTS",
            reason="the base SHA carries no test directory, so no test can be proof",
        )
    return _execute(plan, python_executable, wall_seconds, "FROZEN_BASELINE")


def run_candidate(
    tree: Path,
    workspace: Path,
    python_executable: str,
    wall_seconds: float = DEFAULT_WALL_SECONDS,
) -> TestRun:
    if workspace.exists():
        shutil.rmtree(workspace)
    shutil.copytree(tree, workspace, ignore=shutil.ignore_patterns(".git"))
    paths = test_directories(tree)
    if not paths:
        return TestRun(
            source="CANDIDATE",
            executed=False,
            passed=0,
            failed=0,
            skipped=0,
            outcome="NO_CANDIDATE_TESTS",
            reason="the candidate SHA carries no test directory",
        )
    plugin = coverage.install(workspace)
    plan = FrozenTestPlan(
        workspace=workspace,
        report=workspace / coverage.REPORT_FILENAME,
        plugin=plugin,
        test_paths=paths,
    )
    return _execute(plan, python_executable, wall_seconds, "CANDIDATE")


def _execute(
    plan: FrozenTestPlan,
    python_executable: str,
    wall_seconds: float,
    source: Literal["FROZEN_BASELINE", "CANDIDATE"],
) -> TestRun:
    environment = coverage.environment(plan.workspace, plan.report)
    try:
        outcome, exit_code, stdout, stderr = _pytest(
            plan, python_executable, wall_seconds, environment
        )
    except ToolingMissing as error:
        return TestRun(
            source=source,
            executed=False,
            passed=0,
            failed=0,
            skipped=0,
            outcome="TOOLING_MISSING",
            reason=str(error),
        )
    tests = coverage.read_report(plan.report)
    counts = _counts(stdout.decode("utf-8", errors="replace"))
    if outcome in ("WALL_TIMEOUT", "OUTPUT_LIMIT"):
        return TestRun(
            source=source,
            executed=True,
            passed=counts["passed"],
            failed=counts["failed"],
            skipped=counts["skipped"],
            outcome=outcome,
            reason=f"the {source.lower()} test run ended {outcome} and proves nothing",
            tests=tests,
        )
    detail = stderr.decode("utf-8", errors="replace").strip()
    if exit_code not in (0, 1) and not tests:
        return TestRun(
            source=source,
            executed=False,
            passed=0,
            failed=0,
            skipped=0,
            outcome="COLLECTION_FAILED",
            reason=detail or f"pytest exited {exit_code} before running a test",
            tests=(),
        )
    return TestRun(
        source=source,
        executed=True,
        passed=counts["passed"],
        failed=counts["failed"],
        skipped=counts["skipped"],
        outcome="COMPLETED",
        reason=detail[:512],
        tests=tests,
    )


def _pytest(
    plan: FrozenTestPlan,
    python_executable: str,
    wall_seconds: float,
    environment: dict[str, str],
) -> tuple[str, int | None, bytes, bytes]:
    return bounded_process(
        _argv(plan, python_executable),
        wall_seconds,
        DEFAULT_OUTPUT_BYTES,
        "pytest",
        {**environment, "PYTHONPATH": str(plan.workspace)},
    )


def _argv(plan: FrozenTestPlan, python_executable: str) -> tuple[str, ...]:
    return (
        python_executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "--rootdir",
        str(plan.workspace),
        "-p",
        coverage.PLUGIN_FILENAME.removesuffix(".py"),
        *(str(plan.workspace / path) for path in plan.test_paths),
    )


def _counts(output: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    for number, label in SUMMARY.findall(output):
        if label == "passed" or label == "xpassed":
            counts["passed"] += int(number)
        elif label in ("failed", "error", "errors"):
            counts["failed"] += int(number)
        elif label in ("skipped", "xfailed"):
            counts["skipped"] += int(number)
    return counts


def languages_of(paths: Sequence[str]) -> dict[str, str]:
    suffixes = {
        ".py": "python",
        ".js": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".php": "php",
        ".rb": "ruby",
        ".go": "go",
        ".java": "java",
        ".cs": "csharp",
    }
    return {path: suffixes.get(f".{path.rsplit('.', 1)[-1]}", "unknown") for path in paths}


__all__ = [
    "FrozenTestPlan",
    "languages_of",
    "run_candidate",
    "run_frozen",
    "stage_frozen",
    "test_directories",
]
