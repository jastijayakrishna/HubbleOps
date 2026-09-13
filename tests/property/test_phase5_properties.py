from __future__ import annotations

import dataclasses

from hypothesis import given, settings
from hypothesis import strategies as st

from hubbleops.core.verification import VERDICTS, ChangeSet, SubjectChange, VerificationResult
from hubbleops.verify.gitdiff import parse_hunks
from hubbleops.verify.verdict import decide

FLAG_NAMES = (
    "audit_pass",
    "oracle_all_accepted",
    "zero_unexplained_hunks",
    "request_shape_differential_pass",
    "response_consumer_check_pass",
    "frozen_baseline_tests_pass",
    "falsifiers_pass",
    "unknown_conservation_pass",
)

modules = st.text(alphabet="abcdefghij/._", min_size=1, max_size=12)
reasons = st.text(alphabet="abcdefghij ", min_size=0, max_size=20)


@st.composite
def results(draw: st.DrawFn) -> VerificationResult:
    flags = {name: draw(st.booleans()) for name in FLAG_NAMES}
    return VerificationResult(
        **flags,
        unknown_blast=tuple(draw(st.lists(modules, max_size=4))),
        oracle_available=draw(st.booleans()),
        unresolved=tuple(draw(st.lists(reasons, max_size=3))),
        reasons=tuple(draw(st.lists(reasons, max_size=3))),
    )


@settings(max_examples=400, deadline=None)
@given(results())
def test_every_input_maps_to_exactly_one_verdict(result: VerificationResult) -> None:
    judgement = decide(result)
    assert judgement.verdict in VERDICTS
    assert decide(result).verdict == judgement.verdict


@settings(max_examples=400, deadline=None)
@given(results())
def test_the_verdict_is_a_pure_function_of_the_result(result: VerificationResult) -> None:
    assert decide(result) == decide(dataclasses.replace(result))


@settings(max_examples=300, deadline=None)
@given(results())
def test_a_verified_verdict_implies_every_conjunct(result: VerificationResult) -> None:
    if decide(result).verdict != "VERIFIED_FOR_SCOPE":
        return
    assert all(result.flags().values())
    assert result.unknown_blast == ()
    assert result.oracle_available is True
    assert result.unresolved == ()


@settings(max_examples=300, deadline=None)
@given(results(), st.sampled_from(FLAG_NAMES))
def test_flipping_any_flag_of_a_verified_input_never_yields_verified(
    result: VerificationResult, name: str
) -> None:
    if decide(result).verdict != "VERIFIED_FOR_SCOPE":
        return
    flipped = dataclasses.replace(result, **{name: False})
    assert decide(flipped).verdict != "VERIFIED_FOR_SCOPE"


@settings(max_examples=300, deadline=None)
@given(results())
def test_a_human_required_verdict_carries_the_module_list(result: VerificationResult) -> None:
    judgement = decide(result)
    if judgement.verdict != "HUMAN_REQUIRED":
        return
    assert judgement.unknown_blast
    assert all(
        any(module in reason for reason in judgement.reasons) for module in judgement.unknown_blast
    )


@settings(max_examples=300, deadline=None)
@given(results())
def test_a_failed_verdict_names_the_conjunct_that_failed(result: VerificationResult) -> None:
    judgement = decide(result)
    if judgement.verdict != "FAILED":
        return
    failing = [name for name, value in result.flags().items() if not value]
    assert failing
    assert all(any(name in reason for reason in judgement.reasons) for name in failing)


@settings(max_examples=200, deadline=None)
@given(results())
def test_the_verdict_never_says_safe(result: VerificationResult) -> None:
    assert decide(result).verdict != "SAFE"


@settings(max_examples=200, deadline=None)
@given(st.lists(st.tuples(st.integers(1, 500), st.integers(0, 20)), min_size=1, max_size=6))
def test_diff_parsing_is_total_over_generated_hunk_headers(
    spans: list[tuple[int, int]],
) -> None:
    lines = ["diff --git a/f.py b/f.py"]
    for start, length in spans:
        lines.append(f"@@ -{start},{length} +{start},{length} @@")
        lines.extend(f"+line {index}" for index in range(length))
    hunks = parse_hunks("\n".join(lines) + "\n")
    assert len(hunks) == len(spans)
    for hunk, (start, length) in zip(hunks, spans, strict=True):
        assert hunk.path == "f.py"
        assert (hunk.new_start, hunk.new_lines) == (start, length)
        assert hunk.spans(start) is True
        assert hunk.spans(start + max(length, 1)) is False


@settings(max_examples=200, deadline=None)
@given(st.text(max_size=400))
def test_diff_parsing_never_raises_on_arbitrary_text(payload: str) -> None:
    assert isinstance(parse_hunks(payload), tuple)


@settings(max_examples=200, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.text(alphabet="abc.", min_size=1, max_size=8),
            st.sampled_from(["ADDED", "REMOVED", "CHANGED"]),
            st.one_of(st.none(), st.text(alphabet="xyz.", min_size=1, max_size=8)),
        ),
        max_size=8,
    )
)
def test_a_change_set_partitions_by_kind(raw: list[tuple[str, str, str | None]]) -> None:
    changes = ChangeSet(
        from_version="v1",
        to_version="v2",
        pair_hash="h",
        changes=tuple(
            SubjectChange(subject, kind, replacement, "VALID", "")  # type: ignore[arg-type]
            for subject, kind, replacement in raw
        ),
    )
    assert set(changes.removed()) <= set(changes.changes)
    assert all(item.change == "REMOVED" for item in changes.removed())
    assert all(item.replacement is not None for item in changes.renamed())
