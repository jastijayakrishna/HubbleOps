from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from hubbleops.verify import runners, suites


def _php_missing(name: str) -> str | None:
    return None


def _php_present(name: str) -> str | None:
    return "/usr/bin/php"


CONFIG = """<?xml version="1.0" encoding="utf-8"?>
<phpunit bootstrap="tests/bootstrap.php" defaultTestSuite="unit">
\t<testsuites>
\t\t<testsuite name="unit">
\t\t\t<directory suffix=".php">./tests/Unit</directory>
\t\t</testsuite>
\t\t<testsuite name="integration">
\t\t\t<directory suffix=".php">./tests/integration</directory>
\t\t</testsuite>
\t</testsuites>
</phpunit>
"""

COMPOSER = """{
  "require-dev": {"phpunit/phpunit": "^9.5"},
  "scripts": {"test-unit": "./vendor/bin/phpunit"}
}
"""

JUNIT_PASSED = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
  <testsuite name="unit" tests="2">
    <testsuite name="AdsTest" tests="2">
      <testcase name="test_sends" class="AdsTest" time="0.01"/>
      <testcase name="test_reads" class="AdsTest" time="0.01"/>
    </testsuite>
  </testsuite>
</testsuites>
"""

JUNIT_MIXED = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
  <testsuite name="unit" tests="4">
    <testcase name="test_ok" class="AdsTest"/>
    <testcase name="test_bad" class="AdsTest">
      <failure type="AssertionFailed">boom</failure>
    </testcase>
    <testcase name="test_broken" class="AdsTest"><error type="Error">boom</error></testcase>
    <testcase name="test_later" class="AdsTest"><skipped/></testcase>
  </testsuite>
</testsuites>
"""


def _clover(project: Path) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<coverage clover="3.2.0">
  <project timestamp="0">
    <file name="{(project / "src" / "Ads.php").as_posix()}">
      <line num="12" type="stmt" count="3"/>
      <metrics statements="4" coveredstatements="1"/>
    </file>
    <file name="{(project / "src" / "Untouched.php").as_posix()}">
      <line num="8" type="stmt" count="0"/>
      <metrics statements="2" coveredstatements="0"/>
    </file>
  </project>
