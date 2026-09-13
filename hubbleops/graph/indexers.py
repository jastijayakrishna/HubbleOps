from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import posixpath
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from hubbleops.core.errors import ToolingFailed, ToolingMissing, ToolingTimeout
from hubbleops.core.precise import SymbolIndex, is_local, package_of, read_index
from hubbleops.core.toolchain import ToolBinary, locate
from hubbleops.graph.imports import language_for

ECMASCRIPT_INDEXER = "scip-typescript"
ECMASCRIPT_LANGUAGES = ("javascript", "tsx", "typescript")
CONFIG_BASENAMES = ("tsconfig.json", "jsconfig.json", "package.json")
PROJECT_CONFIG = "tsconfig.json"
SCRIPT_CONFIG = "jsconfig.json"
PACKAGE_MANIFEST = "package.json"
ROOT_PROJECT = "."
INDEX_TIMEOUT_SECONDS = 900.0
VERSION_TIMEOUT_SECONDS = 30.0
STOP_TIMEOUT_SECONDS = 10.0
OUTPUT_TAIL_LINES = 20
ENTRY_SCRIPT = Path("@sourcegraph") / "scip-typescript" / "dist" / "src" / "main.js"
PYTHON_INDEXER = "scip-python"
PYTHON_LANGUAGES = ("python",)
PYTHON_ENTRY_SCRIPT = Path("@sourcegraph") / "scip-python" / "index.js"
PYTHON_HEAP_MEGABYTES = 4096
WINDOWS_START_FAILURE = (
    "does not start on Windows: its startup builds a regular expression from the path separator"
)
SYNTHESIZED_MANIFEST: Mapping[str, object] = {
    "name": "repository",
    "version": "0.0.0",
    "private": True,
}
FORCED_COMPILER_OPTIONS: Mapping[str, object] = {
    "allowJs": True,
    "skipLibCheck": True,
    "typeRoots": [],
    "types": [],
}
VERSION_LINE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
RECALL_ONLY_LANGUAGES: Mapping[str, str] = {
    "php": "PHP 8.1+, composer and the repository's composer install (scip-php)",
    "java": "a JDK and a full Gradle or Maven build with the semanticdb plugin (scip-java)",
    "csharp": "the .NET SDK, a solution path and a restored package cache (scip-dotnet)",
    "go": "a Go toolchain with the module cache populated (scip-go)",
    "ruby": "a Ruby toolchain with the bundle installed (scip-ruby)",
    "rust": "a Rust toolchain and rust-analyzer's SCIP export over a building workspace",
}


def recall_only_sentence(language: str) -> str:
    return f"recall-only: precise indexing needs {RECALL_ONLY_LANGUAGES[language]}"


@dataclass(frozen=True, slots=True)
class IndexRun:
    indexer: str
    identity: str
    languages: tuple[str, ...]
    index: SymbolIndex
    dropped_documents: int
    projects: tuple[str, ...]
    rewritten_configs: tuple[str, ...]
    first_party: tuple[str, ...] = ()
    dropped_occurrences: int = 0


