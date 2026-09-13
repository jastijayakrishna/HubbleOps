from __future__ import annotations

import json
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import yaml

from hubbleops.core.canonical import canonical_text, content_id
from hubbleops.core.errors import ToolingFailed, ToolingMissing, ToolingTimeout
from hubbleops.core.precise import Occurrence, SymbolIndex, descriptor_name
from hubbleops.core.records import as_mapping, as_sequence
from hubbleops.core.toolchain import ToolBinary, identity, locate, vendored

AST_GREP = "ast-grep"


class _HasPath(Protocol):
    @property
    def path(self) -> str: ...


AST_GREP_MINIMUM = (0, 45, 0)
AST_GREP_MAXIMUM = (0, 46, 0)
AST_GREP_TIMEOUT_SECONDS = 30.0
ARGUMENT_BUDGET_CHARS = 24_000
MAX_OUTPUT_CHARS = 512 * 1024 * 1024
MAX_MATCHES_PER_INVOCATION = 2_000_000
LANGUAGE_EXTENSIONS = {
    ".cs": "csharp",
    ".cjs": "javascript",
    ".go": "go",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".json": "json",
    ".md": "markdown",
    ".mjs": "javascript",
    ".php": "php",
    ".py": "python",
    ".pyi": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
}
AST_GREP_LANGUAGES = {
    "javascript": "javascript",
    "php": "php",
    "python": "python",
    "tsx": "tsx",
    "typescript": "typescript",
}


@dataclass(frozen=True, order=True, slots=True)
class SourceRange:
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int
    start_column: int = 0
    end_column: int = 0

    def contains(self, other: SourceRange) -> bool:
        return self.start_byte <= other.start_byte and self.end_byte >= other.end_byte

    def to_mapping(self) -> dict[str, int]:
        return {
            "start_byte": self.start_byte,
            "end_byte": self.end_byte,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }


@dataclass(frozen=True, order=True, slots=True)
class Capture:
    name: str
    text: str
    range: SourceRange

    def to_mapping(self) -> dict[str, Any]:
        return {"name": self.name, "text": self.text, "range": self.range.to_mapping()}


@dataclass(frozen=True, order=True, slots=True)
class SyntaxMatch:
    path: str
    language: str
    kind: str
    text: str
    range: SourceRange
    captures: tuple[Capture, ...]

    def capture(self, name: str) -> Capture | None:
        return next((item for item in self.captures if item.name == name), None)

    def captures_named(self, name: str) -> tuple[Capture, ...]:
        return tuple(
            sorted(
                (item for item in self.captures if item.name == name),
                key=lambda item: (item.range.start_byte, item.range.end_byte, item.text),
            )
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "language": self.language,
            "kind": self.kind,
            "text": self.text,
            "range": self.range.to_mapping(),
            "captures": [item.to_mapping() for item in self.captures],
        }


@dataclass(frozen=True, order=True, slots=True)
class Definition:
    id: str
    path: str
    language: str
    name: str
    parameters: tuple[str, ...]
    range: SourceRange
    asynchronous: bool
    bindings: tuple[tuple[str, int], ...] = ()

    def slot_of(self, name: str) -> int | None:
        for bound, position in self.bindings:
            if bound == name:
                return position
        return None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "language": self.language,
            "name": self.name,
            "parameters": list(self.parameters),
            "range": self.range.to_mapping(),
            "asynchronous": self.asynchronous,
            "bindings": [list(item) for item in self.bindings],
        }


@dataclass(frozen=True, order=True, slots=True)
class Call:
    id: str
    path: str
    language: str
    callee: str
    arguments: tuple[Capture, ...]
    range: SourceRange
    definition_id: str | None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "language": self.language,
            "callee": self.callee,
            "arguments": [item.to_mapping() for item in self.arguments],
            "range": self.range.to_mapping(),
            "definition_id": self.definition_id,
        }


@dataclass(frozen=True, order=True, slots=True)
class Assignment:
    id: str
    path: str
    language: str
    target: Capture
    value: Capture
    range: SourceRange
    definition_id: str | None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "language": self.language,
            "target": self.target.to_mapping(),
            "value": self.value.to_mapping(),
            "range": self.range.to_mapping(),
            "definition_id": self.definition_id,
        }


@dataclass(frozen=True, order=True, slots=True)
class ImportBinding:
    id: str
    path: str
    language: str
    module: str
    symbols: tuple[str, ...]
    target_path: str | None
    range: SourceRange

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "language": self.language,
            "module": self.module,
            "symbols": list(self.symbols),
            "target_path": self.target_path,
            "range": self.range.to_mapping(),
        }


@dataclass(frozen=True, order=True, slots=True)
class ClassRelation:
    id: str
    path: str
    language: str
    name: str
    bases: tuple[str, ...]
    range: SourceRange

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "language": self.language,
            "name": self.name,
            "bases": list(self.bases),
            "range": self.range.to_mapping(),
        }


@dataclass(frozen=True, slots=True)
class _GraphIndex:
    atoms: dict[str, list[SyntaxMatch]]
    comments: dict[str, list[SyntaxMatch]]
    definitions: dict[str, list[Definition]]
    assignments: dict[str, list[Assignment]]
    imports: dict[str, list[ImportBinding]]
    definitions_by_id: dict[str, Definition]
    call_symbols: dict[str, str | None]
    calls_by_symbol: dict[str, list[Call]]
    definition_symbols: dict[str, str | None]
    definitions_by_symbol: dict[str, list[Definition]]


