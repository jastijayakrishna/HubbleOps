from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, cast

DATA = Path(__file__).parent / "data"
SCHEMA = DATA / "schema.json"
CORPUS = DATA / "wire_conformance.json"
FIELDS = frozenset(
    {"version", "service", "method", "request_text", "request_type", "stack", "ts", "mode"}
)
REQUIRED = FIELDS - {"mode"}


class EventInvalid(ValueError):
    pass


def normalize(value: object, mode: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EventInvalid("event must be an object")
    event = dict(cast(dict[str, Any], value))
    if set(event) - FIELDS or not REQUIRED <= set(event):
        raise EventInvalid("event fields do not match schema revision 1")
    if event.get("mode", mode) != mode or mode not in ("hook", "proxy"):
        raise EventInvalid("event mode disagrees with invocation mode")
    event["mode"] = mode
    bounds = {"service": 256, "method": 256, "request_type": 64}
    for field, maximum in bounds.items():
        if not isinstance(event[field], str) or not event[field]:
            raise EventInvalid(f"{field} must be a non-empty string")
        if len(event[field]) > maximum:
            raise EventInvalid(f"{field} exceeds schema bound")
    version = event["version"]
    if (
        not isinstance(version, str)
        or len(version) > 32
        or not version.startswith("v")
        or not version[1:].isdigit()
    ):
        raise EventInvalid("version must match v followed by digits")
    request_text = event["request_text"]
    if request_text is not None and (
        not isinstance(request_text, str) or len(request_text) > 65_536
    ):
        raise EventInvalid("request_text exceeds schema bound")
    timestamp = event["ts"]
    if not isinstance(timestamp, str) or len(timestamp) > 32 or not timestamp.endswith("Z"):
        raise EventInvalid("timestamp must be RFC 3339 UTC")
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo != UTC:
        raise EventInvalid("timestamp must use UTC")
    event["stack"] = _stack(event["stack"])
    return event


def _stack(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise EventInvalid("stack exceeds schema bound")
    values = cast(list[object], value)
    if len(values) > 256:
        raise EventInvalid("stack exceeds schema bound")
    frames: list[dict[str, Any]] = []
    for value_frame in values:
        if not isinstance(value_frame, dict):
            raise EventInvalid("stack frame must be an object")
        frame = dict(cast(dict[str, Any], value_frame))
        required = {"kind", "path", "line", "function"}
        allowed = required | {"omitted"}
        if not required <= set(frame) or set(frame) - allowed:
            raise EventInvalid("stack frame fields do not match schema")
        kind = frame["kind"]
        path = frame["path"]
        if kind not in ("repository", "dependency", "runtime", "truncation"):
            raise EventInvalid("stack frame kind is invalid")
        if (
            not isinstance(path, str)
            or not path
            or len(path) > 1024
            or "\\" in path
            or "\x00" in path
        ):
            raise EventInvalid("stack frame path is invalid")
        if kind == "repository":
            pure = PurePosixPath(path)
            if pure.is_absolute() or ".." in pure.parts:
                raise EventInvalid("repository stack frame escapes its root")
        if kind in ("dependency", "runtime") and not path.startswith(f"<{kind}>/"):
            raise EventInvalid("external stack frame is not normalized")
        omitted = frame.get("omitted")
        if kind == "truncation" and (
            not isinstance(omitted, int) or isinstance(omitted, bool) or omitted < 1
        ):
            raise EventInvalid("truncation frame requires omitted count")
        if kind != "truncation" and "omitted" in frame:
            raise EventInvalid("only truncation frames carry omitted count")
        line = frame["line"]
        if line is not None and (not isinstance(line, int) or isinstance(line, bool) or line < 1):
            raise EventInvalid("stack line is invalid")
        function = frame["function"]
        if function is not None and (not isinstance(function, str) or len(function) > 512):
            raise EventInvalid("stack function is invalid")
        frames.append(frame)
    return frames


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
