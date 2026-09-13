from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from hubbleops.core.errors import ToolingMissing
from hubbleops.core.process import bounded_process
from hubbleops.core.verification import SuiteCase, SuiteRun
from hubbleops.graph import language_for
from hubbleops.verify import coverage, runners

DEFAULT_WALL_SECONDS = 900.0
DEFAULT_OUTPUT_BYTES = 8_388_608
SOURCE_LANGUAGES = frozenset(
    {"csharp", "go", "java", "javascript", "php", "python", "ruby", "rust", "tsx", "typescript"}
)
SEVERITY = ("COMPLETED", "EXECUTION_FAILED", "OUTPUT_LIMIT", "WALL_TIMEOUT", "TOOLING_MISSING")


@dataclass(frozen=True, slots=True)
class SuitePart:
    layout: runners.SuiteLayout
    report: Path
    plugin: Path | None
    missing: str | None = None


@dataclass(frozen=True, slots=True)
class FrozenSuitePlan:
    workspace: Path
    report: Path
    plugin: Path | None
    paths: tuple[str, ...]
    layout: runners.SuiteLayout | None = None
    missing: str | None = None
    parts: tuple[SuitePart, ...] = ()

    def runner_plan(self) -> runners.RunnerPlan | None:
        if self.layout is None:
            return None
        return runners.RunnerPlan(
            layout=self.layout,
            workspace=self.workspace,
            report=self.report,
            plugin=self.plugin,
        )

    def suite_parts(self) -> tuple[SuitePart, ...]:
        if self.parts:
            return self.parts
        if self.layout is None:
            return ()
        return (
            SuitePart(
                layout=self.layout, report=self.report, plugin=self.plugin, missing=self.missing
            ),
        )


def suite_paths(tree: Path) -> tuple[str, ...]:
    return tuple(sorted({path for layout in runners.layouts_of(tree) for path in layout.paths}))


def covered_languages(run: SuiteRun) -> frozenset[str]:
    return frozenset(run.languages) if run.languages else frozenset({"python"})


def stage_frozen(
    base: Path, candidate: Path, workspace: Path, dependencies: Path | None = None
) -> FrozenSuitePlan:
    if workspace.exists():
        shutil.rmtree(workspace, onexc=_drop_readonly)
    shutil.copytree(candidate, workspace, ignore=shutil.ignore_patterns(".git"))
    layouts = runners.layouts_of(base)
    frozen = tuple(sorted({path for layout in layouts for path in layout.paths}))
    for name in frozen:
        source = base / name
        target = workspace / name
        if target.exists():
            shutil.rmtree(target, onexc=_drop_readonly) if target.is_dir() else target.unlink()
        if source.is_dir():
            shutil.copytree(source, target)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    for name in set(suite_paths(candidate)) - set(frozen):
        stale = workspace / name
        if stale.is_dir():
            shutil.rmtree(stale, onexc=_drop_readonly)
        elif stale.is_file():
            stale.unlink()
    if not layouts:
        return FrozenSuitePlan(workspace=workspace, report=workspace, plugin=None, paths=())
    if dependencies is not None:
        for layout in layouts:
            runners.link_dependencies(dependencies, workspace, layout)
    parts = _parts(workspace, layouts)
    return FrozenSuitePlan(
        workspace=workspace,
        report=parts[0].report,
        plugin=parts[0].plugin,
        paths=frozen,
        layout=parts[0].layout,
        missing=parts[0].missing,
        parts=parts,
    )


def run_frozen(
    plan: FrozenSuitePlan,
    python_executable: str,
    wall_seconds: float = DEFAULT_WALL_SECONDS,
) -> SuiteRun:
    return _run(plan, python_executable, wall_seconds, "FROZEN_BASELINE")