@dataclass(frozen=True, slots=True)
class ImportGraph:
    definitions: tuple[Definition, ...]
    calls: tuple[Call, ...]
    assignments: tuple[Assignment, ...]
    imports: tuple[ImportBinding, ...]
    classes: tuple[ClassRelation, ...]
    decorators: tuple[SyntaxMatch, ...]
    registrations: tuple[SyntaxMatch, ...]
    atoms: tuple[SyntaxMatch, ...]
    comments: tuple[SyntaxMatch, ...]
    parse_errors: tuple[SyntaxMatch, ...]
    concatenations: tuple[SyntaxMatch, ...]
    formats: tuple[SyntaxMatch, ...]
    indexes: tuple[SymbolIndex, ...] = ()
    _index: _GraphIndex = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        call_symbols = {item.id: self._call_symbol(item) for item in self.calls}
        calls_by_symbol: dict[str, list[Call]] = {}
        for item in self.calls:
            symbol = call_symbols[item.id]
            if symbol is not None:
                calls_by_symbol.setdefault(symbol, []).append(item)
        definition_symbols = {item.id: self._definition_symbol(item) for item in self.definitions}
        definitions_by_symbol: dict[str, list[Definition]] = {}
        for item in self.definitions:
            symbol = definition_symbols[item.id]
            if symbol is not None:
                definitions_by_symbol.setdefault(symbol, []).append(item)
        object.__setattr__(
            self,
            "_index",
            _GraphIndex(
                atoms=_group_by_path(self.atoms),
                comments=_group_by_path(self.comments),
                definitions=_group_by_path(self.definitions),
                assignments=_group_by_path(self.assignments),
                imports=_group_by_path(self.imports),
                definitions_by_id={item.id: item for item in self.definitions},
                call_symbols=call_symbols,
                calls_by_symbol=calls_by_symbol,
                definition_symbols=definition_symbols,
                definitions_by_symbol=definitions_by_symbol,
            ),
        )

    def index_for(self, path: str) -> SymbolIndex | None:
        return next((item for item in self.indexes if item.covers(path)), None)

    def _call_symbol(self, call: Call) -> str | None:
        index = self.index_for(call.path)
        if index is None:
            return None
        name = call.callee.rsplit(".", 1)[-1]
        column = call.range.start_column + len(call.callee) - len(name)
        found = index.occurrence_at(call.path, call.range.start_line - 1, column)
        if (
            found is None
            or found.start_line != found.end_line
            or found.start_column != column
            or found.end_column - found.start_column != len(name)
        ):
            return None
        return found.symbol

    def call_symbol(self, call: Call) -> str | None:
        return self._index.call_symbols.get(call.id)

    def definition_symbol(self, definition: Definition) -> str | None:
        return self._index.definition_symbols.get(definition.id)

    def _definition_symbol(self, definition: Definition) -> str | None:
        index = self.index_for(definition.path)
        if index is None:
            return None
        first_line = definition.range.start_line - 1
        last_line = definition.range.end_line - 1
        named = [
            item
            for item in index.occurrences_in(definition.path)
            if item.is_definition()
            and first_line <= item.start_line <= last_line
            and descriptor_name(item.symbol) == definition.name
        ]
        on_first_line = {item.symbol for item in named if item.start_line == first_line}
        if len(on_first_line) == 1:
            return next(iter(on_first_line))
        symbols = {item.symbol for item in named}
        if len(symbols) == 1:
            return next(iter(symbols))
        return None

    def caller_binding(self, definition: Definition, call: Call) -> str:
        symbol = self.definition_symbol(definition)
        if symbol is not None and self.call_symbol(call) == symbol:
            return "symbol"
        return "name"

    def precise_definition(self, path: str, name: str) -> Occurrence | None:
        index = self.index_for(path)
        if index is None:
            return None
        symbols = {
            item.symbol
            for item in index.occurrences_in(path)
            if not item.is_definition() and descriptor_name(item.symbol) == name
        }
        if len(symbols) != 1:
            return None
        return index.definition_of(next(iter(symbols)))

    def atoms_in(self, path: str) -> Sequence[SyntaxMatch]:
        return self._index.atoms.get(path, ())

    def comments_in(self, path: str) -> Sequence[SyntaxMatch]:
        return self._index.comments.get(path, ())

    def definitions_in(self, path: str) -> Sequence[Definition]:
        return self._index.definitions.get(path, ())

    def assignments_in(self, path: str) -> Sequence[Assignment]:
        return self._index.assignments.get(path, ())

    def imports_in(self, path: str) -> Sequence[ImportBinding]:
        return self._index.imports.get(path, ())

    def serialize(self) -> bytes:
        value = {
            "definitions": [item.to_mapping() for item in self.definitions],
            "calls": [item.to_mapping() for item in self.calls],
            "assignments": [item.to_mapping() for item in self.assignments],
            "imports": [item.to_mapping() for item in self.imports],
            "classes": [item.to_mapping() for item in self.classes],
            "decorators": [item.to_mapping() for item in self.decorators],
            "registrations": [item.to_mapping() for item in self.registrations],
            "atoms": [item.to_mapping() for item in self.atoms],
            "comments": [item.to_mapping() for item in self.comments],
            "parse_errors": [item.to_mapping() for item in self.parse_errors],
            "concatenations": [item.to_mapping() for item in self.concatenations],
            "formats": [item.to_mapping() for item in self.formats],
            "indexes": [
                {"tool": item.tool, "tool_version": item.tool_version, **item.counts()}
                for item in self.indexes
            ],
        }
        return f"{canonical_text(value)}\n".encode()

    def definitions_named(self, name: str) -> tuple[Definition, ...]:
        tail = name.rsplit(".", 1)[-1]
        return tuple(item for item in self.definitions if item.name == tail)

    def definition(self, identifier: str | None) -> Definition | None:
        if identifier is None:
            return None
        return self._index.definitions_by_id.get(identifier)

    def callers(self, definition: Definition) -> tuple[Call, ...]:
        by_name = [
            item
            for item in self.calls
            if item.callee.rsplit(".", 1)[-1] == definition.name
            and _arity_matches(len(item.arguments), len(definition.parameters))
        ]
        symbol = self.definition_symbol(definition)
        if symbol is None:
            return tuple(by_name)
        kept = {
            item for item in by_name if not self._bound_elsewhere(self.call_symbol(item), symbol)
        }
        kept.update(self._index.calls_by_symbol.get(symbol, ()))
        return tuple(sorted(kept))

    def _bound_elsewhere(self, call_symbol: str | None, symbol: str) -> bool:
        if call_symbol is None or call_symbol == symbol:
            return False
        return bool(self._index.definitions_by_symbol.get(call_symbol))

    def wrapper_context(self, definition: Definition) -> tuple[str, ...]:
        facts: set[str] = set()
        owners = [
            item
            for item in self.classes
            if item.path == definition.path and item.range.contains(definition.range)
        ]
        family_names: set[str] = set()
        for owner in owners:
            family_names.add(owner.name)
            if owner.bases:
                facts.add(f"class {owner.name} extends {', '.join(owner.bases)}")
            else:
                facts.add(f"class {owner.name}")
            family_names.update(base.rsplit(".", 1)[-1] for base in owner.bases)
            for relation in self.classes:
                if any(base.rsplit(".", 1)[-1] in family_names for base in relation.bases):
                    family_names.add(relation.name)
        for registration in self.registrations:
            if registration.path != definition.path:
                continue
            registered = sorted(
                name
                for name in family_names
                if re.search(rf"\b{re.escape(name)}\b", registration.text)
            )
            if not registered:
                continue
            registry = registration.capture("REGISTRY")
            registry_name = registry.text if registry is not None else "registry"
            facts.add(f"registration {registry_name} carries {', '.join(registered)}")
            for factory in self.definitions:
                if factory.path != registration.path:
                    continue
                if any(
                    factory.range.contains(atom.range) and atom.text == registry_name
                    for atom in self.atoms
                    if atom.path == factory.path
                ):
                    facts.add(f"factory {factory.name} reads registration {registry_name}")
        for decorator in self.decorators:
            if (
                decorator.path != definition.path
                or decorator.range.end_byte > definition.range.start_byte
            ):
                continue
            following = [
                item
                for item in self.definitions
                if item.path == definition.path
                and item.range.start_byte >= decorator.range.end_byte
            ]
            if (
                following
                and min(following, key=lambda item: item.range.start_byte).id == definition.id
            ):
                captured = decorator.capture("DECORATOR")
                facts.add(f"decorator {captured.text if captured is not None else decorator.text}")
        if definition.asynchronous:
            facts.add(f"async definition {definition.name}")
        return tuple(sorted(facts))


