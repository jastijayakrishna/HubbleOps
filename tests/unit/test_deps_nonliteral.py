from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hubbleops.closure import source_closure
from hubbleops.core.canonical import content_id
from hubbleops.core.observer import ObserverContext
from hubbleops.core.records import as_mapping
from hubbleops.core.surface import SurfaceSpec
from hubbleops.observe import deps, ledger

SURFACE = SurfaceSpec.from_mapping(
    {
        "name": "p",
        "identifiers": ["probe-sdk"],
        "hosts": ["api.probe.test"],
        "package_names": ["probe-sdk", "probe/sdk-php", "Probe.Sdk", "com.probe:sdk"],
        "version_carriers": [],
        "request_languages": [],
        "sink_argument_positions": [],
        "config_env_keys": [],
    }
)

CTX = ObserverContext(
    provider="p",
    run_id=content_id({"run": "nonliteral"}),
    proof_scope_hash=content_id({"scope": "nonliteral"}),
    repo_sha=None,
    dependency_context_hash=None,
    surface=SURFACE,
)

BOUND_TO_A_NAME = (
    "from pathlib import Path\n"
    "from setuptools import setup\n"
    "\n"
    'Path("EXECUTED").write_text("the scan evaluated this module", encoding="utf-8")\n'
    "\n"
    'MAIN_REQUIREMENTS = ["probe-sdk==22.1.0"]\n'
    "\n"
    'setup(name="connector", install_requires=MAIN_REQUIREMENTS)\n'
)

HOLES: tuple[tuple[str, str, str], ...] = (
    ("requirements", "requirements.txt", "-r base.txt\n"),
    (
        "pyproject-dynamic",
        "pyproject.toml",
        '[project]\nname = "x"\nversion = "1"\ndynamic = ["dependencies"]\n',
    ),
    (
        "pyproject-url",
        "pyproject.toml",
        '[project]\nname = "x"\nversion = "1"\n'
        'dependencies = ["probe-sdk @ https://example.test/probe.whl"]\n',
    ),
    ("python-lock", "uv.lock", '[[package]]\nversion = "22.1.0"\n'),
    ("setup-cfg", "setup.cfg", "[options]\ninstall_requires = file: requirements.txt\n"),
    ("setup-py", "setup.py", BOUND_TO_A_NAME),
    ("package-json", "package.json", '{"dependencies": {"probe-sdk": {"version": "17.0.0"}}}'),
    (
        "package-lock",
        "package-lock.json",
        '{"lockfileVersion": 3, "packages": {"": {"name": "x"}, "node_modules/probe-sdk": {}}}',
    ),
    ("yarn-lock", "yarn.lock", 'probe-sdk@^1.0.0:\n  resolved "https://example.test/probe"\n'),
    ("pnpm-lock", "pnpm-lock.yaml", "lockfileVersion: '9.0'\npackages: []\n"),
    ("composer-json", "composer.json", '{"require": {"probe/sdk-php": ["1.0"]}}'),
    ("composer-lock", "composer.lock", '{"packages": [{"version": "1.0.0"}]}'),
    (
        "pom",
        "pom.xml",
        "<project><dependencies><dependency>"
        "<groupId>${probe.group}</groupId><artifactId>sdk</artifactId>"
        "</dependency></dependencies></project>",
    ),
    (
        "gradle",
        "build.gradle",
        'dependencies {\n    implementation "com.probe:sdk:$probeVersion"\n}\n',
    ),
    ("gradle-lock", "gradle.lockfile", "# a lockfile\ncom.probe:sdk\nempty=\n"),
    ("nuget-lock", "packages.lock.json", '{"dependencies": {"net8.0": {"Probe.Sdk": {}}}}'),
    ("packages-config", "packages.config", '<packages><package version="1.0" /></packages>'),
    (
        "csproj",
        "app.csproj",
        '<Project><ItemGroup><PackageReference Update="Probe.Sdk" Version="1.0" />'
        "</ItemGroup></Project>",
    ),
    ("go-mod", "go.mod", "module example.test/x\n\nrequire (\n\tprobe-sdk-without-a-version\n)\n"),
    ("go-sum", "go.sum", "this line is not a checksummed module\n"),
    ("gemfile", "Gemfile", 'source "https://rubygems.test"\ngemspec\n'),
)


def write(root: Path, files: dict[str, str]) -> source_closure.SourceClosure:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return source_closure.build(root)


def dependency_states(records: list[dict[str, Any]], state: str) -> list[dict[str, Any]]:
    return [
        record
        for record in records
        if record["claim_type"] == "dependency_state"
        and as_mapping(record["value"]).get("state") == state
    ]


def test_install_requires_bound_to_a_name_never_reports_zero_dependencies_as_fact(
    tmp_path: Path,
) -> None:
    closure = write(tmp_path, {"setup.py": BOUND_TO_A_NAME})
    resolution = deps.resolve(closure)
    manifest = next(item for item in resolution.files if item.path == "setup.py")
    declared = [item for item in resolution.dependencies if item.path == "setup.py"]
    holes = resolution.unresolved_for("setup.py")

    assert manifest.error is None
    assert declared or holes
    if declared:
        return
    assert not resolution.fully_read(manifest)
    hole = next(item for item in holes if item.field == "install_requires")
    assert hole.expression == "MAIN_REQUIREMENTS"
    assert hole.path == "setup.py"
    assert hole.line == 8
    assert hole.reason