def run_candidate(
    tree: Path,
    workspace: Path,
    python_executable: str,
    wall_seconds: float = DEFAULT_WALL_SECONDS,
) -> SuiteRun:
    if workspace.exists():
        shutil.rmtree(workspace, onexc=_drop_readonly)
    shutil.copytree(tree, workspace, ignore=shutil.ignore_patterns(".git"))
    layouts = runners.layouts_of(tree)
    if not layouts:
        return _no_suite("CANDIDATE", None)
    parts = _parts(workspace, layouts)
    plan = FrozenSuitePlan(
        workspace=workspace,
        report=parts[0].report,
        plugin=parts[0].plugin,
        paths=tuple(sorted({path for layout in layouts for path in layout.paths})),
        layout=parts[0].layout,
        missing=parts[0].missing,
        parts=parts,
    )
    return _run(plan, python_executable, wall_seconds, "CANDIDATE")


def _parts(workspace: Path, layouts: Sequence[runners.SuiteLayout]) -> tuple[SuitePart, ...]:
    return tuple(
        SuitePart(
            layout=layout,
            report=runners.report_path(workspace, layout),
            plugin=coverage.install(workspace) if layout.runner == "pytest" else None,
            missing=runners.missing_runner(workspace, layout),
        )
        for layout in layouts
    )


def _run(
    plan: FrozenSuitePlan,
    python_executable: str,
    wall_seconds: float,
    source: Literal["FROZEN_BASELINE", "CANDIDATE"],
) -> SuiteRun:
    parts = plan.suite_parts()
    if not plan.paths or not parts:
        return _no_suite(source, plan.layout)
    results = [
        _run_part(plan.workspace, part, python_executable, wall_seconds, source) for part in parts
    ]
    if len(results) == 1:
        return results[0]
    return _merge(results, source)


def _run_part(
    workspace: Path,
    part: SuitePart,
    python_executable: str,
    wall_seconds: float,
    source: Literal["FROZEN_BASELINE", "CANDIDATE"],
) -> SuiteRun:
    if part.missing is not None:
        return SuiteRun(
            source=source,
            executed=False,
            passed=0,
            failed=0,
            skipped=0,
            outcome="TOOLING_MISSING",
            reason=part.missing,
            languages=part.layout.languages,
            runner=part.layout.runner,
        )
    return _execute(workspace, part, python_executable, wall_seconds, source)


def _merge(
    results: Sequence[SuiteRun], source: Literal["FROZEN_BASELINE", "CANDIDATE"]
) -> SuiteRun:
    worst = max(results, key=lambda run: _severity(run.outcome))
    reasons = [f"{run.runner}: {run.reason}" for run in results if run.reason]
    return SuiteRun(
        source=source,
        executed=all(run.executed for run in results),
        passed=sum(run.passed for run in results),
        failed=sum(run.failed for run in results),
        skipped=sum(run.skipped for run in results),
        outcome=worst.outcome,
        reason="; ".join(reasons)[:512],
        tests=tuple(_named(run.runner, case) for run in results for case in run.tests),
        languages=tuple(sorted({language for run in results for language in run.languages})),
        runner="+".join(run.runner for run in results),
    )


def _severity(outcome: str) -> int:
    return SEVERITY.index(outcome) if outcome in SEVERITY else len(SEVERITY)


def _named(runner: str, case: SuiteCase) -> SuiteCase:
    prefix = f"{runner}:"
    if case.name.startswith(prefix):
        return case
    return SuiteCase(name=f"{prefix}{case.name}", outcome=case.outcome, files=case.files)


def _no_suite(
    source: Literal["FROZEN_BASELINE", "CANDIDATE"], layout: runners.SuiteLayout | None
) -> SuiteRun:
    reason = (
        "the base SHA carries no test directory, so no test can be proof"
        if source == "FROZEN_BASELINE"
        else "the candidate SHA carries no test directory"
    )
    return SuiteRun(
        source=source,
        executed=False,
        passed=0,
        failed=0,
        skipped=0,
        outcome="NO_FROZEN_TESTS" if source == "FROZEN_BASELINE" else "NO_CANDIDATE_TESTS",
        reason=reason,
        languages=layout.languages if layout is not None else (),
        runner=layout.runner if layout is not None else "",
    )