@dataclass(frozen=True, slots=True)
class Query:
    kind: str
    pattern: str | None = None
    selector: str | None = None
    node_kind: str | None = None


COMMON_QUERIES = (
    Query("call", "$CALLEE($$$ARGS)"),
    Query("assignment", "$TARGET = $VALUE"),
    Query("registration", "$REGISTRY[$KEY] = $VALUE"),
    Query("registration", "$REGISTRY = $VALUE"),
    Query("string", node_kind="string"),
    Query("comment", node_kind="comment"),
    Query("parse_error", node_kind="ERROR"),
    Query("concatenation", "$LEFT + $RIGHT"),
)

TYPESCRIPT_QUERIES = (
    Query("definition", "function $NAME($$$PARAMS) { $$$BODY }"),
    Query("definition", "async function $NAME($$$PARAMS) { $$$BODY }"),
    Query("definition", "const $NAME = ($$$PARAMS) => $BODY"),
    Query("definition", "const $NAME = async ($$$PARAMS) => $BODY"),
    Query("definition", "const $NAME = ($$$PARAMS): $RET => $BODY"),
    Query("definition", "const $NAME = async ($$$PARAMS): $RET => $BODY"),
    Query("definition", "const $NAME = <$T>($$$PARAMS) => $BODY"),
    Query("definition", "const $NAME = async <$T>($$$PARAMS) => $BODY"),
    Query("definition", "const $NAME = <$T>($$$PARAMS): $RET => $BODY"),
    Query("definition", "const $NAME = async <$T>($$$PARAMS): $RET => $BODY"),
    Query(
        "definition",
        "class C { async $NAME($$$PARAMS) { $$$BODY } }",
        "method_definition",
    ),
    Query("definition", "class C { $NAME($$$PARAMS) { $$$BODY } }", "method_definition"),
    Query("call", "$CALLEE<$T>($$$ARGS)"),
    Query("assignment", "const $TARGET = $VALUE"),
    Query("assignment", "let $TARGET = $VALUE"),
    Query("assignment", "export const $TARGET = $VALUE"),
    Query("import", "import { $$$NAMES } from $MODULE"),
    Query("import", "import $NAME from $MODULE"),
    Query("class", "class $NAME extends $BASE { $$$BODY }"),
    Query("class", "class $NAME { $$$BODY }"),
    Query("decorator", "@$DECORATOR", "decorator"),
    Query("identifier", node_kind="identifier"),
    Query("template_string", node_kind="template_string"),
)

