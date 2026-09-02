from __future__ import annotations

import hashlib
import json
from typing import Any

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_text(value: Any) -> str:
    return canonical_bytes(value).decode("utf-8")


def content_id(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def blob_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def export_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False).encode(
            "utf-8"
        )
        + b"\n"
    )