def _execute(
    workspace: Path,
    part: SuitePart,
    python_executable: str,
    wall_seconds: float,
    source: Literal["FROZEN_BASELINE", "CANDIDATE"],
) -> SuiteRun:
    layout = part.layout
    runner_plan = runners.RunnerPlan(
        layout=layout, workspace=workspace, report=part.report, plugin=part.plugin
    )
    environment = runners.environment(runner_plan)
    runner_plan.report.parent.mkdir(parents=True, exist_ok=True)
    try:
        outcome, exit_code, stdout, stderr = bounded_process(
            runners.command(workspace, runner_plan, python_executable),
            wall_seconds,
            DEFAULT_OUTPUT_BYTES,
            layout.runner,
            {**environment, "PYTHONPATH": str(workspace)},
            str(runners.working_directory(workspace, layout)),
        )
    except ToolingMissing as error:
        return SuiteRun(
            source=source,
            executed=False,
            passed=0,
            failed=0,
            skipped=0,
            outcome="TOOLING_MISSING",
            reason=str(error),
            languages=layout.languages,
            runner=layout.runner,
        )
    printed = stdout.decode("utf-8", errors="replace")
    failed_output = stderr.decode("utf-8", errors="replace")
    text = printed + failed_output
    reported = runners.run_counts(runner_plan, text)
    counts = reported or {"passed": 0, "failed": 0, "skipped": 0}
    tests = runners.read_cases(runner_plan, source, counts)
    detail = failed_output.strip()
    failure = runners.execution_failure(layout, exit_code, printed, failed_output)
    if outcome in ("WALL_TIMEOUT", "OUTPUT_LIMIT"):
        return SuiteRun(
            source=source,
            executed=True,
            passed=counts["passed"],
            failed=counts["failed"],
            skipped=counts["skipped"],
            outcome=outcome,
            reason=f"the {source.lower()} test run ended {outcome} and proves nothing",
            tests=tests,
            languages=layout.languages,
            runner=layout.runner,
        )
    if reported is None:
        return SuiteRun(
            source=source,
            executed=False,
            passed=0,
            failed=0,
            skipped=0,
            outcome="EXECUTION_FAILED",
            reason=(
                f"{layout.runner} left no readable report at {runner_plan.report.name}, "
                f"so the run counts nothing: {failure}"
            )[:512],
            languages=layout.languages,
            runner=layout.runner,
        )
    if exit_code not in (0, 1):
        return SuiteRun(
            source=source,
            executed=bool(tests),
            passed=counts["passed"],
            failed=counts["failed"],
            skipped=counts["skipped"],
            outcome="EXECUTION_FAILED",
            reason=failure,
            tests=tests,
            languages=layout.languages,
            runner=layout.runner,
        )
    if exit_code == 1 and counts["failed"] == 0:
        return SuiteRun(
            source=source,
            executed=bool(tests),
            passed=counts["passed"],
            failed=0,
            skipped=counts["skipped"],
            outcome="EXECUTION_FAILED",
            reason=f"{failure}; no failed test was reported"[:512],
            tests=tests,
            languages=layout.languages,
            runner=layout.runner,
        )
    gap = runners.coverage_gap(workspace, layout)
    return SuiteRun(
        source=source,
        executed=True,
        passed=counts["passed"],
        failed=counts["failed"],
        skipped=counts["skipped"],
        outcome="COMPLETED",
        reason=(gap or detail)[:512],
        tests=tests,
        languages=() if gap is not None else layout.languages,
        runner=layout.runner,
    )


def _drop_readonly(function: Callable[[str], object], path: str, error: BaseException) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        function(path)
    except OSError:
        raise error from None


def languages_of(paths: Sequence[str]) -> dict[str, str]:
    detected = {path: language_for(path) for path in paths}
    return {
        path: language if language in SOURCE_LANGUAGES else "unknown"
        for path, language in detected.items()
    }


__all__ = [
    "FrozenSuitePlan",
    "covered_languages",
    "languages_of",
    "run_candidate",
    "run_frozen",
    "stage_frozen",
    "suite_paths",
]
