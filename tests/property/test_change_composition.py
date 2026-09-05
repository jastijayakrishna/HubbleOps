from dataclasses import replace

from hypothesis import given
from hypothesis import strategies as st

from hubbleops.packs._protocol import ContractDiff, DiffFact
from hubbleops.packs.google_ads.composition import compose
from hubbleops.packs.google_ads.contract import CONTRACT


def hop(source: int, before: int, after: int, unknown: bool = False) -> ContractDiff:
    return ContractDiff(
        f"v{source}",
        f"v{source + 1}",
        str(source),
        (
            DiffFact(
                "field",
                "CHANGED",
                {"n": before},
                {"n": after},
                None,
                "DOCUMENTED" if unknown else "PROVEN",
                "UNKNOWN_PROVIDER_CONTRACT" if unknown else "VALID",
                "computed",
            ),
        ),
    )


@given(st.lists(st.integers(), min_size=4, max_size=4, unique=True))
def test_surviving_mapping_composition_is_associative(values: list[int]) -> None:
    a, b, c, d = values
    first, second, third = hop(1, a, b), hop(2, b, c), hop(3, c, d)
    assert (
        compose(compose(first, second), third).facts == compose(first, compose(second, third)).facts
    )
    fact = compose(compose(first, second), third).facts[0]
    assert fact.before == {"n": a} and fact.after == {"n": d}


def test_intermediate_conflict_survives_identical_endpoints() -> None:
    result = compose(hop(1, 1, 2, True), hop(2, 2, 1))
    assert len(result.facts) == 1
    assert result.facts[0].result == "UNKNOWN_PROVIDER_CONTRACT"


def test_replacement_failure_propagates_to_original_subject() -> None:
    first = hop(1, 1, 2)
    original = replace(first.facts[0], subject="old", replacement="new", after=None)
    second = replace(
        hop(2, 2, 3, True), facts=(replace(hop(2, 2, 3, True).facts[0], subject="new"),)
    )
    result = compose(replace(first, facts=(original,)), second)
    assert (
        next(item for item in result.facts if item.subject == "old").result
        == "UNKNOWN_PROVIDER_CONTRACT"
    )


def test_real_three_plus_hop_composition_matches_each_split() -> None:
    expected = CONTRACT.diff("v19", "v25")
    for middle in range(20, 25):
        composed = compose(CONTRACT.diff("v19", f"v{middle}"), CONTRACT.diff(f"v{middle}", "v25"))
        assert [item.to_mapping() for item in composed.facts] == [
            item.to_mapping() for item in expected.facts
        ]
