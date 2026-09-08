from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from hubbleops.core.records import parse_json


@given(st.binary(max_size=2048))
def test_parse_json_is_total_over_arbitrary_bytes(payload: bytes) -> None:
    parsed = parse_json(payload)
    assert parsed.ok() == (parsed.reason is None)


@given(st.binary(max_size=2048), st.integers(min_value=0, max_value=4096))
def test_parse_json_never_exceeds_its_declared_bound(payload: bytes, maximum: int) -> None:
    parsed = parse_json(payload, maximum=maximum)
    if len(payload) > maximum:
        assert not parsed.ok()
        assert parsed.reason is not None
        assert parsed.reason.startswith("oversize:")


@given(st.binary(max_size=512))
def test_parse_json_is_deterministic(payload: bytes) -> None:
    first = parse_json(payload)
    second = parse_json(payload)
    assert first.reason == second.reason
    assert first.value == second.value
