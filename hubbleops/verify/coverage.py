from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from hubbleops.core.records import as_mapping, as_sequence, parse_json
from hubbleops.core.verification import SuiteCase

PLUGIN_FILENAME = "hops_coverage_plugin.py"
REPORT_FILENAME = "coverage.jsonl"
SUPPORTED_LANGUAGES = frozenset({"python"})
COVERAGE_UNSUPPORTED = "COVERAGE_UNSUPPORTED"
MAX_REPORT_BYTES = 16_777_216

PLUGIN_SOURCE = """from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ["HOPS_COVERAGE_ROOT"]).resolve()
REPORT = Path(os.environ["HOPS_COVERAGE_REPORT"])
PLUGIN = "hops_coverage_plugin.py"
TOOL_ID = 4

_active: set[str] = set()
_registered = False


def _relative(filename: str) -> str | None:
    if not filename or filename.startswith("<") or filename.endswith(PLUGIN):
        return None
    try:
        resolved = Path(filename).resolve()
    except (OSError, ValueError):
        return None
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return None


def _on_line(code: object, line_number: int) -> object:
    filename = getattr(code, "co_filename", "")
    relative = _relative(filename)
    if relative is not None:
        _active.add(relative)
    return sys.monitoring.DISABLE if relative is None else None


def _start() -> None:
    global _registered
    if not _registered:
        sys.monitoring.use_tool_id(TOOL_ID, "hops-coverage")
        sys.monitoring.register_callback(TOOL_ID, sys.monitoring.events.LINE, _on_line)
        _registered = True
    _active.clear()
    sys.monitoring.set_events(TOOL_ID, sys.monitoring.events.LINE)
    sys.monitoring.restart_events()


def _stop() -> list[str]:
    sys.monitoring.set_events(TOOL_ID, 0)
    return sorted(_active)


def pytest_runtest_logstart(nodeid, location):
    _start()


def pytest_runtest_logreport(report):
    if report.when != "call":
        return
    files = _stop()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {"test": report.nodeid, "outcome": report.outcome, "files": files},
        sort_keys=True,
        separators=(",", ":"),
    )
    with REPORT.open("a", encoding="utf-8") as handle:
        handle.write(line + "\\n")
"""


def plugin_source() -> str:
    return PLUGIN_SOURCE


def install(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / PLUGIN_FILENAME
    target.write_text(PLUGIN_SOURCE, encoding="utf-8")
    return target


def environment(root: Path, report: Path) -> dict[str, str]:
    return {
        "HOPS_COVERAGE_ROOT": str(root.resolve()),
        "HOPS_COVERAGE_REPORT": str(report),
    }


def read_report(path: Path) -> tuple[SuiteCase, ...]:
    if not path.is_file():
        return ()
    payload = path.read_bytes()[:MAX_REPORT_BYTES]
    outcomes: list[SuiteCase] = []
    for line in payload.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        parsed = parse_json(line.encode("utf-8"))
        if not parsed.ok():
            continue
        record = as_mapping(parsed.value)
        if record:
            outcomes.append(_outcome(record))
    return tuple(sorted(outcomes, key=lambda item: item.name))


def _outcome(record: Mapping[str, Any]) -> SuiteCase:
    return SuiteCase(
        name=str(record.get("test", "")),
        outcome=str(record.get("outcome", "")),
        files=tuple(sorted(str(item) for item in as_sequence(record.get("files")))),
    )


def unsupported_modules(modules: Iterable[str], languages: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            module
            for module in modules
            if languages.get(module, "unknown") not in SUPPORTED_LANGUAGES
        )
    )


def summarize(outcomes: Sequence[SuiteCase]) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    for outcome in outcomes:
        if outcome.outcome in counts:
            counts[outcome.outcome] += 1
        else:
            counts["failed"] += 1
    return counts


def encode(outcomes: Sequence[SuiteCase]) -> bytes:
    return b"".join(
        json.dumps(outcome.to_mapping(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        + b"\n"
        for outcome in outcomes
    )


__all__ = [
    "COVERAGE_UNSUPPORTED",
    "PLUGIN_FILENAME",
    "REPORT_FILENAME",
    "SUPPORTED_LANGUAGES",
    "encode",
    "environment",
    "install",
    "plugin_source",
    "read_report",
    "summarize",
    "unsupported_modules",
]
