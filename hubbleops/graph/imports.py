from __future__ import annotations

import json
import os
import posixpath
import re
import shutil
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from hubbleops.core.canonical import canonical_text, content_id
from hubbleops.core.errors import ToolingFailed, ToolingMissing, ToolingTimeout
from hubbleops.core.records import as_mapping, as_sequence

AST_GREP_MINIMUM = (0, 45, 0)
AST_GREP_MAXIMUM = (0, 46, 0)
AST_GREP_TIMEOUT_SECONDS = 30.0
LANGUAGE_EXTENSIONS = {
    ".cs": "csharp",
    ".go": "go",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".json": "json",
    ".md": "markdown",
    ".php": "php",
    ".py": "python",
    ".pyi": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
}
AST_GREP_LANGUAGES = {
    "javascript": "javascript",
    "php": "php",
    "python": "python",
    "typescript": "typescript",
}


@dataclass(frozen=True, order=True, slots=True)
class SourceRange:
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int

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

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "language": self.language,
            "name": self.name,
            "parameters": list(self.parameters),
            "range": self.range.to_mapping(),
            "asynchronous": self.asynchronous,
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
class ImportGraph:
    definitions: tuple[Definition, ...]
    calls: tuple[Call, ...]
    assignments: tuple[Assignment, ...]
    imports: tuple[ImportBinding, ...]
    classes: tuple[ClassRelation, ...]
    decorators: tuple[SyntaxMatch, ...]
    registrations: tuple[SyntaxMatch, ...]
    atoms: tuple[SyntaxMatch, ...]
    parse_errors: tuple[SyntaxMatch, ...]
    concatenations: tuple[SyntaxMatch, ...]
    formats: tuple[SyntaxMatch, ...]

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
            "parse_errors": [item.to_mapping() for item in self.parse_errors],
            "concatenations": [item.to_mapping() for item in self.concatenations],
            "formats": [item.to_mapping() for item in self.formats],
        }
        return f"{canonical_text(value)}\n".encode()

    def definitions_named(self, name: str) -> tuple[Definition, ...]:
        tail = name.rsplit(".", 1)[-1]
        return tuple(item for item in self.definitions if item.name == tail)

    def definition(self, identifier: str | None) -> Definition | None:
        return next((item for item in self.definitions if item.id == identifier), None)

    def callers(self, definition: Definition) -> tuple[Call, ...]:
        return tuple(
            item
            for item in self.calls
            if item.callee.rsplit(".", 1)[-1] == definition.name
            and _arity_matches(len(item.arguments), len(definition.parameters))
        )

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
    Query("parse_error", node_kind="ERROR"),
    Query("concatenation", "$LEFT + $RIGHT"),
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
    "typescript": (
        Query("definition", "function $NAME($$$PARAMS) { $$$BODY }"),
        Query("definition", "async function $NAME($$$PARAMS) { $$$BODY }"),
        Query("definition", "const $NAME = ($$$PARAMS) => $BODY"),
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
    ),
}


class AstGrep:
    def __init__(
        self,
        executable: str = "ast-grep",
        timeout_seconds: float = AST_GREP_TIMEOUT_SECONDS,
    ) -> None:
        self.executable = _executable(executable)
        self.timeout_seconds = timeout_seconds

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
        return match.group("version")

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
        args.extend(["-l", AST_GREP_LANGUAGES[language], "--json=compact", *paths])
        return self.decode(self._run(args, root).stdout, root, query.kind)

    def rule(
        self,
        root: Path,
        paths: Sequence[str],
        language: str,
        rule_path: Path,
    ) -> tuple[SyntaxMatch, ...]:
        if not paths:
            return ()
        args = ["scan", "-r", str(rule_path), "--json=compact", *paths]
        return self.decode(self._run(args, root).stdout, root, f"rule:{language}")

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
        except FileNotFoundError as error:
            raise ToolingMissing("ast-grep", f"{self.executable!r} is not executable") from error
        except PermissionError as error:
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
        try:
            decoded: object = json.loads(payload or "[]")
        except json.JSONDecodeError as error:
            raise ToolingFailed("ast-grep", "output was malformed JSON") from error
        if not isinstance(decoded, list):
            raise ToolingFailed("ast-grep", "output JSON root was not an array")
        entries = cast(list[object], decoded)
        matches = tuple(_decode_match(item, root, kind) for item in entries)
        return tuple(sorted(set(matches)))