LANGUAGE_QUERIES = {
    "python": (
        Query("definition", "def $NAME($$$PARAMS):\n  $$$BODY"),
        Query("definition", "def $NAME($$$PARAMS) -> $RETURN:\n  $$$BODY"),
        Query("definition", "async def $NAME($$$PARAMS):\n  $$$BODY"),
        Query("definition", "async def $NAME($$$PARAMS) -> $RETURN:\n  $$$BODY"),
        Query("import", "from $MODULE import $$$NAMES"),
        Query("import", "import $$$NAMES"),
        Query("class", "class $NAME($$$BASES):\n  $$$BODY"),
        Query("class", "class $NAME:\n  $$$BODY"),
        Query("decorator", "@$DECORATOR", "decorator"),
        Query("identifier", node_kind="identifier"),
        Query("format", "$FORMAT.format($$$ARGS)"),
    ),
    "php": (
        Query("call", "$OBJ->$CALLEE($$$ARGS)"),
        Query("definition", "function $NAME($$$PARAMS) { $$$BODY }"),
        Query("class", "class $NAME extends $BASE { $$$BODY }"),
        Query("class", "class $NAME { $$$BODY }"),
        Query("import", "use $MODULE"),
        Query("identifier", node_kind="name"),
        Query("identifier", node_kind="variable_name"),
        Query("string", node_kind="encapsed_string"),
        Query("concatenation", "$LEFT . $RIGHT"),
    ),
    "javascript": (
        Query("definition", "function $NAME($$$PARAMS) { $$$BODY }"),
        Query("definition", "async function $NAME($$$PARAMS) { $$$BODY }"),
        Query("definition", "const $NAME = ($$$PARAMS) => $BODY"),
        Query("definition", "const $NAME = async ($$$PARAMS) => $BODY"),
        Query("definition", "const $NAME = ($$$PARAMS): $RET => $BODY"),
        Query("definition", "const $NAME = async ($$$PARAMS): $RET => $BODY"),
        Query("definition", "const $NAME = <$T>($$$PARAMS) => $BODY"),
        Query("definition", "const $NAME = async <$T>($$$PARAMS) => $BODY"),
        Query("definition", "const $NAME = <$T>($$$PARAMS): $RET => $BODY"),
        Query("definition", "const $NAME = async <$T>($$$PARAMS): $RET => $BODY"),
        Query(
            "definition",
            "class C { async $NAME($$$PARAMS) { $$$BODY } }",
            "method_definition",
        ),
        Query("definition", "class C { $NAME($$$PARAMS) { $$$BODY } }", "method_definition"),
        Query("call", "$CALLEE<$T>($$$ARGS)"),
        Query("assignment", "const $TARGET = $VALUE"),
        Query("assignment", "let $TARGET = $VALUE"),
        Query("import", "import { $$$NAMES } from $MODULE"),
        Query("import", "import $NAME from $MODULE"),
        Query("class", "class $NAME extends $BASE { $$$BODY }"),
        Query("class", "class $NAME { $$$BODY }"),
        Query("decorator", "@$DECORATOR", "decorator"),
        Query("identifier", node_kind="identifier"),
        Query("template_string", node_kind="template_string"),
    ),
    "tsx": TYPESCRIPT_QUERIES,
    "typescript": TYPESCRIPT_QUERIES,
}


NON_COMBINABLE_QUERIES = frozenset(
    {
        ("javascript", "import", "import $NAME from $MODULE"),
        ("javascript", "import", "import { $$$NAMES } from $MODULE"),
        ("php", "assignment", "$TARGET = $VALUE"),
        ("php", "registration", "$REGISTRY = $VALUE"),
        ("tsx", "import", "import $NAME from $MODULE"),
        ("tsx", "import", "import { $$$NAMES } from $MODULE"),
        ("typescript", "import", "import $NAME from $MODULE"),
        ("typescript", "import", "import { $$$NAMES } from $MODULE"),
    }
)


def queries_for(language: str) -> tuple[Query, ...]:
    return (*COMMON_QUERIES, *LANGUAGE_QUERIES.get(language, ()))


def combinable_queries(language: str) -> tuple[Query, ...]:
    return tuple(query for query in queries_for(language) if _combinable(language, query))


def separate_queries(language: str) -> tuple[Query, ...]:
    return tuple(query for query in queries_for(language) if not _combinable(language, query))


def _combinable(language: str, query: Query) -> bool:
    return (language, query.kind, query.pattern or "") not in NON_COMBINABLE_QUERIES


