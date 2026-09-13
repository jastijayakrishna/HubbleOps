from __future__ import annotations

import json
from dataclasses import dataclass

from hypothesis import given, settings
from hypothesis import strategies as st

from hubbleops.core.errors import ToolingFailed
from hubbleops.core.precise import Occurrence, SymbolIndex, is_local, package_of, read_index

INT32_MAXIMUM = 2**31 - 1
WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_LENGTH_DELIMITED = 2
WIRE_FIXED32 = 5
PROJECT_ROOT_NOISE = b"file:///machine/specific/root"


@dataclass(frozen=True, slots=True)
class RawOccurrence:
    bounds: tuple[int, ...]
    symbol: str
    roles: int


@dataclass(frozen=True, slots=True)
class RawDocument:
    path: str
    occurrences: tuple[RawOccurrence, ...]


def varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def field_key(number: int, wire: int) -> bytes:
    return varint((number << 3) | wire)


def scalar(number: int, value: int) -> bytes:
    return field_key(number, WIRE_VARINT) + varint(value)


def delimited(number: int, payload: bytes) -> bytes:
    return field_key(number, WIRE_LENGTH_DELIMITED) + varint(len(payload)) + payload


def encode_occurrence(occurrence: RawOccurrence, noise: bool = False) -> bytes:
    packed = b"".join(varint(value) for value in occurrence.bounds)
    body = (
        delimited(1, packed)
        + delimited(2, occurrence.symbol.encode("utf-8"))
        + scalar(3, occurrence.roles)
    )
    if noise:
        body = (
            scalar(99, 1)
            + body
            + field_key(97, WIRE_FIXED64)
            + bytes(8)
            + field_key(96, WIRE_FIXED32)
            + bytes(4)
        )
    return body


def encode_document(document: RawDocument, noise: bool = False) -> bytes:
    body = delimited(1, document.path.encode("utf-8"))
    if noise:
        body += delimited(98, b"unknown document field")
    for occurrence in document.occurrences:
        body += delimited(2, encode_occurrence(occurrence, noise))
    if noise:
        body += delimited(4, b"typescript")
    return body


def encode_index(
    tool: str,
    tool_version: str,
    documents: tuple[RawDocument, ...],
    noise: bool = False,
) -> bytes:
    tool_info = delimited(1, tool.encode("utf-8")) + delimited(2, tool_version.encode("utf-8"))
    metadata = (
        scalar(1, 0) + delimited(2, tool_info) + delimited(3, PROJECT_ROOT_NOISE) + scalar(4, 1)
    )
    body = delimited(1, metadata)
    if noise:
        body += scalar(99, 7)
    for document in documents:
        body += delimited(2, encode_document(document, noise))
    if noise:
        body += delimited(3, delimited(1, b"external symbol"))
    return body


def normalize_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def expected_occurrence(path: str, raw: RawOccurrence) -> Occurrence:
    if len(raw.bounds) == 3:
        start_line, start_column, end_column = raw.bounds
        end_line = start_line
    else:
        start_line, start_column, end_line, end_column = raw.bounds
    return Occurrence(
        normalize_path(path),
        start_line,
        start_column,
        end_line,
        end_column,
        raw.symbol,
        raw.roles,
    )


def expected_index(tool: str, tool_version: str, documents: tuple[RawDocument, ...]) -> SymbolIndex:
    return SymbolIndex(
        tool=tool,
        tool_version=tool_version,
        documents=tuple(sorted({normalize_path(item.path) for item in documents})),
        occurrences=tuple(
            sorted(
                {
                    expected_occurrence(document.path, occurrence)
                    for document in documents
                    for occurrence in document.occurrences
                }
            )
        ),
    )


