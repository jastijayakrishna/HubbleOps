from __future__ import annotations

from itertools import pairwise

import pytest

from hubbleops.packs.google_ads.contract import CONTRACT

VERSIONS = ("v19", "v20", "v21", "v22", "v23", "v24", "v25")
ADJACENT = tuple(pairwise(VERSIONS))
NAMED_FIELDS = ("customer.id", "metrics.clicks", "segments.date")


@pytest.mark.parametrize(("source", "target"), ADJACENT)
def test_no_adjacent_change_reports_an_identical_before_and_after(source: str, target: str) -> None:
    for fact in CONTRACT.diff(source, target).facts:
        assert fact.before != fact.after or fact.replacement is not None, (
            f"{source}->{target} reports {fact.subject} as {fact.change} "
            f"with an identical before and after"
        )
        if fact.change == "CHANGED":
            assert fact.before is not None and fact.after is not None
        assert (fact.change == "ADDED") == (fact.before is None)
        assert (fact.change == "REMOVED") == (fact.after is None)


@pytest.mark.parametrize(("source", "target"), ADJACENT)
def test_an_unknown_adjacent_fact_always_names_an_unresolved_side(source: str, target: str) -> None:
    before = {fact.subject: fact for fact in CONTRACT.catalog(source).facts}
    after = {fact.subject: fact for fact in CONTRACT.catalog(target).facts}
    for fact in CONTRACT.diff(source, target).facts:
        if fact.result != "UNKNOWN_PROVIDER_CONTRACT":
            continue
        sides = [item for item in (before.get(fact.subject), after.get(fact.subject)) if item]
        assert any(item.resolution != "RESOLVED" for item in sides) or fact.replacement, (
            f"{source}->{target} calls {fact.subject} conflicting with nothing in conflict"
        )


def test_the_v23_to_v24_diff_reports_few_unknown_changes() -> None:
    facts = [
        fact
        for fact in CONTRACT.diff("v23", "v24").facts
        if fact.change == "CHANGED" and fact.result == "UNKNOWN_PROVIDER_CONTRACT"
    ]
    assert len(facts) < 200, f"v23->v24 reports {len(facts)} unknown changed subjects"


def test_stable_fields_raise_nothing_a_migration_must_answer() -> None:
    facts = {fact.subject: fact for fact in CONTRACT.diff("v22", "v25").facts}
    for subject in NAMED_FIELDS:
        fact = facts.get(subject)
        if fact is None:
            continue
        assert fact.change == "CHANGED"
        assert fact.result == "VALID"
        assert fact.replacement is None
        assert fact.before is not None and fact.after is not None
        assert {
            key
            for key in set(fact.before) | set(fact.after)
            if fact.before.get(key) != fact.after.get(key)
        } == {"selectable_with"}


@pytest.mark.parametrize(("source", "target"), ADJACENT)
def test_a_documented_replacement_only_ever_retargets_a_removed_subject(
    source: str, target: str
) -> None:
    after = {fact.subject for fact in CONTRACT.catalog(target).facts}
    for fact in CONTRACT.diff(source, target).facts:
        if fact.replacement is None:
            continue
        assert fact.change == "REMOVED", f"{source}->{target} {fact.subject}"
        assert fact.after is None
        assert fact.replacement in after
        assert fact.replacement != fact.subject


def test_composition_keeps_the_documented_replacements_of_every_hop() -> None:
    hops = [
        fact
        for source, target in ADJACENT[ADJACENT.index(("v22", "v23")) :]
        for fact in CONTRACT.diff(source, target).facts
        if fact.replacement is not None
    ]
    composed = {
        fact.subject: fact
        for fact in CONTRACT.diff("v22", "v25").facts
        if fact.replacement is not None
    }
    assert len(composed) > 0
    assert {fact.subject for fact in hops} == set(composed)
    for fact in hops:
        assert composed[fact.subject].change == "REMOVED"
        assert composed[fact.subject].replacement == fact.replacement
