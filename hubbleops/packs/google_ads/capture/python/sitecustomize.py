from __future__ import annotations

import datetime
import http.client
import inspect
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

GRPC = re.compile(
    r"^/?google\.ads\.googleads\.(v[0-9]+)\.services\."
    r"([A-Za-z][A-Za-z0-9]*Service)/([A-Za-z][A-Za-z0-9]*)$"
)
REST = re.compile(r"^/?(v[0-9]+)/customers/[^/?]+/googleAds:(searchStream|search|mutate)(?:\?.*)?$")
ORIGINAL = http.client.HTTPConnection.request


def _target(path: str) -> tuple[str, str, str, str] | None:
    match = GRPC.fullmatch(path)
    if match:
        return match[1], match[2], match[3], "grpc"
    match = REST.fullmatch(path)
    if match:
        methods = {"search": "Search", "searchStream": "SearchStream", "mutate": "Mutate"}
        return match[1], "GoogleAdsService", methods[match[2]], "rest"
    return None


def _frame(frame: inspect.FrameInfo) -> dict[str, Any]:
    path = Path(frame.filename).as_posix()
    if path.startswith("/workspace/"):
        kind = "repository"
        path = path.removeprefix("/workspace/")
    elif "site-packages/" in path:
        kind = "dependency"
        path = "<dependency>/" + path.split("site-packages/", 1)[1]
    else:
        kind = "runtime"
        path = "<runtime>/" + Path(path).name
    return {"kind": kind, "path": path, "line": frame.lineno, "function": frame.function}


def _stack() -> list[dict[str, Any]]:
    frames = [_frame(frame) for frame in inspect.stack()[3:]]
    if len(frames) <= 256:
        return frames
    return [
        *frames[:255],
        {
            "kind": "truncation",
            "path": "<runtime>/truncated",
            "line": None,
            "function": None,
            "omitted": len(frames) - 255,
        },
    ]


def _text(body: object) -> str | None:
    if body is None:
        return None
    if isinstance(body, bytes):
        return body[:65_536].decode("utf-8", errors="replace")
    return str(body)[:65_536]


def _emit(path: str, body: object) -> None:
    matched = _target(path)
    destination = os.environ.get("HUBBLEOPS_EVENT_PATH")
    if matched is None or destination is None:
        return
    version, service, method, request_type = matched
    event = {
        "version": version,
        "service": service,
        "method": method,
        "request_text": _text(body),
        "request_type": request_type,
        "stack": _stack(),
        "ts": datetime.datetime.now(datetime.UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        "mode": "hook",
    }
    payload = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    descriptor = os.open(destination, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, payload.encode("utf-8"))
    finally:
        os.close(descriptor)


def _request(
    self: http.client.HTTPConnection,
    method: str,
    url: str,
    body: Any = None,
    headers: Mapping[str, str] | None = None,
    *,
    encode_chunked: bool = False,
) -> None:
    _emit(url, body)
    offered_headers: Mapping[str, str] = {} if headers is None else headers
    ORIGINAL(self, method, url, body, offered_headers, encode_chunked=encode_chunked)


def _attest() -> None:
    destination = os.environ.get("HUBBLEOPS_INSTALL_PATH")
    nonce = os.environ.get("HUBBLEOPS_INSTALL_NONCE")
    if destination is None or nonce is None:
        return
    payload = json.dumps({"language": "python", "nonce": nonce}, sort_keys=True) + "\n"
    descriptor = os.open(destination, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, payload.encode("utf-8"))
    finally:
        os.close(descriptor)


setattr(http.client.HTTPConnection, "request", _request)  # noqa: B010
_attest()