SEGMENT_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789_."
COMPONENT_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789-_.@/`()#"
segments = st.text(alphabet=SEGMENT_ALPHABET, min_size=1, max_size=8)
words = st.text(alphabet=COMPONENT_ALPHABET, min_size=1, max_size=6)
tool_names = st.text(alphabet=COMPONENT_ALPHABET, max_size=12)


@st.composite
def raw_paths(draw: st.DrawFn) -> str:
    parts = draw(st.lists(segments, min_size=1, max_size=4))
    separators = draw(
        st.lists(st.sampled_from("/\\"), min_size=len(parts) - 1, max_size=len(parts) - 1)
    )
    prefix = draw(st.sampled_from(["", "./", ".\\", "././"]))
    text = parts[0]
    for separator, part in zip(separators, parts[1:], strict=True):
        text += separator + part
    return prefix + text


@st.composite
def components(draw: st.DrawFn) -> str:
    head = draw(words)
    if draw(st.booleans()):
        return f"{head} {draw(words)}"
    return head


def escape_component(component: str) -> str:
    return component.replace(" ", "  ")


@st.composite
def package_symbols(draw: st.DrawFn) -> tuple[str, str, str]:
    scheme = draw(components())
    manager = draw(components())
    name = draw(st.one_of(components(), st.just(".")))
    version = draw(st.one_of(components(), st.just(".")))
    descriptors = draw(st.lists(components(), min_size=1, max_size=3))
    symbol = " ".join(
        escape_component(part) for part in (scheme, manager, name, version, *descriptors)
    )
    return symbol, name, version


local_symbols = st.integers(min_value=0, max_value=10_000).map(lambda number: f"local {number}")
symbols = st.one_of(local_symbols, package_symbols().map(lambda item: item[0]))
coordinates = st.one_of(st.integers(min_value=0, max_value=200), st.just(INT32_MAXIMUM))


@st.composite
def raw_bounds(draw: st.DrawFn) -> tuple[int, ...]:
    start_line = draw(coordinates)
    start_column = draw(coordinates)
    end_column = draw(coordinates)
    if draw(st.booleans()):
        return (start_line, start_column, end_column)
    end_line = draw(st.integers(min_value=start_line, max_value=INT32_MAXIMUM))
    return (start_line, start_column, end_line, end_column)


raw_occurrences = st.builds(
    RawOccurrence,
    bounds=raw_bounds(),
    symbol=symbols,
    roles=st.integers(min_value=0, max_value=127),
)
raw_documents = st.builds(
    RawDocument,
    path=raw_paths(),
    occurrences=st.lists(raw_occurrences, max_size=6).map(tuple),
)
raw_indexes = st.tuples(tool_names, tool_names, st.lists(raw_documents, max_size=6).map(tuple))


@settings(max_examples=100, deadline=None)
@given(raw_indexes)
def test_reading_an_encoded_index_yields_the_normalized_content(
    raw: tuple[str, str, tuple[RawDocument, ...]],
) -> None:
    tool, tool_version, documents = raw
    index = read_index(encode_index(tool, tool_version, documents))
    assert index == expected_index(tool, tool_version, documents)
    assert index.documents == tuple(sorted(set(index.documents)))
    assert index.occurrences == tuple(sorted(set(index.occurrences)))
    assert all("\\" not in path and not path.startswith("./") for path in index.documents)


@settings(max_examples=100, deadline=None)
@given(raw_indexes)
def test_serialization_ignores_unknown_fields_and_project_root(
    raw: tuple[str, str, tuple[RawDocument, ...]],
) -> None:
    tool, tool_version, documents = raw
    plain = read_index(encode_index(tool, tool_version, documents, noise=False))
    noisy = read_index(encode_index(tool, tool_version, documents, noise=True))
    assert plain == noisy
    assert plain.serialize() == noisy.serialize()
    assert plain.serialize().endswith(b"\n")
    assert PROJECT_ROOT_NOISE not in plain.serialize()
    decoded = json.loads(plain.serialize())
    assert decoded == {
        "tool": tool,
        "tool_version": tool_version,
        "documents": list(plain.documents),
        "occurrences": [item.to_mapping() for item in plain.occurrences],
    }


@settings(max_examples=100, deadline=None)
@given(raw_indexes)
def test_lookups_agree_with_the_flat_occurrence_list(
    raw: tuple[str, str, tuple[RawDocument, ...]],
) -> None:
    tool, tool_version, documents = raw
    index = read_index(encode_index(tool, tool_version, documents))
    for path in index.documents:
        assert index.covers(path)
        assert tuple(index.occurrences_in(path)) == tuple(
            item for item in index.occurrences if item.path == path
        )
    for item in index.occurrences:
        definitions = [
            other
            for other in index.occurrences
            if other.symbol == item.symbol and other.is_definition()
        ]
        assert index.definition_of(item.symbol) == (definitions[0] if definitions else None)
        assert index.defines_in_index(item.symbol) == bool(definitions)
        assert index.references(item.symbol) == tuple(
            other
            for other in index.occurrences
            if other.symbol == item.symbol and not other.is_definition()
        )
        if item.contains(item.start_line, item.start_column):
            found = index.symbol_at(item.path, item.start_line, item.start_column)
            containing = [
                other
                for other in index.occurrences_in(item.path)
                if other.contains(item.start_line, item.start_column)
            ]
            narrowest = min(other.span() for other in containing)
            assert found in {other.symbol for other in containing if other.span() == narrowest}
    counts = index.counts()
    assert counts["documents"] == len(index.documents)
    assert counts["occurrences"] == len(index.occurrences)
    assert counts["definitions"] + counts["references"] == counts["occurrences"]


@settings(max_examples=100, deadline=None)
@given(package_symbols())
def test_package_of_recovers_the_escaped_name_and_version(symbol: tuple[str, str, str]) -> None:
    text, name, version = symbol
    assert not is_local(text)
    if name == ".":
        assert package_of(text) is None
    else:
        assert package_of(text) == (name, version)


@settings(max_examples=100, deadline=None)
@given(local_symbols)
def test_local_symbols_carry_no_package(symbol: str) -> None:
    assert is_local(symbol)
    assert package_of(symbol) is None


@settings(max_examples=100, deadline=None)
@given(st.binary(max_size=512))
def test_read_index_is_total_over_arbitrary_bytes(payload: bytes) -> None:
    try:
        first = read_index(payload)
    except ToolingFailed as error:
        assert str(error).startswith("TOOLING_FAILED: scip")
        return
    second = read_index(payload)
    assert first == second
    assert first.serialize() == second.serialize()