class ScipIndexer:
    name: str
    languages: tuple[str, ...]
    entry: Path
    config_basenames: tuple[str, ...]

    def __init__(
        self,
        executable: str,
        timeout_seconds: float = INDEX_TIMEOUT_SECONDS,
    ) -> None:
        self.requested = executable
        self.timeout_seconds = timeout_seconds
        self._binary: ToolBinary | None = None
        self._version: str | None = None
        self._identity: str | None = None

    def stage(
        self, root: Path, sources: Sequence[str], configs: Sequence[str], destination: Path
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        raise NotImplementedError(self.name)

    def arguments(self, output: Path, projects: Sequence[str]) -> list[str]:
        raise NotImplementedError(self.name)

    def environment(self) -> Mapping[str, str] | None:
        return None

    def first_party(self, root: Path, configs: Sequence[str]) -> tuple[str, ...]:
        return (str(SYNTHESIZED_MANIFEST["name"]),)

    def binary(self) -> ToolBinary:
        if self._binary is None:
            self._binary = locate(self.name, self.requested)
        return self._binary

    def version(self) -> str:
        if self._version is None:
            completed = self._run(["--version"], None, VERSION_TIMEOUT_SECONDS)
            if completed.returncode != 0:
                raise ToolingFailed(self.name, _tail(completed.stdout))
            reported = next(
                (
                    line.strip()
                    for line in completed.stdout.splitlines()
                    if VERSION_LINE.fullmatch(line.strip())
                ),
                None,
            )
            if reported is None:
                raise ToolingMissing(self.name, "version output is not compatible")
            self._version = reported
        return self._version

    def identity(self) -> str:
        if self._identity is None:
            script = entry_script(self.binary(), self.entry)
            self._identity = f"{self.version()}+sha256:{_sha256(self.name, script)}"
        return self._identity

    def index(self, root: Path, sources: Sequence[str], configs: Sequence[str]) -> IndexRun:
        if not sources:
            raise ValueError("an index run needs at least one source file")
        languages = tuple(sorted({language_for(path) for path in sources}))
        foreign = [language for language in languages if language not in self.languages]
        if foreign:
            raise ValueError(f"{self.name} does not index {', '.join(foreign)} sources")
        version = self.version()
        identity = self.identity()
        with tempfile.TemporaryDirectory(prefix="hops-precise-") as temporary:
            workspace = Path(temporary)
            staged = workspace / "repo"
            output = workspace / "index.scip"
            projects, rewritten = self.stage(root, sources, configs, staged)
            completed = self._run(
                self.arguments(output, projects),
                staged,
                self.timeout_seconds,
                self.environment(),
            )
            if completed.returncode != 0:
                raise ToolingFailed(
                    self.name,
                    f"exit code {completed.returncode}: {_tail(completed.stdout)}",
                )
            if not output.is_file():
                raise ToolingFailed(
                    self.name, f"exit code 0 but no index was written: {_tail(completed.stdout)}"
                )
            payload = output.read_bytes()
        index = read_index(payload)
        if index.tool_version != version:
            raise ToolingFailed(
                self.name,
                f"the index reports tool version {index.tool_version!r} but the binary "
                f"reports {version!r}; a version the binary did not report cannot be "
                "bound to a proof",
            )
        kept, dropped = restrict(index, sources)
        first_party = self.first_party(root, configs)
        kept, dropped_occurrences = restrict_to_first_party(kept, first_party)
        return IndexRun(
            indexer=self.name,
            identity=identity,
            languages=languages,
            index=kept,
            dropped_documents=dropped,
            projects=projects,
            rewritten_configs=rewritten,
            first_party=first_party,
            dropped_occurrences=dropped_occurrences,
        )

    def _run(
        self,
        args: Sequence[str],
        cwd: Path | None,
        timeout: float,
        environment: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        executable = self.binary().path
        try:
            process = subprocess.Popen(
                [executable, *args],
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=None if environment is None else {**os.environ, **environment},
                start_new_session=sys.platform != "win32",
            )
        except OSError as error:
            raise ToolingMissing(self.name, f"{executable!r} is not executable: {error}") from error
        with process:
            try:
                output, _ = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as error:
                _stop(self.name, process)
                raise ToolingTimeout(self.name, timeout) from error
        return subprocess.CompletedProcess(process.args, process.returncode, output, "")


class ScipTypescript(ScipIndexer):
    name = ECMASCRIPT_INDEXER
    languages = ECMASCRIPT_LANGUAGES
    entry = ENTRY_SCRIPT
    config_basenames = CONFIG_BASENAMES

    def __init__(
        self,
        executable: str = ECMASCRIPT_INDEXER,
        timeout_seconds: float = INDEX_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(executable, timeout_seconds)

    def stage(
        self, root: Path, sources: Sequence[str], configs: Sequence[str], destination: Path
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return stage(root, sources, configs, destination)

    def arguments(self, output: Path, projects: Sequence[str]) -> list[str]:
        return ["index", "--no-progress-bar", "--output", str(output), *projects]

    def first_party(self, root: Path, configs: Sequence[str]) -> tuple[str, ...]:
        names = {str(SYNTHESIZED_MANIFEST["name"])}
        for relative in configs:
            normalized = _relative(relative)
            if posixpath.basename(normalized) != PACKAGE_MANIFEST:
                continue
            try:
                document = json.loads((root / normalized).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(document, dict):
                name = cast(dict[str, object], document).get("name")
                if isinstance(name, str) and name:
                    names.add(name)
        return tuple(sorted(names))


class ScipPython(ScipIndexer):
    name = PYTHON_INDEXER
    languages = PYTHON_LANGUAGES
    entry = PYTHON_ENTRY_SCRIPT
    config_basenames = ()

    def __init__(
        self,
        executable: str = PYTHON_INDEXER,
        timeout_seconds: float = INDEX_TIMEOUT_SECONDS,
        heap_megabytes: int = PYTHON_HEAP_MEGABYTES,
        platform: str = sys.platform,
    ) -> None:
        super().__init__(executable, timeout_seconds)
        self.heap_megabytes = heap_megabytes
        self.platform = platform

    def version(self) -> str:
        if self.platform == "win32":
            raise ToolingMissing(self.name, WINDOWS_START_FAILURE)
        return super().version()

    def stage(
        self, root: Path, sources: Sequence[str], configs: Sequence[str], destination: Path
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        destination.mkdir(parents=True, exist_ok=True)
        for relative in sorted(set(sources)):
            _copy(root, _relative(relative), destination)
        return (ROOT_PROJECT,), ()

    def arguments(self, output: Path, projects: Sequence[str]) -> list[str]:
        return [
            "index",
            *projects,
            "--project-name",
            str(SYNTHESIZED_MANIFEST["name"]),
            "--project-version",
            str(SYNTHESIZED_MANIFEST["version"]),
            "--output",
            str(output),
        ]

    def environment(self) -> Mapping[str, str]:
        return {"NODE_OPTIONS": f"--max-old-space-size={self.heap_megabytes}"}


def runners(executables: Mapping[str, str] | None = None) -> tuple[ScipIndexer, ...]:
    chosen = dict(executables or {})
    return (
        ScipTypescript(chosen.get(ECMASCRIPT_INDEXER, ECMASCRIPT_INDEXER)),
        ScipPython(chosen.get(PYTHON_INDEXER, PYTHON_INDEXER)),
    )


def entry_script(binary: ToolBinary, entry: Path = ENTRY_SCRIPT) -> Path:
    located = Path(binary.path)
    shim_layout = located.parent / "node_modules" / entry
    if shim_layout.is_file():
        return shim_layout
    for ancestor in located.resolve().parents:
        candidate = ancestor / entry
        if candidate.is_file():
            return candidate
    return located


def stage(
    root: Path, sources: Sequence[str], configs: Sequence[str], destination: Path
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    destination.mkdir(parents=True, exist_ok=True)
    for relative in sorted(set(sources)):
        _copy(root, _relative(relative), destination)
    staged: set[str] = set()
    rewritten: set[str] = set()
    projects: set[str] = set()
    by_directory: dict[str, set[str]] = {}
    for relative in sorted(set(configs)):
        normalized = _relative(relative)
        name = posixpath.basename(normalized)
        if name not in CONFIG_BASENAMES:
            raise ValueError(f"{relative!r} is not one of {', '.join(CONFIG_BASENAMES)}")
        by_directory.setdefault(posixpath.dirname(normalized), set()).add(name)
    for directory in sorted(by_directory):
        names = by_directory[directory]
        if PACKAGE_MANIFEST in names:
            _copy(root, posixpath.join(directory, PACKAGE_MANIFEST), destination)
        if PROJECT_CONFIG in names:
            source = posixpath.join(directory, PROJECT_CONFIG)
            _stage_config(root, source, source, destination, staged, rewritten)
            projects.add(directory or ROOT_PROJECT)
        elif SCRIPT_CONFIG in names:
            source = posixpath.join(directory, SCRIPT_CONFIG)
            target = posixpath.join(directory, PROJECT_CONFIG)
            _stage_config(root, source, target, destination, staged, rewritten)
            rewritten.add(target)
            projects.add(directory or ROOT_PROJECT)
    if PACKAGE_MANIFEST not in by_directory.get("", set()):
        _write_json(destination / PACKAGE_MANIFEST, SYNTHESIZED_MANIFEST)
    if not projects:
        _write_json(destination / PROJECT_CONFIG, _synthesized_config())
        rewritten.add(PROJECT_CONFIG)
        projects.add(ROOT_PROJECT)
    return tuple(sorted(projects)), tuple(sorted(rewritten))


def restrict_to_first_party(
    index: SymbolIndex, first_party: Sequence[str]
) -> tuple[SymbolIndex, int]:
    allowed = set(first_party)

    def owned(symbol: str) -> bool:
        if is_local(symbol):
            return True
        package = package_of(symbol)
        return package is not None and package[0] in allowed

    occurrences = tuple(item for item in index.occurrences if owned(item.symbol))
    dropped = len(index.occurrences) - len(occurrences)
    if dropped == 0:
        return index, 0
    populated = {item.path for item in occurrences}
    documents = tuple(path for path in index.documents if path in populated)
    return dataclasses.replace(index, documents=documents, occurrences=occurrences), dropped


def restrict(index: SymbolIndex, sources: Sequence[str]) -> tuple[SymbolIndex, int]:
    allowed = {_relative(path) for path in sources}
    documents = tuple(path for path in index.documents if path in allowed)
    occurrences = tuple(item for item in index.occurrences if item.path in allowed)
    dropped = len(index.documents) - len(documents)
    if dropped == 0:
        return index, 0
    return dataclasses.replace(index, documents=documents, occurrences=occurrences), dropped


def derive_tsconfig(text: str, config_dir: Path) -> tuple[str, bool]:
    document = _parse_config(text)
    dropped = False
    if "extends" in document:
        kept, dropped = _resolved_extends(document["extends"], config_dir)
        if not kept:
            del document["extends"]
        elif isinstance(document["extends"], str):
            document["extends"] = kept[0]
        else:
            document["extends"] = kept
    options = document.get("compilerOptions", {})
    if not isinstance(options, dict):
        raise ValueError("compilerOptions is not an object")
    document["compilerOptions"] = {**cast(dict[str, Any], options), **FORCED_COMPILER_OPTIONS}
    return _json_text(document), dropped


def strip_jsonc(text: str) -> str:
    without_comments = _without_comments(text.removeprefix("﻿"))
    return _without_trailing_commas(without_comments)


def _stage_config(
    root: Path,
    source: str,
    target: str,
    destination: Path,
    staged: set[str],
    rewritten: set[str],
) -> None:
    if target in staged:
        return
    staged.add(target)
    staged_path = destination / target
    staged_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        text = (root / source).read_text(encoding="utf-8")
        document = _parse_config(text)
    except (UnicodeDecodeError, ValueError):
        _write_json(staged_path, _synthesized_config())
        rewritten.add(target)
        return
    for base in _extends_values(document.get("extends")):
        resolved = _resolve_extends(base, Path(posixpath.dirname(source)))
        if resolved is None:
            continue
        candidate = root / resolved
        try:
            relative = candidate.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        if candidate.is_file():
            _stage_config(root, relative, relative, destination, staged, rewritten)
    try:
        derived, dropped = derive_tsconfig(text, staged_path.parent)
    except ValueError:
        _write_json(staged_path, _synthesized_config())
        rewritten.add(target)
        return
    staged_path.write_text(derived, encoding="utf-8")
    if dropped:
        rewritten.add(target)


def _resolved_extends(value: object, config_dir: Path) -> tuple[list[str], bool]:
    kept: list[str] = []
    dropped = False
    for base in _extends_values(value):
        resolved = _resolve_extends(base, config_dir)
        if resolved is not None and resolved.is_file():
            kept.append(base)
        else:
            dropped = True
    return kept, dropped


def _extends_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in cast(list[object], value) if isinstance(item, str)]
    return []


def _resolve_extends(base: str, config_dir: Path) -> Path | None:
    if base.startswith(("./", "../")) or base in (".", ".."):
        target = config_dir / base
        return target if target.suffix == ".json" else target.with_name(target.name + ".json")
    if base.endswith(".json") and "/" not in base and not Path(base).is_absolute():
        return config_dir / base
    return None


def _parse_config(text: str) -> dict[str, Any]:
    try:
        document: object = json.loads(strip_jsonc(text))
    except (json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f"tsconfig is not parseable: {error}") from error
    if not isinstance(document, dict):
        raise ValueError("tsconfig root is not an object")
    return cast(dict[str, Any], document)


def _without_comments(text: str) -> str:
    output: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character == '"':
            end = _string_end(text, index)
            output.append(text[index:end])
            index = end
        elif text.startswith("//", index):
            newline = text.find("\n", index)
            index = length if newline == -1 else newline
        elif text.startswith("/*", index):
            close = text.find("*/", index + 2)
            index = length if close == -1 else close + 2
        else:
            output.append(character)
            index += 1
    return "".join(output)


def _without_trailing_commas(text: str) -> str:
    output: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character == '"':
            end = _string_end(text, index)
            output.append(text[index:end])
            index = end
        elif character == ",":
            following = index + 1
            while following < length and text[following].isspace():
                following += 1
            if following < length and text[following] in "}]":
                index += 1
                continue
            output.append(character)
            index += 1
        else:
            output.append(character)
            index += 1
    return "".join(output)


def _string_end(text: str, opening: int) -> int:
    index = opening + 1
    while index < len(text):
        character = text[index]
        if character == "\\":
            index += 2
            continue
        if character == '"':
            return index + 1
        index += 1
    return len(text)


def _synthesized_config() -> dict[str, object]:
    return {"compilerOptions": dict(FORCED_COMPILER_OPTIONS)}


def _write_json(path: Path, document: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_text(document), encoding="utf-8")


def _json_text(document: Mapping[str, object]) -> str:
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def _copy(root: Path, relative: str, destination: Path) -> None:
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / relative, target)


def _relative(path: str) -> str:
    normalized = posixpath.normpath(path.replace("\\", "/"))
    if normalized.startswith(("/", "../")) or normalized == ".." or Path(path).is_absolute():
        raise ValueError(f"{path!r} does not stay inside the repository")
    return "" if normalized == "." else normalized


def _tail(output: str) -> str:
    lines = [line for line in output.splitlines() if line.strip()]
    return "\n".join(lines[-OUTPUT_TAIL_LINES:]) or "no output"


def _sha256(name: str, path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as error:
        raise ToolingMissing(name, f"entry script unreadable: {path}: {error}") from error
    return digest.hexdigest()


def _stop(name: str, process: subprocess.Popen[str]) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
                timeout=STOP_TIMEOUT_SECONDS,
            )
        elif process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.kill()
        process.wait(timeout=STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise ToolingFailed(
            name, f"process {process.pid} survived termination for {STOP_TIMEOUT_SECONDS:g}s"
        ) from error


__all__ = [
    "CONFIG_BASENAMES",
    "ECMASCRIPT_INDEXER",
    "ECMASCRIPT_LANGUAGES",
    "PYTHON_INDEXER",
    "PYTHON_LANGUAGES",
    "RECALL_ONLY_LANGUAGES",
    "IndexRun",
    "ScipIndexer",
    "ScipPython",
    "ScipTypescript",
    "derive_tsconfig",
    "entry_script",
    "recall_only_sentence",
    "restrict",
    "restrict_to_first_party",
    "runners",
    "stage",
    "strip_jsonc",
]
