from __future__ import annotations

from pathlib import Path
from typing import Any

from hubbleops.closure import source_closure
from hubbleops.core.canonical import content_id
from hubbleops.core.observer import ObserverContext
from hubbleops.core.surface import SurfaceSpec
from hubbleops.observe import deps

CTX = ObserverContext(
    provider="p",
    run_id=content_id({"run": 1}),
    proof_scope_hash=content_id({"scope": 1}),
    repo_sha=None,
    dependency_context_hash=None,
)

SURFACE = SurfaceSpec.from_mapping(
    {
        "name": "p",
        "identifiers": ["probe-sdk"],
        "hosts": ["api.probe.test"],
        "package_names": [
            "probe-sdk",
            "probe/sdk-php",
            "Probe.Sdk",
            "com.probe:sdk",
            "probe.example/sdk",
            "probe_sdk_gem",
        ],
        "version_carriers": [],
        "request_languages": [],
        "sink_argument_positions": [],
        "config_env_keys": [],
    }
)


def write(root: Path, files: dict[str, str]) -> source_closure.SourceClosure:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return source_closure.build(root)


def resolved(root: Path, files: dict[str, str]) -> dict[str, str | None]:
    resolution = deps.resolve(write(root, files))
    return {item.name: item.version for item in resolution.dependencies}


def claims(closure: source_closure.SourceClosure) -> list[dict[str, Any]]:
    return deps.scan(closure, SURFACE, CTX)


def test_requirements_pins_and_ranges(tmp_path: Path) -> None:
    found = resolved(
        tmp_path,
        {"requirements.txt": "probe-sdk==22.1.0\nrequests>=2.31\n# comment\n-r other.txt\n"},
    )
    assert found == {"probe-sdk": "22.1.0", "requests": None}


def test_pyproject_project_and_poetry_tables(tmp_path: Path) -> None:
    found = resolved(
        tmp_path,
        {
            "pyproject.toml": (
                '[project]\nname = "x"\nversion = "1"\n'
                'dependencies = ["probe-sdk==22.1.0", "httpx>=0.27"]\n\n'
                "[tool.poetry.dependencies]\n"
                'legacy-lib = "^1.2"\n'
            )
        },
    )
    assert found["probe-sdk"] == "22.1.0"
    assert found["httpx"] is None
    assert found["legacy-lib"] is None


def test_poetry_and_uv_locks_resolve_versions(tmp_path: Path) -> None:
    body = '[[package]]\nname = "probe-sdk"\nversion = "22.1.0"\n'
    assert resolved(tmp_path / "a", {"poetry.lock": body}) == {"probe-sdk": "22.1.0"}
    assert resolved(tmp_path / "b", {"uv.lock": body}) == {"probe-sdk": "22.1.0"}


def test_package_json_and_lockfile_v3(tmp_path: Path) -> None:
    resolution = deps.resolve(
        write(
            tmp_path,
            {
                "package.json": '{"dependencies": {"probe-sdk": "^17.0.0"}}',
                "package-lock.json": (
                    '{"lockfileVersion": 3, "packages": {"": {"name": "x"}, '
                    '"node_modules/probe-sdk": {"version": "17.1.0"}}}'
                ),
            },
        )
    )
    entries = {
        (item.source_kind, item.version)
        for item in resolution.dependencies
        if item.name == "probe-sdk"
    }
    assert entries == {("lock", "17.1.0"), ("manifest", None)}


def test_yarn_and_pnpm_locks(tmp_path: Path) -> None:
    yarn = resolved(
        tmp_path / "a",
        {"yarn.lock": '"probe-sdk@^17.0.0":\n  version "17.1.0"\n  resolved "https://x"\n'},
    )
    assert yarn == {"probe-sdk": "17.1.0"}
    pnpm = resolved(
        tmp_path / "b",
        {
            "pnpm-lock.yaml": (
                "lockfileVersion: '6.0'\npackages:\n  /probe-sdk/17.1.0:\n    dev: false\n"
            )
        },
    )
    assert pnpm == {"probe-sdk": "17.1.0"}


def test_composer_manifest_and_lock(tmp_path: Path) -> None:
    found = resolved(
        tmp_path,
        {
            "composer.json": '{"require": {"probe/sdk-php": "^22.0"}}',
            "composer.lock": '{"packages": [{"name": "probe/sdk-php", "version": "22.1.0"}]}',
        },
    )
    assert found["probe/sdk-php"] == "22.1.0"


