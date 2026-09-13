from __future__ import annotations

import json
import shutil
from pathlib import Path

from hubbleops.verify import runners, suites

FIXTURE = Path("tests/fixtures/phase7/nested_vitest_suite")


def _tree(tmp_path: Path) -> Path:
    destination = tmp_path / "repo"
    shutil.copytree(FIXTURE, destination)
    return destination


def test_a_vitest_suite_below_the_root_is_found(tmp_path: Path) -> None:
    layout = runners.layout_of(_tree(tmp_path))
    assert layout is not None
    assert layout.runner == "vitest"
    assert layout.project == "apps/web"
    assert layout.paths == ("apps/web/tests",)
    assert layout.languages == ("javascript", "typescript", "tsx")


def test_a_python_suite_wins_when_the_repository_has_both(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    (tree / "tests").mkdir()
    (tree / "tests" / "test_reporting.py").write_text("def test_ok():\n    assert True\n", "utf-8")
    layout = runners.layout_of(tree)
    assert layout is not None
    assert layout.runner == "pytest"


def test_a_runner_the_repository_never_installed_is_named_not_assumed(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    layout = runners.layout_of(tree)
    assert layout is not None
    missing = runners.missing_runner(tree, layout)
    assert missing is not None
    assert "vitest" in missing
    assert "installs\nnone" in missing or "installs none" in missing.replace("\n", " ")


def test_an_installed_runner_is_accepted(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    binary = tree / "apps" / "web" / "node_modules" / ".bin"
    binary.mkdir(parents=True)
    (binary / "vitest").write_text("#!/bin/sh\n", encoding="utf-8")
    layout = runners.layout_of(tree)
    assert layout is not None
    assert runners.missing_runner(tree, layout) is None


def test_the_command_runs_the_repositorys_own_binary_with_coverage(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    (tree / "apps" / "web" / "node_modules" / "@vitest" / "coverage-v8").mkdir(parents=True)
    layout = runners.layout_of(tree)
    assert layout is not None
    plan = runners.RunnerPlan(
        layout=layout,
        workspace=tree,
        report=runners.report_path(tree, layout),
        plugin=None,
    )
    command = runners.command(tree, plan, "python")
    assert "node_modules" in command[0]
    assert "--coverage.enabled" in command
    assert "--coverage.reporter=json" in command
    assert command[-1] == "tests"
    assert runners.working_directory(tree, layout) == tree / "apps" / "web"


def test_a_missing_runner_stays_no_frozen_tests_and_names_the_runner(tmp_path: Path) -> None:
    base = _tree(tmp_path)
    candidate = tmp_path / "candidate"
    shutil.copytree(base, candidate)
    plan = suites.stage_frozen(base, candidate, tmp_path / "workspace")
    run = suites.run_frozen(plan, "python", 5)
    assert run.outcome == "TOOLING_MISSING"
    assert run.executed is False
    assert "vitest" in run.reason
    assert run.all_passed() is False


def test_coverage_reads_one_aggregate_case_per_suite(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    layout = runners.layout_of(tree)
    assert layout is not None
    project = tree / "apps" / "web"
    report = project / "coverage" / "coverage-final.json"
    report.parent.mkdir(parents=True)
    report.write_text(
        json.dumps(
            {
                str(project / "src" / "api.ts"): {
                    "path": str(project / "src" / "api.ts"),
                    "s": {"0": 3, "1": 1},
                },
                str(project / "src" / "constants.ts"): {
                    "path": str(project / "src" / "constants.ts"),
                    "s": {"0": 2},
                },
                str(project / "src" / "unused.ts"): {
                    "path": str(project / "src" / "unused.ts"),
                    "s": {"0": 0},
                },
            }
        ),
        encoding="utf-8",
    )
    plan = runners.RunnerPlan(layout=layout, workspace=tree, report=report, plugin=None)
    cases = runners.read_cases(plan, "FROZEN_BASELINE", {"passed": 2, "failed": 0, "skipped": 0})
    assert len(cases) == 1
    assert cases[0].name == "vitest:apps/web"
    assert cases[0].outcome == "passed"
    assert cases[0].files == ("apps/web/src/api.ts", "apps/web/src/constants.ts")


def test_a_failing_suite_covers_nothing_that_counts_as_proof(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    layout = runners.layout_of(tree)
    assert layout is not None
    project = tree / "apps" / "web"
    report = project / "coverage" / "coverage-final.json"
    report.parent.mkdir(parents=True)
    report.write_text(
        json.dumps(
            {
                str(project / "src" / "api.ts"): {
                    "path": str(project / "src" / "api.ts"),
                    "s": {"0": 1},
                }
            }
        ),
        encoding="utf-8",
    )
    plan = runners.RunnerPlan(layout=layout, workspace=tree, report=report, plugin=None)
    cases = runners.read_cases(plan, "FROZEN_BASELINE", {"passed": 1, "failed": 1, "skipped": 0})
    assert cases[0].outcome == "failed"
    run_files = frozenset(path for case in cases if case.outcome == "passed" for path in case.files)
    assert run_files == frozenset()


def test_a_suite_without_a_coverage_provider_covers_nothing_and_says_so(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    layout = runners.layout_of(tree)
    assert layout is not None
    assert runners.coverage_provider(tree, layout) is None
    gap = runners.coverage_gap(tree, layout)
    assert gap is not None
    assert "UNKNOWN_BLAST" in gap
    plan = runners.RunnerPlan(
        layout=layout, workspace=tree, report=runners.report_path(tree, layout), plugin=None
    )
    assert "--coverage.enabled" not in runners.command(tree, plan, "python")


def test_an_installed_provider_turns_coverage_on(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    (tree / "apps" / "web" / "node_modules" / "@vitest" / "coverage-v8").mkdir(parents=True)
    layout = runners.layout_of(tree)
    assert layout is not None
    assert runners.coverage_provider(tree, layout) == "v8"
    assert runners.coverage_gap(tree, layout) is None
    plan = runners.RunnerPlan(
        layout=layout, workspace=tree, report=runners.report_path(tree, layout), plugin=None
    )
    assert "--coverage.provider=v8" in runners.command(tree, plan, "python")


def test_vitest_output_is_counted(tmp_path: Path) -> None:
    layout = runners.SuiteLayout(
        runner="vitest", languages=("javascript",), paths=("tests",), project="."
    )
    counts = runners.counts_of(layout, " Test Files  1 passed (1)\n      Tests  2 passed (2)\n")
    assert counts == {"passed": 2, "failed": 0, "skipped": 0}
    failing = runners.counts_of(layout, "      Tests  1 failed | 3 passed (4)\n")
    assert failing == {"passed": 3, "failed": 1, "skipped": 0}
