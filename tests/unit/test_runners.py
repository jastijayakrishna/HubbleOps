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


def test_a_pytest_summary_is_counted_once_not_once_per_line_that_mentions_it() -> None:
    transcript = (
        "ERROR tests/test_one.py\n"
        "!!!!!!!! Interrupted: 8 errors during collection !!!!!!!!\n"
        "======================== 8 errors in 1.89s ========================\n"
    )

    assert runners.counts_of(_pytest_layout(), transcript) == {
        "passed": 0,
        "failed": 8,
        "skipped": 0,
    }, (
        "pytest prints its error count twice, in the interrupt banner and in the footer; "
        "adding both up reports twice as many tests as the run ever had"
    )


def test_a_pytest_footer_is_read_and_the_lines_above_it_are_not() -> None:
    transcript = (
        "FAILED tests/test_two.py::test_b\n"
        "=============== 431 passed, 1 failed, 2 skipped in 12.3s ===============\n"
    )

    assert runners.counts_of(_pytest_layout(), transcript) == {
        "passed": 431,
        "failed": 1,
        "skipped": 2,
    }


def _python_tree(tmp_path: Path, *suite_dirs: str) -> Path:
    tree = tmp_path / "pyrepo"
    for relative in suite_dirs:
        directory = tree / relative
        directory.mkdir(parents=True)
        (directory / "test_one.py").write_text("def test_ok():\n    assert True\n", "utf-8")
    (tree / "pkg").mkdir(exist_ok=True)
    (tree / "pkg" / "__init__.py").write_text("", "utf-8")
    return tree


def _pytest_layout() -> runners.SuiteLayout:
    return runners.SuiteLayout(
        runner="pytest", languages=("python",), paths=("tests",), project="."
    )


def test_pyproject_testpaths_are_honoured_before_directory_names(tmp_path: Path) -> None:
    tree = _python_tree(tmp_path, "tests/unittests", "tests/integration")
    (tree / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests/unittests"]\n', "utf-8"
    )
    layout = runners.python_layout(tree)
    assert layout is not None
    assert layout.paths == ("tests/unittests",)


def test_pytest_ini_testpaths_are_honoured(tmp_path: Path) -> None:
    tree = _python_tree(tmp_path, "tests/unittests", "tests/integration")
    (tree / "pytest.ini").write_text("[pytest]\ntestpaths = tests/unittests\n", "utf-8")
    layout = runners.python_layout(tree)
    assert layout is not None
    assert layout.paths == ("tests/unittests",)


def test_setup_cfg_testpaths_are_honoured(tmp_path: Path) -> None:
    tree = _python_tree(tmp_path, "tests/unittests", "tests/integration")
    (tree / "setup.cfg").write_text(
        "[metadata]\nname = demo\n\n[tool:pytest]\ntestpaths =\n    tests/unittests\n", "utf-8"
    )
    layout = runners.python_layout(tree)
    assert layout is not None
    assert layout.paths == ("tests/unittests",)


def test_tox_ini_testpaths_are_honoured(tmp_path: Path) -> None:
    tree = _python_tree(tmp_path, "tests/unittests", "tests/integration")
    (tree / "tox.ini").write_text(
        "[tox]\nenvlist = py312\n\n[pytest]\ntestpaths = tests/unittests tests/integration\n",
        "utf-8",
    )
    layout = runners.python_layout(tree)
    assert layout is not None
    assert layout.paths == ("tests/integration", "tests/unittests")


def test_pytest_ini_wins_over_pyproject_as_pytest_itself_decides(tmp_path: Path) -> None:
    tree = _python_tree(tmp_path, "tests/unittests", "tests/integration")
    (tree / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests/integration"]\n', "utf-8"
    )
    (tree / "pytest.ini").write_text("[pytest]\ntestpaths = tests/unittests\n", "utf-8")
    layout = runners.python_layout(tree)
    assert layout is not None
    assert layout.paths == ("tests/unittests",)