class AstGrep:
    def __init__(
        self,
        executable: str = "ast-grep",
        timeout_seconds: float = AST_GREP_TIMEOUT_SECONDS,
    ) -> None:
        self.requested = executable
        self.timeout_seconds = timeout_seconds
        self._binary: ToolBinary | None = None

    @property
    def binary(self) -> ToolBinary:
        if self._binary is None:
            self._binary = _binary(self.requested)
        return self._binary

    @property
    def executable(self) -> str:
        return self.binary.path

    def version(self) -> str:
        completed = self._run(["--version"], None)
        match = re.search(r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+)", completed.stdout)
        if match is None:
            raise ToolingMissing("ast-grep", "version output is not compatible")
        version = tuple(int(part) for part in match.group("version").split("."))
        if version < AST_GREP_MINIMUM or version >= AST_GREP_MAXIMUM:
            raise ToolingMissing(
                "ast-grep",
                f"version {match.group('version')} is incompatible; require >=0.45.0,<0.46.0",
            )
        return identity(self.binary, match.group("version"))

    def query(
        self,
        root: Path,
        paths: Sequence[str],
        language: str,
        query: Query,
    ) -> tuple[SyntaxMatch, ...]:
        if not paths:
            return ()
        args = ["run"]
        if query.node_kind is not None:
            args.extend(["-k", query.node_kind])
        elif query.pattern is not None:
            args.extend(["-p", query.pattern])
        else:
            raise ValueError("ast-grep query requires a pattern or node kind")
        if query.selector is not None:
            args.extend(["--selector", query.selector])
        args.extend(["-l", AST_GREP_LANGUAGES[language], "--json=compact"])
        matches: list[SyntaxMatch] = []
        for batch in argument_batches(paths):
            completed = self._run([*args, *batch], root)
            matches.extend(self.decode(completed.stdout, root, query.kind))
        return tuple(matches)

    def query_all(
        self,
        root: Path,
        paths: Sequence[str],
        language: str,
        queries: Sequence[Query],
    ) -> tuple[SyntaxMatch, ...]:
        if not paths or not queries:
            return ()
        kinds = {f"q{index}": query.kind for index, query in enumerate(queries)}
        documents = [
            _rule_document(identifier, language, query)
            for identifier, query in zip(kinds, queries, strict=True)
        ]
        matches: list[SyntaxMatch] = []
        with tempfile.TemporaryDirectory(prefix="hops-graph-queries-") as temporary:
            rule_path = Path(temporary) / "queries.yml"
            rule_path.write_text(
                yaml.safe_dump_all(documents, sort_keys=True, allow_unicode=True),
                encoding="utf-8",
            )
            args = ["scan", "-r", str(rule_path), "--json=compact"]
            for batch in argument_batches(paths):
                completed = self._run([*args, *batch], root)
                matches.extend(self.decode_tagged(completed.stdout, root, kinds))
        return tuple(sorted(set(matches)))

    def rule(
        self,
        root: Path,
        paths: Sequence[str],
        language: str,
        rule_path: Path,
    ) -> tuple[SyntaxMatch, ...]:
        if not paths:
            return ()
        args = ["scan", "-r", str(rule_path), "--json=compact"]
        matches: list[SyntaxMatch] = []
        for batch in argument_batches(paths):
            completed = self._run([*args, *batch], root)
            matches.extend(self.decode(completed.stdout, root, f"rule:{language}"))
        return tuple(matches)

    def _run(self, args: Sequence[str], cwd: Path | None) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                [self.executable, *args],
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=self.timeout_seconds,
                check=False,
            )
        except (FileNotFoundError, PermissionError) as error:
            if Path(self.executable).is_file():
                length = sum(len(argument) + 1 for argument in args)
                raise ToolingFailed(
                    "ast-grep",
                    f"{self.executable!r} exists but the operating system refused to start it "
                    f"with a {length} character argument list of {len(args)} arguments",
                ) from error
            raise ToolingMissing("ast-grep", f"{self.executable!r} is not executable") from error
        except subprocess.TimeoutExpired as error:
            raise ToolingTimeout("ast-grep", self.timeout_seconds) from error
        except UnicodeError as error:
            raise ToolingFailed("ast-grep", "output was not valid UTF-8") from error
        if completed.returncode not in (0, 1):
            detail = completed.stderr.strip() or f"exit code {completed.returncode}"
            raise ToolingFailed("ast-grep", detail)
        return completed

    def decode(self, payload: str, root: Path, kind: str) -> tuple[SyntaxMatch, ...]:
        if len(payload) > MAX_OUTPUT_CHARS:
            raise ToolingFailed(
                "ast-grep",
                f"a single {kind} invocation returned {len(payload)} characters, over the "
                f"{MAX_OUTPUT_CHARS} character bound; narrow the rule or split the closure",
            )
        try:
            decoded: object = json.loads(payload or "[]")
        except json.JSONDecodeError as error:
            raise ToolingFailed("ast-grep", "output was malformed JSON") from error
        except RecursionError as error:
            raise ToolingFailed("ast-grep", "output nesting exceeded the parser bound") from error
        if not isinstance(decoded, list):
            raise ToolingFailed("ast-grep", "output JSON root was not an array")
        entries = cast(list[object], decoded)
        if len(entries) > MAX_MATCHES_PER_INVOCATION:
            raise ToolingFailed(
                "ast-grep",
                f"a single {kind} invocation matched {len(entries)} nodes, over the "
                f"{MAX_MATCHES_PER_INVOCATION} match bound; narrow the rule",
            )
        matches = tuple(_decode_match(item, root, kind) for item in entries)
        return tuple(sorted(set(matches)))

    def decode_tagged(
        self, payload: str, root: Path, kinds: Mapping[str, str]
    ) -> tuple[SyntaxMatch, ...]:
        scale = max(len(kinds), 1)
        if len(payload) > MAX_OUTPUT_CHARS * scale:
            raise ToolingFailed(
                "ast-grep",
                f"a combined {scale} rule invocation returned {len(payload)} characters, over "
                f"the {MAX_OUTPUT_CHARS * scale} character bound; split the closure",
            )
        try:
            decoded: object = json.loads(payload or "[]")
        except json.JSONDecodeError as error:
            raise ToolingFailed("ast-grep", "output was malformed JSON") from error
        except RecursionError as error:
            raise ToolingFailed("ast-grep", "output nesting exceeded the parser bound") from error
        if not isinstance(decoded, list):
            raise ToolingFailed("ast-grep", "output JSON root was not an array")
        entries = cast(list[object], decoded)
        if len(entries) > MAX_MATCHES_PER_INVOCATION * scale:
            raise ToolingFailed(
                "ast-grep",
                f"a combined {scale} rule invocation matched {len(entries)} nodes, over the "
                f"{MAX_MATCHES_PER_INVOCATION * scale} match bound; split the closure",
            )
        matches: list[SyntaxMatch] = []
        for item in entries:
            record = as_mapping(cast(Mapping[str, object], item))
            identifier = record.get("ruleId")
            if identifier is None:
                raise ToolingFailed(
                    "ast-grep",
                    "a combined rule invocation returned a match with no ruleId, so the "
                    "query that produced it cannot be identified",
                )
            kind = kinds.get(str(identifier))
            if kind is None:
                raise ToolingFailed(
                    "ast-grep",
                    f"a combined rule invocation returned unknown ruleId {identifier!r}",
                )
            matches.append(_decode_match(item, root, kind))
        return tuple(sorted(set(matches)))


