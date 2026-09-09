from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from hubbleops.core.repair import TransformInput, TransformOutput
from hubbleops.packs._protocol import CatalogFact

VERSION_TOKEN = re.compile(r"^v\d+$")
SEMVER = re.compile(r"\d+\.\d+\.\d+")

MANIFEST_LANGUAGES = {
    "requirements.txt": "python",
    "pyproject.toml": "python",
    "setup.py": "python",
    "setup.cfg": "python",
    "pipfile": "python",
    "composer.json": "php",
    "pom.xml": "java",
    "build.gradle": "java",
    "gemfile": "ruby",
    "cpanfile": "perl",
}

DEPENDENCY_CLAIMS = ("sdk_installed", "dependency_state", "package_reference")


def _line_span(text: str, line: int | None) -> tuple[str, str, str]:
    if line is None:
        return "", text, ""
    lines = text.splitlines(keepends=True)
    if line < 1 or line > len(lines):
        return "", text, ""
    index = line - 1
    return "".join(lines[:index]), lines[index], "".join(lines[index + 1 :])


def _token_pattern(version: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(version)}\b")


@dataclass(frozen=True, slots=True)
class VersionLiteralTransform:
    name: str = "version-literal"
    failure_class: str = "call_version"

    def precondition(self, subject: TransformInput) -> bool:
        if subject.claim_type != self.failure_class:
            return False
        if subject.from_version == subject.to_version:
            return False
        if not VERSION_TOKEN.match(subject.from_version):
            return False
        _, target, _ = _line_span(subject.text, subject.line)
        return _token_pattern(subject.from_version).search(target) is not None

    def apply(self, subject: TransformInput) -> TransformOutput:
        head, target, tail = _line_span(subject.text, subject.line)
        rewritten = _token_pattern(subject.from_version).sub(subject.to_version, target)
        return TransformOutput(
            source=subject,
            result="APPLIED",
            text=f"{head}{rewritten}{tail}",
            reason=(
                f"rewrote the {subject.from_version} version literal to {subject.to_version} "
                f"at {subject.path}"
            ),
            sites=(f"{subject.path}:{subject.line}" if subject.line else subject.path,),
        )

    def postcondition(self, subject: TransformOutput) -> bool:
        source = subject.source
        _, target, _ = _line_span(subject.text, source.line)
        if _token_pattern(source.from_version).search(target) is not None:
            return False
        return _token_pattern(source.to_version).search(target) is not None


@dataclass(frozen=True, slots=True)
class RestPathTransform:
    name: str = "rest-path"
    failure_class: str = "endpoint_reference"

    def _pattern(self, version: str) -> re.Pattern[str]:
        return re.compile(rf"(?<=/){re.escape(version)}\b")

    def precondition(self, subject: TransformInput) -> bool:
        if subject.claim_type != self.failure_class:
            return False
        if subject.from_version == subject.to_version:
            return False
        if not VERSION_TOKEN.match(subject.from_version):
            return False
        _, target, _ = _line_span(subject.text, subject.line)
        return self._pattern(subject.from_version).search(target) is not None

    def apply(self, subject: TransformInput) -> TransformOutput:
        head, target, tail = _line_span(subject.text, subject.line)
        rewritten = self._pattern(subject.from_version).sub(subject.to_version, target)
        return TransformOutput(
            source=subject,
            result="APPLIED",
            text=f"{head}{rewritten}{tail}",
            reason=(
                f"re-versioned the endpoint path from {subject.from_version} to "
                f"{subject.to_version} at {subject.path}"
            ),
            sites=(f"{subject.path}:{subject.line}" if subject.line else subject.path,),
        )

    def postcondition(self, subject: TransformOutput) -> bool:
        source = subject.source
        _, target, _ = _line_span(subject.text, source.line)
        if self._pattern(source.from_version).search(target) is not None:
            return False
        return self._pattern(source.to_version).search(target) is not None


