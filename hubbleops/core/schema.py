from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from hubbleops.core.errors import SchemaViolation

SCHEMA_DIR = Path(__file__).parent / "schemas"


@cache
def _registry() -> Registry[Any]:
    resources: list[tuple[str, Resource[Any]]] = []
    for path in sorted(SCHEMA_DIR.glob("*.json")):
        document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        resources.append((str(document["$id"]), Resource.from_contents(document)))
    registry: Registry[Any] = Registry()
    return registry.with_resources(resources)


@cache
def validator_for(name: str) -> Draft202012Validator:
    path = SCHEMA_DIR / f"{name}.json"
    if not path.is_file():
        raise SchemaViolation(f"no frozen schema named {name!r} in {SCHEMA_DIR}")
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return Draft202012Validator(document, registry=_registry())


def validate(name: str, record: dict[str, Any]) -> dict[str, Any]:
    found = validator_for(name).iter_errors(record)  # pyright: ignore[reportUnknownMemberType]
    errors = sorted(found, key=lambda err: list(err.absolute_path))
    if errors:
        detail = "; ".join(
            f"{'/'.join(str(part) for part in err.absolute_path) or '<root>'}: {err.message}"
            for err in errors
        )
        raise SchemaViolation(f"{name} record rejected: {detail}")
    return record


def schema_names() -> tuple[str, ...]:
    return tuple(sorted(path.stem for path in SCHEMA_DIR.glob("*.json")))