def test_reading_a_manifest_never_executes_it(tmp_path: Path) -> None:
    closure = write(tmp_path, {"setup.py": BOUND_TO_A_NAME})
    deps.resolve(closure)
    assert not (tmp_path / "EXECUTED").exists()


def test_the_unevaluated_expression_reaches_the_evidence_the_coverage_rule_reads(
    tmp_path: Path,
) -> None:
    closure = write(tmp_path, {"setup.py": BOUND_TO_A_NAME})
    records = deps.scan(closure, CTX)
    unevaluated = dependency_states(records, deps.UNEVALUATED)
    assert unevaluated
    value = as_mapping(unevaluated[0]["value"])
    assert value["field"] == "install_requires"
    assert value["expression"] == "MAIN_REQUIREMENTS"
    assert value["reason"]
    assert unevaluated[0]["path"] == "setup.py"
    assert unevaluated[0]["line_start"] == 8


def test_a_lock_the_parser_cannot_fully_read_proves_no_package_absent(tmp_path: Path) -> None:
    closure = write(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "x"\nversion = "1"\ndependencies = ["httpx"]\n',
            "uv.lock": '[[package]]\nname = "httpx"\nversion = "0.27.0"\n\n[[package]]\n'
            'version = "22.1.0"\n',
        },
    )
    resolution = deps.resolve(closure)
    assert "python" not in resolution.locked_ecosystems()
    assert resolution.locks_for("python") == ()

    records = deps.scan(closure, CTX)
    absent = [
        record
        for record in records
        if record["claim_type"] == "sdk_installed"
        and as_mapping(record["value"]).get("state") == "ABSENT"
    ]
    assert absent == []
    assert dependency_states(records, "UNRESOLVED_ECOSYSTEM")
    assert dependency_states(records, deps.UNEVALUATED)


def test_a_clean_lock_still_proves_a_package_absent(tmp_path: Path) -> None:
    closure = write(tmp_path, {"uv.lock": '[[package]]\nname = "httpx"\nversion = "0.27.0"\n'})
    resolution = deps.resolve(closure)
    assert resolution.unresolved == ()
    assert resolution.locked_ecosystems() == ("python",)
    absent = [
        record
        for record in deps.scan(closure, CTX)
        if record["claim_type"] == "sdk_installed"
        and as_mapping(record["value"]).get("state") == "ABSENT"
    ]
    assert absent


def test_the_unevaluated_record_becomes_an_open_candidate_with_a_closing_instruction(
    tmp_path: Path,
) -> None:
    closure = write(tmp_path, {"setup.py": BOUND_TO_A_NAME})
    records = deps.scan(closure, CTX)
    built = ledger.build(
        provider="p",
        run_id=CTX.run_id,
        proof_scope_hash=CTX.proof_scope_hash,
        evidence=records,
        closure=closure,
        surface=SURFACE,
    )
    unevaluated = {record["id"] for record in dependency_states(records, deps.UNEVALUATED)}
    carriers = [
        entry["candidate"]
        for entry in built.export()["candidates"]
        if unevaluated & set(entry["candidate"]["evidence_ids"])
    ]
    assert carriers
    for candidate in carriers:
        assert candidate["status"] == "UNKNOWN"
        assert candidate["close_with"]
        assert "MAIN_REQUIREMENTS" in candidate["reason"]
    assert built.counts()["unexplained"] == 0


@pytest.mark.parametrize(("slug", "filename", "body"), HOLES, ids=[item[0] for item in HOLES])
def test_every_manifest_the_parser_cannot_fully_evaluate_names_its_expression(
    tmp_path: Path, slug: str, filename: str, body: str
) -> None:
    closure = write(tmp_path / slug, {filename: body})
    resolution = deps.resolve(closure)
    manifest = next(item for item in resolution.files if item.path == filename)
    assert manifest.error is None
    holes = resolution.unresolved_for(filename)
    assert holes
    for hole in holes:
        assert hole.expression
        assert hole.reason
        assert hole.path == filename
    assert not resolution.fully_read(manifest)
    assert dependency_states(deps.scan(closure, CTX), deps.UNEVALUATED)


def test_a_manifest_the_parser_fully_evaluates_raises_nothing(tmp_path: Path) -> None:
    closure = write(
        tmp_path,
        {
            "requirements.txt": "probe-sdk==22.1.0\nhttpx>=0.27\n# a comment\n--index-url https://x\n"
        },
    )
    resolution = deps.resolve(closure)
    assert resolution.unresolved == ()
    assert dependency_states(deps.scan(closure, CTX), deps.UNEVALUATED) == []


def test_the_resolution_hash_moves_when_an_expression_becomes_unreadable(tmp_path: Path) -> None:
    literal = deps.resolve(
        write(tmp_path / "literal", {"setup.py": 'setup(install_requires=["probe-sdk==22.1.0"])\n'})
    )
    bound = deps.resolve(write(tmp_path / "bound", {"setup.py": BOUND_TO_A_NAME}))
    assert literal.resolution_hash() != bound.resolution_hash()
    again = deps.resolve(write(tmp_path / "again", {"setup.py": BOUND_TO_A_NAME}))
    assert bound.resolution_hash() == again.resolution_hash()
