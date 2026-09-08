from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hubbleops.closure import source_closure
from hubbleops.closure.source_closure import Classification

SAFE_NAME = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="-_."),
    min_size=1,
    max_size=12,
).filter(lambda name: name not in (".", "..") and not name.endswith("."))

HOSTILE_NAME = st.sampled_from(
    [
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".hubbleops",
        "node_modules",
        "vendor",
        "build",
        ".git",
        "src",
        "tests",
    ]
)

NAME = st.one_of(HOSTILE_NAME, SAFE_NAME)

CONTENT = st.one_of(
    st.just(b""),
    st.just(b"\x00\x01\x02binary"),
    st.just(b"value = 1\n"),
    st.just(b"# @generated do not edit\n"),
    st.binary(max_size=64),
)

ENTRY = st.tuples(st.lists(NAME, min_size=1, max_size=4), NAME, CONTENT)


def materialize(root: Path, entries: list[tuple[list[str], str, bytes]]) -> None:
    for directories, name, content in entries:
        target = root
        for part in directories:
            target = target / part
        try:
            target.mkdir(parents=True, exist_ok=True)
            (target / name).write_bytes(content)
        except (OSError, ValueError):
            continue


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(ENTRY, max_size=12))
def test_the_closure_is_total_over_hostile_trees(
    entries: list[tuple[list[str], str, bytes]],
) -> None:
    with tempfile.TemporaryDirectory(prefix="hops-adversarial-") as raw:
        root = Path(raw)
        materialize(root, entries)
        closure = source_closure.build(root)

        paths = [entry.path for entry in closure.entries]
        assert len(paths) == len(set(paths)), "a path may carry exactly one classification"
        for entry in closure.entries:
            assert entry.reason, f"{entry.path} carries no reason"
            assert isinstance(entry.classification, Classification)
            assert not entry.path.startswith("/")
            assert ".." not in entry.path.split("/")
            assert "\\" not in entry.path


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(ENTRY, max_size=10))
def test_a_hostile_tree_hashes_identically_twice(
    entries: list[tuple[list[str], str, bytes]],
) -> None:
    with tempfile.TemporaryDirectory(prefix="hops-adversarial-") as raw:
        root = Path(raw)
        materialize(root, entries)
        first = source_closure.build(root)
        second = source_closure.build(root)
        assert first.tree_hash() == second.tree_hash()
        assert [entry.path for entry in first.entries] == [entry.path for entry in second.entries]


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(ENTRY, max_size=10))
def test_pruned_directories_are_never_enumerated_in_any_tree(
    entries: list[tuple[list[str], str, bytes]],
) -> None:
    with tempfile.TemporaryDirectory(prefix="hops-adversarial-") as raw:
        root = Path(raw)
        materialize(root, entries)
        closure = source_closure.build(root)

        enumerated = {entry.path for entry in closure.entries}
        for directory in closure.unenumerated_directories():
            assert directory in enumerated
            assert not any(path.startswith(f"{directory}/") for path in enumerated)
            assert directory in closure.search_exclusions()
