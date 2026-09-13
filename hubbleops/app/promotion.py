from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, cast

import yaml

from hubbleops.core.canonical import content_id
from hubbleops.core.errors import HubbleOpsError
from hubbleops.core.observer import StructuralRule
from hubbleops.core.records import as_mapping, as_sequence, as_text
from hubbleops.store.sqlite import Store

FILENAME = Path(".hubbleops") / "surface.yml"
LANGUAGES = {
    ".js": ("javascript", "JavaScript"),
    ".jsx": ("javascript", "JavaScript"),
    ".php": ("php", "Php"),
    ".py": ("python", "Python"),
    ".ts": ("typescript", "TypeScript"),
    ".tsx": ("tsx", "Tsx"),
}
ENTRY_KEYS = frozenset(
    {
        "candidate_id",
        "evidence_ids",
        "id",
        "language",
        "provider",
        "rule",
        "run_id",
        "source_hash",
        "source_path",
        "state",
        "symbol",
    }
)


class PromotionInvalid(HubbleOpsError):
    pass


def promote(
    repository: Path,
    store: Store,
    run_id: str,
    candidate_id: str,
    *,
    revoke: bool,
) -> Path:
    root = repository.resolve()
    path = root / FILENAME
    document = _load(path)
    entries = list(as_sequence(document.get("entries")))
    if revoke:
        changed = False
        for entry_object in entries:
            entry = as_mapping(entry_object)
            if entry.get("id") == candidate_id or entry.get("candidate_id") == candidate_id:
                if isinstance(entry_object, dict):
                    entry_object["state"] = "revoked"
                changed = True
        if not changed:
            raise PromotionInvalid(f"promotion entry not found for revocation: {candidate_id}")
        _write(path, {"entries": entries, "version": 1})
        return path
    run = store.run(run_id)
    if run is None:
        raise PromotionInvalid(f"capture run not found: {run_id}")
    candidate = next(
        (item for item in store.candidates_for(run_id) if item["id"] == candidate_id), None
    )
    if candidate is None:
        raise PromotionInvalid(f"candidate not found in capture run: {candidate_id}")
    evidence_index = {item["id"]: item for item in store.evidence_for(run_id)}
    evidence = [
        evidence_index[evidence_id]
        for evidence_id in candidate["evidence_ids"]
        if evidence_id in evidence_index
        and evidence_index[evidence_id]["observer"] in ("dynamic", "sentinel")
        and evidence_index[evidence_id]["derivation"] == "OBSERVED"
    ]
    selected = _observed_frame(evidence)
    if selected is None:
        raise PromotionInvalid("promotion requires observed dynamic or sentinel repository stack")
    frame, expected = selected
    source_path = as_text(frame.get("path"))
    symbol = as_text(frame.get("function"))
    if (
        source_path is None
        or symbol is None
        or re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", symbol) is None
    ):
        raise PromotionInvalid("promotion stack frame has no supported source symbol")
    source = _source(root, source_path)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if source_hash != expected:
        raise PromotionInvalid("PROMOTION_SOURCE_DRIFT: evidence source bytes changed")
    language = LANGUAGES.get(source.suffix.lower())
    if language is None:
        raise PromotionInvalid(f"promotion language is unsupported: {source.suffix}")
    identity = content_id(
        {
            "language": language[0],
            "provider": run.provider,
            "source_hash": source_hash,
            "source_path": source_path,
            "symbol": symbol,
        }
    )
    entry = {
        "candidate_id": candidate_id,
        "evidence_ids": sorted(item["id"] for item in evidence),
        "id": identity,
        "language": language[0],
        "provider": run.provider,
        "rule": {
            "constraints": {"SINK": {"regex": f"^{re.escape(symbol)}$"}},
            "id": f"hops-promoted-{identity[:16]}",
            "language": language[1],
            "message": "Observed repository wrapper",
            "rule": {"pattern": "$SINK($$$ARGS)"},
            "severity": "info",
        },
        "run_id": run_id,
        "source_hash": source_hash,
        "source_path": source_path,
        "state": "active",
        "symbol": symbol,
    }
    retained = [
        item
        for item in entries
        if as_mapping(item).get("id") != identity
        and as_mapping(item).get("candidate_id") != candidate_id
    ]
    retained.append(entry)
    ordered = sorted(retained, key=lambda item: str(as_mapping(item).get("id")))
    _write(path, {"entries": ordered, "version": 1})
    return path


