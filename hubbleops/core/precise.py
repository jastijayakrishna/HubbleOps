from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from hubbleops.core.canonical import canonical_text
from hubbleops.core.errors import ToolingFailed

TOOL = "scip"
ROLE_DEFINITION = 1
INT32_MAXIMUM = 2**31 - 1
VARINT_MAXIMUM_BYTES = 10
WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_LENGTH_DELIMITED = 2
WIRE_FIXED32 = 5
FIXED_WIDTHS = {WIRE_FIXED64: 8, WIRE_FIXED32: 4}
LOCAL_PREFIX = "local "
PACKAGE_PLACEHOLDER = "."
PACKAGE_COMPONENTS = 4
SIMPLE_IDENTIFIER_EXTRA = "_+-$"
DESCRIPTOR_SUFFIXES = "/#.:!"
BRACKETS = {"(": ")", "[": "]"}


@dataclass(frozen=True, order=True, slots=True)
class Occurrence:
    path: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    symbol: str
    roles: int

    def is_definition(self) -> bool:
        return bool(self.roles & ROLE_DEFINITION)

    def contains(self, line: int, column: int) -> bool:
        return (
            (self.start_line, self.start_column)
            <= (line, column)
            < (
                self.end_line,
                self.end_column,
            )
        )

    def span(self) -> tuple[int, int]:
        return (self.end_line - self.start_line, self.end_column - self.start_column)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "start_line": self.start_line,
            "start_column": self.start_column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "symbol": self.symbol,
            "roles": self.roles,
        }


@dataclass(frozen=True, slots=True)
class _SymbolLookup:
    by_path: dict[str, tuple[Occurrence, ...]]
    definitions: dict[str, Occurrence]
    references: dict[str, tuple[Occurrence, ...]]


@dataclass(frozen=True, slots=True)
class SymbolIndex:
    tool: str
    tool_version: str
    documents: tuple[str, ...]
    occurrences: tuple[Occurrence, ...]
    _lookup: _SymbolLookup = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        by_path: dict[str, list[Occurrence]] = {}
        definitions: dict[str, Occurrence] = {}
        references: dict[str, list[Occurrence]] = {}
        for item in self.occurrences:
            by_path.setdefault(item.path, []).append(item)
            if item.is_definition():
                definitions.setdefault(item.symbol, item)
            else:
                references.setdefault(item.symbol, []).append(item)
        object.__setattr__(
            self,
            "_lookup",
            _SymbolLookup(
                by_path={path: tuple(items) for path, items in by_path.items()},
                definitions=definitions,
                references={symbol: tuple(items) for symbol, items in references.items()},
            ),
        )

    def covers(self, path: str) -> bool:
        return path in self.documents

    def occurrences_in(self, path: str) -> Sequence[Occurrence]:
        return self._lookup.by_path.get(path, ())

    def occurrence_at(self, path: str, line: int, column: int) -> Occurrence | None:
        containing = [item for item in self.occurrences_in(path) if item.contains(line, column)]
        if not containing:
            return None
        return min(containing, key=Occurrence.span)

    def symbol_at(self, path: str, line: int, column: int) -> str | None:
        found = self.occurrence_at(path, line, column)
        return None if found is None else found.symbol

    def definition_of(self, symbol: str) -> Occurrence | None:
        return self._lookup.definitions.get(symbol)

    def references(self, symbol: str) -> tuple[Occurrence, ...]:
        return self._lookup.references.get(symbol, ())

    def defines_in_index(self, symbol: str) -> bool:
        return self.definition_of(symbol) is not None

    def counts(self) -> dict[str, int]:
        definitions = sum(1 for item in self.occurrences if item.is_definition())
        return {
            "documents": len(self.documents),
            "occurrences": len(self.occurrences),
            "definitions": definitions,
            "references": len(self.occurrences) - definitions,
        }

    def serialize(self) -> bytes:
        value = {
            "tool": self.tool,
            "tool_version": self.tool_version,
            "documents": list(self.documents),
            "occurrences": [item.to_mapping() for item in self.occurrences],
        }
        return f"{canonical_text(value)}\n".encode()