def _rule_document(identifier: str, language: str, query: Query) -> dict[str, Any]:
    if query.node_kind is not None:
        rule: dict[str, Any] = {"kind": query.node_kind}
    elif query.pattern is not None and query.selector is not None:
        rule = {"pattern": {"context": query.pattern, "selector": query.selector}}
    elif query.pattern is not None:
        rule = {"pattern": query.pattern}
    else:
        raise ValueError("ast-grep query requires a pattern or node kind")
    return {"id": identifier, "language": AST_GREP_LANGUAGES[language], "rule": rule}


def argument_batches(
    paths: Sequence[str], budget: int = ARGUMENT_BUDGET_CHARS
) -> Iterator[tuple[str, ...]]:
    current: list[str] = []
    used = 0
    for path in paths:
        cost = len(path) + 1
        if current and used + cost > budget:
            yield tuple(current)
            current = []
            used = 0
        current.append(path)
        used += cost
    if current:
        yield tuple(current)


def language_for(path: str) -> str:
    return LANGUAGE_EXTENSIONS.get(Path(path).suffix.lower(), "unknown")


def build(
    root: Path,
    paths_by_language: Mapping[str, Sequence[str]],
    runner: AstGrep,
    indexes: Sequence[SymbolIndex] = (),
) -> ImportGraph:
    matches: list[SyntaxMatch] = []
    for language in sorted(paths_by_language):
        paths = tuple(sorted(paths_by_language[language]))
        if not paths:
            continue
        matches.extend(runner.query_all(root, paths, language, combinable_queries(language)))
        for query in separate_queries(language):
            matches.extend(runner.query(root, paths, language, query))
    unique = tuple(sorted(set(matches)))
    definitions = _definitions(unique)
    calls = _calls(unique, definitions)
    assignments = _assignments(unique, definitions)
    all_paths = tuple(path for paths in paths_by_language.values() for path in paths)
    imports = _imports(unique, all_paths, module_aliases(root))
    classes = _classes(unique)
    decorators = tuple(item for item in unique if item.kind == "decorator")
    registrations = tuple(item for item in unique if item.kind == "registration")
    atoms = tuple(
        item for item in unique if item.kind in ("identifier", "string", "template_string")
    )
    comments = tuple(item for item in unique if item.kind == "comment")
    parse_errors = tuple(item for item in unique if item.kind == "parse_error")
    concatenations = tuple(item for item in unique if item.kind == "concatenation")
    formats = tuple(item for item in unique if item.kind == "format")
    return ImportGraph(
        definitions=definitions,
        calls=calls,
        assignments=assignments,
        imports=imports,
        classes=classes,
        decorators=decorators,
        registrations=registrations,
        atoms=atoms,
        comments=comments,
        parse_errors=parse_errors,
        concatenations=concatenations,
        formats=formats,
        indexes=tuple(indexes),
    )


def scan_rules(
    root: Path,
    paths_by_language: Mapping[str, Sequence[str]],
    rules: Mapping[str, Sequence[Path]],
    runner: AstGrep,
) -> tuple[SyntaxMatch, ...]:
    matches: list[SyntaxMatch] = []
    for language in sorted(rules):
        paths = tuple(sorted(paths_by_language.get(language, ())))
        if not paths:
            continue
        for rule_path in sorted(rules[language]):
            matches.extend(runner.rule(root, paths, language, rule_path))
    return tuple(sorted(set(matches)))


def _decode_match(value: object, root: Path, kind: str) -> SyntaxMatch:
    if not isinstance(value, Mapping):
        raise ToolingFailed("ast-grep", "match entry was not an object")
    record = as_mapping(cast(Mapping[str, object], value))
    try:
        path = _relative(root, str(record["file"]))
        language = str(record["language"]).lower()
        text = str(record["text"])
        source_range = _range(record["range"])
        variables = as_mapping(record.get("metaVariables"))
        single = as_mapping(variables.get("single"))
        multi = as_mapping(variables.get("multi"))
    except (KeyError, TypeError, ValueError) as error:
        raise ToolingFailed("ast-grep", "match entry omitted required fields") from error
    captures = [
        Capture(str(name), str(as_mapping(item)["text"]), _range(as_mapping(item)["range"]))
        for name, item in single.items()
    ]
    for name, items in multi.items():
        for item in as_sequence(items):
            capture = as_mapping(item)
            captures.append(Capture(str(name), str(capture["text"]), _range(capture["range"])))
    return SyntaxMatch(path, language, kind, text, source_range, tuple(sorted(captures)))


def _range(value: object) -> SourceRange:
    record = as_mapping(value)
    offsets = as_mapping(record["byteOffset"])
    start = as_mapping(record["start"])
    end = as_mapping(record["end"])
    return SourceRange(
        int(offsets["start"]),
        int(offsets["end"]),
        int(start["line"]) + 1,
        int(end["line"]) + 1,
        int(start["column"]),
        int(end["column"]),
    )


def _relative(root: Path, value: str) -> str:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    normalized = value.replace("\\", "/")
    root_text = root.as_posix().rstrip("/")
    if normalized.startswith(f"{root_text}/"):
        return normalized[len(root_text) + 1 :]
    return normalized.removeprefix("./")


def _definitions(matches: Iterable[SyntaxMatch]) -> tuple[Definition, ...]:
    materialized = tuple(matches)
    identifiers = _group_by_path(item for item in materialized if item.kind == "identifier")
    definitions: list[Definition] = []
    for match in materialized:
        if match.kind != "definition":
            continue
        name = match.capture("NAME")
        if name is None:
            continue
        slots = [
            item for item in match.captures_named("PARAMS") if item.text not in (",", "(", ")")
        ]
        parameters = tuple(_parameter_name(match.path, item, identifiers) for item in slots)
        bindings = tuple(
            (bound, position)
            for position, item in enumerate(slots)
            for bound in _bound_names(match.path, item, identifiers)
        )
        identity = content_id(
            {
                "path": match.path,
                "name": name.text,
                "start": match.range.start_byte,
                "parameters": parameters,
            }
        )
        definitions.append(
            Definition(
                identity,
                match.path,
                match.language.lower(),
                name.text,
                parameters,
                match.range,
                match.text.lstrip().startswith("async "),
                bindings,
            )
        )
    return tuple(sorted(set(definitions)))


