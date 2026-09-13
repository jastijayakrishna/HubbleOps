from __future__ import annotations

import hashlib
import json
import shutil
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from hubbleops.core import toolchain
from hubbleops.core.errors import ToolingFailed, ToolingMissing, ToolingTimeout
from hubbleops.core.precise import read_index
from hubbleops.graph.indexers import (
    CONFIG_BASENAMES,
    ECMASCRIPT_INDEXER,
    ECMASCRIPT_LANGUAGES,
    IndexRun,
    ScipTypescript,
    derive_tsconfig,
    entry_script,
    restrict_to_first_party,
    stage,
    strip_jsonc,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "precise" / "ts_symbol_binding"
FIXTURE_INDEX = FIXTURE / "index.scip"
FIXTURE_REPO = FIXTURE / "repo"
FIXTURE_SOURCES = (
    "app/notify.ts",
    "app/report.ts",
    "lib/index.ts",
    "lib/mail.ts",
    "lib/transport.ts",
    "lib/version.ts",
)
FIXTURE_CONFIGS = ("package.json", "tsconfig.json")
FORCED: dict[str, object] = {
    "allowJs": True,
    "skipLibCheck": True,
    "typeRoots": [],
    "types": [],
}
SYNTHESIZED_CONFIG: dict[str, object] = {"compilerOptions": dict(FORCED)}
SYNTHESIZED_MANIFEST = {"name": "repository", "private": True, "version": "0.0.0"}

FAKE_SCRIPT = """
import json
import shutil
import sys
import time
from pathlib import Path

config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
args = sys.argv[2:]
if args == ["--version"]:
    print(config["version"])
    sys.exit(0)
cwd = Path.cwd()
snapshot = Path(config["snapshot"])
if snapshot.exists():
    shutil.rmtree(snapshot)
shutil.copytree(cwd, snapshot)
Path(config["record"]).write_text(
    json.dumps({"argv": args, "cwd": str(cwd)}), encoding="utf-8"
)
time.sleep(config["sleep"])
if config["exit"] != 0:
    print("first diagnostic line")
    print("indexer exploded", file=sys.stderr)
    sys.exit(config["exit"])
if config["write_output"]:
    shutil.copyfile(config["fixture"], args[args.index("--output") + 1])
sys.exit(0)
"""


@dataclass(frozen=True)
class FakeIndexer:
    executable: Path
    config: Path
    record: Path
    snapshot: Path

    def configure(
        self,
        version: str = "0.4.0",
        sleep: float = 0.0,
        exit_code: int = 0,
        write_output: bool = True,
    ) -> None:
        self.config.write_text(
            json.dumps(
                {
                    "version": version,
                    "sleep": sleep,
                    "exit": exit_code,
                    "write_output": write_output,
                    "fixture": str(FIXTURE_INDEX),
                    "record": str(self.record),
                    "snapshot": str(self.snapshot),
                }
            ),
            encoding="utf-8",
        )

    def argv(self) -> list[str]:
        return list(json.loads(self.record.read_text(encoding="utf-8"))["argv"])

    def cwd(self) -> Path:
        return Path(json.loads(self.record.read_text(encoding="utf-8"))["cwd"])

    def staged_json(self, relative: str) -> Mapping[str, Any]:
        return json.loads((self.snapshot / relative).read_text(encoding="utf-8"))

    def staged_tree(self) -> dict[str, bytes]:
        return _tree(self.snapshot)

    def sha256(self) -> str:
        return hashlib.sha256(self.executable.read_bytes()).hexdigest()


def _fake_indexer(directory: Path, name: str = "fake") -> FakeIndexer:
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / f"{name}_indexer.py"
    script.write_text(FAKE_SCRIPT, encoding="utf-8")
    config = directory / f"{name}.json"
    if sys.platform == "win32":
        executable = directory / f"{name}.cmd"
        executable.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" "{config}" %*\r\n', encoding="utf-8"
        )
    else:
        executable = directory / name
        executable.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}" "{config}" "$@"\n', encoding="utf-8"
        )
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    fake = FakeIndexer(executable, config, directory / f"{name}.record", directory / "snapshot")
    fake.configure()
    return fake


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _write(root: Path, relative: str, text: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _write(root, "src/a.ts", "export const a = 1;\n")
    _write(root, "packages/app/b.tsx", "export const b = <div />;\n")
    _write(root, "packages/plain/index.js", "module.exports = 1;\n")
    _write(
        root,
        "tsconfig.json",
        '{\n  // workspace preset lives in node_modules\n  "extends": "tsconfig/nextjs.json",\n'
        '  "compilerOptions": {\n    "strict": true,\n    "paths": {"@/lib/*": ["lib/*"]},\n'
        '  },\n  "include": ["src"],\n}\n',
    )
    _write(
        root,
        "packages/app/tsconfig.json",
        '{"extends": "./base.json", "compilerOptions": {"target": "es2020"}}\n',
    )
    _write(root, "packages/app/base.json", '{"compilerOptions": {"module": "esnext"}}\n')
    return root


WORKSPACE_SOURCES = ("packages/app/b.tsx", "packages/plain/index.js", "src/a.ts")
WORKSPACE_CONFIGS = ("packages/app/tsconfig.json", "tsconfig.json")


def _nothing_on_path(_: str) -> str | None:
    return None


def _runner(fake: FakeIndexer, timeout_seconds: float = 30.0) -> ScipTypescript:
    return ScipTypescript(executable=str(fake.executable), timeout_seconds=timeout_seconds)


def test_public_names_are_pinned() -> None:
    assert ECMASCRIPT_INDEXER == "scip-typescript"
    assert ECMASCRIPT_LANGUAGES == ("javascript", "tsx", "typescript")
    assert CONFIG_BASENAMES == ("tsconfig.json", "jsconfig.json", "package.json")
    assert ScipTypescript.name == ECMASCRIPT_INDEXER
    assert ScipTypescript.languages == ECMASCRIPT_LANGUAGES


def test_happy_path_returns_a_bound_index_run(tmp_path: Path) -> None:
    fake = _fake_indexer(tmp_path / "tool")

    run = _runner(fake).index(FIXTURE_REPO, FIXTURE_SOURCES, FIXTURE_CONFIGS)

    assert isinstance(run, IndexRun)
    assert run.indexer == "scip-typescript"
    assert run.identity == f"0.4.0+sha256:{fake.sha256()}"
    assert run.languages == ("typescript",)
    assert run.index.counts()["documents"] == 6
    assert run.index.tool_version == "0.4.0"
    assert run.dropped_documents == 0
    assert run.projects == (".",)
    assert run.rewritten_configs == ()
    assert all(run.index.covers(path) for path in FIXTURE_SOURCES)


def test_the_indexer_runs_in_a_staged_copy_with_output_outside_it(tmp_path: Path) -> None:
    fake = _fake_indexer(tmp_path / "tool")

    _runner(fake).index(FIXTURE_REPO, FIXTURE_SOURCES, FIXTURE_CONFIGS)

    argv = fake.argv()
    cwd = fake.cwd()
    assert argv[:3] == ["index", "--no-progress-bar", "--output"]
    assert argv[4:] == ["."]
    output = Path(argv[3])
    assert output.is_absolute()
    assert output.name == "index.scip"
    assert cwd.name == "repo"
    assert cwd.parent.name.startswith("hops-precise-")
    assert output.parent == cwd.parent
    assert cwd.resolve() != FIXTURE_REPO.resolve()
    assert not cwd.exists()
    staged = fake.staged_tree()
    for path in FIXTURE_SOURCES:
        assert staged[path] == (FIXTURE_REPO / path).read_bytes()
    assert staged["package.json"] == (FIXTURE_REPO / "package.json").read_bytes()
    derived = fake.staged_json("tsconfig.json")
    assert derived["compilerOptions"]["allowJs"] is True
    assert derived["compilerOptions"]["skipLibCheck"] is True
    assert derived["compilerOptions"]["paths"] == {"@/lib/*": ["lib/*"]}
    assert derived["include"] == ["**/*.ts"]


def test_nothing_is_written_into_the_repository(tmp_path: Path) -> None:
    fake = _fake_indexer(tmp_path / "tool")
    root = _workspace(tmp_path)
    before_fixture = _tree(FIXTURE_REPO)
    before_workspace = _tree(root)

    _runner(fake).index(FIXTURE_REPO, FIXTURE_SOURCES, FIXTURE_CONFIGS)
    _runner(fake).index(root, WORKSPACE_SOURCES, WORKSPACE_CONFIGS)

    assert _tree(FIXTURE_REPO) == before_fixture
    assert _tree(root) == before_workspace


def test_package_style_extends_is_dropped_and_relative_extends_is_kept(tmp_path: Path) -> None:
    fake = _fake_indexer(tmp_path / "tool")
    root = _workspace(tmp_path)

    run = _runner(fake).index(root, WORKSPACE_SOURCES, WORKSPACE_CONFIGS)

    assert run.projects == (".", "packages/app")
    assert run.rewritten_configs == ("tsconfig.json",)
    assert fake.argv()[4:] == [".", "packages/app"]
    top = fake.staged_json("tsconfig.json")
    assert "extends" not in top
    assert top["compilerOptions"] == {
        "paths": {"@/lib/*": ["lib/*"]},
        "strict": True,
        **FORCED,
    }
    assert top["include"] == ["src"]
    nested = fake.staged_json("packages/app/tsconfig.json")
    assert nested["extends"] == "./base.json"
    assert nested["compilerOptions"] == {"target": "es2020", **FORCED}
    base = fake.staged_json("packages/app/base.json")
    assert base["compilerOptions"] == {"module": "esnext", **FORCED}


def test_missing_configs_are_synthesized_only_inside_the_staging_directory(
    tmp_path: Path,
) -> None:
    fake = _fake_indexer(tmp_path / "tool")
    root = tmp_path / "bare"
    _write(root, "src/a.ts", "export const a = 1;\n")
    _write(root, "src/b.js", "module.exports = 1;\n")
    before = _tree(root)

    run = _runner(fake).index(root, ("src/a.ts", "src/b.js"), ())

    assert run.projects == (".",)
    assert run.rewritten_configs == ("tsconfig.json",)
    assert run.languages == ("javascript", "typescript")
    assert fake.staged_json("tsconfig.json") == SYNTHESIZED_CONFIG
    assert fake.staged_json("package.json") == SYNTHESIZED_MANIFEST
    assert set(fake.staged_tree()) == {"package.json", "src/a.ts", "src/b.js", "tsconfig.json"}
    assert _tree(root) == before


def test_stage_reports_projects_and_rewrites_without_running_anything(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _write(root, "tools/jsconfig.json", '{"compilerOptions": {"checkJs": true}}\n')
    _write(root, "tools/run.js", "console.log(1);\n")
    _write(root, "broken/tsconfig.json", '{"compilerOptions": {\n')
    _write(root, "broken/x.ts", "export {};\n")
    _write(root, "packages/app/package.json", '{"name": "app", "version": "2.0.0"}\n')
    destination = tmp_path / "staged"

    projects, rewritten = stage(
        root,
        (*WORKSPACE_SOURCES, "tools/run.js", "broken/x.ts"),
        (
            *WORKSPACE_CONFIGS,
            "tools/jsconfig.json",
            "broken/tsconfig.json",
            "packages/app/package.json",
        ),
        destination,
    )

    assert projects == (".", "broken", "packages/app", "tools")
    assert rewritten == ("broken/tsconfig.json", "tools/tsconfig.json", "tsconfig.json")
    staged = _tree(destination)
    assert "tools/jsconfig.json" not in staged
    tools = json.loads(staged["tools/tsconfig.json"])
    assert tools["compilerOptions"] == {"checkJs": True, **FORCED}
    assert json.loads(staged["broken/tsconfig.json"]) == SYNTHESIZED_CONFIG
    assert json.loads(staged["package.json"]) == SYNTHESIZED_MANIFEST
    assert staged["packages/app/package.json"] == (root / "packages/app/package.json").read_bytes()
    assert "packages/app/base.json" in staged


def test_stage_rejects_paths_that_leave_the_repository(tmp_path: Path) -> None:
    root = _workspace(tmp_path)

    with pytest.raises(ValueError, match="does not stay inside"):
        stage(root, ("../outside.ts",), (), tmp_path / "staged")
    with pytest.raises(ValueError, match="is not one of"):
        stage(root, ("src/a.ts",), ("src/settings.json",), tmp_path / "staged-2")


def test_strip_jsonc_removes_comments_and_trailing_commas() -> None:
    text = (
        "﻿{\n"
        '  // line comment\n  "a": "http://not.a/comment", /* block */\n'
        '  "b": [1, 2, ],\n  "c": "x \\" // still text",\n}\n'
    )

    assert json.loads(strip_jsonc(text)) == {
        "a": "http://not.a/comment",
        "b": [1, 2],
        "c": 'x " // still text',
    }


def test_derive_tsconfig_parses_jsonc_and_forces_options(tmp_path: Path) -> None:
    (tmp_path / "base.json").write_text("{}", encoding="utf-8")
    text = (
        '{\n  "extends": "./base.json", // kept\n'
        '  "compilerOptions": {"strict": true, "allowJs": false,},\n'
        '  "exclude": ["dist",],\n}\n'
    )

    derived, dropped = derive_tsconfig(text, tmp_path)

    assert dropped is False
    assert json.loads(derived) == {
        "extends": "./base.json",
        "compilerOptions": {"strict": True, **FORCED},
        "exclude": ["dist"],
    }
    assert derived == json.dumps(json.loads(derived), indent=2, sort_keys=True) + "\n"


def test_derive_tsconfig_drops_only_unresolvable_extends(tmp_path: Path) -> None:
    (tmp_path / "shared.json").write_text("{}", encoding="utf-8")

    derived, dropped = derive_tsconfig(
        '{"extends": ["@tsconfig/node18/tsconfig.json", "shared.json"]}', tmp_path
    )

    assert dropped is True
    assert json.loads(derived)["extends"] == ["shared.json"]
    derived, dropped = derive_tsconfig('{"extends": "./missing"}', tmp_path)
    assert dropped is True
    assert "extends" not in json.loads(derived)


def test_derive_tsconfig_refuses_unparsable_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not parseable"):
        derive_tsconfig('{"compilerOptions": {', tmp_path)
    with pytest.raises(ValueError, match="not an object"):
        derive_tsconfig("[1, 2]", tmp_path)
    with pytest.raises(ValueError, match="compilerOptions is not an object"):
        derive_tsconfig('{"compilerOptions": "strict"}', tmp_path)


def test_version_mismatch_between_index_and_binary_is_refused(tmp_path: Path) -> None:
    fake = _fake_indexer(tmp_path / "tool")
    fake.configure(version="9.9.9")

    with pytest.raises(ToolingFailed, match=r"9\.9\.9") as raised:
        _runner(fake).index(FIXTURE_REPO, FIXTURE_SOURCES, FIXTURE_CONFIGS)

    assert "0.4.0" in raised.value.detail
    assert raised.value.tool == "scip-typescript"


def test_nonzero_exit_carries_the_tool_output(tmp_path: Path) -> None:
    fake = _fake_indexer(tmp_path / "tool")
    fake.configure(exit_code=3)

    with pytest.raises(ToolingFailed) as raised:
        _runner(fake).index(FIXTURE_REPO, FIXTURE_SOURCES, FIXTURE_CONFIGS)

    assert "exit code 3" in raised.value.detail
    assert "indexer exploded" in raised.value.detail
    assert "first diagnostic line" in raised.value.detail


def test_missing_output_file_is_a_failure(tmp_path: Path) -> None:
    fake = _fake_indexer(tmp_path / "tool")
    fake.configure(write_output=False)

    with pytest.raises(ToolingFailed, match="no index was written"):
        _runner(fake).index(FIXTURE_REPO, FIXTURE_SOURCES, FIXTURE_CONFIGS)


def test_timeout_raises_tooling_timeout_and_releases_the_staging_directory(
    tmp_path: Path,
) -> None:
    fake = _fake_indexer(tmp_path / "tool")
    fake.configure(sleep=5.0)

    with pytest.raises(ToolingTimeout) as raised:
        _runner(fake, timeout_seconds=0.5).index(FIXTURE_REPO, FIXTURE_SOURCES, FIXTURE_CONFIGS)

    assert raised.value.tool == "scip-typescript"
    assert raised.value.seconds == 0.5
    assert not fake.cwd().exists()


def test_missing_executable_raises_tooling_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    absent = ScipTypescript(executable=str(tmp_path / "absent" / "scip-typescript"))
    with pytest.raises(ToolingMissing, match="TOOLING_MISSING: scip-typescript"):
        absent.index(FIXTURE_REPO, FIXTURE_SOURCES, FIXTURE_CONFIGS)

    monkeypatch.setattr(toolchain, "vendored_root", lambda: tmp_path / "no-vendor")
    monkeypatch.setattr(shutil, "which", _nothing_on_path)
    with pytest.raises(ToolingMissing, match="not found on PATH"):
        ScipTypescript().version()


def test_a_file_that_cannot_start_raises_tooling_missing(tmp_path: Path) -> None:
    executable = tmp_path / "not-runnable.bin"
    executable.write_bytes(b"\x00\x01\x02")

    with pytest.raises(ToolingMissing, match="is not executable"):
        ScipTypescript(executable=str(executable)).version()


def test_documents_outside_the_sources_are_dropped_and_counted(tmp_path: Path) -> None:
    fake = _fake_indexer(tmp_path / "tool")
    kept = FIXTURE_SOURCES[:-1]

    run = _runner(fake).index(FIXTURE_REPO, kept, FIXTURE_CONFIGS)

    assert run.dropped_documents == 1
    assert run.index.counts()["documents"] == 5
    assert not run.index.covers("lib/version.ts")
    assert all(run.index.covers(path) for path in kept)
    assert all(item.path != "lib/version.ts" for item in run.index.occurrences)
    committed = read_index(FIXTURE_INDEX.read_bytes())
    assert run.index.counts()["occurrences"] == sum(
        1
        for item in committed.occurrences
        if item.path != "lib/version.ts" and "symbol-binding-fixture" in item.symbol
    )
    assert run.dropped_occurrences == sum(
        1 for item in committed.occurrences if "symbol-binding-fixture" not in item.symbol
    )
    assert run.first_party == ("repository", "symbol-binding-fixture")


def test_non_ecmascript_sources_are_rejected_before_anything_runs(tmp_path: Path) -> None:
    fake = _fake_indexer(tmp_path / "tool")

    with pytest.raises(ValueError, match="does not index python"):
        _runner(fake).index(FIXTURE_REPO, ("lib/mail.ts", "tool.py"), ())
    with pytest.raises(ValueError, match="at least one source"):
        _runner(fake).index(FIXTURE_REPO, (), ())
    assert not fake.record.exists()


def test_entry_script_resolves_the_npm_shim_layout(tmp_path: Path) -> None:
    fake = _fake_indexer(tmp_path / "npm")
    main = tmp_path / "npm" / "node_modules" / "@sourcegraph" / "scip-typescript" / "dist"
    main = main / "src" / "main.js"
    main.parent.mkdir(parents=True)
    main.write_bytes(b"console.log('indexer');\n")
    binary = toolchain.locate("scip-typescript", str(fake.executable))

    assert entry_script(binary) == main
    assert _runner(fake).identity() == (
        "0.4.0+sha256:" + hashlib.sha256(main.read_bytes()).hexdigest()
    )


def test_entry_script_resolves_a_link_into_the_package(tmp_path: Path) -> None:
    package = tmp_path / "lib" / "node_modules" / "@sourcegraph" / "scip-typescript"
    main = package / "dist" / "src" / "main.js"
    main.parent.mkdir(parents=True)
    main.write_bytes(b"entry")
    launcher = package / "bin" / "scip-typescript"
    launcher.parent.mkdir()
    launcher.write_bytes(b"launcher")
    binary = toolchain.locate("scip-typescript", str(launcher))

    assert entry_script(binary) == main


def test_entry_script_falls_back_to_the_executable_itself(tmp_path: Path) -> None:
    fake = _fake_indexer(tmp_path / "tool")
    binary = toolchain.locate("scip-typescript", str(fake.executable))

    assert entry_script(binary) == fake.executable


def test_version_and_identity_are_cached_per_instance(tmp_path: Path) -> None:
    fake = _fake_indexer(tmp_path / "tool")
    runner = _runner(fake)

    first = runner.version()
    fake.configure(version="5.5.5")

    assert runner.version() == first == "0.4.0"
    assert runner.identity() == f"0.4.0+sha256:{fake.sha256()}"
    assert _runner(fake).version() == "5.5.5"


@pytest.mark.skipif(
    shutil.which("scip-typescript") is None, reason="scip-typescript is not on PATH"
)
def test_real_indexer_reproduces_the_committed_index() -> None:
    before = _tree(FIXTURE_REPO)
    committed = read_index(FIXTURE_INDEX.read_bytes())

    run = ScipTypescript().index(FIXTURE_REPO, FIXTURE_SOURCES, FIXTURE_CONFIGS)

    assert _tree(FIXTURE_REPO) == before
    assert run.index.tool_version == committed.tool_version
    first_party, dropped = restrict_to_first_party(committed, run.first_party)
    assert dropped > 0 and run.dropped_occurrences > 0
    assert {(item.path, item.symbol) for item in run.index.occurrences} == {
        (item.path, item.symbol) for item in first_party.occurrences
    }
    assert run.dropped_documents == 0
    assert run.identity.startswith(f"{committed.tool_version}+sha256:")
