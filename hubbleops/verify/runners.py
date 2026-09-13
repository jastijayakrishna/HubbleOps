from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import subprocess
import tomllib
import xml.etree.ElementTree as ElementTree
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from hubbleops.core.records import as_mapping, as_sequence, as_text, parse_json
from hubbleops.core.verification import SuiteCase
from hubbleops.verify import coverage

SUITE_DIRECTORY_NAMES = ("tests", "test", "spec", "__tests__")
PYTHON_SUITE_FILE = re.compile(r"^(test_.*|.*_test|.*\.test|.*\.spec)\.py$")
PYTEST_INI_SECTIONS = (("pytest.ini", "pytest"), (".pytest.ini", "pytest"))
PYTEST_TOML = "pyproject.toml"
PYTEST_FALLBACK_INI_SECTIONS = (("tox.ini", "pytest"), ("setup.cfg", "tool:pytest"))
TESTPATHS = "testpaths"
GLOB_CHARACTERS = "*?["
FAILURE_TAIL_LINES = 5
COLLECTION_EXIT_CODE = 2
JAVASCRIPT_SUITE_FILE = re.compile(r"^.*\.(test|spec)\.[cm]?[jt]sx?$")
MANIFEST = "package.json"
MODULES = "node_modules"
JAVASCRIPT_REPORT = "coverage/coverage-final.json"
JAVASCRIPT_RUNNERS = ("vitest", "jest")
VITEST_PROVIDERS = {"@vitest/coverage-v8": "v8", "@vitest/coverage-istanbul": "istanbul"}
NO_COVERAGE_PROVIDER = (
    "the suite ran but reported no coverage: {runner} needs a coverage provider "
    "({providers}) and the repository has installed none, so every module it touches "
    "stays UNKNOWN_BLAST rather than covered"
)
PHP_RUNNER = "phpunit"
PHP_SUITE_FILE = re.compile(r"^.+Test\.php$")
COMPOSER = "composer.json"
VENDOR = "vendor"
PHP_CONFIGS = ("phpunit.xml", "phpunit.xml.dist")
PHP_PACKAGE = "phpunit/phpunit"
PHP_REPORT = "coverage/junit.xml"
PHP_COVERAGE = "coverage/clover.xml"
PHP_BINARIES = ("phpunit.bat", "phpunit.cmd", "phpunit", "phpunit.phar")
PHP_LAUNCHERS = (".bat", ".cmd")
XML_ENTITY = re.compile(r"<!ENTITY", re.IGNORECASE)
NO_COVERAGE_DRIVER = (
    "the suite ran but produced no coverage at {report}: phpunit needs a coverage driver "
    "(pcov or xdebug) and the php the repository runs has none, so every module it touches "
    "stays UNKNOWN_BLAST rather than covered"
)
NO_VENDOR_RUNNER = (
    "phpunit is declared by {project}/{manifest} but is not installed at "
    "{project}/{vendor}/bin/phpunit; the verifier runs the repository's own runner and installs "
    "none"
)
NO_PHP_INTERPRETER = (
    "php is not on PATH, so {project}/{vendor}/bin/phpunit cannot run; the verifier runs the "
    "repository's own toolchain and installs none"
)
MAX_SEARCH_DEPTH = 6
MAX_REPORT_BYTES = 33_554_432
SUMMARY = re.compile(r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed|todo)")
JAVASCRIPT_SUMMARY = re.compile(r"Tests\s+(?:(\d+) failed[^\n]*?)?(\d+) passed", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SuiteLayout:
    runner: str
    languages: tuple[str, ...]
    paths: tuple[str, ...]
    project: str
    suites: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RunnerPlan:
    layout: SuiteLayout
    workspace: Path
    report: Path
    plugin: Path | None


def python_layout(tree: Path) -> SuiteLayout | None:
    declared = _declared_testpaths(tree)
    if declared is not None:
        found = _paths_inside(tree, declared)
        if not found:
            return None
        return SuiteLayout(runner="pytest", languages=("python",), paths=found, project=".")
    found = [
        child.name
        for child in sorted(tree.iterdir())
        if child.is_dir()
        and child.name in SUITE_DIRECTORY_NAMES
        and _holds(child, PYTHON_SUITE_FILE)
    ]
    if not found:
        found = sorted(
            path.relative_to(tree).as_posix()
            for path in tree.rglob("*.py")
            if PYTHON_SUITE_FILE.match(path.name) and ".git" not in path.parts
        )
    if not found:
        return None
    return SuiteLayout(runner="pytest", languages=("python",), paths=tuple(found), project=".")


def _declared_testpaths(tree: Path) -> tuple[str, ...] | None:
    for name, section in PYTEST_INI_SECTIONS:
        if (tree / name).is_file():
            return _ini_testpaths(tree / name, section) or None
    toml_paths = _toml_testpaths(tree / PYTEST_TOML)
    if toml_paths is not None:
        return toml_paths or None
    for name, section in PYTEST_FALLBACK_INI_SECTIONS:
        declared = _ini_testpaths(tree / name, section)
        if declared is not None:
            return declared or None
    return None


def _ini_testpaths(config: Path, section: str) -> tuple[str, ...] | None:
    try:
        text = config.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(text, source=str(config))
    except configparser.Error:
        return None
    if not parser.has_section(section):
        return None
    value = parser.get(section, TESTPATHS, fallback=None)
    return tuple(value.split()) if value is not None else ()


def _toml_testpaths(config: Path) -> tuple[str, ...] | None:
    try:
        text = config.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    tool = as_mapping(document.get("tool"))
    pytest_tool = as_mapping(tool.get("pytest"))
    if "ini_options" not in pytest_tool:
        return None
    options = as_mapping(pytest_tool.get("ini_options"))
    raw = options.get(TESTPATHS)
    if raw is None:
        return ()
    if isinstance(raw, str):
        return tuple(raw.split())
    return tuple(str(item) for item in as_sequence(raw))


def _paths_inside(tree: Path, entries: Sequence[str]) -> tuple[str, ...]:
    root = tree.resolve()
    found: set[str] = set()
    for entry in entries:
        cleaned = entry.strip().replace("\\", "/")
        if not cleaned or cleaned.startswith("/") or ":" in cleaned:
            return ()
        matches = _expand(tree, cleaned)
        if not matches:
            return ()
        for match in matches:
            try:
                relative = match.resolve().relative_to(root).as_posix()
            except (OSError, ValueError):
                return ()
            if ".git" in relative.split("/"):
                return ()
            found.add(relative)
    return tuple(sorted(found))


def _expand(tree: Path, entry: str) -> tuple[Path, ...]:
    if not any(character in entry for character in GLOB_CHARACTERS):
        target = tree / entry
        return (target,) if target.exists() else ()
    try:
        return tuple(sorted(path for path in tree.glob(entry) if path.exists()))
    except (OSError, ValueError, NotImplementedError):
        return ()


def execution_failure(layout: SuiteLayout, exit_code: int | None, stdout: str, stderr: str) -> str:
    head = f"{layout.runner} exited {exit_code}"
    if layout.runner == "pytest" and exit_code == COLLECTION_EXIT_CODE:
        head = f"{head} (collection or usage error)"
    lines = [line.rstrip() for line in f"{stdout}\n{stderr}".splitlines() if line.strip()]
    tail = lines[-FAILURE_TAIL_LINES:]
    return f"{head}: {' | '.join(tail)}"[:512] if tail else head


def javascript_layout(tree: Path) -> SuiteLayout | None:
    for project in _projects(tree):
        declared = _declared_runner(tree / project)
        if declared is None:
            continue
        paths = _javascript_paths(tree / project)
        if not paths:
            continue
        return SuiteLayout(
            runner=declared,
            languages=("javascript", "typescript", "tsx"),
            paths=tuple(f"{project}/{item}" if project != "." else item for item in paths),
            project=project,
        )
    return None


def phpunit_layout(tree: Path) -> SuiteLayout | None:
    for project in _php_projects(tree):
        directory = tree / project
        config = _php_config(directory)
        if config is None and not _composer_declares_phpunit(directory):
            continue
        paths, names = _php_paths(directory, config)
        if not paths:
            continue
        return SuiteLayout(
            runner=PHP_RUNNER,
            languages=("php",),
            paths=tuple(f"{project}/{item}" if project != "." else item for item in paths),
            project=project,
            suites=names,
        )
    return None


def layouts_of(tree: Path) -> tuple[SuiteLayout, ...]:
    detected = (python_layout(tree), javascript_layout(tree), phpunit_layout(tree))
    return tuple(layout for layout in detected if layout is not None)


def layout_of(tree: Path) -> SuiteLayout | None:
    detected = layouts_of(tree)
    return detected[0] if detected else None


def missing_runner(tree: Path, layout: SuiteLayout) -> str | None:
    if layout.runner == "pytest":
        return None
    if layout.runner == PHP_RUNNER:
        return _php_missing(tree, layout)
    binary = tree / layout.project / MODULES / ".bin" / layout.runner
    if any(binary.with_suffix(suffix).exists() for suffix in ("", ".cmd", ".ps1", ".CMD")):
        return None
    return (
        f"{layout.runner} is declared by {layout.project}/{MANIFEST} but is not installed in "
        f"{layout.project}/{MODULES}; the verifier runs the repository's own runner and installs "
        "none"
    )


def command(tree: Path, plan: RunnerPlan, python_executable: str) -> tuple[str, ...]:
    if plan.layout.runner == "pytest":
        plugin = coverage.PLUGIN_FILENAME.removesuffix(".py")
        return (
            python_executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            ".",
            "-p",
            plugin,
            *plan.layout.paths,
        )
    if plan.layout.runner == PHP_RUNNER:
        return _php_command(tree, plan.layout)
    binary = _binary(tree, plan.layout)
    relative = _project_relative(plan.layout)
    if plan.layout.runner == "jest":
        return (
            str(binary),
            "--ci",
            "--coverage",
            "--coverageReporters=json",
            "--coverageDirectory=coverage",
            *relative,
        )
    provider = coverage_provider(tree, plan.layout)
    if provider is None:
        return (str(binary), "run", *relative)
    return (
        str(binary),
        "run",
        "--coverage.enabled",
        f"--coverage.provider={provider}",
        "--coverage.reporter=json",
        "--coverage.reportsDirectory=coverage",
        *relative,
    )


def coverage_provider(tree: Path, layout: SuiteLayout) -> str | None:
    if layout.runner == PHP_RUNNER:
        return "clover" if _clover_files(tree, layout) else None
    if layout.runner != "vitest":
        return "built-in"
    modules = tree / layout.project / MODULES
    for package, provider in VITEST_PROVIDERS.items():
        if (modules / package).is_dir():
            return provider
    return None


def coverage_gap(tree: Path, layout: SuiteLayout) -> str | None:
    if coverage_provider(tree, layout) is not None:
        return None
    if layout.runner == PHP_RUNNER:
        return NO_COVERAGE_DRIVER.format(report=PHP_COVERAGE)
    return NO_COVERAGE_PROVIDER.format(
        runner=layout.runner, providers=", ".join(sorted(VITEST_PROVIDERS))
    )


def link_dependencies(source: Path, workspace: Path, layout: SuiteLayout) -> tuple[str, ...]:
    if layout.runner == "pytest":
        return ()
    installed = VENDOR if layout.runner == PHP_RUNNER else MODULES
    linked: list[str] = []
    for project in {layout.project, "."}:
        target = _link_one(source / project / installed, workspace / project / installed)
        if target is not None:
            linked.append(target)
    return tuple(sorted(linked))


def _link_one(installed: Path, target: Path) -> str | None:
    if target.exists() or not installed.is_dir():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(installed, target, target_is_directory=True)
        return str(target)
    except (OSError, NotImplementedError):
        pass
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(target), str(installed)],
        capture_output=True,
        text=True,
        check=False,
    )
    return str(target) if completed.returncode == 0 else None