def test_maven_gradle_and_dotnet(tmp_path: Path) -> None:
    maven = resolved(
        tmp_path / "a",
        {
            "pom.xml": (
                '<project xmlns="http://maven.apache.org/POM/4.0.0"><dependencies><dependency>'
                "<groupId>com.probe</groupId><artifactId>sdk</artifactId><version>22.1.0</version>"
                "</dependency></dependencies></project>"
            )
        },
    )
    assert maven == {"com.probe:sdk": "22.1.0"}
    gradle = resolved(
        tmp_path / "b",
        {"build.gradle": "dependencies {\n  implementation 'com.probe:sdk:22.1.0'\n}\n"},
    )
    assert gradle == {"com.probe:sdk": "22.1.0"}
    dotnet = resolved(
        tmp_path / "c",
        {
            "app.csproj": (
                '<Project><ItemGroup><PackageReference Include="Probe.Sdk" Version="22.1.0" />'
                "</ItemGroup></Project>"
            )
        },
    )
    assert dotnet == {"Probe.Sdk": "22.1.0"}


def test_go_and_ruby(tmp_path: Path) -> None:
    go = resolved(
        tmp_path / "a",
        {
            "go.mod": (
                "module example.test/x\n\ngo 1.22\n\nrequire (\n\tprobe.example/sdk v22.1.0\n)\n"
            )
        },
    )
    assert go == {"probe.example/sdk": "v22.1.0"}
    ruby = resolved(
        tmp_path / "b",
        {"Gemfile.lock": "GEM\n  specs:\n    probe_sdk_gem (22.1.0)\n      rack (~> 2.0)\n"},
    )
    assert ruby["probe_sdk_gem"] == "22.1.0"


def test_name_matching_normalises_underscores_and_case(tmp_path: Path) -> None:
    closure = write(tmp_path, {"requirements.txt": "Probe_SDK==22.1.0\n"})
    matched = [record for record in claims(closure) if record["claim_type"] == "sdk_installed"]
    assert [record["provider_subject"] for record in matched] == ["probe-sdk"]


def test_a_lock_proves_absence_but_a_manifest_does_not(tmp_path: Path) -> None:
    with_lock = claims(
        write(
            tmp_path / "a",
            {
                "package.json": '{"dependencies": {"left-pad": "1.3.0"}}',
                "package-lock.json": (
                    '{"lockfileVersion": 3, "packages": '
                    '{"node_modules/left-pad": {"version": "1.3.0"}}}'
                ),
            },
        )
    )
    states = {record["value"]["state"] for record in with_lock}
    assert "ABSENT" in states

    manifest_only = claims(
        write(tmp_path / "b", {"package.json": '{"dependencies": {"left-pad": "1.3.0"}}'})
    )
    states = {record["value"]["state"] for record in manifest_only}
    assert states == {"UNRESOLVED_ECOSYSTEM"}


def test_no_manifest_at_all_is_unknown_never_absent(tmp_path: Path) -> None:
    closure = write(tmp_path, {"src/app.py": "value = 1\n"})
    records = claims(closure)
    assert [record["value"]["state"] for record in records] == ["NO_MANIFEST"]
    assert records[0]["path"] == "."


def test_an_unparsable_manifest_is_recorded_not_dropped(tmp_path: Path) -> None:
    closure = write(tmp_path, {"composer.json": '{"require": {"probe/sdk-php": "1.0",}}'})
    resolution = deps.resolve(closure)
    assert [item.error is not None for item in resolution.files] == [True]
    records = claims(closure)
    assert records[0]["value"]["state"] == "UNPARSABLE"


def test_xml_entities_are_refused_rather_than_expanded(tmp_path: Path) -> None:
    closure = write(
        tmp_path,
        {"pom.xml": '<!DOCTYPE p [<!ENTITY a "x">]>\n<project><dependencies/></project>'},
    )
    resolution = deps.resolve(closure)
    assert "entities" in (resolution.files[0].error or "")


def test_resolution_hash_is_deterministic_and_content_bound(tmp_path: Path) -> None:
    files = {"requirements.txt": "probe-sdk==22.1.0\n"}
    first = deps.resolve(write(tmp_path / "a", files))
    second = deps.resolve(write(tmp_path / "b", files))
    assert first.resolution_hash() == second.resolution_hash()
    changed = deps.resolve(write(tmp_path / "c", {"requirements.txt": "probe-sdk==22.2.0\n"}))
    assert changed.resolution_hash() != first.resolution_hash()


def test_manifest_recognition_covers_every_declared_ecosystem() -> None:
    for filename in deps.EXACT_FILENAMES:
        assert deps.is_manifest(f"nested/dir/{filename}")
    assert deps.is_manifest("requirements-dev.txt")
    assert deps.is_manifest("src/App.csproj")
    assert not deps.is_manifest("src/app.py")
