from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from hubbleops.core.errors import ToolingMissing
from hubbleops.core.precise import read_index
from hubbleops.graph.imports import AstGrep, Definition, ImportGraph, build
from hubbleops.graph.indexers import (
    PYTHON_INDEXER,
    WINDOWS_START_FAILURE,
    ScipPython,
    ScipTypescript,
    runners,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "precise" / "py_symbol_binding"
REPO = FIXTURE / "repo"
PATHS = {
    "python": (
        "app/__init__.py",
        "app/notify.py",
        "app/report.py",
        "lib/__init__.py",
        "lib/mail.py",
        "lib/transport.py",
        "lib/version.py",
    )
}
FAKE_SCRIPT = """
import json
import os
import shutil
import sys
from pathlib import Path

fixture, record = sys.argv[1], sys.argv[2]
args = sys.argv[3:]
if args == ["--version"]:
    print("0.6.6")
    sys.exit(0)
Path(record).write_text(
    json.dumps(
        {
            "argv": args,
            "cwd": str(Path.cwd()),
            "staged": sorted(
                str(p.relative_to(Path.cwd())) for p in Path.cwd().rglob("*") if p.is_file()
            ),
            "node_options": os.environ.get("NODE_OPTIONS"),
        }
    ),
    encoding="utf-8",
)
shutil.copyfile(fixture, args[args.index("--output") + 1])
"""


def fake_indexer(directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "fake_python_indexer.py"
    script.write_text(FAKE_SCRIPT, encoding="utf-8")
    record = directory / "record.json"
    fixture = FIXTURE / "index.scip"
    if sys.platform == "win32":
        executable = directory / "fake.cmd"
        executable.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" "{fixture}" "{record}" %*\r\n',
            encoding="utf-8",
        )
        return executable, record
    executable = directory / "fake"
    executable.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{script}" "{fixture}" "{record}" "$@"\n',
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable, record


def definition_named(graph: ImportGraph, path: str, name: str) -> Definition:
    return next(item for item in graph.definitions if item.path == path and item.name == name)


def test_the_committed_python_index_reads_and_binds_callers_by_symbol() -> None:
    index = read_index((FIXTURE / "index.scip").read_bytes())
    assert (index.tool, index.tool_version) == ("scip-python", "0.6.6")
    assert index.counts()["documents"] == 7
    precise = build(REPO, PATHS, AstGrep(), (index,))
    loose = build(REPO, PATHS, AstGrep())
    transport = definition_named(precise, "lib/transport.py", "send")
    mail = definition_named(precise, "lib/mail.py", "send")
    assert {item.path for item in loose.callers(transport)} == {"app/notify.py", "app/report.py"}
    assert {item.path for item in precise.callers(transport)} == {"app/report.py"}
    assert {item.path for item in precise.callers(mail)} == {"app/notify.py"}
    definition = precise.precise_definition("app/report.py", "API_RELEASE")
    assert definition is not None
    assert (definition.path, definition.start_line) == ("lib/version.py", 0)
    assert loose.precise_definition("app/report.py", "API_RELEASE") is None


def test_the_python_indexer_refuses_windows_with_the_reason() -> None:
    runner = ScipPython(platform="win32")
    with pytest.raises(ToolingMissing) as caught:
        runner.version()
    assert caught.value.detail == WINDOWS_START_FAILURE


def test_the_python_indexer_stages_only_sources_and_sizes_the_heap(tmp_path: Path) -> None:
    executable, record = fake_indexer(tmp_path / "indexer")
    runner = ScipPython(str(executable), heap_megabytes=2048, platform="linux")
    run = runner.index(REPO, PATHS["python"], ())
    recorded = json.loads(record.read_text(encoding="utf-8"))
    assert recorded["node_options"] == "--max-old-space-size=2048"
    assert recorded["argv"][:3] == ["index", ".", "--project-name"]
    assert recorded["argv"][3:6] == ["repository", "--project-version", "0.0.0"]
    assert sorted(item.replace("\\", "/") for item in recorded["staged"]) == sorted(PATHS["python"])
    assert run.indexer == PYTHON_INDEXER
    assert run.languages == ("python",)
    assert run.projects == (".",)
    assert run.rewritten_configs == ()
    assert run.index.counts()["documents"] == 7
    assert run.identity.startswith("0.6.6+sha256:")


def test_runners_are_pinned_by_name_and_accept_executable_overrides() -> None:
    chosen = runners({"scip-typescript": "ts-here", "scip-python": "py-here"})
    assert [type(item) for item in chosen] == [ScipTypescript, ScipPython]
    assert [item.requested for item in chosen] == ["ts-here", "py-here"]
    assert [item.requested for item in runners()] == ["scip-typescript", "scip-python"]
