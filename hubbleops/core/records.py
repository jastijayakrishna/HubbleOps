from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

EMPTY_MAPPING: Mapping[str, Any] = {}
EMPTY_SEQUENCE: Sequence[Any] = ()


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