def language_for(path: str) -> str:
    return LANGUAGE_EXTENSIONS.get(Path(path).suffix.lower(), "unknown")


def build(
    root: Path,
    paths_by_language: Mapping[str, Sequence[str]],
    runner: AstGrep,
) -> ImportGraph:
    matches: list[SyntaxMatch] = []
    for language in sorted(paths_by_language):
        paths = tuple(sorted(paths_by_language[language]))
        for query in (*COMMON_QUERIES, *LANGUAGE_QUERIES.get(language, ())):
            matches.extend(runner.query(root, paths, language, query))
    unique = tuple(sorted(set(matches)))
    definitions = _definitions(unique)
    calls = _calls(unique, definitions)
    assignments = _assignments(unique, definitions)
    all_paths = tuple(path for paths in paths_by_language.values() for path in paths)
    imports = _imports(unique, all_paths)
    classes = _classes(unique)
    decorators = tuple(item for item in unique if item.kind == "decorator")
    registrations = tuple(item for item in unique if item.kind == "registration")
    atoms = tuple(
        item for item in unique if item.kind in ("identifier", "string", "template_string")
    )
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
        parse_errors=parse_errors,
        concatenations=concatenations,
        formats=formats,
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
    identifiers = tuple(item for item in materialized if item.kind == "identifier")
    definitions: list[Definition] = []
    for match in materialized:
        if match.kind != "definition":
            continue
        name = match.capture("NAME")
        if name is None:
            continue
        parameters = tuple(
            _parameter_name(match.path, item, identifiers)
            for item in match.captures_named("PARAMS")
            if item.text not in (",", "(", ")")
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
            )
        )
    return tuple(sorted(set(definitions)))


def _calls(matches: Iterable[SyntaxMatch], definitions: Sequence[Definition]) -> tuple[Call, ...]:
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
        owner = _owner(match.path, match.range, definitions)
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
    assignments: list[Assignment] = []
    for match in matches:
        if match.kind != "assignment":
            continue
        target = match.capture("TARGET")
        value = match.capture("VALUE")
        if target is None or value is None:
            continue
        owner = _owner(match.path, match.range, definitions)
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


def _imports(matches: Iterable[SyntaxMatch], paths: Sequence[str]) -> tuple[ImportBinding, ...]:
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
        target = _import_target(match.path, module, match.language.lower(), paths)
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


def _owner(path: str, source_range: SourceRange, definitions: Sequence[Definition]) -> str | None:
    candidates = [
        item for item in definitions if item.path == path and item.range.contains(source_range)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda item: item.range.end_byte - item.range.start_byte).id


def _import_target(path: str, module: str, language: str, paths: Sequence[str]) -> str | None:
    parent = Path(path).parent
    candidates: list[Path] = []
    if language == "python":
        base = Path(*module.split("."))
        candidates.extend((parent / base.with_suffix(".py"), parent / base / "__init__.py"))
    elif language in ("javascript", "typescript") and module.startswith("."):
        base = parent / module
        candidates.extend(base.with_suffix(suffix) for suffix in (".ts", ".tsx", ".js", ".jsx"))
        candidates.extend(base / f"index{suffix}" for suffix in (".ts", ".tsx", ".js", ".jsx"))
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


def _parameter_name(path: str, capture: Capture, identifiers: Sequence[SyntaxMatch]) -> str:
    inside = [
        item for item in identifiers if item.path == path and capture.range.contains(item.range)
    ]
    if inside:
        return min(inside, key=lambda item: item.range.start_byte).text
    return capture.text


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
    "scan_rules",
]
