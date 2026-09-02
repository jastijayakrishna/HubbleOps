from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from hubbleops.core.canonical import content_id
from hubbleops.core.errors import SurfaceSpecInvalid
from hubbleops.core.records import as_mapping, as_sequence, as_text, is_list

SURFACE_KEYS = (
    "name",
    "identifiers",
    "hosts",
    "package_names",
    "version_carriers",
    "request_languages",
    "sink_argument_positions",
    "config_env_keys",
)


VERSION_SLOTS = ("per_call", "client_init", "sdk_default")


@dataclass(frozen=True, slots=True)
class VersionCarrier:
    name: str
    regex: str
    slot: str
    languages: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "regex": self.regex,
            "slot": self.slot,
            "languages": list(self.languages),
        }


@dataclass(frozen=True, slots=True)
class RequestLanguage:
    name: str
    anchors: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {"name": self.name, "anchors": list(self.anchors)}


@dataclass(frozen=True, slots=True)
class SinkArgument:
    sink: str
    argument: str
    position: int

    def to_mapping(self) -> dict[str, Any]:
        return {"sink": self.sink, "argument": self.argument, "position": self.position}


@dataclass(frozen=True, slots=True)
class SurfaceSpec:
    name: str
    identifiers: tuple[str, ...]
    hosts: tuple[str, ...]
    package_names: tuple[str, ...]
    version_carriers: tuple[VersionCarrier, ...]
    request_languages: tuple[RequestLanguage, ...]
    sink_argument_positions: tuple[SinkArgument, ...]
    config_env_keys: tuple[str, ...]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> SurfaceSpec:
        unknown = sorted(set(data) - set(SURFACE_KEYS))
        if unknown:
            raise SurfaceSpecInvalid(f"unknown surface keys: {', '.join(unknown)}")
        missing = sorted(set(SURFACE_KEYS) - set(data))
        if missing:
            raise SurfaceSpecInvalid(f"missing surface keys: {', '.join(missing)}")
        return cls(
            name=_text(data, "name"),
            identifiers=_strings(data, "identifiers"),
            hosts=_strings(data, "hosts"),
            package_names=_strings(data, "package_names"),
            version_carriers=tuple(
                VersionCarrier(
                    name=_text(item, "name"),
                    regex=_text(item, "regex"),
                    slot=_slot(item),
                    languages=_strings(item, "languages"),
                )
                for item in _mappings(data, "version_carriers")
            ),
            request_languages=tuple(
                RequestLanguage(name=_text(item, "name"), anchors=_strings(item, "anchors"))
                for item in _mappings(data, "request_languages")
            ),
            sink_argument_positions=tuple(
                SinkArgument(
                    sink=_text(item, "sink"),
                    argument=_text(item, "argument"),
                    position=_integer(item, "position"),
                )
                for item in _mappings(data, "sink_argument_positions")
            ),
            config_env_keys=_strings(data, "config_env_keys"),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "identifiers": list(self.identifiers),
            "hosts": list(self.hosts),
            "package_names": list(self.package_names),
            "version_carriers": [carrier.to_mapping() for carrier in self.version_carriers],
            "request_languages": [language.to_mapping() for language in self.request_languages],
            "sink_argument_positions": [
                argument.to_mapping() for argument in self.sink_argument_positions
            ],
            "config_env_keys": list(self.config_env_keys),
        }

    def surface_hash(self) -> str:
        return content_id(self.to_mapping())


def _text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise SurfaceSpecInvalid(f"{key!r} must be a non-empty string, got {value!r}")
    return value


def _slot(data: Mapping[str, Any]) -> str:
    value = _text(data, "slot")
    if value not in VERSION_SLOTS:
        raise SurfaceSpecInvalid(
            f"version carrier slot {value!r} is not one of {', '.join(VERSION_SLOTS)}"
        )
    return value


def _integer(data: Mapping[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SurfaceSpecInvalid(f"{key!r} must be an integer, got {value!r}")
    return value


def _strings(data: Mapping[str, Any], key: str) -> tuple[str, ...]:
    raw = data.get(key)
    if not is_list(raw):
        raise SurfaceSpecInvalid(f"{key!r} must be a list of strings, got {raw!r}")
    items: list[str] = []
    for candidate in as_sequence(raw):
        entry = as_text(candidate)
        if entry is None or not entry:
            raise SurfaceSpecInvalid(f"{key!r} contains a non-string entry: {candidate!r}")
        items.append(entry)
    return tuple(items)


def _mappings(data: Mapping[str, Any], key: str) -> tuple[Mapping[str, Any], ...]:
    raw = data.get(key)
    if not is_list(raw):
        raise SurfaceSpecInvalid(f"{key!r} must be a list of mappings, got {raw!r}")
    items: list[Mapping[str, Any]] = []
    for candidate in as_sequence(raw):
        entry = as_mapping(candidate)
        if not entry:
            raise SurfaceSpecInvalid(f"{key!r} contains a non-mapping entry: {candidate!r}")
        items.append(entry)
    return tuple(items)
