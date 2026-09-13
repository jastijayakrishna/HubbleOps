from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from itertools import pairwise
from typing import Any

from hubbleops.app.registry import LoadedPack
from hubbleops.core.records import as_text
from hubbleops.packs._protocol import CatalogFact

STABLE = "STABLE"
UNDECIDED = "UNDECIDED"
CHANGES_AT = "CHANGES_AT:"
UNRESOLVED = "UNKNOWN_PROVIDER_CONTRACT"
DOCUMENTED_CHANGE_KIND = "documented_change"
CHANGE_SUBJECT_ATTRIBUTE = "change_subject"
BREAKING_ATTRIBUTES = (
    "data_type",
    "proto_data_type",
    "category",
    "selectable",
    "filterable",
    "repeated",
)

Facts = Mapping[str, Sequence[CatalogFact]]
_COMPUTED: dict[tuple[str, ...], dict[str, str]] = {}


def compute(pack: LoadedPack) -> dict[str, str]:
    versions = pack.versions()
    key = (pack.name, *(f"{version.id}={version.catalog_hash}" for version in versions))
    cached = _COMPUTED.get(key)
    if cached is not None:
        return dict(cached)
    table = _compute(pack, tuple(version.id for version in versions))
    _COMPUTED[key] = dict(table)
    return table


def _compute(pack: LoadedPack, lattice: tuple[str, ...]) -> dict[str, str]:
    if not lattice:
        return {}
    facts: Facts = {version: pack.contract.catalog(version).facts for version in lattice}
    table: dict[str, str] = {}
    for entry, disposition in _surface_dispositions(pack, lattice, facts).items():
        _merge(table, lattice, entry, disposition)
    for subject, disposition in _catalog_dispositions(lattice, facts).items():
        _merge(table, lattice, subject, disposition)
    return dict(sorted(table.items()))


def _catalog_dispositions(lattice: tuple[str, ...], facts: Facts) -> dict[str, str]:
    shapes: dict[str, dict[str, tuple[Any, ...]]] = {}
    undecided: set[str] = set()
    bare_names: dict[str, set[str]] = {}
    for version in lattice:
        for fact in facts[version]:
            if fact.resolution == UNRESOLVED:
                undecided.add(fact.subject)
            shapes.setdefault(fact.subject, {})[version] = _shape(fact)
            prefix = f"{fact.kind}."
            if fact.subject.startswith(prefix) and len(fact.subject) > len(prefix):
                bare_names.setdefault(fact.subject, set()).add(fact.subject[len(prefix) :])
    result: dict[str, str] = {}
    for subject in sorted(shapes):
        disposition = UNDECIDED if subject in undecided else _across(lattice, shapes[subject])
        _merge(result, lattice, subject, disposition)
        for bare in sorted(bare_names.get(subject, ())):
            _merge(result, lattice, bare, disposition)
    return result


def _shape(fact: CatalogFact) -> tuple[Any, ...]:
    return (fact.kind, *(fact.attributes.get(name) for name in BREAKING_ATTRIBUTES))


def _across(lattice: tuple[str, ...], present: Mapping[str, tuple[Any, ...]]) -> str:
    boundaries = [
        later for earlier, later in pairwise(lattice) if present.get(earlier) != present.get(later)
    ]
    return STABLE if not boundaries else CHANGES_AT + ",".join(boundaries)


def _surface_dispositions(
    pack: LoadedPack, lattice: tuple[str, ...], facts: Facts
) -> dict[str, str]:
    surface = pack.surface
    carriers = [re.compile(carrier.regex) for carrier in surface.version_carriers]
    named_versions = [
        re.compile(rf"(?<![0-9a-z]){re.escape(version)}(?![0-9a-z])", re.IGNORECASE)
        for version in lattice
    ]
    version_selecting = frozenset(surface.config_env_keys)
    changed: dict[str, set[str]] = {}
    for earlier, later in pairwise(lattice):
        for diff_fact in pack.contract.diff(earlier, later).facts:
            changed.setdefault(diff_fact.subject, set()).add(later)
    for version in lattice:
        for fact in facts[version]:
            if fact.kind != DOCUMENTED_CHANGE_KIND:
                continue
            named = (fact.subject, as_text(fact.attributes.get(CHANGE_SUBJECT_ATTRIBUTE)))
            for subject in named:
                if subject:
                    changed.setdefault(subject, set()).add(version)
    result: dict[str, str] = {}
    entries = set(surface.identifiers) | set(surface.hosts) | set(surface.package_names)
    for entry in sorted(entries):
        if entry in version_selecting:
            continue
        if any(pattern.search(entry) for pattern in (*carriers, *named_versions)):
            continue
        boundaries = sorted(changed.get(entry, ()), key=lattice.index)
        result[entry] = STABLE if not boundaries else CHANGES_AT + ",".join(boundaries)
    return result


def _merge(table: dict[str, str], lattice: tuple[str, ...], key: str, disposition: str) -> None:
    current = table.get(key)
    if current is None or current == disposition:
        table[key] = disposition
        return
    if UNDECIDED in (current, disposition):
        table[key] = UNDECIDED
        return
    boundaries = sorted(_boundaries(current) | _boundaries(disposition), key=lattice.index)
    table[key] = CHANGES_AT + ",".join(boundaries)


def _boundaries(disposition: str) -> set[str]:
    if not disposition.startswith(CHANGES_AT):
        return set()
    return {item for item in disposition[len(CHANGES_AT) :].split(",") if item}