def test_a_config_without_testpaths_falls_back_and_shadows_the_others(tmp_path: Path) -> None:
    tree = _python_tree(tmp_path, "tests/unittests", "tests/integration")
    (tree / "pytest.ini").write_text("[pytest]\naddopts = -q\n", "utf-8")
    (tree / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests/integration"]\n', "utf-8"
    )
    layout = runners.python_layout(tree)
    assert layout is not None
    assert layout.paths == ("tests",)


def test_a_declared_path_that_is_missing_fails_closed(tmp_path: Path) -> None:
    tree = _python_tree(tmp_path, "tests/unittests")
    (tree / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests/unittests", "tests/gone"]\n', "utf-8"
    )
    assert runners.python_layout(tree) is None


def test_a_declared_path_outside_the_tree_fails_closed(tmp_path: Path) -> None:
    tree = _python_tree(tmp_path, "tests/unittests")
    (tmp_path / "elsewhere").mkdir()
    (tree / "pytest.ini").write_text("[pytest]\ntestpaths = ../elsewhere\n", "utf-8")
    assert runners.python_layout(tree) is None


def test_a_declared_glob_expands_inside_the_tree(tmp_path: Path) -> None:
    tree = _python_tree(tmp_path, "tests/unit_a", "tests/unit_b", "tests/integration")
    (tree / "pytest.ini").write_text("[pytest]\ntestpaths = tests/unit_*\n", "utf-8")
    layout = runners.python_layout(tree)
    assert layout is not None
    assert layout.paths == ("tests/unit_a", "tests/unit_b")


def test_without_a_declaration_directory_names_are_the_fallback(tmp_path: Path) -> None:
    tree = _python_tree(tmp_path, "tests/unittests", "tests/integration")
    (tree / "pyproject.toml").write_text('[project]\nname = "demo"\n', "utf-8")
    (tree / "setup.cfg").write_text("[metadata]\nname = demo\n", "utf-8")
    layout = runners.python_layout(tree)
    assert layout is not None
    assert layout.paths == ("tests",)


def test_a_declaration_the_build_cannot_parse_falls_back_to_discovery(tmp_path: Path) -> None:
    tree = _python_tree(tmp_path, "tests")
    (tree / "pyproject.toml").write_text("[tool.pytest.ini_options\ntestpaths = [\n", "utf-8")
    layout = runners.python_layout(tree)
    assert layout is not None
    assert layout.paths == ("tests",)


def test_an_execution_failure_reason_carries_the_last_output_lines() -> None:
    stdout = "\n".join(
        [
            "collecting ...",
            "ImportError while importing test module 'tests/integration/test_x.py'.",
            "Hint: make sure your test modules/packages have valid Python names.",
            "Traceback:",
            '  File "tests/integration/test_x.py", line 1, in <module>',
            "    import tap_tester",
            "ModuleNotFoundError: No module named 'tap_tester'",
            "",
        ]
    )
    reason = runners.execution_failure(_pytest_layout(), 2, stdout, "")
    assert reason.startswith("pytest exited 2 (collection or usage error): ")
    assert "ModuleNotFoundError: No module named 'tap_tester'" in reason
    assert "import tap_tester" in reason
    assert "collecting ..." not in reason


def test_an_execution_failure_without_output_still_names_the_exit_code() -> None:
    assert runners.execution_failure(_pytest_layout(), 3, "", "") == "pytest exited 3"
    assert (
        runners.execution_failure(_pytest_layout(), 2, "", "")
        == "pytest exited 2 (collection or usage error)"
    )


CIRCLECI = """version: 2.1
jobs:
  run_unit_tests:
    steps:
      - run:
          name: 'Run Unit Tests'
          command: |
            source /usr/local/share/virtualenvs/app/bin/activate
            uv pip install pytest coverage
            coverage run -m pytest tests/unittests
            coverage html
"""

ACTIONS = """name: checks
jobs:
  test:
    steps:
      - run: uv run pytest tests/unittests -q
"""


