from __future__ import annotations

import ast
import configparser
import json
import re
import tomllib
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import yaml

from hubbleops.closure.source_closure import SourceClosure
from hubbleops.core.canonical import EMPTY_SHA256, content_id
from hubbleops.core.evidence import make_evidence
from hubbleops.core.observer import ObserverContext
from hubbleops.core.records import as_mapping, as_sequence, as_text, is_mapping

NAME = "deps"

LOCK = "lock"
MANIFEST = "manifest"

EXACT_FILENAMES: dict[str, tuple[str, str]] = {
    "Gemfile": ("ruby", MANIFEST),
    "Gemfile.lock": ("ruby", LOCK),
    "Pipfile": ("python", MANIFEST),
    "Pipfile.lock": ("python", LOCK),
    "build.gradle": ("java", MANIFEST),
    "build.gradle.kts": ("java", MANIFEST),
    "composer.json": ("php", MANIFEST),
    "composer.lock": ("php", LOCK),
    "go.mod": ("go", MANIFEST),
    "go.sum": ("go", LOCK),
    "gradle.lockfile": ("java", LOCK),
    "npm-shrinkwrap.json": ("javascript", LOCK),
    "package-lock.json": ("javascript", LOCK),
    "package.json": ("javascript", MANIFEST),
    "packages.config": ("dotnet", MANIFEST),
    "packages.lock.json": ("dotnet", LOCK),
    "pnpm-lock.yaml": ("javascript", LOCK),
    "pom.xml": ("java", MANIFEST),
    "poetry.lock": ("python", LOCK),
    "pyproject.toml": ("python", MANIFEST),
    "setup.cfg": ("python", MANIFEST),
    "setup.py": ("python", MANIFEST),
    "uv.lock": ("python", LOCK),
    "yarn.lock": ("javascript", LOCK),
}

REQUIREMENT_LINE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?P<extras>\[[^\]]*\])?\s*(?P<spec>[<>=!~^].*)?$"
)
EXACT_PIN = re.compile(r"^\s*==\s*(?P<version>[0-9][^,;\s]*)\s*$")
NPM_EXACT = re.compile(r"^\s*[0-9][0-9A-Za-z.+-]*\s*$")
GRADLE_COORDINATE = re.compile(
    r"""['"](?P<group>[A-Za-z0-9_.\-]+):(?P<artifact>[A-Za-z0-9_.\-]+):(?P<version>[A-Za-z0-9_.+\-]+)['"]"""
)
GRADLE_LOCK_LINE = re.compile(
    r"^(?P<group>[A-Za-z0-9_.\-]+):(?P<artifact>[A-Za-z0-9_.\-]+):(?P<version>[^=\s]+)="
)
GEMFILE_GEM = re.compile(r"""^\s*gem\s+['"](?P<name>[^'"]+)['"](?P<rest>.*)$""")
GEMFILE_LOCK_SPEC = re.compile(r"^ {4}(?P<name>[A-Za-z0-9._\-]+) \((?P<version>[^)]+)\)\s*$")
GO_REQUIRE_LINE = re.compile(r"^\s*(?P<name>[^\s]+)\s+(?P<version>v[0-9][^\s]*)")
GO_SUM_LINE = re.compile(r"^(?P<name>[^\s]+)\s+(?P<version>v[0-9][^\s/]*)(/go\.mod)?\s+h1:")
XML_ENTITY = re.compile(r"<!ENTITY", re.IGNORECASE)
PIP_INCLUDE_OPTION = re.compile(r"^(?:-r|-c|-e|--requirement|--constraint|--editable)(?:[=\s]|$)")
GRADLE_DECLARATION = re.compile(
    r"^\s*(?:testImplementation|androidTestImplementation|debugImplementation"
    r"|releaseImplementation|testCompileOnly|testRuntimeOnly|providedCompile|providedRuntime"
    r"|annotationProcessor|developmentOnly|implementation|compileOnly|runtimeOnly|testCompile"
    r"|classpath|compile|kapt|ksp|api)\s*[\s(]"
)
GRADLE_LOCAL_DEPENDENCY = re.compile(r"\b(?:project|files|fileTree|gradleApi|localGroovy)\s*\(")
GRADLE_LOCK_IGNORED = re.compile(r"^(?:#|empty=)")
GEMFILE_DELEGATION = re.compile(r"^\s*(?P<directive>gemspec|eval_gemfile|instance_eval)\b")
UNRESOLVED_LIMIT = 200

UNEVALUATED = "UNEVALUATED_DEPENDENCIES"


@dataclass(frozen=True, slots=True)
class RawDependency:
    name: str
    version: str | None
    spec: str | None
    line: int | None


@dataclass(frozen=True, slots=True)
class RawUnresolved:
    field: str
    expression: str
    line: int | None
    reason: str


@dataclass(frozen=True, slots=True)
class ManifestParse:
    dependencies: tuple[RawDependency, ...]
    unresolved: tuple[RawUnresolved, ...]


@dataclass(frozen=True, slots=True)
class UnresolvedExpression:
    ecosystem: str
    source_kind: str
    path: str
    field: str
    expression: str
    line: int | None
    reason: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "ecosystem": self.ecosystem,
            "source_kind": self.source_kind,
            "path": self.path,
            "field": self.field,
            "expression": self.expression,
            "line": self.line,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ParsedDependency:
    ecosystem: str
    source_kind: str
    path: str
    name: str
    version: str | None
    spec: str | None
    line: int | None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "ecosystem": self.ecosystem,
            "source_kind": self.source_kind,
            "path": self.path,
            "name": self.name,
            "version": self.version,
            "spec": self.spec,
            "line": self.line,
        }


