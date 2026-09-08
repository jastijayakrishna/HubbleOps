from __future__ import annotations

import dataclasses

import pytest

from hubbleops.core.verification import VERDICTS, VerificationResult
from hubbleops.verify.verdict import decide

FLAGS = tuple(
    name
    for name, field in VerificationResult.__dataclass_fields__.items()
    if field.type in ("bool", bool) and name != "oracle_available"
)


def verified() -> VerificationResult:
    return VerificationResult(
        audit_pass=True,
        oracle_all_accepted=True,
        zero_unexplained_hunks=True,
        request_shape_differential_pass=True,
        response_consumer_check_pass=True,
        frozen_baseline_tests_pass=True,
        falsifiers_pass=True,
        unknown_conservation_pass=True,
        unknown_blast=(),
        oracle_available=True,
        unresolved=(),
        reasons=(),
    )


def test_the_clean_input_is_the_only_route_to_verified() -> None:
    assert decide(verified()).verdict == "VERIFIED_FOR_SCOPE"


def test_every_flag_reaches_the_verdict() -> None:
    for name in FLAGS:
        flipped = dataclasses.replace(verified(), **{name: False})
        assert decide(flipped).verdict != "VERIFIED_FOR_SCOPE", (
            f"{name} is computed but never consulted; a check wired to nothing is trap (5)"
        )
        assert decide(flipped).verdict == "FAILED"
        assert any(name in reason for reason in decide(flipped).reasons)


def test_the_flag_list_covers_every_boolean_the_result_carries() -> None:
    assert set(FLAGS) == set(verified().flags())
    assert len(FLAGS) == 8, "the frozen verdict rule names exactly eight conjuncts"


def test_unknown_blast_alone_is_the_only_route_to_human_required() -> None:
    judgement = decide(dataclasses.replace(verified(), unknown_blast=("billing", "reporting")))
    assert judgement.verdict == "HUMAN_REQUIRED"
    assert judgement.unknown_blast == ("billing", "reporting")
    assert any("billing" in reason for reason in judgement.reasons)


def test_an_unavailable_oracle_caps_the_verdict_at_unknown() -> None:
    judgement = decide(dataclasses.replace(verified(), oracle_available=False))
    assert judgement.verdict == "UNKNOWN"
    assert "ORACLE_UNAVAILABLE" in judgement.reasons


def test_an_unresolvable_input_is_unknown_not_verified() -> None:
    judgement = decide(dataclasses.replace(verified(), unresolved=("a request skeleton",)))
    assert judgement.verdict == "UNKNOWN"


def test_a_failure_outranks_an_absence() -> None:
    both = dataclasses.replace(
        verified(),
        falsifiers_pass=False,
        oracle_available=False,
        unresolved=("something",),
        unknown_blast=("reporting",),
    )
    assert decide(both).verdict == "FAILED"


def test_unknown_outranks_human_required() -> None:
    both = dataclasses.replace(verified(), oracle_available=False, unknown_blast=("reporting",))
    assert decide(both).verdict == "UNKNOWN"


@pytest.mark.parametrize("verdict", VERDICTS)
def test_never_safe(verdict: str) -> None:
    assert verdict != "SAFE"


def test_the_verdict_is_total_over_every_flag_combination() -> None:
    seen: set[str] = set()
    for mask in range(1 << len(FLAGS)):
        flags = {name: bool(mask & (1 << index)) for index, name in enumerate(FLAGS)}
        for available in (True, False):
            for blast in ((), ("reporting",)):
                judgement = decide(
                    dataclasses.replace(
                        verified(), **flags, oracle_available=available, unknown_blast=blast
                    )
                )
                assert judgement.verdict in VERDICTS
                seen.add(judgement.verdict)
    assert seen == set(VERDICTS)


def test_the_verdict_does_not_depend_on_input_order_or_time() -> None:
    left = dataclasses.replace(
        verified(), unknown_blast=("b", "a"), reasons=("second", "first"), audit_pass=False
    )
    right = dataclasses.replace(
        verified(), unknown_blast=("a", "b"), reasons=("first", "second"), audit_pass=False
    )
    assert decide(left) == decide(right)


def test_a_repeated_decision_is_identical() -> None:
    result = dataclasses.replace(verified(), unknown_blast=("reporting",))
    assert decide(result) == decide(result)