def read_index(payload: bytes) -> SymbolIndex:
    tool = ""
    tool_version = ""
    documents: set[str] = set()
    occurrences: list[Occurrence] = []
    for number, wire, value in _fields(payload, "Index"):
        if number == 1:
            tool, tool_version = _metadata(_message(value, wire, "Index", number))
        elif number == 2:
            path, found = _document(_message(value, wire, "Index", number))
            documents.add(path)
            occurrences.extend(found)
    return SymbolIndex(
        tool=tool,
        tool_version=tool_version,
        documents=tuple(sorted(documents)),
        occurrences=tuple(sorted(set(occurrences))),
    )


def package_of(symbol: str) -> tuple[str, str] | None:
    if is_local(symbol):
        return None
    components, _ = _split(symbol, PACKAGE_COMPONENTS)
    if len(components) < PACKAGE_COMPONENTS:
        return None
    name, version = components[2], components[3]
    if name in ("", PACKAGE_PLACEHOLDER) or version == "":
        return None
    return (name, version)


def is_local(symbol: str) -> bool:
    return symbol.startswith(LOCAL_PREFIX)


def descriptor_name(symbol: str) -> str | None:
    if is_local(symbol):
        return None
    components, descriptors = _split(symbol, PACKAGE_COMPONENTS)
    if len(components) < PACKAGE_COMPONENTS:
        return None
    names = _descriptor_names(descriptors)
    if not names:
        return None
    return names[-1]


def _split(symbol: str, count: int) -> tuple[list[str], str]:
    parts: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(symbol) and len(parts) < count:
        if symbol.startswith("  ", index):
            current.append(" ")
            index += 2
        elif symbol[index] == " ":
            parts.append("".join(current))
            current = []
            index += 1
        else:
            current.append(symbol[index])
            index += 1
    if len(parts) < count:
        parts.append("".join(current))
        return parts, ""
    return parts, symbol[index:]


def _descriptor_names(text: str) -> list[str] | None:
    names: list[str] = []
    index = 0
    while index < len(text):
        opening = text[index]
        if opening in BRACKETS:
            name, index = _identifier(text, index + 1)
            if name is None or index >= len(text) or text[index] != BRACKETS[opening]:
                return None
            index += 1
            names.append(name)
            continue
        name, index = _identifier(text, index)
        if name is None or index >= len(text):
            return None
        suffix = text[index]
        if suffix in DESCRIPTOR_SUFFIXES:
            index += 1
        elif suffix == "(":
            closing = text.find(")", index)
            if closing < 0 or not text.startswith(".", closing + 1):
                return None
            index = closing + 2
        else:
            return None
        names.append(name)
    return names


def _identifier(text: str, index: int) -> tuple[str | None, int]:
    if index >= len(text):
        return None, index
    if text[index] != "`":
        start = index
        while index < len(text) and (
            text[index].isascii()
            and (text[index].isalnum() or text[index] in SIMPLE_IDENTIFIER_EXTRA)
        ):
            index += 1
        if index == start:
            return None, index
        return text[start:index], index
    index += 1
    escaped: list[str] = []
    while index < len(text):
        if text.startswith("``", index):
            escaped.append("`")
            index += 2
        elif text[index] == "`":
            if not escaped:
                return None, index
            return "".join(escaped), index + 1
        else:
            escaped.append(text[index])
            index += 1
    return None, index


def _metadata(payload: bytes) -> tuple[str, str]:
    tool = ""
    tool_version = ""
    for number, wire, value in _fields(payload, "Metadata"):
        if number == 2:
            tool, tool_version = _tool_info(_message(value, wire, "Metadata", number))
    return tool, tool_version


def _tool_info(payload: bytes) -> tuple[str, str]:
    name = ""
    version = ""
    for number, wire, value in _fields(payload, "ToolInfo"):
        if number == 1:
            name = _string(value, wire, "ToolInfo", number)
        elif number == 2:
            version = _string(value, wire, "ToolInfo", number)
    return name, version


def _document(payload: bytes) -> tuple[str, list[Occurrence]]:
    relative_path = ""
    encoded: list[bytes] = []
    for number, wire, value in _fields(payload, "Document"):
        if number == 1:
            relative_path = _string(value, wire, "Document", number)
        elif number == 2:
            encoded.append(_message(value, wire, "Document", number))
    path = _normalize_path(relative_path)
    return path, [_occurrence(path, item) for item in encoded]


