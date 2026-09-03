from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from hubbleops.core.canonical import content_id
from hubbleops.core.schema import validate

PROOF_SCOPE_PREFIX = "ps_"


def scanner_fingerprint(sources: Iterable[Path]) -> str:
    paths = sorted(sources)
    root = (
        Path(os.path.commonpath([str(path) for path in paths]))
        if len(paths) > 1
        else paths[0].parent
    )
    return content_id(
        {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths
        }
    )


def make_proof_scope(
    *,
    repo_sha: str | None,
    tree_hash: str,
    dependency_resolution_hash: str | None,
    scanner_version: str,
    build_command: str | None = None,
    build_config_hash: str | None = None,
    provider_contract_hash: str | None = None,
    rules_hash: str | None = None,
    verifier_version: str | None = None,
    verifier_image_hash: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "repo_sha": repo_sha,
        "tree_hash": tree_hash,
        "dependency_resolution_hash": dependency_resolution_hash,
        "build_command": build_command,
        "build_config_hash": build_config_hash,
        "provider_contract_hash": provider_contract_hash,
        "rules_hash": rules_hash,
        "scanner_version": scanner_version,
        "verifier_version": verifier_version,
        "verifier_image_hash": verifier_image_hash,
    }
    return validate("proof_scope", record)


def proof_scope_hash(record: dict[str, Any]) -> str:
    return content_id(record)


def short_scope(scope_hash: str) -> str:
    return f"{PROOF_SCOPE_PREFIX}{scope_hash[:8]}"


def run_id_for(*, scope_hash: str, provider: str, verb: str, target: str) -> str:
    return content_id(
        {"proof_scope_hash": scope_hash, "provider": provider, "verb": verb, "target": target}
    )
