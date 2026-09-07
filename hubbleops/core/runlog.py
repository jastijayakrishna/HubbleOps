from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any, TextIO

LOGGER_NAME = "hubbleops"
LEVEL_VARIABLE = "HOPS_LOG"

LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

RESERVED = frozenset(
    {
        "args",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)

FIELD_ORDER = (
    "level",
    "component",
    "event",
    "run_id",
    "candidate_id",
    "obligation_id",
    "duration_ms",
    "outcome",
)


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "component": record.name.partition(".")[2] or record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in RESERVED or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info is not None:
            payload["error"] = self.formatException(record.exc_info).splitlines()[-1]
        ordered = {key: payload[key] for key in FIELD_ORDER if key in payload}
        ordered.update({key: payload[key] for key in sorted(payload) if key not in ordered})
        return json.dumps(ordered, sort_keys=False, separators=(",", ":"), default=str)


def configure(stream: TextIO | None = None) -> bool:
    requested = os.environ.get(LEVEL_VARIABLE, "").strip().lower()
    root = logging.getLogger(LOGGER_NAME)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    if requested not in LEVELS:
        root.addHandler(logging.NullHandler())
        root.propagate = False
        return False
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(StructuredFormatter())
    root.addHandler(handler)
    root.setLevel(LEVELS[requested])
    root.propagate = False
    return True


class RunLogger:
    def __init__(self, component: str, run_id: str | None = None) -> None:
        self._logger = logging.getLogger(f"{LOGGER_NAME}.{component}")
        self._run_id = run_id

    def bind(self, run_id: str) -> RunLogger:
        return RunLogger(self._logger.name.partition(".")[2], run_id)

    def _fields(self, fields: dict[str, Any]) -> dict[str, Any]:
        if self._run_id is not None:
            fields.setdefault("run_id", self._run_id)
        return fields

    def event(self, event: str, **fields: Any) -> None:
        self._logger.info(event, extra=self._fields(fields))

    def warning(self, event: str, **fields: Any) -> None:
        self._logger.warning(event, extra=self._fields(fields))

    @contextmanager
    def stage(self, event: str, **fields: Any) -> Generator[dict[str, Any]]:
        extra: dict[str, Any] = {}
        started = time.perf_counter()
        try:
            yield extra
        except BaseException:
            elapsed = round((time.perf_counter() - started) * 1000, 3)
            merged = self._fields({**fields, **extra})
            merged.update({"duration_ms": elapsed, "outcome": "FAILED"})
            self._logger.error(event, exc_info=True, extra=merged)
            raise
        elapsed = round((time.perf_counter() - started) * 1000, 3)
        merged = self._fields({**fields, **extra})
        merged.setdefault("outcome", "OK")
        merged["duration_ms"] = elapsed
        self._logger.info(event, extra=merged)


def logger(component: str, run_id: str | None = None) -> RunLogger:
    return RunLogger(component, run_id)
