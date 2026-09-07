from __future__ import annotations

import argparse
import hashlib
import json
import runpy
import sys
import tempfile
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from hubbleops_sentinel import __version__
from hubbleops_sentinel.adapters.google_ads import install, uninstall
from hubbleops_sentinel.events import (
    CORPUS,
    SCHEMA,
    EventInvalid,
    canonical,
    digest,
    normalize,
    write_atomic,
)
from hubbleops_sentinel.wire import parse

MAX_INPUT_BYTES = 16_777_216
MAX_EVENTS = 10_000


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hubbleops-sentinel",
        description="Observational sensor; proxy mode is the recommended default.",
    )
    parser.add_argument("mode", choices=("hook", "proxy"), nargs="?", default="proxy")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--export-url")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args(argv)
    try:
        events, issues = _read(Path(args.input), args.mode)
        output = Path(args.output).resolve()
        payload = b"".join(canonical(event) + b"\n" for event in events)
        write_atomic(output, payload)
        manifest = _manifest(args.mode, payload, issues)
        write_atomic(output.with_name(f"{output.name}.manifest.json"), canonical(manifest) + b"\n")
        if args.export_url:
            _export(str(args.export_url), payload, float(args.timeout))
    except (OSError, ValueError, EventInvalid) as error:
        print(f"UNKNOWN_SENTINEL: {error}", file=sys.stderr)
        return 5
    return 5 if issues else 0


def _read(path: Path, mode: str) -> tuple[list[dict[str, Any]], list[dict[str, object]]]:
    data = _hook_data(path) if mode == "hook" else path.read_bytes()
    if len(data) > MAX_INPUT_BYTES:
        raise EventInvalid("input exceeds byte bound")
    events: list[dict[str, Any]] = []
    issues: list[dict[str, object]] = []
    for row, line in enumerate(data.splitlines(), start=1):
        if not line.strip():
            continue
        if len(events) >= MAX_EVENTS:
            issues.append({"code": "EVENT_COUNT_LIMIT", "row": row})
            break
        try:
            value = json.loads(line)
            event = normalize(value, "hook") if mode == "hook" else _proxy_event(value)
        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            EventInvalid,
            TypeError,
            KeyError,
        ) as error:
            issues.append({"code": "EVENT_INVALID", "row": row, "reason": str(error)})
            continue
        if any(frame.get("kind") == "truncation" for frame in event["stack"]):
            issues.append(
                {
                    "code": "STACK_TRUNCATED",
                    "row": row,
                    "reason": "event stack exceeded frame bound",
                }
            )
        events.append(event)
    return sorted(events, key=canonical), issues


def _hook_data(path: Path) -> bytes:
    if path.suffix.casefold() != ".py":
        raise EventInvalid("hook input must be a Python application entrypoint")
    with tempfile.TemporaryDirectory(prefix="hubbleops-sentinel-") as temporary:
        events = Path(temporary) / "events.jsonl"
        original = install(events)
        try:
            runpy.run_path(str(path.resolve()), run_name="__main__")
        finally:
            uninstall(original)
        return events.read_bytes() if events.is_file() else b""


def _proxy_event(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EventInvalid("proxy record must be an object")
    record = cast(dict[str, Any], value)
    path = record.get("path")
    headers = record.get("headers", {})
    if not isinstance(path, str) or not isinstance(headers, Mapping):
        raise EventInvalid("proxy record path or headers are invalid")
    header_map = cast(Mapping[object, object], headers)
    matched = parse(path, {str(key): str(item) for key, item in header_map.items()})
    if matched is None:
        raise EventInvalid("UNKNOWN_WIRE_SIGNATURE")
    return normalize(
        {
            **matched,
            "request_text": record.get("request_text"),
            "request_type": record.get("request_type", "http"),
            "stack": record.get("stack", []),
            "ts": record["ts"],
            "mode": "proxy",
        },
        "proxy",
    )


def _manifest(mode: str, payload: bytes, issues: list[dict[str, object]]) -> dict[str, object]:
    adapter = Path(__file__).parent / "adapters" / "google_ads.py"
    return {
        "adapter_sha256": digest(adapter),
        "corpus_sha256": digest(CORPUS),
        "events_sha256": hashlib.sha256(payload).hexdigest(),
        "issues": issues,
        "mode": mode,
        "output_limits": {"events": MAX_EVENTS, "input_bytes": MAX_INPUT_BYTES},
        "package_version": __version__,
        "schema_sha256": digest(SCHEMA),
    }


def _export(url: str, payload: bytes, timeout: float) -> None:
    if timeout <= 0:
        raise ValueError("export timeout must be positive")
    if not url.startswith(("http://", "https://")):
        raise ValueError("export URL must use HTTP or HTTPS")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/x-ndjson"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if not 200 <= response.status < 300:
            raise OSError(f"export returned HTTP {response.status}")