@dataclass(frozen=True, slots=True)
class ManifestFile:
    path: str
    ecosystem: str
    source_kind: str
    blob_sha: str | None
    error: str | None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "ecosystem": self.ecosystem,
            "source_kind": self.source_kind,
            "blob_sha": self.blob_sha,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class DependencyResolution:
    files: tuple[ManifestFile, ...]
    dependencies: tuple[ParsedDependency, ...]
    unresolved: tuple[UnresolvedExpression, ...] = ()

    def resolution_hash(self) -> str:
        return content_id(
            {
                "files": [item.to_mapping() for item in self.files],
                "dependencies": [item.to_mapping() for item in self.dependencies],
                "unresolved": [item.to_mapping() for item in self.unresolved],
            }
        )

    def unresolved_paths(self) -> frozenset[str]:
        return frozenset(item.path for item in self.unresolved)

    def fully_read(self, item: ManifestFile) -> bool:
        return item.error is None and item.path not in self.unresolved_paths()

    def locked_ecosystems(self) -> tuple[str, ...]:
        holes = self.unresolved_paths()
        return tuple(
            sorted(
                {
                    item.ecosystem
                    for item in self.files
                    if item.source_kind == LOCK and item.error is None and item.path not in holes
                }
            )
        )

    def manifest_ecosystems(self) -> tuple[str, ...]:
        return tuple(sorted({item.ecosystem for item in self.files if item.error is None}))

    def locks_for(self, ecosystem: str) -> tuple[str, ...]:
        holes = self.unresolved_paths()
        return tuple(
            sorted(
                item.path
                for item in self.files
                if item.ecosystem == ecosystem
                and item.source_kind == LOCK
                and item.error is None
                and item.path not in holes
            )
        )

    def manifests_for(self, ecosystem: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                item.path
                for item in self.files
                if item.ecosystem == ecosystem and item.error is None
            )
        )

    def unresolved_for(self, path: str) -> tuple[UnresolvedExpression, ...]:
        return tuple(item for item in self.unresolved if item.path == path)


def classify_manifest(relative_path: str) -> tuple[str, str] | None:
    filename = relative_path.rsplit("/", 1)[-1]
    exact = EXACT_FILENAMES.get(filename)
    if exact is not None:
        return exact
    lowered = filename.lower()
    if lowered.startswith("requirements") and lowered.endswith(".txt"):
        return ("python", MANIFEST)
    if lowered.startswith("constraints") and lowered.endswith(".txt"):
        return ("python", MANIFEST)
    if lowered.endswith(".csproj") or lowered.endswith(".vbproj"):
        return ("dotnet", MANIFEST)
    return None


def is_manifest(relative_path: str) -> bool:
    return classify_manifest(relative_path) is not None


