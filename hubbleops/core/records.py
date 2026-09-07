from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

EMPTY_MAPPING: Mapping[str, Any] = {}
EMPTY_SEQUENCE: Sequence[Any] = ()

MAX_JSON_BYTES = 16_777_216


@dataclass(frozen=True, slots=True)
class ParsedJson:
    value: Any
    reason: str | None

    def ok(self) -> bool:
        return self.reason is None


def parse_json(payload: bytes | str, maximum: int = MAX_JSON_BYTES) -> ParsedJson:
    size = len(payload)
    if size > maximum:
        return ParsedJson(None, f"oversize: {size} exceeds the {maximum} byte parse bound")
    text = payload
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError as error:
            return ParsedJson(None, f"not_utf8: {error.reason} at byte {error.start}")
    try:
        return ParsedJson(json.loads(text), None)
    except json.JSONDecodeError as error:
        return ParsedJson(
            None, f"invalid_json: {error.msg} (line {error.lineno} column {error.colno})"
        )
    except RecursionError:
        return ParsedJson(None, "invalid_json: nesting depth exceeds the parser bound")


def is_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def is_list(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes)


def as_mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, Any], value)
    return EMPTY_MAPPING


def as_sequence(value: object) -> Sequence[Any]:
    if isinstance(value, str | bytes):
        return EMPTY_SEQUENCE
    if isinstance(value, Sequence):
        return cast(Sequence[Any], value)
    return EMPTY_SEQUENCE


def as_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def as_line(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