</coverage>
"""


def _repository(root: Path, *, config: bool = True, composer: bool = True) -> Path:
    tree = root / "repo"
    (tree / "tests" / "Unit").mkdir(parents=True)
    (tree / "tests" / "integration").mkdir(parents=True)
    (tree / "src").mkdir()
    (tree / "tests" / "Unit" / "AdsTest.php").write_text("<?php\n", encoding="utf-8")
    (tree / "tests" / "integration" / "ApiTest.php").write_text("<?php\n", encoding="utf-8")
    (tree / "src" / "Ads.php").write_text("<?php\n", encoding="utf-8")
    if composer:
        (tree / "composer.json").write_text(COMPOSER, encoding="utf-8")
    if config:
        (tree / "phpunit.xml.dist").write_text(CONFIG, encoding="utf-8")
    return tree


def _install(tree: Path, name: str = "phpunit") -> Path:
    binary = tree / "vendor" / "bin"
    binary.mkdir(parents=True, exist_ok=True)
    target = binary / name
    target.write_text("#!/usr/bin/env php\n", encoding="utf-8")
    return target


def _plan(tree: Path, layout: runners.SuiteLayout) -> runners.RunnerPlan:
    return runners.RunnerPlan(
        layout=layout, workspace=tree, report=runners.report_path(tree, layout), plugin=None
    )


def test_the_config_the_repository_ships_names_the_frozen_paths(tmp_path: Path) -> None:
    layout = runners.layout_of(_repository(tmp_path))
    assert layout is not None
    assert layout.runner == "phpunit"
    assert layout.languages == ("php",)
    assert layout.paths == ("tests/Unit", "tests/integration")
    assert layout.suites == ("unit", "integration")
    assert layout.project == "."


def test_a_composer_declaration_without_a_config_falls_back_to_the_conventional_directory(
    tmp_path: Path,
) -> None:
    layout = runners.layout_of(_repository(tmp_path, config=False))
    assert layout is not None
    assert layout.runner == "phpunit"
    assert layout.paths == ("tests",)
    assert layout.suites == ()


def test_a_repository_that_declares_no_phpunit_has_no_php_layout(tmp_path: Path) -> None:
    tree = _repository(tmp_path, config=False, composer=False)
    (tree / "composer.json").write_text('{"require": {"guzzlehttp/guzzle": "^7"}}', "utf-8")
    assert runners.phpunit_layout(tree) is None
    assert runners.layout_of(tree) is None


def test_a_config_directory_that_does_not_exist_is_never_frozen(tmp_path: Path) -> None:
    tree = _repository(tmp_path)
    shutil.rmtree(tree / "tests" / "integration")
    layout = runners.layout_of(tree)
    assert layout is not None
    assert layout.paths == ("tests/Unit",)
    assert layout.suites == ("unit",)


def test_a_runner_the_repository_never_installed_is_named_not_assumed(tmp_path: Path) -> None:
    tree = _repository(tmp_path)
    layout = runners.layout_of(tree)
    assert layout is not None
    missing = runners.missing_runner(tree, layout)
    assert missing is not None
    assert "phpunit" in missing
    assert "vendor/bin/phpunit" in missing
    assert "installs" in missing


def test_a_missing_php_interpreter_is_named_not_assumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _repository(tmp_path)
    _install(tree)
    layout = runners.layout_of(tree)
    assert layout is not None
    monkeypatch.setattr(runners.shutil, "which", _php_missing)
    missing = runners.missing_runner(tree, layout)
    assert missing is not None
    assert missing.startswith("php is not on PATH")
    monkeypatch.setattr(runners.shutil, "which", _php_present)
    assert runners.missing_runner(tree, layout) is None


def test_the_command_runs_the_repositorys_own_runner_and_logs_junit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _repository(tmp_path)
    _install(tree)
    monkeypatch.setattr(runners.shutil, "which", _php_present)
    layout = runners.layout_of(tree)
    assert layout is not None
    command = runners.command(tree, _plan(tree, layout), "python")
    assert command[0] == "/usr/bin/php"
    assert Path(command[1]) == tree / "vendor" / "bin" / "phpunit"
    assert "--log-junit=coverage/junit.xml" in command
    assert "--coverage-clover=coverage/clover.xml" in command
    assert command[-2:] == ("--testsuite", "unit,integration")
    assert runners.report_path(tree, layout) == tree / "coverage" / "junit.xml"


def test_a_windows_launcher_runs_without_a_separate_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _repository(tmp_path)
    _install(tree, "phpunit.bat")
    monkeypatch.setattr(runners.shutil, "which", _php_missing)
    layout = runners.layout_of(tree)
    assert layout is not None
    assert runners.missing_runner(tree, layout) is None
    command = runners.command(tree, _plan(tree, layout), "python")
    assert command[0].endswith("phpunit.bat")


def test_the_conventional_fallback_is_passed_as_the_single_argument(tmp_path: Path) -> None:
    tree = _repository(tmp_path, config=False)
    _install(tree)
    layout = runners.layout_of(tree)
    assert layout is not None
    command = runners.command(tree, _plan(tree, layout), "python")
    assert command[-1] == "tests"
    assert "--testsuite" not in command


def test_junit_counts_every_case_it_reports(tmp_path: Path) -> None:
    report = tmp_path / "junit.xml"
    report.write_text(JUNIT_PASSED, encoding="utf-8")
    assert runners.junit_counts(report) == {"passed": 2, "failed": 0, "skipped": 0}
    report.write_text(JUNIT_MIXED, encoding="utf-8")
    assert runners.junit_counts(report) == {"passed": 1, "failed": 2, "skipped": 1}


def test_a_malformed_or_absent_junit_report_is_unresolved_not_a_pass(tmp_path: Path) -> None:
    report = tmp_path / "junit.xml"
    assert runners.junit_counts(report) is None
    report.write_text("<testsuites><testcase", encoding="utf-8")
    assert runners.junit_counts(report) is None
    report.write_text("<html><body>not a report</body></html>", encoding="utf-8")
    assert runners.junit_counts(report) is None
    report.write_text(
        '<!DOCTYPE t [<!ENTITY a "x">]><testsuites><testcase name="t"/></testsuites>', "utf-8"
    )
    assert runners.junit_counts(report) is None


def test_clover_coverage_becomes_one_aggregate_case_per_suite(tmp_path: Path) -> None:
    tree = _repository(tmp_path)
    layout = runners.layout_of(tree)
    assert layout is not None
    (tree / "coverage").mkdir()
    (tree / "coverage" / "clover.xml").write_text(_clover(tree), encoding="utf-8")
    plan = _plan(tree, layout)
    cases = runners.read_cases(plan, "FROZEN_BASELINE", {"passed": 2, "failed": 0, "skipped": 0})
    assert len(cases) == 1
    assert cases[0].name == "phpunit:."
    assert cases[0].outcome == "passed"
    assert cases[0].files == ("src/Ads.php",)
    assert runners.coverage_gap(tree, layout) is None


def test_a_failing_php_suite_covers_nothing_that_counts_as_proof(tmp_path: Path) -> None:
    tree = _repository(tmp_path)
    layout = runners.layout_of(tree)
    assert layout is not None
    (tree / "coverage").mkdir()
    (tree / "coverage" / "clover.xml").write_text(_clover(tree), encoding="utf-8")
    plan = _plan(tree, layout)
    cases = runners.read_cases(plan, "FROZEN_BASELINE", {"passed": 1, "failed": 1, "skipped": 0})
    assert cases[0].outcome == "failed"
    assert (
        frozenset(path for case in cases if case.outcome == "passed" for path in case.files)
        == frozenset()
    )


def test_a_run_without_a_coverage_driver_covers_nothing_and_says_so(tmp_path: Path) -> None:
    tree = _repository(tmp_path)
    layout = runners.layout_of(tree)
    assert layout is not None
    assert runners.coverage_provider(tree, layout) is None
    gap = runners.coverage_gap(tree, layout)
    assert gap is not None
    assert "pcov or xdebug" in gap
    assert "UNKNOWN_BLAST" in gap
    assert runners.read_cases(plan := _plan(tree, layout), "FROZEN_BASELINE", {"failed": 0}) == ()
    assert plan.report.name == "junit.xml"


def test_an_empty_clover_report_never_counts_as_coverage(tmp_path: Path) -> None:
    tree = _repository(tmp_path)
    layout = runners.layout_of(tree)
    assert layout is not None
    (tree / "coverage").mkdir()
    (tree / "coverage" / "clover.xml").write_text(
        '<?xml version="1.0"?><coverage><project timestamp="0"/></coverage>', encoding="utf-8"
    )
    assert runners.coverage_provider(tree, layout) is None
    assert runners.coverage_gap(tree, layout) is not None


def test_an_uninstalled_php_runner_stays_tooling_missing_and_names_it(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    candidate = tmp_path / "candidate"
    shutil.copytree(base, candidate)
    plan = suites.stage_frozen(base, candidate, tmp_path / "workspace")
    run = suites.run_frozen(plan, "python", 5)
    assert run.outcome == "TOOLING_MISSING"
    assert run.executed is False
    assert "phpunit" in run.reason
    assert run.all_passed() is False


def test_a_php_run_without_a_report_is_unresolved_not_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _repository(tmp_path)
    _install(tree)
    monkeypatch.setattr(runners.shutil, "which", _php_present)
    layout = runners.layout_of(tree)
    assert layout is not None
    plan = suites.FrozenSuitePlan(
        workspace=tree,
        report=runners.report_path(tree, layout),
        plugin=None,
        paths=layout.paths,
        layout=layout,
    )

    def ran(*arguments: object, **options: object) -> tuple[str, int | None, bytes, bytes]:
        return ("COMPLETED", 0, b"OK (2 tests, 2 assertions)", b"")

    monkeypatch.setattr(suites, "bounded_process", ran)
    run = suites.run_frozen(plan, "python", 5)
    assert run.outcome == "EXECUTION_FAILED"
    assert run.passed == 0
    assert run.all_passed() is False
    assert "junit.xml" in run.reason


def test_a_php_run_with_a_report_but_no_driver_leaves_php_uncovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _repository(tmp_path)
    _install(tree)
    monkeypatch.setattr(runners.shutil, "which", _php_present)
    layout = runners.layout_of(tree)
    assert layout is not None
    report = runners.report_path(tree, layout)
    plan = suites.FrozenSuitePlan(
        workspace=tree, report=report, plugin=None, paths=layout.paths, layout=layout
    )

    def ran(*arguments: object) -> tuple[str, int, bytes, bytes]:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(JUNIT_PASSED, encoding="utf-8")
        return "COMPLETED", 0, b"OK (2 tests, 2 assertions)", b""

    monkeypatch.setattr(suites, "bounded_process", ran)
    run = suites.run_frozen(plan, "python", 5)
    assert run.outcome == "COMPLETED"
    assert (run.passed, run.failed, run.skipped) == (2, 0, 0)
    assert run.languages == ()
    assert run.tests == ()
    assert "UNKNOWN_BLAST" in run.reason
    assert "php" not in suites.covered_languages(run)


def test_a_php_run_with_a_driver_reports_the_covered_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _repository(tmp_path)
    _install(tree)
    monkeypatch.setattr(runners.shutil, "which", _php_present)
    layout = runners.layout_of(tree)
    assert layout is not None
    report = runners.report_path(tree, layout)
    plan = suites.FrozenSuitePlan(
        workspace=tree, report=report, plugin=None, paths=layout.paths, layout=layout
    )

    def ran(*arguments: object) -> tuple[str, int, bytes, bytes]:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(JUNIT_PASSED, encoding="utf-8")
        (tree / "coverage" / "clover.xml").write_text(_clover(tree), encoding="utf-8")
        return "COMPLETED", 0, b"OK (2 tests, 2 assertions)", b""

    monkeypatch.setattr(suites, "bounded_process", ran)
    run = suites.run_frozen(plan, "python", 5)
    assert run.outcome == "COMPLETED"
    assert run.all_passed() is True
    assert run.languages == ("php",)
    assert run.covered_files() == frozenset({"src/Ads.php"})
    assert suites.covered_languages(run) == frozenset({"php"})


def test_the_layout_is_stable_across_repeated_reads(tmp_path: Path) -> None:
    tree = _repository(tmp_path)
    assert runners.layout_of(tree) == runners.layout_of(tree)


def test_a_python_or_javascript_suite_still_wins_over_php(tmp_path: Path) -> None:
    tree = _repository(tmp_path)
    (tree / "tests" / "test_reporting.py").write_text("def test_ok():\n    assert True\n", "utf-8")
    layout = runners.layout_of(tree)
    assert layout is not None
    assert layout.runner == "pytest"


def _mixed_repository(root: Path, *, vendor: bool = True) -> Path:
    tree = _repository(root)
    (tree / "__tests__").mkdir()
    (tree / "__tests__" / "api.test.js").write_text("test('ok', () => {});\n", encoding="utf-8")
    (tree / "src" / "api.js").write_text("export const send = () => {};\n", encoding="utf-8")
    (tree / "package.json").write_text('{"devDependencies": {"jest": "^29"}}', encoding="utf-8")
    binary = tree / "node_modules" / ".bin"
    binary.mkdir(parents=True)
    (binary / "jest").write_text("#!/bin/sh\n", encoding="utf-8")
    if vendor:
        _install(tree)
    return tree


def _both_ran(junit: str = JUNIT_PASSED) -> Callable[..., tuple[str, int, bytes, bytes]]:
    def ran(
        argv: tuple[str, ...],
        wall_seconds: float,
        output_bytes: int,
        tool: str,
        environment: dict[str, str],
        working_directory: str,
    ) -> tuple[str, int, bytes, bytes]:
        root = Path(working_directory)
        (root / "coverage").mkdir(parents=True, exist_ok=True)
        if tool == "phpunit":
            (root / "coverage" / "junit.xml").write_text(junit, encoding="utf-8")
            (root / "coverage" / "clover.xml").write_text(_clover(root), encoding="utf-8")
            broken = "failure" in junit or "<error" in junit
            return "COMPLETED", 1 if broken else 0, b"", b""
        covered = (root / "src" / "api.js").as_posix()
        (root / "coverage" / "coverage-final.json").write_text(
            json.dumps({covered: {"path": covered, "s": {"0": 1}}}), encoding="utf-8"
        )
        return "COMPLETED", 0, b"Tests  2 passed (2)\n", b""

    return ran


def test_every_declared_runner_is_detected_in_a_stable_order(tmp_path: Path) -> None:
    tree = _mixed_repository(tmp_path)
    detected = runners.layouts_of(tree)
    assert [layout.runner for layout in detected] == ["jest", "phpunit"]
    assert runners.layouts_of(tree) == detected
    assert runners.layout_of(tree) == detected[0]
    assert suites.suite_paths(tree) == ("__tests__", "tests/Unit", "tests/integration")


def test_two_runners_that_both_pass_merge_into_one_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _mixed_repository(tmp_path)
    monkeypatch.setattr(runners.shutil, "which", _php_present)
    monkeypatch.setattr(suites, "bounded_process", _both_ran())
    run = suites.run_candidate(tree, tmp_path / "workspace", "python", 5)
    assert run.outcome == "COMPLETED"
    assert run.all_passed() is True
    assert (run.passed, run.failed, run.skipped) == (4, 0, 0)
    assert run.runner == "jest+phpunit"
    assert run.languages == ("javascript", "php", "tsx", "typescript")
    assert [case.name for case in run.tests] == ["jest:.", "phpunit:."]
    assert run.covered_files() == frozenset({"src/Ads.php", "src/api.js"})
    assert suites.covered_languages(run) == frozenset({"javascript", "php", "tsx", "typescript"})


def test_a_declared_runner_that_cannot_run_sinks_the_whole_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _mixed_repository(tmp_path, vendor=False)
    candidate = tmp_path / "candidate"
    shutil.copytree(base, candidate)
    monkeypatch.setattr(suites, "bounded_process", _both_ran())
    plan = suites.stage_frozen(base, candidate, tmp_path / "workspace")
    assert [part.layout.runner for part in plan.suite_parts()] == ["jest", "phpunit"]
    run = suites.run_frozen(plan, "python", 5)
    assert run.outcome == "TOOLING_MISSING"
    assert run.executed is False
    assert run.all_passed() is False
    assert "phpunit" in run.reason
    assert run.passed == 2


def test_one_failing_runner_denies_the_merged_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _mixed_repository(tmp_path)
    monkeypatch.setattr(runners.shutil, "which", _php_present)
    monkeypatch.setattr(suites, "bounded_process", _both_ran(JUNIT_MIXED))
    run = suites.run_candidate(tree, tmp_path / "workspace", "python", 5)
    assert run.all_passed() is False
    assert (run.passed, run.failed, run.skipped) == (3, 2, 1)
    assert run.covered_files() == frozenset({"src/api.js"})