def working_directory(tree: Path, layout: SuiteLayout) -> Path:
    return tree / layout.project if layout.project != "." else tree


def environment(plan: RunnerPlan) -> dict[str, str]:
    if plan.layout.runner == "pytest":
        return coverage.environment(plan.workspace, plan.report)
    return {"CI": "1", "NO_COLOR": "1", "FORCE_COLOR": "0"}


def report_path(workspace: Path, layout: SuiteLayout) -> Path:
    if layout.runner == "pytest":
        return workspace / coverage.REPORT_FILENAME
    if layout.runner == PHP_RUNNER:
        return working_directory(workspace, layout) / PHP_REPORT
    return working_directory(workspace, layout) / JAVASCRIPT_REPORT


def read_cases(plan: RunnerPlan, source: str, counts: Mapping[str, int]) -> tuple[SuiteCase, ...]:
    if plan.layout.runner == "pytest":
        return coverage.read_report(plan.report)
    if plan.layout.runner == PHP_RUNNER:
        return _php_cases(plan, counts)
    return _javascript_cases(plan, source, counts)


def run_counts(plan: RunnerPlan, output: str) -> dict[str, int] | None:
    if plan.layout.runner == PHP_RUNNER:
        return junit_counts(plan.report)
    return counts_of(plan.layout, output)


