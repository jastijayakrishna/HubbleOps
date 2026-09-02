from __future__ import annotations

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


@dataclass(frozen=True, slots=True)
class RawDependency:
    name: str
    version: str | None
    spec: str | None
    line: int | None


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

    def resolution_hash(self) -> str:
        return content_id(
            {
                "files": [item.to_mapping() for item in self.files],
                "dependencies": [item.to_mapping() for item in self.dependencies],
            }
        )

    def locked_ecosystems(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    item.ecosystem
                    for item in self.files
                    if item.source_kind == LOCK and item.error is None
                }
            )
        )

    def manifest_ecosystems(self) -> tuple[str, ...]:
        return tuple(sorted({item.ecosystem for item in self.files if item.error is None}))

    def locks_for(self, ecosystem: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                item.path
                for item in self.files
                if item.ecosystem == ecosystem and item.source_kind == LOCK and item.error is None
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
        for item in raw:
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
    return DependencyResolution(
        files=tuple(sorted(files, key=lambda item: item.path)),
        dependencies=tuple(
            sorted(dependencies, key=lambda item: (item.path, item.name, item.version or ""))
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


def _parse(path: str, ecosystem: str, source_kind: str, text: str) -> list[RawDependency]:
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


def _parse_requirements(text: str) -> list[RawDependency]:
    found: list[RawDependency] = []
    for number, line in enumerate(text.splitlines(), start=1):
        item = _requirement(line, number)
        if item is not None:
            found.append(item)
    return found


def _load_toml(text: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ManifestParseError(f"TOML parse failure: {error}") from error


def _parse_pyproject(text: str) -> list[RawDependency]:
    data = _load_toml(text)
    found: list[RawDependency] = []
    project = as_mapping(data.get("project"))
    found.extend(_requirement_strings(project.get("dependencies"), text))
    for group in as_mapping(project.get("optional-dependencies")).values():
        found.extend(_requirement_strings(group, text))
    for group in as_mapping(data.get("dependency-groups")).values():
        found.extend(_requirement_strings(group, text))
    poetry = _nested(data, ("tool", "poetry"))
    found.extend(_poetry_dependencies(poetry.get("dependencies"), text))
    for group in as_mapping(poetry.get("group")).values():
        found.extend(_poetry_dependencies(as_mapping(group).get("dependencies"), text))
    return found


def _requirement_strings(value: Any, text: str) -> list[RawDependency]:
    found: list[RawDependency] = []
    for candidate in as_sequence(value):
        entry = as_text(candidate)
        if entry is None:
            continue
        item = _requirement(entry, _line_of(text, entry))
        if item is not None:
            found.append(item)
    return found


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


def _parse_python_lock(text: str) -> list[RawDependency]:
    data = _load_toml(text)
    found: list[RawDependency] = []
    for item in as_sequence(data.get("package")):
        entry = as_mapping(item)
        name = as_text(entry.get("name"))
        if name is not None:
            found.append(
                RawDependency(
                    name=name,
                    version=as_text(entry.get("version")),
                    spec=None,
                    line=_line_of(text, name),
                )
            )
    return found


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


def _parse_setup_cfg(text: str) -> list[RawDependency]:
    found: list[RawDependency] = []
    inside = False
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            inside = False
            continue
        if stripped.startswith("install_requires"):
            inside = True
            continue
        if inside and (not line.startswith((" ", "\t")) or not stripped):
            inside = False
            continue
        if inside:
            item = _requirement(stripped, number)
            if item is not None:
                found.append(item)
    return found


def _parse_package_json(text: str) -> list[RawDependency]:
    data = _json_object(text, "package.json is not a JSON object")
    found: list[RawDependency] = []
    for section in (
        "dependencies",
        "devDependencies",
        "peerDependencies",
        "optionalDependencies",
    ):
        for key, raw in as_mapping(data.get(section)).items():
            name = as_text(key)
            spec = as_text(raw)
            if name is None or spec is None:
                continue
            version = spec if NPM_EXACT.match(spec) else None
            found.append(
                RawDependency(name=name, version=version, spec=spec, line=_line_of(text, name))
            )
    return found


def _parse_package_lock(text: str) -> list[RawDependency]:
    data = _json_object(text, "lock file is not a JSON object")
    found: list[RawDependency] = []
    packages = as_mapping(data.get("packages"))
    if packages:
        for raw_key, raw_spec in packages.items():
            key = as_text(raw_key)
            spec = as_mapping(raw_spec)
            if not key or not spec:
                continue
            found.append(
                RawDependency(
                    name=key.rsplit("node_modules/", 1)[-1],
                    version=as_text(spec.get("version")),
                    spec=None,
                    line=_line_of(text, key),
                )
            )
        return found
    found.extend(_walk_legacy_lock(as_mapping(data.get("dependencies")), text))
    return found


def _walk_legacy_lock(block: Mapping[str, Any], text: str) -> list[RawDependency]:
    found: list[RawDependency] = []
    for raw_name, raw_spec in block.items():
        name = as_text(raw_name)
        spec = as_mapping(raw_spec)
        if name is None or not spec:
            continue
        found.append(
            RawDependency(
                name=name,
                version=as_text(spec.get("version")),
                spec=None,
                line=_line_of(text, name),
            )
        )
        found.extend(_walk_legacy_lock(as_mapping(spec.get("dependencies")), text))
    return found


def _parse_yarn_lock(text: str) -> list[RawDependency]:
    found: list[RawDependency] = []
    specs: list[str] = []
    header_line = 0
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")) and line.rstrip().endswith(":"):
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
    return found


def _yarn_name(spec: str) -> str:
    without_scope = spec[1:] if spec.startswith("@") else spec
    name = without_scope.rsplit("@", 1)[0]
    return f"@{name}" if spec.startswith("@") else name


def _parse_pnpm_lock(text: str) -> list[RawDependency]:
    loaded = _load_yaml(text)
    if not is_mapping(loaded):
        raise ManifestParseError("pnpm-lock.yaml is not a mapping")
    data = as_mapping(loaded)
    found: list[RawDependency] = []
    for raw_key in as_mapping(data.get("packages")):
        key = as_text(raw_key)
        if key is None:
            continue
        trimmed = key[1:] if key.startswith("/") else key
        if "@" in trimmed[1:]:
            name, _, version = trimmed.rpartition("@")
        else:
            name, _, version = trimmed.rpartition("/")
        if name:
            found.append(
                RawDependency(
                    name=name, version=version or None, spec=None, line=_line_of(text, key)
                )
            )
    return found


def _load_yaml(text: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ManifestParseError(f"YAML parse failure: {error}") from error


def _parse_composer_json(text: str) -> list[RawDependency]:
    data = _json_object(text, "composer.json is not a JSON object")
    found: list[RawDependency] = []
    for section in ("require", "require-dev"):
        for raw_name, raw_spec in as_mapping(data.get(section)).items():
            name = as_text(raw_name)
            spec = as_text(raw_spec)
            if name is None or spec is None:
                continue
            version = spec if NPM_EXACT.match(spec) else None
            found.append(
                RawDependency(name=name, version=version, spec=spec, line=_line_of(text, name))
            )
    return found


def _parse_composer_lock(text: str) -> list[RawDependency]:
    data = _json_object(text, "composer.lock is not a JSON object")
    found: list[RawDependency] = []
    for section in ("packages", "packages-dev"):
        for item in as_sequence(data.get(section)):
            entry = as_mapping(item)
            name = as_text(entry.get("name"))
            if name is not None:
                found.append(
                    RawDependency(
                        name=name,
                        version=as_text(entry.get("version")),
                        spec=None,
                        line=_line_of(text, name),
                    )
                )
    return found


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


def _parse_pom(text: str) -> list[RawDependency]:
    root = _parse_xml(text)
    found: list[RawDependency] = []
    for element in root.iter():
        if _tag(element) != "dependency":
            continue
        group = _child_text(element, "groupId")
        artifact = _child_text(element, "artifactId")
        version = _child_text(element, "version")
        if group and artifact:
            name = f"{group}:{artifact}"
            found.append(
                RawDependency(
                    name=name,
                    version=version if version and not version.startswith("${") else None,
                    spec=version,
                    line=_line_of(text, artifact),
                )
            )
    return found


def _parse_gradle(text: str) -> list[RawDependency]:
    found: list[RawDependency] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match in GRADLE_COORDINATE.finditer(line):
            name = f"{match.group('group')}:{match.group('artifact')}"
            found.append(
                RawDependency(
                    name=name,
                    version=match.group("version"),
                    spec=match.group("version"),
                    line=number,
                )
            )
    return found


def _parse_gradle_lock(text: str) -> list[RawDependency]:
    found: list[RawDependency] = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = GRADLE_LOCK_LINE.match(line.strip())
        if match is not None:
            found.append(
                RawDependency(
                    name=f"{match.group('group')}:{match.group('artifact')}",
                    version=match.group("version"),
                    spec=None,
                    line=number,
                )
            )
    return found


def _parse_nuget_lock(text: str) -> list[RawDependency]:
    data = _json_object(text, "packages.lock.json is not a JSON object")
    found: list[RawDependency] = []
    for block in as_mapping(data.get("dependencies")).values():
        for raw_name, raw_spec in as_mapping(block).items():
            name = as_text(raw_name)
            spec = as_mapping(raw_spec)
            if name is None or not spec:
                continue
            found.append(
                RawDependency(
                    name=name,
                    version=as_text(spec.get("resolved")),
                    spec=None,
                    line=_line_of(text, name),
                )
            )
    return found


def _parse_packages_config(text: str) -> list[RawDependency]:
    root = _parse_xml(text)
    found: list[RawDependency] = []
    for element in root.iter():
        if _tag(element) != "package":
            continue
        name = element.get("id")
        version = element.get("version")
        if name:
            found.append(
                RawDependency(name=name, version=version, spec=version, line=_line_of(text, name))
            )
    return found


def _parse_csproj(text: str) -> list[RawDependency]:
    root = _parse_xml(text)
    found: list[RawDependency] = []
    for element in root.iter():
        if _tag(element) != "PackageReference":
            continue
        name = element.get("Include")
        version = element.get("Version") or _child_text(element, "Version")
        if name:
            found.append(
                RawDependency(
                    name=name,
                    version=version if version and not version.startswith("$(") else None,
                    spec=version,
                    line=_line_of(text, name),
                )
            )
    return found


def _parse_go_mod(text: str) -> list[RawDependency]:
    found: list[RawDependency] = []
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
        if match is not None:
            found.append(
                RawDependency(
                    name=match.group("name"),
                    version=match.group("version"),
                    spec=match.group("version"),
                    line=number,
                )
            )
    return found


def _parse_go_sum(text: str) -> list[RawDependency]:
    found: list[RawDependency] = []
    seen: set[tuple[str, str]] = set()
    for number, line in enumerate(text.splitlines(), start=1):
        match = GO_SUM_LINE.match(line.strip())
        if match is None:
            continue
        key = (match.group("name"), match.group("version"))
        if key in seen:
            continue
        seen.add(key)
        found.append(
            RawDependency(name=key[0], version=key[1], spec=None, line=number),
        )
    return found


def _parse_gemfile(text: str) -> list[RawDependency]:
    found: list[RawDependency] = []
    for number, line in enumerate(text.splitlines(), start=1):
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
    return found


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