def materialize_active(repository: Path, destination: Path) -> tuple[StructuralRule, ...]:
    root = repository.resolve()
    path = root / FILENAME
    document = _load(path)
    rules: list[StructuralRule] = []
    for raw in as_sequence(document.get("entries")):
        entry = as_mapping(raw)
        _validate_entry(entry)
        if entry["state"] != "active":
            continue
        source = _source(root, str(entry["source_path"]))
        found = hashlib.sha256(source.read_bytes()).hexdigest()
        if found != entry["source_hash"]:
            raise PromotionInvalid(
                "PROMOTION_SOURCE_DRIFT: "
                f"{entry['source_path']} no longer matches {entry['source_hash']}"
            )
        payload = yaml.safe_dump(dict(as_mapping(entry["rule"])), sort_keys=True).encode("utf-8")
        rule_path = destination / f"{entry['id']}.yml"
        _exclusive_write(rule_path, payload)
        rules.append(
            StructuralRule(
                id=str(as_mapping(entry["rule"])["id"]),
                language=str(entry["language"]),
                path=rule_path,
                sha256=hashlib.sha256(payload).hexdigest(),
            )
        )
    return tuple(sorted(rules))


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"entries": [], "version": 1}
    try:
        raw = cast(object, yaml.safe_load(path.read_bytes()))
    except yaml.YAMLError as error:
        raise PromotionInvalid(f"promotion YAML is invalid: {error}") from error
    mapping = as_mapping(raw)
    if (
        not isinstance(raw, Mapping)
        or mapping.get("version") != 1
        or not isinstance(mapping.get("entries"), list)
    ):
        raise PromotionInvalid("promotion file must contain version 1 and an entries list")
    return dict(mapping)


def _validate_entry(entry: Mapping[str, Any]) -> None:
    if frozenset(entry.keys()) != ENTRY_KEYS:
        raise PromotionInvalid("promotion entry fields do not match version 1")
    if entry.get("state") not in ("active", "revoked"):
        raise PromotionInvalid("promotion state must be active or revoked")
    if entry.get("language") not in set(item[0] for item in LANGUAGES.values()):
        raise PromotionInvalid("promotion language is unsupported")
    for key in ("candidate_id", "id", "provider", "run_id", "source_hash", "source_path", "symbol"):
        if not isinstance(entry.get(key), str) or not entry[key]:
            raise PromotionInvalid(f"promotion {key} must be a non-empty string")


def _observed_frame(
    evidence: list[dict[str, Any]],
) -> tuple[dict[str, Any], str] | None:
    for record in evidence:
        source_hashes = {
            str(item.get("path")): str(item.get("source_hash"))
            for raw in as_sequence(as_mapping(record["value"]).get("stack_sources"))
            if (item := as_mapping(raw))
        }
        for raw in as_sequence(as_mapping(record["value"]).get("stack")):
            frame = dict(as_mapping(raw))
            expected = source_hashes.get(str(frame.get("path")))
            if frame.get("kind") == "repository" and expected is not None:
                return frame, expected
    return None


def _source(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise PromotionInvalid("promotion source path is not repository-relative")
    source = (root / pure).resolve()
    try:
        source.relative_to(root)
    except ValueError as error:
        raise PromotionInvalid("promotion source path escapes repository") from error
    if not source.is_file():
        raise PromotionInvalid(f"PROMOTION_SOURCE_DRIFT: source is missing: {relative}")
    return source


def _write(path: Path, document: dict[str, Any]) -> None:
    data = yaml.safe_dump(document, sort_keys=True).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _exclusive_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)


__all__ = ["PromotionInvalid", "materialize_active", "promote"]