def junit_counts(report: Path) -> dict[str, int] | None:
    root = _read_xml(report)
    if root is None:
        return None
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    seen = False
    for element in root.iter():
        if _tag(element) != "testcase":
            continue
        seen = True
        kinds = {_tag(child) for child in element}
        if kinds & {"failure", "error"}:
            counts["failed"] += 1
        elif "skipped" in kinds:
            counts["skipped"] += 1
        else:
            counts["passed"] += 1
    return counts if seen or _tag(root) in ("testsuites", "testsuite") else None


def counts_of(layout: SuiteLayout, output: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    if layout.runner != "pytest":
        match = JAVASCRIPT_SUMMARY.search(output)
        if match is not None:
            counts["failed"] = int(match.group(1) or 0)
            counts["passed"] = int(match.group(2))
        return counts
    for number, label in SUMMARY.findall(output):
        if label in ("passed", "xpassed"):
            counts["passed"] += int(number)
        elif label in ("failed", "error", "errors"):
            counts["failed"] += int(number)
        elif label in ("skipped", "xfailed", "todo"):
            counts["skipped"] += int(number)
    return counts


def _javascript_cases(
    plan: RunnerPlan, source: str, counts: Mapping[str, int]
) -> tuple[SuiteCase, ...]:
    if not plan.report.is_file():
        return ()
    payload = plan.report.read_bytes()[:MAX_REPORT_BYTES]
    parsed = parse_json(payload)
    if not parsed.ok():
        return ()
    root = working_directory(plan.workspace, plan.layout).resolve()
    covered: set[str] = set()
    for key, entry in as_mapping(parsed.value).items():
        record = as_mapping(entry)
        path = as_text(record.get("path")) or str(key)
        if not _executed(record):
            continue
        relative = _relative(Path(path), root, plan.workspace.resolve())
        if relative is not None:
            covered.add(relative)
    if not covered:
        return ()
    return (
        SuiteCase(
            name=f"{plan.layout.runner}:{plan.layout.project}",
            outcome="passed" if not counts.get("failed") else "failed",
            files=tuple(sorted(covered)),
        ),
    )


def _executed(record: Mapping[str, object]) -> bool:
    statements = as_mapping(record.get("s"))
    if statements:
        return any(_positive(value) for value in statements.values())
    return any(_positive(value) for value in as_sequence(record.get("statementMap")))


def _positive(value: object) -> bool:
    return isinstance(value, int) and value > 0


def _relative(path: Path, project_root: Path, workspace: Path) -> str | None:
    for base in (project_root, workspace):
        try:
            return path.resolve().relative_to(base if base == workspace else workspace).as_posix()
        except ValueError:
            continue
    return None


def _php_cases(plan: RunnerPlan, counts: Mapping[str, int]) -> tuple[SuiteCase, ...]:
    covered = _clover_files(plan.workspace, plan.layout)
    if not covered:
        return ()
    return (
        SuiteCase(
            name=f"{PHP_RUNNER}:{plan.layout.project}",
            outcome="passed" if not counts.get("failed") else "failed",
            files=tuple(sorted(covered)),
        ),
    )


def _clover_files(workspace: Path, layout: SuiteLayout) -> frozenset[str]:
    root = working_directory(workspace, layout)
    document = _read_xml(root / PHP_COVERAGE)
    if document is None:
        return frozenset()
    covered: set[str] = set()
    for element in document.iter():
        if _tag(element) != "file":
            continue
        name = element.get("name") or element.get("path")
        if not name or not _clover_executed(element):
            continue
        relative = _relative(Path(name), root.resolve(), workspace.resolve())
        if relative is not None:
            covered.add(relative)
    return frozenset(covered)


def _clover_executed(element: ElementTree.Element) -> bool:
    for child in element.iter():
        tag = _tag(child)
        if tag == "line" and _as_number(child.get("count")) > 0:
            return True
        if tag == "metrics" and _as_number(child.get("coveredstatements")) > 0:
            return True
    return False


def _as_number(value: str | None) -> int:
    if value is None or not value.strip().lstrip("-").isdigit():
        return 0
    return int(value)


def _php_command(tree: Path, layout: SuiteLayout) -> tuple[str, ...]:
    binary = _php_binary(tree, layout) or tree / layout.project / VENDOR / "bin" / PHP_RUNNER
    prefix = () if binary.suffix.lower() in PHP_LAUNCHERS else (_php_interpreter() or "php",)
    selection = (
        ("--testsuite", ",".join(layout.suites)) if layout.suites else _project_relative(layout)
    )
    return (
        *prefix,
        str(binary),
        "--do-not-cache-result",
        "--colors=never",
        f"--log-junit={PHP_REPORT}",
        f"--coverage-clover={PHP_COVERAGE}",
        *selection,
    )


def _php_missing(tree: Path, layout: SuiteLayout) -> str | None:
    binary = _php_binary(tree, layout)
    if binary is None:
        return NO_VENDOR_RUNNER.format(project=layout.project, manifest=COMPOSER, vendor=VENDOR)
    if binary.suffix.lower() in PHP_LAUNCHERS or _php_interpreter() is not None:
        return None
    return NO_PHP_INTERPRETER.format(project=layout.project, vendor=VENDOR)


def _php_interpreter() -> str | None:
    return shutil.which("php")


def _php_binary(tree: Path, layout: SuiteLayout) -> Path | None:
    directory = tree / layout.project / VENDOR / "bin"
    for name in PHP_BINARIES:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def _php_projects(tree: Path) -> tuple[str, ...]:
    found: set[str] = set()
    for name in (COMPOSER, *PHP_CONFIGS):
        for path in tree.rglob(name):
            parts = path.parts
            if VENDOR in parts or MODULES in parts or ".git" in parts:
                continue
            relative = path.parent.relative_to(tree).as_posix() or "."
            if relative.count("/") + 1 > MAX_SEARCH_DEPTH:
                continue
            found.add(relative)
    return tuple(sorted(found, key=lambda item: (item.count("/"), item)))


def _php_config(project: Path) -> Path | None:
    for name in PHP_CONFIGS:
        candidate = project / name
        if candidate.is_file():
            return candidate
    return None


def _composer_declares_phpunit(project: Path) -> bool:
    try:
        payload = (project / COMPOSER).read_bytes()
    except OSError:
        return False
    parsed = parse_json(payload)
    if not parsed.ok():
        return False
    record = as_mapping(parsed.value)
    if PHP_PACKAGE in {*as_mapping(record.get("require-dev")), *as_mapping(record.get("require"))}:
        return True
    declared = " ".join(str(value) for value in as_mapping(record.get("scripts")).values())
    return re.search(rf"\b{PHP_RUNNER}\b", declared) is not None


def _php_paths(project: Path, config: Path | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if config is not None:
        paths, names = _php_declared_paths(project, config)
        if paths:
            return paths, names
    for name in SUITE_DIRECTORY_NAMES:
        candidate = project / name
        if candidate.is_dir() and _holds(candidate, PHP_SUITE_FILE):
            return (name,), ()
    return (), ()


def _php_declared_paths(project: Path, config: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    document = _read_xml(config)
    if document is None:
        return (), ()
    paths: set[str] = set()
    names: list[str] = []
    unnamed = False
    for element in document.iter():
        if _tag(element) != "testsuite":
            continue
        declared = [
            relative
            for child in element
            if _tag(child) in ("directory", "file") and child.text is not None
            for relative in (_inside(project, child.text),)
            if relative is not None
        ]
        if not declared:
            continue
        paths.update(declared)
        name = (element.get("name") or "").strip()
        if not name:
            unnamed = True
        elif name not in names:
            names.append(name)
    return tuple(sorted(paths)), () if unnamed else tuple(names)


def _inside(root: Path, raw: str) -> str | None:
    cleaned = raw.strip().replace("\\", "/")
    if not cleaned or cleaned.startswith("/") or "*" in cleaned:
        return None
    target = root / cleaned
    if not target.exists():
        return None
    try:
        return target.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def _read_xml(path: Path) -> ElementTree.Element | None:
    if not path.is_file():
        return None
    try:
        payload = path.read_bytes()[:MAX_REPORT_BYTES]
    except OSError:
        return None
    text = payload.decode("utf-8", errors="replace")
    if XML_ENTITY.search(text):
        return None
    try:
        return ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return None


def _tag(element: ElementTree.Element) -> str:
    return str(element.tag).rpartition("}")[2]


def _project_relative(layout: SuiteLayout) -> tuple[str, ...]:
    if layout.project == ".":
        return layout.paths
    return tuple(item[len(layout.project) + 1 :] for item in layout.paths)


def _binary(tree: Path, layout: SuiteLayout) -> Path:
    directory = tree / layout.project / MODULES / ".bin"
    for suffix in (".cmd", ".CMD", ""):
        candidate = directory / f"{layout.runner}{suffix}"
        if candidate.exists():
            return candidate
    return directory / layout.runner


def _holds(directory: Path, pattern: re.Pattern[str]) -> bool:
    return any(pattern.match(path.name) for path in directory.rglob("*") if path.is_file())


def _projects(tree: Path) -> tuple[str, ...]:
    found: list[str] = []
    for path in sorted(tree.rglob(MANIFEST)):
        if MODULES in path.parts or ".git" in path.parts:
            continue
        relative = path.parent.relative_to(tree).as_posix() or "."
        if relative.count("/") + 1 > MAX_SEARCH_DEPTH:
            continue
        found.append(relative)
    return tuple(sorted(found, key=lambda item: (item.count("/"), item)))


def _declared_runner(project: Path) -> str | None:
    manifest = project / MANIFEST
    try:
        payload = manifest.read_bytes()
    except OSError:
        return None
    parsed = parse_json(payload)
    if not parsed.ok():
        return None
    record = as_mapping(parsed.value)
    scripts = as_mapping(record.get("scripts"))
    declared = " ".join(str(value) for value in scripts.values())
    dependencies = {
        *as_mapping(record.get("devDependencies")),
        *as_mapping(record.get("dependencies")),
    }
    for runner in JAVASCRIPT_RUNNERS:
        if runner in dependencies or re.search(rf"\b{runner}\b", declared):
            return runner
    for name in os.listdir(project):
        for runner in JAVASCRIPT_RUNNERS:
            if name.startswith(f"{runner}.config."):
                return runner
    return None


def _javascript_paths(project: Path) -> tuple[str, ...]:
    found = [
        child.name
        for child in sorted(project.iterdir())
        if child.is_dir()
        and child.name in SUITE_DIRECTORY_NAMES
        and _holds(child, JAVASCRIPT_SUITE_FILE)
    ]
    if found:
        return tuple(found)
    return tuple(
        sorted(
            path.relative_to(project).as_posix()
            for path in project.rglob("*")
            if path.is_file()
            and JAVASCRIPT_SUITE_FILE.match(path.name)
            and MODULES not in path.parts
            and ".git" not in path.parts
        )
    )


def encode(cases: Sequence[SuiteCase]) -> bytes:
    return b"".join(
        json.dumps(case.to_mapping(), sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        for case in cases
    )


__all__ = [
    "JAVASCRIPT_RUNNERS",
    "MODULES",
    "PHP_COVERAGE",
    "PHP_REPORT",
    "PHP_RUNNER",
    "VENDOR",
    "RunnerPlan",
    "SuiteLayout",
    "command",
    "counts_of",
    "coverage_gap",
    "coverage_provider",
    "encode",
    "environment",
    "execution_failure",
    "javascript_layout",
    "junit_counts",
    "layout_of",
    "layouts_of",
    "link_dependencies",
    "missing_runner",
    "phpunit_layout",
    "python_layout",
    "read_cases",
    "report_path",
    "run_counts",
    "working_directory",
]
