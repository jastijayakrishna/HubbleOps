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
    "adjacent_contracts",
    "contract_surfaces",
)

OPTIONAL_SURFACE_KEYS = ("adjacent_contracts", "contract_surfaces")


VERSION_SLOTS = ("per_call", "client_init", "sdk_default")

CARRIER_SCOPES = ("wire", "sdk")

ANY_LANGUAGE = ("any",)

CONTRACT_SURFACE_KINDS = (
    "auth_scope",
    "endpoint_path",
    "protocol_header",
    "resource_name",
    "response_field",
)


@dataclass(frozen=True, slots=True)
class VersionCarrier:
    name: str
    regex: str
    slot: str
    languages: tuple[str, ...]
    scope: str = "sdk"

    def applies_to(self, language: str) -> bool:
        return self.scope == "wire" or "any" in self.languages or language.lower() in self.languages

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "regex": self.regex,
            "slot": self.slot,
            "languages": list(self.languages),
            "scope": self.scope,
        }


@dataclass(frozen=True, slots=True)
class RequestLanguage:
    name: str
    anchors: tuple[str, ...]
    shapes: tuple[str, ...] = ()
    resource_group: str = "resource"
    known_resources: tuple[str, ...] = ()

    def knows(self, resource: str) -> bool:
        return resource.lower() in self.known_resources

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "anchors": list(self.anchors),
            "shapes": list(self.shapes),
            "resource_group": self.resource_group,
            "known_resources": list(self.known_resources),
        }


@dataclass(frozen=True, slots=True)
class AdjacentContract:
    name: str
    hosts: tuple[str, ...]
    reason: str

    def to_mapping(self) -> dict[str, Any]:
        return {"name": self.name, "hosts": list(self.hosts), "reason": self.reason}


@dataclass(frozen=True, slots=True)
class ContractSurface:
    kind: str
    name: str
    shape: str
    gated: bool = True

    def to_mapping(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "shape": self.shape,
            "gated": self.gated,
        }


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
    adjacent_contracts: tuple[AdjacentContract, ...]
    contract_surfaces: tuple[ContractSurface, ...]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> SurfaceSpec:
        unknown = sorted(set(data) - set(SURFACE_KEYS))
        if unknown:
            raise SurfaceSpecInvalid(f"unknown surface keys: {', '.join(unknown)}")
        missing = sorted(set(SURFACE_KEYS) - set(OPTIONAL_SURFACE_KEYS) - set(data))
        if missing:
            raise SurfaceSpecInvalid(f"missing surface keys: {', '.join(missing)}")
        return cls(
            name=_text(data, "name"),
            identifiers=_strings(data, "identifiers"),
            hosts=_strings(data, "hosts"),
            package_names=_strings(data, "package_names"),
            version_carriers=tuple(_carrier(item) for item in _mappings(data, "version_carriers")),
            request_languages=tuple(
                _request_language(item) for item in _mappings(data, "request_languages")
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
            adjacent_contracts=tuple(
                AdjacentContract(
                    name=_text(item, "name"),
                    hosts=_strings(item, "hosts"),
                    reason=_text(item, "reason"),
                )
                for item in _optional_mappings(data, "adjacent_contracts")
            ),
            contract_surfaces=tuple(
                ContractSurface(
                    kind=_kind(item),
                    name=_text(item, "name"),
                    shape=_text(item, "shape"),
                    gated=_flag(item, "gated"),
                )
                for item in _optional_mappings(data, "contract_surfaces")
            ),
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
            "adjacent_contracts": [item.to_mapping() for item in self.adjacent_contracts],
            "contract_surfaces": [item.to_mapping() for item in self.contract_surfaces],
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


def _carrier(item: Mapping[str, Any]) -> VersionCarrier:
    scope = item.get("scope", "sdk")
    if not isinstance(scope, str) or scope not in CARRIER_SCOPES:
        raise SurfaceSpecInvalid(
            f"version carrier scope {scope!r} is not one of {', '.join(CARRIER_SCOPES)}"
        )
    languages = _strings(item, "languages")
    name = _text(item, "name")
    if scope == "wire" and tuple(languages) != ANY_LANGUAGE:
        raise SurfaceSpecInvalid(
            f"wire-scope version carrier {name!r} declares languages "
            f"{list(languages)}; a wire identifier is carried by the request itself and is "
            "readable in every language, so it must declare languages: [any]"
        )
    return VersionCarrier(
        name=name,
        regex=_text(item, "regex"),
        slot=_slot(item),
        languages=languages,
        scope=scope,
    )


def _request_language(item: Mapping[str, Any]) -> RequestLanguage:
    shapes = _strings(item, "shapes") if "shapes" in item else ()
    group = item.get("resource_group", "resource")
    if not isinstance(group, str) or not group:
        raise SurfaceSpecInvalid(f"resource_group must be a non-empty string, got {group!r}")
    for shape in shapes:
        if f"(?P<{group}>" not in shape:
            raise SurfaceSpecInvalid(
                f"request language {_text(item, 'name')!r} declares a shape without a "
                f"(?P<{group}>...) capture; the shape exists to name the resource it matched"
            )
    resources = _strings(item, "known_resources") if "known_resources" in item else ()
    return RequestLanguage(
        name=_text(item, "name"),
        anchors=_strings(item, "anchors"),
        shapes=shapes,
        resource_group=group,
        known_resources=tuple(sorted({value.lower() for value in resources})),
    )


def _flag(item: Mapping[str, Any], key: str) -> bool:
    value = item.get(key, True)
    if not isinstance(value, bool):
        raise SurfaceSpecInvalid(f"{key!r} must be a boolean, got {value!r}")
    return value


def _kind(item: Mapping[str, Any]) -> str:
    value = _text(item, "kind")
    if value not in CONTRACT_SURFACE_KINDS:
        raise SurfaceSpecInvalid(
            f"contract surface kind {value!r} is not one of {', '.join(CONTRACT_SURFACE_KINDS)}"
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


def _optional_mappings(data: Mapping[str, Any], key: str) -> tuple[Mapping[str, Any], ...]:
    if key not in data:
        return ()
    return _mappings(data, key)


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
