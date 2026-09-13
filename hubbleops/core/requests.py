from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hubbleops.core.records import as_mapping, as_sequence, as_text, is_mapping, parse_json

REQUEST_CLAIM_TYPE = "request_text"
CAPTURING_OBSERVERS = ("dynamic", "sentinel")
QUERY_METHODS = ("Search", "SearchStream")


def static_request(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = as_mapping(record.get("value"))
    dynamic = request_from_text(value)
    if dynamic is not None:
        return {
            "service": as_text(value.get("service")) or "",
            "method": as_text(value.get("method")) or "",
            "request": dynamic,
            "origin": (
                "captured"
                if record.get("observer") in CAPTURING_OBSERVERS
                else f"static:{record['path']}:{record['line_start'] or 0}"
            ),
        }
    skeleton = as_mapping(value.get("skeleton"))
    if as_sequence(skeleton.get("holes")):
        return None
    fragments = [str(item) for item in as_sequence(skeleton.get("fragments"))]
    text = as_text(value.get("text")) or as_text(value.get("query")) or " ".join(fragments)
    if not text.strip():
        return None
    sink = as_mapping(value.get("sink"))
    return {
        "service": as_text(value.get("service")) or "",
        "method": as_text(value.get("method")) or "",
        "request": {"query": text},
        "origin": f"static:{record['path']}:{record['line_start'] or 0}",
        **({"sink": dict(sink)} if sink else {}),
    }


def request_from_text(value: Mapping[str, Any]) -> Mapping[str, Any] | None:
    text = as_text(value.get("request_text"))
    if text is None or not text.strip():
        return None
    parsed = parse_json(text)
    if parsed.ok() and is_mapping(parsed.value) and parsed.value:
        return dict(as_mapping(parsed.value))
    method = as_text(value.get("method")) or ""
    if method in QUERY_METHODS:
        return {"query": text}
    return None


__all__ = ["REQUEST_CLAIM_TYPE", "request_from_text", "static_request"]