def normalize_package(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def resolve(closure: SourceClosure) -> DependencyResolution:
    files: list[ManifestFile] = []
    dependencies: list[ParsedDependency] = []
    unresolved: list[UnresolvedExpression] = []
    for entry in closure.scannable():
        kind = classify_manifest(entry.path)
        if kind is None:
            continue
        ecosystem, source_kind = kind
        text = _read_text(closure, entry.path)
        if text is None:
            files.append(
                ManifestFile(
                    path=entry.path,
                    ecosystem=ecosystem,
                    source_kind=source_kind,
                    blob_sha=entry.blob_sha,
                    error="unreadable or not UTF-8 decodable",
                )
            )
            continue
        try:
            raw = _parse(entry.path, ecosystem, source_kind, text)
        except ManifestParseError as error:
            files.append(
                ManifestFile(
                    path=entry.path,
                    ecosystem=ecosystem,
                    source_kind=source_kind,
                    blob_sha=entry.blob_sha,
                    error=str(error),
                )
            )
            continue
        files.append(
            ManifestFile(
                path=entry.path,
                ecosystem=ecosystem,
                source_kind=source_kind,
                blob_sha=entry.blob_sha,
                error=None,
            )
        )
        for item in raw.dependencies:
            dependencies.append(
                ParsedDependency(
                    ecosystem=ecosystem,
                    source_kind=source_kind,
                    path=entry.path,
                    name=item.name,
                    version=item.version,
                    spec=item.spec,
                    line=item.line,
                )
            )
        for hole in raw.unresolved:
            unresolved.append(
                UnresolvedExpression(
                    ecosystem=ecosystem,
                    source_kind=source_kind,
                    path=entry.path,
                    field=hole.field,
                    expression=hole.expression,
                    line=hole.line,
                    reason=hole.reason,
                )
            )
    return DependencyResolution(
        files=tuple(sorted(files, key=lambda item: item.path)),
        dependencies=tuple(
            sorted(dependencies, key=lambda item: (item.path, item.name, item.version or ""))
        ),
        unresolved=tuple(
            sorted(
                unresolved,
                key=lambda item: (item.path, item.line or 0, item.field, item.expression),
            )
        ),
    )


def scan(
    closure: SourceClosure,
    ctx: ObserverContext,
    resolution: DependencyResolution | None = None,
) -> list[dict[str, Any]]:
    state = resolution if resolution is not None else resolve(closure)
    blobs = {entry.path: entry.blob_sha for entry in closure.entries}
    targets = {normalize_package(name): name for name in ctx.surface.package_names}
    records: list[dict[str, Any]] = []
    matched_ecosystems: set[str] = set()

    for dependency in state.dependencies:
        subject = targets.get(normalize_package(dependency.name))
        if subject is None:
            continue
        matched_ecosystems.add(dependency.ecosystem)
        records.append(
            make_evidence(
                run_id=ctx.run_id,
                proof_scope_hash=ctx.proof_scope_hash,
                claim_type="sdk_installed",
                observer=NAME,
                repo_sha=ctx.repo_sha,
                path=dependency.path,
                line_start=dependency.line,
                line_end=dependency.line,
                source_hash=blobs.get(dependency.path) or EMPTY_SHA256,
                value={
                    "state": "PRESENT",
                    "ecosystem": dependency.ecosystem,
                    "package": dependency.name,
                    "version": dependency.version,
                    "spec": dependency.spec,
                    "source_kind": dependency.source_kind,
                },
                provider_subject=subject,
                dependency_context_hash=ctx.dependency_context_hash,
                derivation="OBSERVED",
                confidence="PROVEN" if dependency.source_kind == LOCK else "DOCUMENTED",
            )
        )

    for ecosystem in state.locked_ecosystems():
        if ecosystem in matched_ecosystems:
            continue
        locks = state.locks_for(ecosystem)
        records.append(
            make_evidence(
                run_id=ctx.run_id,
                proof_scope_hash=ctx.proof_scope_hash,
                claim_type="sdk_installed",
                observer=NAME,
                repo_sha=ctx.repo_sha,
                path=locks[0],
                line_start=None,
                line_end=None,
                source_hash=blobs.get(locks[0]) or EMPTY_SHA256,
                value={
                    "state": "ABSENT",
                    "ecosystem": ecosystem,
                    "source_kind": LOCK,
                    "locks": list(locks),
                },
                provider_subject=None,
                dependency_context_hash=ctx.dependency_context_hash,
                derivation="OBSERVED",
                confidence="PROVEN",
            )
        )

    for ecosystem in state.manifest_ecosystems():
        if ecosystem in matched_ecosystems or state.locks_for(ecosystem):
            continue
        manifests = state.manifests_for(ecosystem)
        records.append(
            make_evidence(
                run_id=ctx.run_id,
                proof_scope_hash=ctx.proof_scope_hash,
                claim_type="dependency_state",
                observer=NAME,
                repo_sha=ctx.repo_sha,
                path=manifests[0],
                line_start=None,
                line_end=None,
                source_hash=blobs.get(manifests[0]) or EMPTY_SHA256,
                value={
                    "state": "UNRESOLVED_ECOSYSTEM",
                    "ecosystem": ecosystem,
                    "manifests": list(manifests),
                },
                provider_subject=None,
                dependency_context_hash=ctx.dependency_context_hash,
                derivation="OBSERVED",
                confidence="RAW",
            )
        )

    for item in state.files:
        if item.error is None:
            continue
        records.append(
            make_evidence(
                run_id=ctx.run_id,
                proof_scope_hash=ctx.proof_scope_hash,
                claim_type="dependency_state",
                observer=NAME,
                repo_sha=ctx.repo_sha,
                path=item.path,
                line_start=None,
                line_end=None,
                source_hash=item.blob_sha or EMPTY_SHA256,
                value={
                    "state": "UNPARSABLE",
                    "ecosystem": item.ecosystem,
                    "source_kind": item.source_kind,
                    "detail": item.error,
                },
                provider_subject=None,
                dependency_context_hash=ctx.dependency_context_hash,
                derivation="OBSERVED",
                confidence="RAW",
            )
        )

    for hole in state.unresolved:
        records.append(
            make_evidence(
                run_id=ctx.run_id,
                proof_scope_hash=ctx.proof_scope_hash,
                claim_type="dependency_state",
                observer=NAME,
                repo_sha=ctx.repo_sha,
                path=hole.path,
                line_start=hole.line,
                line_end=hole.line,
                source_hash=blobs.get(hole.path) or EMPTY_SHA256,
                value={
                    "state": UNEVALUATED,
                    "ecosystem": hole.ecosystem,
                    "source_kind": hole.source_kind,
                    "field": hole.field,
                    "expression": hole.expression,
                    "reason": hole.reason,
                },
                provider_subject=None,
                dependency_context_hash=ctx.dependency_context_hash,
                derivation="OBSERVED",
                confidence="RAW",
            )
        )

    if not state.files:
        records.append(
            make_evidence(
                run_id=ctx.run_id,
                proof_scope_hash=ctx.proof_scope_hash,
                claim_type="dependency_state",
                observer=NAME,
                repo_sha=ctx.repo_sha,
                path=".",
                line_start=None,
                line_end=None,
                source_hash=EMPTY_SHA256,
                value={"state": "NO_MANIFEST", "ecosystem": None},
                provider_subject=None,
                dependency_context_hash=ctx.dependency_context_hash,
                derivation="OBSERVED",
                confidence="RAW",
            )
        )
    return records


class ManifestParseError(Exception):
    pass


def _read_text(closure: SourceClosure, relative: str) -> str | None:
    try:
        return (closure.root / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _parse(path: str, ecosystem: str, source_kind: str, text: str) -> ManifestParse:
    parsed = _dispatch(path, ecosystem, source_kind, text)
    if isinstance(parsed, ManifestParse):
        return parsed
    return ManifestParse(dependencies=tuple(parsed), unresolved=())


def _condense(expression: str) -> str:
    collapsed = " ".join(expression.split())
    if len(collapsed) <= UNRESOLVED_LIMIT:
        return collapsed
    return collapsed[:UNRESOLVED_LIMIT] + "..."


def _hole(field: str, expression: str, line: int | None, reason: str) -> RawUnresolved:
    return RawUnresolved(field=field, expression=_condense(expression), line=line, reason=reason)


def _dispatch(
    path: str, ecosystem: str, source_kind: str, text: str
) -> list[RawDependency] | ManifestParse:
    filename = path.rsplit("/", 1)[-1]
    lowered = filename.lower()
    if filename == "pyproject.toml":
        return _parse_pyproject(text)
    if filename in ("poetry.lock", "uv.lock"):
        return _parse_python_lock(text)
    if filename == "Pipfile.lock":
        return _parse_pipfile_lock(text)
    if filename == "Pipfile":
        return _parse_pipfile(text)
    if filename == "setup.cfg":
        return _parse_setup_cfg(text)
    if filename == "setup.py":
        return _parse_setup_py(text)
    if ecosystem == "python" and lowered.endswith(".txt"):
        return _parse_requirements(text)
    if filename == "package.json":
        return _parse_package_json(text)
    if filename in ("package-lock.json", "npm-shrinkwrap.json"):
        return _parse_package_lock(text)
    if filename == "yarn.lock":
        return _parse_yarn_lock(text)
    if filename == "pnpm-lock.yaml":
        return _parse_pnpm_lock(text)
    if filename == "composer.json":
        return _parse_composer_json(text)
    if filename == "composer.lock":
        return _parse_composer_lock(text)
    if filename == "pom.xml":
        return _parse_pom(text)
    if filename in ("build.gradle", "build.gradle.kts"):
        return _parse_gradle(text)
    if filename == "gradle.lockfile":
        return _parse_gradle_lock(text)
    if filename == "packages.lock.json":
        return _parse_nuget_lock(text)
    if filename == "packages.config":
        return _parse_packages_config(text)
    if lowered.endswith(".csproj") or lowered.endswith(".vbproj"):
        return _parse_csproj(text)
    if filename == "go.mod":
        return _parse_go_mod(text)
    if filename == "go.sum":
        return _parse_go_sum(text)
    if filename == "Gemfile":
        return _parse_gemfile(text)
    if filename == "Gemfile.lock":
        return _parse_gemfile_lock(text)
    raise ManifestParseError(f"no parser for {filename} in ecosystem {ecosystem}/{source_kind}")


def _line_of(text: str, needle: str) -> int | None:
    for number, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return number
    return None


def _requirement(spec_line: str, line: int | None) -> RawDependency | None:
    stripped = spec_line.split(";", 1)[0].strip()
    if not stripped or stripped.startswith("#") or stripped.startswith("-"):
        return None
    if "://" in stripped or stripped.startswith("git+"):
        return None
    match = REQUIREMENT_LINE.match(stripped)
    if match is None:
        return None
    spec = (match.group("spec") or "").strip() or None
    version = None
    if spec is not None:
        pinned = EXACT_PIN.match(spec)
        version = pinned.group("version") if pinned is not None else None
    return RawDependency(name=match.group("name"), version=version, spec=spec, line=line)


def _requirement_hole(field: str, entry: str, line: int | None) -> RawUnresolved | None:
    stripped = entry.split(";", 1)[0].strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("-"):
        if PIP_INCLUDE_OPTION.match(stripped) is None:
            return None
        return _hole(
            field,
            stripped,
            line,
            "an include or editable option whose requirements are declared elsewhere",
        )
    if "://" in stripped or stripped.startswith("git+"):
        return _hole(field, stripped, line, "a URL or VCS requirement with no resolvable version")
    if REQUIREMENT_LINE.match(stripped) is None:
        return _hole(field, stripped, line, "not a requirement this parser can evaluate")
    return None


def _parse_requirements(text: str) -> ManifestParse:
    found: list[RawDependency] = []
    holes: list[RawUnresolved] = []
    for number, line in enumerate(text.splitlines(), start=1):
        item = _requirement(line, number)
        if item is not None:
            found.append(item)
            continue
        hole = _requirement_hole("requirements", line, number)
        if hole is not None:
            holes.append(hole)
    return ManifestParse(dependencies=tuple(found), unresolved=tuple(holes))


def _load_toml(text: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ManifestParseError(f"TOML parse failure: {error}") from error


def _parse_pyproject(text: str) -> ManifestParse:
    data = _load_toml(text)
    found: list[RawDependency] = []
    holes: list[RawUnresolved] = []
    project = as_mapping(data.get("project"))
    _requirement_strings("project.dependencies", project.get("dependencies"), text, found, holes)
    for key, group in as_mapping(project.get("optional-dependencies")).items():
        _requirement_strings(f"project.optional-dependencies.{key}", group, text, found, holes)
    for key, group in as_mapping(data.get("dependency-groups")).items():
        _requirement_strings(f"dependency-groups.{key}", group, text, found, holes)
    poetry = _nested(data, ("tool", "poetry"))
    found.extend(_poetry_dependencies(poetry.get("dependencies"), text))
    for group in as_mapping(poetry.get("group")).values():
        found.extend(_poetry_dependencies(as_mapping(group).get("dependencies"), text))
    holes.extend(_dynamic_metadata(project, text))
    return ManifestParse(dependencies=tuple(found), unresolved=tuple(holes))


DYNAMIC_DEPENDENCY_FIELDS = frozenset({"dependencies", "optional-dependencies"})


def _dynamic_metadata(project: Mapping[str, Any], text: str) -> list[RawUnresolved]:
    declared = sorted(
        {
            name
            for name in (as_text(item) for item in as_sequence(project.get("dynamic")))
            if name in DYNAMIC_DEPENDENCY_FIELDS
        }
    )
    if not declared:
        return []
    return [
        _hole(
            "project.dynamic",
            ", ".join(declared),
            _line_of(text, "dynamic"),
            "the build backend supplies these fields at build time, not this file",
        )
    ]


def _requirement_strings(
    field: str,
    value: Any,
    text: str,
    found: list[RawDependency],
    holes: list[RawUnresolved],
) -> None:
    for candidate in as_sequence(value):
        entry = as_text(candidate)
        if entry is None:
            holes.append(_hole(field, repr(candidate), None, "not a requirement string"))
            continue
        line = _line_of(text, entry)
        item = _requirement(entry, line)
        if item is not None:
            found.append(item)
            continue
        hole = _requirement_hole(field, entry, line)
        if hole is not None:
            holes.append(hole)


def _poetry_dependencies(value: Any, text: str) -> list[RawDependency]:
    found: list[RawDependency] = []
    for key, spec in as_mapping(value).items():
        name = as_text(key)
        if name is None or name == "python":
            continue
        raw_spec = as_text(spec)
        if raw_spec is None:
            raw_spec = as_text(as_mapping(spec).get("version"))
        version = raw_spec if raw_spec is not None and NPM_EXACT.match(raw_spec) else None
        found.append(
            RawDependency(name=name, version=version, spec=raw_spec, line=_line_of(text, name))
        )
    return found


def _parse_python_lock(text: str) -> ManifestParse:
    data = _load_toml(text)
    found: list[RawDependency] = []
    holes: list[RawUnresolved] = []
    for item in as_sequence(data.get("package")):
        entry = as_mapping(item)
        name = as_text(entry.get("name"))
        if name is None:
            holes.append(_hole("package", repr(item), None, "a locked package with no name"))
            continue
        found.append(
            RawDependency(
                name=name,
                version=as_text(entry.get("version")),
                spec=None,
                line=_line_of(text, name),
            )
        )
    return ManifestParse(dependencies=tuple(found), unresolved=tuple(holes))


def _load_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ManifestParseError(f"JSON parse failure: {error}") from error


def _json_object(text: str, message: str) -> Mapping[str, Any]:
    loaded = _load_json(text)
    if not is_mapping(loaded):
        raise ManifestParseError(message)
    return as_mapping(loaded)


def _parse_pipfile_lock(text: str) -> list[RawDependency]:
    data = _json_object(text, "Pipfile.lock is not a JSON object")
    found: list[RawDependency] = []
    for section in ("default", "develop"):
        for key, spec in as_mapping(data.get(section)).items():
            name = as_text(key)
            if name is None:
                continue
            declared = as_text(as_mapping(spec).get("version"))
            pinned = EXACT_PIN.match(declared) if declared is not None else None
            found.append(
                RawDependency(
                    name=name,
                    version=pinned.group("version") if pinned is not None else None,
                    spec=None,
                    line=_line_of(text, name),
                )
            )
    return found


def _parse_pipfile(text: str) -> list[RawDependency]:
    data = _load_toml(text)
    found: list[RawDependency] = []
    for section in ("packages", "dev-packages"):
        for key, spec in as_mapping(data.get(section)).items():
            name = as_text(key)
            if name is None:
                continue
            raw_spec = as_text(spec)
            version = None
            if raw_spec is not None:
                pinned = EXACT_PIN.match(raw_spec)
                version = pinned.group("version") if pinned is not None else None
            found.append(
                RawDependency(name=name, version=version, spec=raw_spec, line=_line_of(text, name))
            )
    return found


def _parse_setup_cfg(text: str) -> ManifestParse:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error as error:
        raise ManifestParseError(f"setup.cfg parse failure: {error}") from error
    blocks: list[tuple[str, str]] = []
    if parser.has_option("options", "install_requires"):
        blocks.append(("options.install_requires", parser.get("options", "install_requires")))
    if parser.has_section("options.extras_require"):
        for key, block in parser["options.extras_require"].items():
            blocks.append((f"options.extras_require.{key}", block))
    found: list[RawDependency] = []
    holes: list[RawUnresolved] = []
    for field, block in blocks:
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            located = _line_of(text, stripped)
            item = _requirement(stripped, located)
            if item is not None:
                found.append(item)
                continue
            hole = _requirement_hole(field, stripped, located)
            if hole is not None:
                holes.append(hole)
    return ManifestParse(dependencies=tuple(found), unresolved=tuple(holes))


SETUP_REQUIREMENT_FIELDS = ("install_requires",)


def _parse_setup_py(text: str) -> ManifestParse:
    try:
        module = ast.parse(text)
    except (SyntaxError, ValueError) as error:
        raise ManifestParseError(f"setup.py parse failure: {error}") from error
    found: list[RawDependency] = []
    holes: list[RawUnresolved] = []
    for node in ast.walk(module):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg in SETUP_REQUIREMENT_FIELDS:
                _literal_requirements(keyword.arg, keyword.value, found, holes)
            elif keyword.arg == "extras_require":
                _extras_require(keyword.value, found, holes)
    return ManifestParse(dependencies=tuple(found), unresolved=tuple(holes))


def _extras_require(node: ast.expr, found: list[RawDependency], holes: list[RawUnresolved]) -> None:
    if not isinstance(node, ast.Dict):
        holes.append(
            _hole(
                "extras_require",
                ast.unparse(node),
                node.lineno,
                "bound to an expression this parser does not evaluate",
            )
        )
        return
    for key, group in zip(node.keys, node.values, strict=True):
        name = key.value if isinstance(key, ast.Constant) else None
        field = f"extras_require.{name}" if isinstance(name, str) else "extras_require"
        _literal_requirements(field, group, found, holes)


def _literal_requirements(
    field: str, node: ast.expr, found: list[RawDependency], holes: list[RawUnresolved]
) -> None:
    if not isinstance(node, (ast.List, ast.Tuple)):
        holes.append(
            _hole(
                field,
                ast.unparse(node),
                node.lineno,
                "bound to an expression this parser does not evaluate",
            )
        )
        return
    for element in node.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            holes.append(
                _hole(
                    field,
                    ast.unparse(element),
                    element.lineno,
                    "an element that is not a string literal",
                )
            )
            continue
        item = _requirement(element.value, element.lineno)
        if item is not None:
            found.append(item)
            continue
        hole = _requirement_hole(field, element.value, element.lineno)
        if hole is not None:
            holes.append(hole)


def _string_map(field: str, value: Any, text: str, holes: list[RawUnresolved]) -> Mapping[str, Any]:
    if value is not None and not is_mapping(value):
        holes.append(
            _hole(field, repr(value), _line_of(text, field), "not a mapping of requirements")
        )
    return as_mapping(value)


def _named_requirements(
    field: str,
    value: Any,
    text: str,
    found: list[RawDependency],
    holes: list[RawUnresolved],
) -> None:
    for key, raw in _string_map(field, value, text, holes).items():
        name = as_text(key)
        if name is None:
            continue
        spec = as_text(raw)
        if spec is None:
            holes.append(
                _hole(f"{field}.{name}", repr(raw), _line_of(text, name), "not a version string")
            )
            continue
        version = spec if NPM_EXACT.match(spec) else None
        found.append(
            RawDependency(name=name, version=version, spec=spec, line=_line_of(text, name))
        )


NPM_SECTIONS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")


def _parse_package_json(text: str) -> ManifestParse:
    data = _json_object(text, "package.json is not a JSON object")
    found: list[RawDependency] = []
    holes: list[RawUnresolved] = []
    for section in NPM_SECTIONS:
        _named_requirements(section, data.get(section), text, found, holes)
    return ManifestParse(dependencies=tuple(found), unresolved=tuple(holes))


def _parse_package_lock(text: str) -> ManifestParse:
    data = _json_object(text, "lock file is not a JSON object")
    found: list[RawDependency] = []
    holes: list[RawUnresolved] = []
    packages = as_mapping(data.get("packages"))
    if packages:
        for raw_key, raw_spec in packages.items():
            key = as_text(raw_key)
            spec = as_mapping(raw_spec)
            if not key:
                continue
            if not spec:
                holes.append(
                    _hole("packages", key, _line_of(text, key), "a locked entry with no resolution")
                )
                continue
            found.append(
                RawDependency(
                    name=key.rsplit("node_modules/", 1)[-1],
                    version=as_text(spec.get("version")),
                    spec=None,
                    line=_line_of(text, key),
                )
            )
        return ManifestParse(dependencies=tuple(found), unresolved=tuple(holes))
    _walk_legacy_lock(as_mapping(data.get("dependencies")), text, found, holes)
    return ManifestParse(dependencies=tuple(found), unresolved=tuple(holes))


def _walk_legacy_lock(
    block: Mapping[str, Any],
    text: str,
    found: list[RawDependency],
    holes: list[RawUnresolved],
) -> None:
    for raw_name, raw_spec in block.items():
        name = as_text(raw_name)
        spec = as_mapping(raw_spec)
        if name is None:
            continue
        if not spec:
            holes.append(
                _hole(
                    "dependencies", name, _line_of(text, name), "a locked entry with no resolution"
                )
            )
            continue
        found.append(
            RawDependency(
                name=name,
                version=as_text(spec.get("version")),
                spec=None,
                line=_line_of(text, name),
            )
        )
        _walk_legacy_lock(as_mapping(spec.get("dependencies")), text, found, holes)


def _parse_yarn_lock(text: str) -> ManifestParse:
    found: list[RawDependency] = []
    holes: list[RawUnresolved] = []
    specs: list[str] = []
    header_line = 0

    def close_pending() -> None:
        for spec in specs:
            holes.append(_hole("resolution", spec, header_line, "a locked entry with no version"))

    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")) and line.rstrip().endswith(":"):
            close_pending()
            specs = [part.strip().strip('"') for part in line.rstrip()[:-1].split(",")]
            header_line = number
            continue
        stripped = line.strip()
        if specs and stripped.startswith("version"):
            version = stripped.split(" ", 1)[-1].strip().strip('"')
            for spec in specs:
                name = _yarn_name(spec)
                if name:
                    found.append(
                        RawDependency(name=name, version=version, spec=spec, line=header_line)
                    )
            specs = []
    close_pending()
    return ManifestParse(dependencies=tuple(found), unresolved=tuple(holes))


def _yarn_name(spec: str) -> str:
    without_scope = spec[1:] if spec.startswith("@") else spec
    name = without_scope.rsplit("@", 1)[0]
    return f"@{name}" if spec.startswith("@") else name


def _parse_pnpm_lock(text: str) -> ManifestParse:
    loaded = _load_yaml(text)
    if not is_mapping(loaded):
        raise ManifestParseError("pnpm-lock.yaml is not a mapping")
    data = as_mapping(loaded)
    found: list[RawDependency] = []
    holes: list[RawUnresolved] = []
    for raw_key in _string_map("packages", data.get("packages"), text, holes):
        key = as_text(raw_key)
        if key is None:
            continue
        trimmed = key[1:] if key.startswith("/") else key
        if "@" in trimmed[1:]:
            name, _, version = trimmed.rpartition("@")
        else:
            name, _, version = trimmed.rpartition("/")
        if not name:
            holes.append(
                _hole("packages", key, _line_of(text, key), "a locked key with no package name")
            )
            continue
        found.append(
            RawDependency(name=name, version=version or None, spec=None, line=_line_of(text, key))
        )
    return ManifestParse(dependencies=tuple(found), unresolved=tuple(holes))


def _load_yaml(text: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ManifestParseError(f"YAML parse failure: {error}") from error


def _parse_composer_json(text: str) -> ManifestParse:
    data = _json_object(text, "composer.json is not a JSON object")
    found: list[RawDependency] = []
    holes: list[RawUnresolved] = []
    for section in ("require", "require-dev"):
        _named_requirements(section, data.get(section), text, found, holes)
    return ManifestParse(dependencies=tuple(found), unresolved=tuple(holes))


def _parse_composer_lock(text: str) -> ManifestParse:
    data = _json_object(text, "composer.lock is not a JSON object")
    found: list[RawDependency] = []
    holes: list[RawUnresolved] = []
    for section in ("packages", "packages-dev"):
        for item in as_sequence(data.get(section)):
            entry = as_mapping(item)
            name = as_text(entry.get("name"))
            if name is None:
                holes.append(_hole(section, repr(item), None, "a locked package with no name"))
                continue
            found.append(
                RawDependency(
                    name=name,
                    version=as_text(entry.get("version")),
                    spec=None,
                    line=_line_of(text, name),
                )
            )
    return ManifestParse(dependencies=tuple(found), unresolved=tuple(holes))


def _parse_xml(text: str) -> ElementTree.Element:
    if XML_ENTITY.search(text):
        raise ManifestParseError("XML declares entities; refusing to expand them")
    try:
        return ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise ManifestParseError(f"XML parse failure: {error}") from error


def _tag(element: ElementTree.Element) -> str:
    return element.tag.rpartition("}")[2]


def _child_text(element: ElementTree.Element, name: str) -> str | None:
    for child in element:
        if _tag(child) == name and child.text is not None:
            return child.text.strip()
    return None


def _parse_pom(text: str) -> ManifestParse:
    root = _parse_xml(text)
    found: list[RawDependency] = []
    holes: list[RawUnresolved] = []
    for element in root.iter():
        if _tag(element) != "dependency":
            continue
        group = _child_text(element, "groupId")
        artifact = _child_text(element, "artifactId")
        version = _child_text(element, "version")
        rendered = f"{group}:{artifact}:{version}"
        if not group or not artifact:
            holes.append(
                _hole(
                    "dependency",
                    rendered,
                    _line_of(text, artifact or group or "dependency"),
                    "a dependency with no groupId or no artifactId",
                )
            )
            continue
        if "${" in group or "${" in artifact:
            holes.append(
                _hole(
                    "dependency",
                    rendered,
                    _line_of(text, artifact),
                    "a coordinate built from a property this file does not resolve",
                )
            )
            continue
        found.append(
            RawDependency(
                name=f"{group}:{artifact}",
                version=version if version and not version.startswith("${") else None,
                spec=version,
                line=_line_of(text, artifact),
            )
        )
    return ManifestParse(dependencies=tuple(found), unresolved=tuple(holes))


def _parse_gradle(text: str) -> ManifestParse:
    found: list[RawDependency] = []
    holes: list[RawUnresolved] = []
    for number, line in enumerate(text.splitlines(), start=1):
        matched = False
        for match in GRADLE_COORDINATE.finditer(line):
            matched = True
            found.append(
                RawDependency(
                    name=f"{match.group('group')}:{match.group('artifact')}",
                    version=match.group("version"),
                    spec=match.group("version"),
                    line=number,
                )
            )
        if matched or GRADLE_DECLARATION.match(line) is None:
            continue
        if GRADLE_LOCAL_DEPENDENCY.search(line):
            continue
        holes.append(
            _hole(
                "dependencies",
                line,
                number,
                "a dependency declaration carrying no literal group:artifact:version coordinate",
            )
        )
    return ManifestParse(dependencies=tuple(found), unresolved=tuple(holes))


def _parse_gradle_lock(text: str) -> ManifestParse:
    found: list[RawDependency] = []
    holes: list[RawUnresolved] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or GRADLE_LOCK_IGNORED.match(stripped):
            continue
        match = GRADLE_LOCK_LINE.match(stripped)
        if match is None:
            holes.append(_hole("lock", stripped, number, "not a locked gradle coordinate"))
            continue
        found.append(
            RawDependency(
                name=f"{match.group('group')}:{match.group('artifact')}",
                version=match.group("version"),
                spec=None,
                line=number,
            )
        )
    return ManifestParse(dependencies=tuple(found), unresolved=tuple(holes))


def _parse_nuget_lock(text: str) -> ManifestParse:
    data = _json_object(text, "packages.lock.json is not a JSON object")
    found: list[RawDependency] = []
    holes: list[RawUnresolved] = []
    for framework, block in as_mapping(data.get("dependencies")).items():
        for raw_name, raw_spec in _string_map(str(framework), block, text, holes).items():
            name = as_text(raw_name)
            if name is None:
                continue
            spec = as_mapping(raw_spec)
            if not spec:
                holes.append(
                    _hole(
                        str(framework),
                        name,
                        _line_of(text, name),
                        "a locked entry with no resolution",
                    )
                )
                continue
            found.append(
                RawDependency(
                    name=name,
                    version=as_text(spec.get("resolved")),
                    spec=None,
                    line=_line_of(text, name),
                )
            )
    return ManifestParse(dependencies=tuple(found), unresolved=tuple(holes))


def _parse_packages_config(text: str) -> ManifestParse:
    root = _parse_xml(text)
    found: list[RawDependency] = []
    holes: list[RawUnresolved] = []
    for element in root.iter():
        if _tag(element) != "package":
            continue
        name = element.get("id")
        version = element.get("version")
        if not name:
            holes.append(_hole("package", f"version={version}", None, "a package entry with no id"))
            continue
        found.append(
            RawDependency(name=name, version=version, spec=version, line=_line_of(text, name))
        )
    return ManifestParse(dependencies=tuple(found), unresolved=tuple(holes))


def _parse_csproj(text: str) -> ManifestParse:
    root = _parse_xml(text)
    found: list[RawDependency] = []
    holes: list[RawUnresolved] = []
    for element in root.iter():
        if _tag(element) != "PackageReference":
            continue
        name = element.get("Include")
        version = element.get("Version") or _child_text(element, "Version")
        if not name:
            updated = element.get("Update")
            holes.append(
                _hole(
                    "PackageReference",
                    f"Update={updated} Version={version}",
                    _line_of(text, updated) if updated else None,
                    "a package reference with no Include",
                )
            )
            continue
        if name.startswith("$("):
            holes.append(
                _hole(
                    "PackageReference",
                    f"Include={name} Version={version}",
                    _line_of(text, name),
                    "a package name built from a property this file does not resolve",
                )
            )
            continue
        found.append(
            RawDependency(
                name=name,
                version=version if version and not version.startswith("$(") else None,
                spec=version,
                line=_line_of(text, name),
            )
        )
    return ManifestParse(dependencies=tuple(found), unresolved=tuple(holes))


def _parse_go_mod(text: str) -> ManifestParse:
    found: list[RawDependency] = []
    holes: list[RawUnresolved] = []
    inside_block = False
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("//") or not stripped:
            continue
        if stripped.startswith("require (") or stripped == "require(":
            inside_block = True
            continue
        if inside_block and stripped == ")":
            inside_block = False
            continue
        target = stripped
        if stripped.startswith("require "):
            target = stripped[len("require ") :].strip()
        elif not inside_block:
            continue
        match = GO_REQUIRE_LINE.match(target)
        if match is None:
            holes.append(_hole("require", stripped, number, "not a resolvable require line"))
            continue
        found.append(
            RawDependency(
                name=match.group("name"),
                version=match.group("version"),
                spec=match.group("version"),
                line=number,
            )
        )
    return ManifestParse(dependencies=tuple(found), unresolved=tuple(holes))


def _parse_go_sum(text: str) -> ManifestParse:
    found: list[RawDependency] = []
    holes: list[RawUnresolved] = []
    seen: set[tuple[str, str]] = set()
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        match = GO_SUM_LINE.match(stripped)
        if match is None:
            holes.append(_hole("sum", stripped, number, "not a checksummed module line"))
            continue
        key = (match.group("name"), match.group("version"))
        if key in seen:
            continue
        seen.add(key)
        found.append(
            RawDependency(name=key[0], version=key[1], spec=None, line=number),
        )
    return ManifestParse(dependencies=tuple(found), unresolved=tuple(holes))


def _parse_gemfile(text: str) -> ManifestParse:
    found: list[RawDependency] = []
    holes: list[RawUnresolved] = []
    for number, line in enumerate(text.splitlines(), start=1):
        delegation = GEMFILE_DELEGATION.match(line)
        if delegation is not None:
            holes.append(
                _hole(
                    delegation.group("directive"),
                    line,
                    number,
                    "gems declared in a file this directive loads, not here",
                )
            )
            continue
        match = GEMFILE_GEM.match(line)
        if match is None:
            continue
        rest = match.group("rest")
        spec_match = re.search(r"""['"]([~><=]*\s*[0-9][^'"]*)['"]""", rest)
        spec = spec_match.group(1).strip() if spec_match is not None else None
        version = spec if spec is not None and NPM_EXACT.match(spec) else None
        found.append(
            RawDependency(name=match.group("name"), version=version, spec=spec, line=number)
        )
    return ManifestParse(dependencies=tuple(found), unresolved=tuple(holes))


def _parse_gemfile_lock(text: str) -> list[RawDependency]:
    found: list[RawDependency] = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = GEMFILE_LOCK_SPEC.match(line)
        if match is not None:
            found.append(
                RawDependency(
                    name=match.group("name"),
                    version=match.group("version"),
                    spec=None,
                    line=number,
                )
            )
    return found


def _nested(data: Mapping[str, Any], path: Iterable[str]) -> Mapping[str, Any]:
    current = data
    for key in path:
        current = as_mapping(current.get(key))
    return current