def _calls(matches: Iterable[SyntaxMatch], definitions: Sequence[Definition]) -> tuple[Call, ...]:
    owners = _group_by_path(definitions)
    calls: list[Call] = []
    for match in matches:
        if match.kind != "call":
            continue
        callee = match.capture("CALLEE")
        if callee is None:
            continue
        arguments = tuple(
            item for item in match.captures_named("ARGS") if item.text not in (",", "(", ")")
        )
        owner = _owner(match.path, match.range, owners)
        identity = content_id(
            {"path": match.path, "start": match.range.start_byte, "callee": callee.text}
        )
        calls.append(
            Call(
                identity,
                match.path,
                match.language.lower(),
                callee.text,
                arguments,
                match.range,
                owner,
            )
        )
    return tuple(sorted(set(calls)))


def _assignments(
    matches: Iterable[SyntaxMatch], definitions: Sequence[Definition]
) -> tuple[Assignment, ...]:
    owners = _group_by_path(definitions)
    assignments: list[Assignment] = []
    for match in matches:
        if match.kind != "assignment":
            continue
        target = match.capture("TARGET")
        value = match.capture("VALUE")
        if target is None or value is None:
            continue
        owner = _owner(match.path, match.range, owners)
        identity = content_id(
            {"path": match.path, "start": match.range.start_byte, "target": target.text}
        )
        assignments.append(
            Assignment(
                identity,
                match.path,
                match.language.lower(),
                target,
                value,
                match.range,
                owner,
            )
        )
    return tuple(sorted(set(assignments)))


def _imports(
    matches: Iterable[SyntaxMatch],
    paths: Sequence[str],
    aliases: Sequence[tuple[str, str, tuple[str, ...]]] = (),
) -> tuple[ImportBinding, ...]:
    imports: list[ImportBinding] = []
    for match in matches:
        if match.kind != "import":
            continue
        module_capture = match.capture("MODULE") or match.capture("NAME")
        if module_capture is None:
            continue
        module = _unquote(module_capture.text)
        symbols = tuple(
            sorted(
                item.text
                for item in (*match.captures_named("NAMES"), *match.captures_named("NAME"))
                if item.text not in (",", "{", "}") and item is not module_capture
            )
        )
        target = _import_target(match.path, module, match.language.lower(), paths, aliases)
        identity = content_id(
            {
                "path": match.path,
                "start": match.range.start_byte,
                "module": module,
                "symbols": symbols,
            }
        )
        imports.append(
            ImportBinding(
                identity,
                match.path,
                match.language.lower(),
                module,
                symbols,
                target,
                match.range,
            )
        )
    return tuple(sorted(set(imports)))


def _classes(matches: Iterable[SyntaxMatch]) -> tuple[ClassRelation, ...]:
    classes: list[ClassRelation] = []
    for match in matches:
        if match.kind != "class":
            continue
        name = match.capture("NAME")
        if name is None:
            continue
        bases = tuple(
            item.text
            for item in (*match.captures_named("BASES"), *match.captures_named("BASE"))
            if item.text != ","
        )
        identity = content_id(
            {"path": match.path, "start": match.range.start_byte, "name": name.text}
        )
        classes.append(
            ClassRelation(
                identity,
                match.path,
                match.language.lower(),
                name.text,
                bases,
                match.range,
            )
        )
    return tuple(sorted(set(classes)))


def _group_by_path[T: _HasPath](items: Iterable[T]) -> dict[str, list[T]]:
    grouped: dict[str, list[T]] = {}
    for item in items:
        grouped.setdefault(item.path, []).append(item)
    return grouped


def _owner(
    path: str, source_range: SourceRange, definitions: Mapping[str, Sequence[Definition]]
) -> str | None:
    candidates = [item for item in definitions.get(path, ()) if item.range.contains(source_range)]
    if not candidates:
        return None
    return min(candidates, key=lambda item: item.range.end_byte - item.range.start_byte).id


MODULE_CONFIG_FILES = ("tsconfig.json", "jsconfig.json")
ECMASCRIPT_LANGUAGES = ("javascript", "tsx", "typescript")
MODULE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_CONFIG_SKIP = frozenset({"node_modules", ".git", "dist", "build", "vendor"})


