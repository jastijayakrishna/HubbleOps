from __future__ import annotations

from hubbleops.core.records import MAX_JSON_BYTES, parse_json


def test_valid_json_parses_and_reports_no_reason() -> None:
    parsed = parse_json(b'{"a": 1}')
    assert parsed.ok()
    assert parsed.reason is None
    assert parsed.value == {"a": 1}


def test_json_null_is_a_value_not_a_failure() -> None:
    parsed = parse_json(b"null")
    assert parsed.ok()
    assert parsed.value is None


def test_non_utf8_bytes_become_a_named_reason() -> None:
    parsed = parse_json(b'{"a": "\xff\xfe"}')
    assert not parsed.ok()
    assert parsed.reason is not None
    assert parsed.reason.startswith("not_utf8:")


def test_malformed_json_becomes_a_named_reason() -> None:
    parsed = parse_json(b"{not json")
    assert not parsed.ok()
    assert parsed.reason is not None
    assert parsed.reason.startswith("invalid_json:")


def test_oversize_input_is_refused_before_decoding() -> None:
    parsed = parse_json(b"0" * 32, maximum=8)
    assert not parsed.ok()
    assert parsed.reason is not None
    assert parsed.reason.startswith("oversize:")


def test_deep_nesting_is_a_named_reason_and_never_a_crash() -> None:
    payload = b"[" * 200_000 + b"]" * 200_000
    parsed = parse_json(payload, maximum=len(payload))
    assert not parsed.ok()
    assert parsed.reason is not None
    assert parsed.reason.startswith("invalid_json:")


def test_the_default_bound_is_declared() -> None:
    assert MAX_JSON_BYTES == 16_777_216