def _occurrence(path: str, payload: bytes) -> Occurrence:
    bounds: list[int] = []
    symbol = ""
    roles = 0
    for number, wire, value in _fields(payload, "Occurrence"):
        if number == 1:
            if wire == WIRE_VARINT:
                bounds.append(_int32(value, wire, "Occurrence", number))
            else:
                bounds.extend(_packed_int32(_message(value, wire, "Occurrence", number)))
        elif number == 2:
            symbol = _string(value, wire, "Occurrence", number)
        elif number == 3:
            roles = _int32(value, wire, "Occurrence", number)
    if len(bounds) == 3:
        start_line, start_column, end_column = bounds
        end_line = start_line
    elif len(bounds) == 4:
        start_line, start_column, end_line, end_column = bounds
    else:
        raise ToolingFailed(
            TOOL, f"Occurrence range in {path!r} has {len(bounds)} elements; expected 3 or 4"
        )
    return Occurrence(path, start_line, start_column, end_line, end_column, symbol, roles)


def _packed_int32(payload: bytes) -> list[int]:
    values: list[int] = []
    position = 0
    while position < len(payload):
        value, position = _varint(payload, position, "packed range")
        values.append(_bounded(value, "Occurrence", 1))
    return values


def _normalize_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _fields(payload: bytes, message: str) -> Iterable[tuple[int, int, int | bytes]]:
    position = 0
    fields: list[tuple[int, int, int | bytes]] = []
    while position < len(payload):
        key, position = _varint(payload, position, f"{message} field key")
        number, wire = key >> 3, key & 7
        if number == 0:
            raise ToolingFailed(TOOL, f"{message} carries field number zero at byte {position}")
        if wire == WIRE_VARINT:
            value, position = _varint(payload, position, f"{message} field {number}")
            fields.append((number, wire, value))
        elif wire == WIRE_LENGTH_DELIMITED:
            length, position = _varint(payload, position, f"{message} field {number} length")
            end = position + length
            if end > len(payload):
                raise ToolingFailed(
                    TOOL,
                    f"{message} field {number} declares {length} bytes but only "
                    f"{len(payload) - position} remain",
                )
            fields.append((number, wire, payload[position:end]))
            position = end
        elif wire in FIXED_WIDTHS:
            end = position + FIXED_WIDTHS[wire]
            if end > len(payload):
                raise ToolingFailed(
                    TOOL, f"{message} field {number} fixed-width value truncated at byte {position}"
                )
            fields.append((number, wire, payload[position:end]))
            position = end
        else:
            raise ToolingFailed(TOOL, f"{message} field {number} uses unsupported wire type {wire}")
    return fields


def _varint(payload: bytes, position: int, what: str) -> tuple[int, int]:
    result = 0
    shift = 0
    consumed = 0
    while True:
        if position >= len(payload):
            raise ToolingFailed(TOOL, f"{what} varint truncated at byte {position}")
        byte = payload[position]
        position += 1
        consumed += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, position
        shift += 7
        if consumed >= VARINT_MAXIMUM_BYTES:
            raise ToolingFailed(TOOL, f"{what} varint exceeds ten bytes at byte {position}")


def _message(value: int | bytes, wire: int, message: str, number: int) -> bytes:
    if wire != WIRE_LENGTH_DELIMITED or isinstance(value, int):
        raise ToolingFailed(
            TOOL, f"{message} field {number} expected a length-delimited value, got wire {wire}"
        )
    return value


def _string(value: int | bytes, wire: int, message: str, number: int) -> str:
    try:
        return _message(value, wire, message, number).decode("utf-8")
    except UnicodeDecodeError as error:
        raise ToolingFailed(TOOL, f"{message} field {number} is not valid UTF-8") from error


def _int32(value: int | bytes, wire: int, message: str, number: int) -> int:
    if wire != WIRE_VARINT or isinstance(value, bytes):
        raise ToolingFailed(
            TOOL, f"{message} field {number} expected a varint value, got wire {wire}"
        )
    return _bounded(value, message, number)


def _bounded(value: int, message: str, number: int) -> int:
    if value > INT32_MAXIMUM:
        raise ToolingFailed(TOOL, f"{message} field {number} value {value} exceeds int32")
    return value


__all__ = [
    "Occurrence",
    "SymbolIndex",
    "descriptor_name",
    "is_local",
    "package_of",
    "read_index",
]
