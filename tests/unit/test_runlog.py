from __future__ import annotations

import io
import json

import pytest

from hubbleops.core import runlog


def test_logging_is_silent_unless_the_operator_asks_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(runlog.LEVEL_VARIABLE, raising=False)
    stream = io.StringIO()
    assert runlog.configure(stream) is False
    runlog.logger("scan", "a" * 64).event("observers")
    assert stream.getvalue() == ""


def test_enabled_logging_emits_one_json_object_per_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(runlog.LEVEL_VARIABLE, "info")
    stream = io.StringIO()
    assert runlog.configure(stream) is True
    runlog.logger("scan", "a" * 64).event("observers", records=12)
    lines = [line for line in stream.getvalue().splitlines() if line]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["component"] == "scan"
    assert record["event"] == "observers"
    assert record["run_id"] == "a" * 64
    assert record["records"] == 12


def test_a_stage_records_duration_and_a_successful_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(runlog.LEVEL_VARIABLE, "info")
    stream = io.StringIO()
    runlog.configure(stream)
    log = runlog.logger("scan", "b" * 64)
    with log.stage("source_closure") as fields:
        fields["entries"] = 3
    record = json.loads(stream.getvalue().splitlines()[0])
    assert record["outcome"] == "OK"
    assert record["entries"] == 3
    assert isinstance(record["duration_ms"], float)


def test_a_failing_stage_records_the_failure_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(runlog.LEVEL_VARIABLE, "info")
    stream = io.StringIO()
    runlog.configure(stream)
    log = runlog.logger("scan", "c" * 64)
    with pytest.raises(ValueError):
        with log.stage("observers"):
            raise ValueError("observer exploded")
    record = json.loads(stream.getvalue().splitlines()[0])
    assert record["outcome"] == "FAILED"
    assert record["level"] == "ERROR"
    assert "observer exploded" in record["error"]


def test_field_order_puts_the_correlation_keys_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(runlog.LEVEL_VARIABLE, "info")
    stream = io.StringIO()
    runlog.configure(stream)
    log = runlog.logger("obligations", "d" * 64)
    with log.stage("resolve", candidate_id="cand", obligation_id="obl"):
        pass
    keys = list(json.loads(stream.getvalue().splitlines()[0]))
    assert keys[:8] == [
        "level",
        "component",
        "event",
        "run_id",
        "candidate_id",
        "obligation_id",
        "duration_ms",
        "outcome",
    ]