@dataclass(frozen=True, slots=True)
class SubjectRenameTransform:
    name: str = "subject-rename"
    failure_class: str = "request_text"

    def precondition(self, subject: TransformInput) -> bool:
        if subject.subject is None or subject.replacement is None:
            return False
        if subject.subject == subject.replacement:
            return False
        return subject.subject in subject.text

    def apply(self, subject: TransformInput) -> TransformOutput:
        name = subject.subject or ""
        replacement = subject.replacement or ""
        pattern = re.compile(rf"(?<![\w.]){re.escape(name)}(?![\w])")
        return TransformOutput(
            source=subject,
            result="APPLIED",
            text=pattern.sub(replacement, subject.text),
            reason=f"renamed {name} to {replacement} at {subject.path}",
            sites=(subject.path,),
        )

    def postcondition(self, subject: TransformOutput) -> bool:
        name = subject.source.subject or ""
        replacement = subject.source.replacement or ""
        pattern = re.compile(rf"(?<![\w.]){re.escape(name)}(?![\w])")
        if pattern.search(subject.text) is not None:
            return False
        return replacement in subject.text


@dataclass(frozen=True, slots=True)
class SdkPinTransform:
    minimums: Mapping[str, Mapping[str, str]]
    name: str = "sdk-pin"
    failure_class: str = "sdk_installed"

    @staticmethod
    def language_of(path: str) -> str | None:
        return MANIFEST_LANGUAGES.get(path.rsplit("/", 1)[-1].lower())

    def _minimum(self, subject: TransformInput) -> str | None:
        language = self.language_of(subject.path)
        if language is None:
            return None
        floor = self.minimums.get(subject.to_version, {}).get(language)
        return floor if isinstance(floor, str) and SEMVER.fullmatch(floor) else None

    def precondition(self, subject: TransformInput) -> bool:
        if subject.claim_type not in DEPENDENCY_CLAIMS:
            return False
        floor = self._minimum(subject)
        if floor is None:
            return False
        _, target, _ = _line_span(subject.text, subject.line)
        found = SEMVER.search(target)
        return found is not None and _below(found.group(0), floor)

    def apply(self, subject: TransformInput) -> TransformOutput:
        floor = self._minimum(subject) or ""
        head, target, tail = _line_span(subject.text, subject.line)
        rewritten = SEMVER.sub(floor, target, count=1)
        return TransformOutput(
            source=subject,
            result="APPLIED",
            text=f"{head}{rewritten}{tail}",
            reason=(
                f"raised the client library pin to {floor}, the documented minimum supporting "
                f"{subject.to_version}"
            ),
            sites=(f"{subject.path}:{subject.line}" if subject.line else subject.path,),
        )

    def postcondition(self, subject: TransformOutput) -> bool:
        floor = self._minimum(subject.source)
        if floor is None:
            return False
        _, target, _ = _line_span(subject.text, subject.source.line)
        found = SEMVER.search(target)
        return found is not None and not _below(found.group(0), floor)


def _below(found: str, floor: str) -> bool:
    return _parts(found) < _parts(floor)


def _parts(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def client_minimums(facts: Sequence[CatalogFact], version: str) -> Mapping[str, str]:
    for fact in facts:
        if fact.kind != "client_compatibility":
            continue
        if fact.subject != f"compatibility.client_libraries.{version}":
            continue
        minimums = fact.attributes.get("minimum_versions")
        if not isinstance(minimums, Mapping):
            continue
        entries = cast(Mapping[str, Any], minimums)
        return {
            str(key): value for key, value in entries.items() if isinstance(value, str) and value
        }
    return {}


def transforms(minimums: Mapping[str, Mapping[str, str]]) -> list[Any]:
    return [
        RestPathTransform(),
        SdkPinTransform(minimums=minimums),
        SubjectRenameTransform(),
        VersionLiteralTransform(),
    ]


__all__ = [
    "RestPathTransform",
    "SdkPinTransform",
    "SubjectRenameTransform",
    "VersionLiteralTransform",
    "client_minimums",
    "transforms",
]