def module_aliases(root: Path) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    entries: list[tuple[str, str, tuple[str, ...]]] = []
    for name in MODULE_CONFIG_FILES:
        for config in sorted(root.rglob(name)):
            relative = config.relative_to(root)
            if any(part in _CONFIG_SKIP for part in relative.parts):
                continue
            document: object
            try:
                document = json.loads(config.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            options = as_mapping(as_mapping(document).get("compilerOptions"))
            mapping = as_mapping(options.get("paths"))
            if not mapping:
                continue
            base = options.get("baseUrl")
            base_dir = config.parent / base if isinstance(base, str) else config.parent
            try:
                directory = posixpath.normpath(base_dir.resolve().relative_to(root).as_posix())
            except ValueError:
                continue
            directory = "" if directory == "." else directory
            for prefix in sorted(mapping):
                targets = tuple(
                    item for item in as_sequence(mapping[prefix]) if isinstance(item, str)
                )
                if targets:
                    entries.append((directory, str(prefix), targets))
    return tuple(sorted(set(entries)))


def _aliased(module: str, aliases: Sequence[tuple[str, str, tuple[str, ...]]]) -> list[str]:
    mapped: list[str] = []
    for directory, prefix, targets in aliases:
        for target in targets:
            if prefix.endswith("*"):
                head = prefix[:-1]
                if not module.startswith(head):
                    continue
                rest = module[len(head) :]
                value = target[:-1] + rest if target.endswith("*") else target
            elif module == prefix:
                value = target
            else:
                continue
            mapped.append(posixpath.normpath(posixpath.join(directory, value)))
    return mapped


def resolve_specifier(
    path: str,
    module: str,
    language: str,
    paths: Sequence[str],
    aliases: Sequence[tuple[str, str, tuple[str, ...]]] = (),
) -> str | None:
    return _import_target(path, module, language, paths, aliases)


def _import_target(
    path: str,
    module: str,
    language: str,
    paths: Sequence[str],
    aliases: Sequence[tuple[str, str, tuple[str, ...]]] = (),
) -> str | None:
    parent = Path(path).parent
    candidates: list[Path] = []
    if language == "python":
        segments = [segment for segment in module.split(".") if segment]
        if segments:
            base = Path(*segments)
            candidates.extend((parent / base.with_suffix(".py"), parent / base / "__init__.py"))
    elif language in ECMASCRIPT_LANGUAGES:
        bases: list[Path] = []
        if module.startswith("."):
            bases.append(parent / module)
        else:
            bases.extend(Path(item) for item in _aliased(module, aliases))
        for base in bases:
            candidates.extend(base.with_suffix(suffix) for suffix in MODULE_SUFFIXES)
            candidates.extend(base / f"index{suffix}" for suffix in MODULE_SUFFIXES)
    normalized = set(paths)
    return next(
        (
            posixpath.normpath(item.as_posix())
            for item in candidates
            if posixpath.normpath(item.as_posix()) in normalized
        ),
        None,
    )


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"`":
        return value[1:-1]
    return value


def _arity_matches(call: int, definition: int) -> bool:
    return call == definition or definition == 0 or call + 1 == definition


def _parameter_name(
    path: str, capture: Capture, identifiers: Mapping[str, Sequence[SyntaxMatch]]
) -> str:
    inside = [item for item in identifiers.get(path, ()) if capture.range.contains(item.range)]
    if inside:
        return min(inside, key=lambda item: item.range.start_byte).text
    return capture.text


DESTRUCTURED_NAME = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


def narrow_to_property(capture: Capture, name: str) -> Capture | None:
    text = capture.text
    if not text.lstrip().startswith("{"):
        return None
    opening = re.compile(rf"(?<![A-Za-z0-9_$]){re.escape(name)}\s*:\s*")
    found = opening.search(text)
    if found is None:
        return None
    start = found.end()
    depth = 0
    end = len(text)
    for index in range(start, len(text)):
        character = text[index]
        if character in "{[(":
            depth += 1
        elif character in "}])":
            if depth == 0:
                end = index
                break
            depth -= 1
        elif character == "," and depth == 0:
            end = index
            break
    value = text[start:end].strip()
    if not value:
        return None
    offset = start + (len(text[start:end]) - len(text[start:end].lstrip()))
    return Capture(
        capture.name,
        value,
        SourceRange(
            capture.range.start_byte + offset,
            capture.range.start_byte + offset + len(value),
            capture.range.start_line + text.count("\n", 0, offset),
            capture.range.start_line + text.count("\n", 0, offset + len(value)),
        ),
    )


def _bound_names(
    path: str, capture: Capture, identifiers: Mapping[str, Sequence[SyntaxMatch]]
) -> tuple[str, ...]:
    text = capture.text
    head = text.split(":", 1)[0] if text.lstrip().startswith(("{", "[")) else text
    if head.lstrip().startswith(("{", "[")):
        names: set[str] = set()
        for part in head.strip().strip("{}[]").split(","):
            candidate = part.split("=", 1)[0].split(":", 1)[-1].strip().lstrip(".")
            match = DESTRUCTURED_NAME.fullmatch(candidate)
            if match:
                names.add(candidate)
        if names:
            return tuple(sorted(names))
    inside = [item for item in identifiers.get(path, ()) if capture.range.contains(item.range)]
    if not inside:
        return (text,)
    return tuple(sorted({item.text for item in inside if item.text.isidentifier()}))


def _binary(requested: str) -> ToolBinary:
    if requested == AST_GREP:
        shipped = vendored(AST_GREP)
        if shipped is not None:
            return shipped
    return locate(AST_GREP, _executable(requested))


def _executable(requested: str) -> str:
    candidate = Path(requested)
    if candidate.parent != Path(".") or candidate.is_absolute():
        return str(candidate)
    names = [requested]
    if os.name == "nt" and not requested.lower().endswith(".exe"):
        names.insert(0, f"{requested}.exe")
    for name in names:
        resolved = shutil.which(name)
        if resolved is not None and Path(resolved).suffix.lower() == ".exe":
            return resolved
    wrapper = shutil.which(f"{requested}.cmd") if os.name == "nt" else None
    if wrapper is not None:
        npm_binary = Path(wrapper).parent / "node_modules" / "@ast-grep" / "cli" / "ast-grep.exe"
        if npm_binary.is_file():
            return str(npm_binary)
    return requested


__all__ = [
    "AST_GREP_MAXIMUM",
    "AST_GREP_MINIMUM",
    "Assignment",
    "AstGrep",
    "Call",
    "Capture",
    "ClassRelation",
    "Definition",
    "ImportBinding",
    "ImportGraph",
    "SourceRange",
    "SyntaxMatch",
    "build",
    "language_for",
    "module_aliases",
    "narrow_to_property",
    "resolve_specifier",
    "scan_rules",
]