def _ci_tree(tmp_path: Path, relative: str, body: str) -> Path:
    tree = _python_tree(tmp_path, "tests", "tests/unittests")
    config = tree / relative
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(body, encoding="utf-8")
    return tree


def test_a_circleci_command_narrows_the_suite_to_what_the_repository_runs(tmp_path: Path) -> None:
    tree = _ci_tree(tmp_path, ".circleci/config.yml", CIRCLECI)
    layout = runners.python_layout(tree)
    assert layout is not None
    assert layout.paths == ("tests/unittests",), (
        "the repository declares its own test command; running the whole tests/ tree runs "
        "integration tests it never intended the verifier to run"
    )


def test_a_github_workflow_command_is_read_the_same_way(tmp_path: Path) -> None:
    tree = _ci_tree(tmp_path, ".github/workflows/checks.yml", ACTIONS)
    layout = runners.python_layout(tree)
    assert layout is not None
    assert layout.paths == ("tests/unittests",)


def test_a_pytest_configuration_always_wins_over_ci(tmp_path: Path) -> None:
    tree = _ci_tree(tmp_path, ".circleci/config.yml", CIRCLECI)
    (tree / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    layout = runners.python_layout(tree)
    assert layout is not None
    assert layout.paths == ("tests",)


def test_a_token_that_is_not_a_test_path_is_never_taken_for_one(tmp_path: Path) -> None:
    tree = _ci_tree(tmp_path, ".circleci/config.yml", CIRCLECI)
    (tree / "coverage").mkdir()
    (tree / "coverage" / "notes.md").write_text("output\n", encoding="utf-8")
    layout = runners.python_layout(tree)
    assert layout is not None
    assert layout.paths == ("tests/unittests",), (
        "`uv pip install pytest coverage` names pytest and then a directory that exists; "
        "only a path that actually holds tests may enter the suite"
    )


def test_an_unresolvable_token_is_refused_rather_than_guessed(tmp_path: Path) -> None:
    body = "jobs:\n  test:\n    steps:\n      - run: pytest ${{ matrix.suite }}\n"
    tree = _ci_tree(tmp_path, ".github/workflows/checks.yml", body)
    layout = runners.python_layout(tree)
    assert layout is not None
    assert layout.paths == ("tests",), "a CI variable names no path, so discovery stands"


def test_a_path_outside_the_tree_never_enters_the_suite(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere" / "tests"
    outside.mkdir(parents=True)
    (outside / "test_x.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    body = "jobs:\n  test:\n    steps:\n      - run: pytest ../elsewhere/tests\n"
    tree = _ci_tree(tmp_path, ".github/workflows/checks.yml", body)
    layout = runners.python_layout(tree)
    assert layout is not None
    assert layout.paths == ("tests",)


def test_a_ci_file_that_does_not_parse_leaves_discovery_alone(tmp_path: Path) -> None:
    tree = _ci_tree(tmp_path, ".circleci/config.yml", "jobs:\n  - [unbalanced\n")
    layout = runners.python_layout(tree)
    assert layout is not None
    assert layout.paths == ("tests",)


def test_a_repository_whose_ci_names_no_pytest_path_is_unchanged(tmp_path: Path) -> None:
    body = "jobs:\n  test:\n    steps:\n      - run: npm run test:js\n"
    tree = _ci_tree(tmp_path, ".github/workflows/checks.yml", body)
    layout = runners.python_layout(tree)
    assert layout is not None
    assert layout.paths == ("tests",)


def test_a_nested_path_never_duplicates_the_directory_that_contains_it(tmp_path: Path) -> None:
    body = (
        "jobs:\n  test:\n    steps:\n"
        "      - run: pytest tests\n"
        "      - run: pytest tests/unittests\n"
    )
    tree = _ci_tree(tmp_path, ".github/workflows/checks.yml", body)
    layout = runners.python_layout(tree)
    assert layout is not None
    assert layout.paths == ("tests",)
