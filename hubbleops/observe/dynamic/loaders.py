from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from hubbleops.core.canonical import content_id
from hubbleops.core.errors import HubbleOpsError

ATTESTATION_FILENAME = "install.jsonl"
ATTESTATION_CONTAINER_PATH = f"/hops/output/{ATTESTATION_FILENAME}"
NONCE = re.compile(r"^[0-9a-f]{64}$")


class LoaderInvalid(HubbleOpsError):
    pass


@dataclass(frozen=True, slots=True)
class LoaderPlan:
    language: str
    hook_paths: tuple[Path, ...]
    environment: tuple[tuple[str, str], ...]

    def fingerprint(self) -> str:
        return content_id(
            {
                "language": self.language,
                "hooks": {
                    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in self.hook_paths
                },
                "environment": list(self.environment),
            }
        )


def plan(language: str, hook_paths: tuple[Path, ...]) -> LoaderPlan:
    normalized = language.casefold()
    paths = tuple(path.resolve() for path in hook_paths)
    if not paths or any(not path.is_file() for path in paths):
        raise LoaderInvalid(f"{normalized} capture hook bundle is missing or unreadable")
    names = {path.name for path in paths}
    if normalized == "python" and names == {"sitecustomize.py"}:
        environment = (("PYTHONPATH", "/hops/hooks"),)
    elif normalized == "php" and names == {"prepend.php"}:
        environment = (("PHP_INI_SCAN_DIR", "/hops/loader"),)
    elif normalized in ("javascript", "typescript", "node") and names == {"hook.cjs"}:
        normalized = "node"
        environment = (("NODE_OPTIONS", "--require=/hops/hooks/hook.cjs"),)
    else:
        raise LoaderInvalid(f"unsupported or malformed capture hook bundle for {normalized}")
    return LoaderPlan(normalized, paths, environment)


def attestation_environment(nonce: str) -> tuple[tuple[str, str], ...]:
    if NONCE.fullmatch(nonce) is None:
        raise LoaderInvalid("install attestation nonce must be a sha256 hex digest")
    return (
        ("HUBBLEOPS_INSTALL_NONCE", nonce),
        ("HUBBLEOPS_INSTALL_PATH", ATTESTATION_CONTAINER_PATH),
    )


def attested(data: bytes, nonce: str) -> bool:
    if NONCE.fullmatch(nonce) is None:
        raise LoaderInvalid("install attestation nonce must be a sha256 hex digest")
    for line in data.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(record, dict) and cast(dict[str, object], record).get("nonce") == nonce:
            return True
    return False


__all__ = [
    "ATTESTATION_CONTAINER_PATH",
    "ATTESTATION_FILENAME",
    "LoaderInvalid",
    "LoaderPlan",
    "attestation_environment",
    "attested",
    "plan",
]
