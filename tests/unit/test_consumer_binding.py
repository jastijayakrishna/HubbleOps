from __future__ import annotations

import re
from pathlib import Path

from hubbleops.core.verification import ChangeSet, SubjectChange, SubjectChangeKind
from hubbleops.graph import ImportGraph, SourceRange, SyntaxMatch
from hubbleops.verify import behavior

TOKEN = re.compile(r'"[^"\n]*"|[A-Za-z_]\w*')
ENUM = "enum_value.enums.mode.ModeEnum.Mode"
OTHER_ENUM = "enum_value.enums.other.OtherEnum.Other"
FILE = "reads.py"


def _change(
    subject: str, change: SubjectChangeKind, replacement: str | None = None
) -> SubjectChange:
    return SubjectChange(
        subject=subject,
        change=change,
        replacement=replacement,
        kind="PROVEN",
        reason="fixture",
    )


def _changes() -> ChangeSet:
    return ChangeSet(
        from_version="v24",
        to_version="v25",
        pair_hash="c" * 64,
        changes=(
            _change(f"{ENUM}.BID_HIGHER", "REMOVED"),
            _change(f"{ENUM}.UNKNOWN", "REMOVED"),
            _change(f"{OTHER_ENUM}.UNKNOWN", "ADDED"),
            _change("lifecycle_goal.customer_id", "REMOVED"),
            _change("campaigns.legacy", "REMOVED", "campaigns.name"),
            _change("campaigns.name", "ADDED"),
        ),
    )


def _atoms(path: str, text: str) -> tuple[SyntaxMatch, ...]:
    encoded = text.encode("utf-8")
    found: list[SyntaxMatch] = []
    for token in TOKEN.finditer(text):
        start = len(text[: token.start()].encode("utf-8"))
        end = start + len(token.group(0).encode("utf-8"))
        line = encoded.count(b"\n", 0, start) + 1
        found.append(
            SyntaxMatch(
                path=path,
                language="python",
                kind="string" if token.group(0).startswith('"') else "identifier",
                text=token.group(0),
                range=SourceRange(start, end, line, line),
                captures=(),
            )
        )
    return tuple(found)


def _graph(root: Path, files: dict[str, str]) -> ImportGraph:
    atoms: list[SyntaxMatch] = []
    for path, text in sorted(files.items()):
        (root / path).write_bytes(text.encode("utf-8"))
        atoms.extend(_atoms(path, text))
    return ImportGraph((), (), (), (), (), (), (), tuple(atoms), (), (), (), ())


def _hits(root: Path, files: dict[str, str]) -> tuple[str, ...]:
    graph = _graph(root, files)
    return behavior.consumers(graph, _changes(), root, frozenset(files)).hits


def test_a_leaf_read_with_no_removed_parent_in_the_file_is_not_a_hit(tmp_path: Path) -> None:
    source = "def ids(rows):\n    return [row.customer_id for row in rows]\n"
    assert _hits(tmp_path, {FILE: source}) == ()


def test_a_leaf_supplied_as_a_keyword_argument_is_a_request_not_a_read(tmp_path: Path) -> None:
    source = (
        "def run(service, lifecycle_goal):\n    return service.search(customer_id=lifecycle_goal)\n"
    )
    assert _hits(tmp_path, {FILE: source}) == ()


def test_a_removed_enum_value_read_next_to_its_enum_name_is_a_hit(tmp_path: Path) -> None:
    source = "def pick(client):\n    Mode = client.enums.Mode\n    return Mode.BID_HIGHER\n"
    assert _hits(tmp_path, {FILE: source}) == (f"Mode.BID_HIGHER at {FILE}:3",)


def test_a_removed_leaf_another_surviving_subject_shares_is_ambiguous(tmp_path: Path) -> None:
    source = "def pick(client):\n    Mode = client.enums.Mode\n    return Mode.UNKNOWN\n"
    assert _hits(tmp_path, {FILE: source}) == ()


def test_a_removed_leaf_read_off_a_removed_parent_field_is_a_hit(tmp_path: Path) -> None:
    source = "def ids(rows, lifecycle_goal):\n    return [row.customer_id for row in rows]\n"
    assert _hits(tmp_path, {FILE: source}) == (f"lifecycle_goal.customer_id at {FILE}:2",)


def test_the_parent_must_be_referenced_in_the_same_file(tmp_path: Path) -> None:
    files = {
        FILE: "def ids(rows):\n    return [row.customer_id for row in rows]\n",
        "other.py": "lifecycle_goal = None\n",
    }
    assert _hits(tmp_path, files) == ()


def test_a_parent_named_inside_a_request_literal_binds_the_leaf(tmp_path: Path) -> None:
    source = (
        'QUERY = "resource=campaigns fields=campaigns.id,campaigns.name"\n'
        "\n"
        "def labels(rows):\n"
        "    return [row.legacy for row in rows]\n"
    )
    assert _hits(tmp_path, {FILE: source}) == (f"campaigns.legacy at {FILE}:4",)


def test_a_qualified_subject_is_a_hit_without_any_binding(tmp_path: Path) -> None:
    source = 'def labels(rows):\n    return [row["campaigns.legacy"] for row in rows]\n'
    assert _hits(tmp_path, {FILE: source}) == (f"campaigns.legacy at {FILE}:2",)


def test_a_bare_leaf_in_a_file_no_provider_evidence_reaches_is_not_a_hit(tmp_path: Path) -> None:
    source = "def pick(client):\n    Mode = client.enums.Mode\n    return Mode.BID_HIGHER\n"
    graph = _graph(tmp_path, {FILE: source})
    assert behavior.consumers(graph, _changes(), tmp_path, frozenset()).hits == ()
