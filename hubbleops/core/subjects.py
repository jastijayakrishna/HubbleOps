from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

JOINER_CHARACTERS = frozenset(" \t\r\n'\"+()\\")
SITE = re.compile(r"(?P<path>[\w./\\-]+\.[A-Za-z0-9]+):(?P<line>\d+)")


def parse_sites(declaration: str) -> tuple[tuple[str, int], ...]:
    found: list[tuple[str, int]] = []
    for match in SITE.finditer(declaration):
        site = (match["path"], int(match["line"]))
        if site not in found:
            found.append(site)
    return tuple(found)


@dataclass(frozen=True, slots=True)
class Occurrence:
    subject: str
    path: str
    line: int
    offset: int

    def site(self) -> str:
        return f"{self.path}:{self.line}"


class _Node:
    __slots__ = ("children", "terminal")

    def __init__(self) -> None:
        self.children: dict[str, _Node] = {}
        self.terminal: str | None = None


def _trie(subjects: Sequence[str]) -> _Node:
    root = _Node()
    for subject in subjects:
        node = root
        for character in subject:
            node = node.children.setdefault(character, _Node())
        node.terminal = subject
    return root


def _word_character(character: str) -> bool:
    return character == "_" or character.isalnum()


def _in_text(text: str, trie: _Node) -> tuple[tuple[str, int], ...]:
    normalized = tuple(
        (character, offset)
        for offset, character in enumerate(text)
        if character not in JOINER_CHARACTERS
    )
    found: set[tuple[str, int]] = set()
    for start, (character, offset) in enumerate(normalized):
        if offset and _word_character(text[offset - 1]):
            continue
        child = trie.children.get(character)
        if child is None:
            continue
        node = child
        cursor = start + 1
        while True:
            terminal = node.terminal
            end = normalized[cursor - 1][1] + 1
            if isinstance(terminal, str) and (end == len(text) or not _word_character(text[end])):
                found.add((terminal, offset))
            if cursor >= len(normalized):
                break
            child = node.children.get(normalized[cursor][0])
            if child is None:
                break
            node = child
            cursor += 1
    return tuple(sorted(found))


def in_text(text: str, subjects: Sequence[str]) -> tuple[tuple[str, int], ...]:
    watched = tuple(sorted({subject for subject in subjects if subject}))
    return _in_text(text, _trie(watched)) if watched else ()


def in_sources(sources: Mapping[str, str], subjects: Sequence[str]) -> tuple[Occurrence, ...]:
    watched = tuple(sorted({subject for subject in subjects if subject}))
    if not watched:
        return ()
    trie = _trie(watched)
    found: list[Occurrence] = []
    for path in sorted(sources):
        text = sources[path]
        for subject, offset in _in_text(text, trie):
            found.append(
                Occurrence(
                    subject=subject,
                    path=path,
                    line=text.count("\n", 0, offset) + 1,
                    offset=offset,
                )
            )
    return tuple(sorted(found, key=lambda item: (item.subject, item.path, item.line)))


__all__ = ["JOINER_CHARACTERS", "SITE", "Occurrence", "in_sources", "in_text", "parse_sites"]
