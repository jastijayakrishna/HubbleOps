from __future__ import annotations

import datetime
import http.client
import inspect
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from hubbleops_sentinel.events import normalize
from hubbleops_sentinel.wire import parse

_original = http.client.HTTPConnection.request


def _stack(repository: Path) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    captured = inspect.stack()[2:]
    for frame in captured[:255]:
        path = Path(frame.filename).as_posix()
        resolved = Path(frame.filename).resolve()
        try:
            relative = resolved.relative_to(repository)
        except ValueError:
            relative = None
        if relative is not None:
            kind = "repository"
            path = relative.as_posix()
        elif "site-packages/" in path:
            kind = "dependency"
            path = "<dependency>/" + path.split("site-packages/", 1)[1]
        else:
            kind = "runtime"
            path = "<runtime>/" + Path(path).name
        frames.append(
            {"kind": kind, "path": path, "line": frame.lineno, "function": frame.function}
        )
    if len(captured) > 255:
        frames.append(
            {
                "kind": "truncation",
                "path": "<runtime>/truncated",
                "line": None,
                "function": None,
                "omitted": len(captured) - 255,
            }
        )
    return frames


Request = Callable[..., None]


def install(output: Path, repository: Path) -> Request:
    destination = output.resolve()
    root = repository.resolve()

    def request(
        connection: http.client.HTTPConnection,
        method: str,
        url: str,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
        *,
        encode_chunked: bool = False,
    ) -> None:
        matched = parse(url, {})
        if matched is not None:
            text = None if body is None else str(body)[:65_536]
            event = normalize(
                {
                    **matched,
                    "request_text": text,
                    "request_type": "http",
                    "stack": _stack(root),
                    "ts": datetime.datetime.now(datetime.UTC)
                    .isoformat(timespec="microseconds")
                    .replace("+00:00", "Z"),
                    "mode": "hook",
                },
                "hook",
            )
            descriptor = os.open(destination, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                os.write(descriptor, json.dumps(event, sort_keys=True).encode("utf-8") + b"\n")
            finally:
                os.close(descriptor)
        offered_headers: Mapping[str, str] = {} if headers is None else headers
        _original(connection, method, url, body, offered_headers, encode_chunked=encode_chunked)

    setattr(http.client.HTTPConnection, "request", request)  # noqa: B010
    return _original


def uninstall(original: Request) -> None:
    setattr(http.client.HTTPConnection, "request", original)  # noqa: B010


__all__ = ["install", "uninstall"]
