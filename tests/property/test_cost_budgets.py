from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hubbleops.closure import source_closure
from hubbleops.graph import imports

WINDOWS_COMMAND_LINE_LIMIT = 32_767


def test_the_argument_budget_stays_under_the_strictest_operating_system_limit() -> None:
    assert imports.ARGUMENT_BUDGET_CHARS < WINDOWS_COMMAND_LINE_LIMIT
    headroom = WINDOWS_COMMAND_LINE_LIMIT - imports.ARGUMENT_BUDGET_CHARS
    assert headroom >= 8_000, "leave room for the executable path, flags and the pattern"


@given(st.lists(st.text(alphabet="abcdefg/", min_size=1, max_size=60), max_size=400))
def test_every_path_is_emitted_exactly_once_across_batches(paths: list[str]) -> None:
    batched = [path for batch in imports.argument_batches(paths) for path in batch]
    assert batched == paths


@given(
    st.lists(st.text(alphabet="abc/", min_size=1, max_size=40), min_size=1, max_size=300),
    st.integers(min_value=64, max_value=4096),
)
def test_no_batch_exceeds_its_budget_unless_one_path_already_does(
    paths: list[str], budget: int
) -> None:
    for batch in imports.argument_batches(paths, budget):
        cost = sum(len(path) + 1 for path in batch)
        assert cost <= budget or len(batch) == 1


def test_a_batch_is_never_empty() -> None:
    assert list(imports.argument_batches([])) == []
    for batch in imports.argument_batches(["a", "b", "c"], 4):
        assert batch


def test_query_invocations_track_batches_not_file_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[int] = []

    def counted(
        self: imports.AstGrep, args: Sequence[str], cwd: Path | None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(len(args))
        return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")

    def resolved(requested: str) -> str:
        return requested

    monkeypatch.setattr(imports.AstGrep, "_run", counted)
    monkeypatch.setattr(imports, "_executable", resolved)
    runner = imports.AstGrep("ast-grep")

    paths = [f"src/package/module_number_{index:05d}.py" for index in range(5_000)]
    chars = sum(len(path) + 1 for path in paths)
    expected = len(list(imports.argument_batches(paths)))

    runner.query(tmp_path, paths, "python", imports.COMMON_QUERIES[0])

    assert len(calls) == expected
    assert expected > 1, "5,000 paths must not be attempted on a single command line"
    assert expected < len(paths), "batching must not degrade to one process per file"
    assert chars // imports.ARGUMENT_BUDGET_CHARS <= expected <= chars // 1_000


def test_the_closure_never_enumerates_a_local_environment(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    noisy = tmp_path / ".venv" / "lib" / "site-packages" / "dep"
    noisy.mkdir(parents=True)
    for index in range(200):
        (noisy / f"mod_{index}.py").write_text("import os\n", encoding="utf-8")

    closure = source_closure.build(tmp_path)

    probed = [entry for entry in closure.entries if entry.blob_sha is not None]
    assert len(closure.entries) < 10, f"walked more than the repository: {len(closure.entries)}"
    assert all(not entry.path.startswith(".venv/") for entry in probed)


def test_the_searcher_is_excluded_from_everything_the_closure_pruned(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "dep.py").write_text("value = 2\n", encoding="utf-8")
    (tmp_path / ".hubbleops" / "artifacts").mkdir(parents=True)
    (tmp_path / ".hubbleops" / "artifacts" / "run.json").write_text("{}\n", encoding="utf-8")

    closure = source_closure.build(tmp_path)
    enumerated = {entry.path for entry in closure.entries}
    pruned = set(closure.unenumerated_directories())

    assert pruned <= set(closure.search_exclusions())
    assert ".venv" in pruned
    assert ".hubbleops/artifacts" in pruned
    for directory in pruned:
        assert not any(path.startswith(f"{directory}/") for path in enumerated)


def _atom(path: str, start: int) -> imports.SyntaxMatch:
    return imports.SyntaxMatch(
        path=path,
        language="python",
        kind="identifier",
        text=f"name_{start}",
        range=imports.SourceRange(start, start + 4, 1, 1),
        captures=(),
    )


def _definition(path: str, start: int) -> imports.Definition:
    return imports.Definition(
        id=f"{path}:{start}",
        path=path,
        language="python",
        name=f"fn_{start}",
        parameters=(),
        range=imports.SourceRange(start, start + 100, 1, 4),
        asynchronous=False,
    )


def _graph(paths: int, per_path: int) -> imports.ImportGraph:
    atoms = tuple(
        _atom(f"src/module_{index}.py", offset * 8)
        for index in range(paths)
        for offset in range(per_path)
    )
    definitions = tuple(_definition(f"src/module_{index}.py", 0) for index in range(paths))
    return imports.ImportGraph(
        definitions=definitions,
        calls=(),
        assignments=(),
        imports=(),
        classes=(),
        decorators=(),
        registrations=(),
        atoms=atoms,
        comments=(),
        parse_errors=(),
        concatenations=(),
        formats=(),
    )


def test_the_graph_answers_path_lookups_without_scanning_the_repository() -> None:
    small = _graph(paths=4, per_path=6)
    large = _graph(paths=400, per_path=6)

    target = "src/module_1.py"
    assert len(small.atoms_in(target)) == 6
    assert len(large.atoms_in(target)) == len(small.atoms_in(target))
    assert len(large.definitions_in(target)) == len(small.definitions_in(target))
    assert all(item.path == target for item in large.atoms_in(target))
    assert large.atoms_in("src/absent.py") == ()

    resolved = large.definition(f"{target}:0")
    assert resolved is not None and resolved.path == target
    assert large.definition(None) is None
    assert large.definition("no-such-id") is None
