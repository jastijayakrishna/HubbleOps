from __future__ import annotations

from dataclasses import replace

from hubbleops.core.canonical import content_id
from hubbleops.core.errors import PackDataError
from hubbleops.packs._protocol import ContractDiff


def compose(left: ContractDiff, right: ContractDiff) -> ContractDiff:
    hops = (left.hops or (left,)) + (right.hops or (right,))
    result = hops[0]
    for hop in hops[1:]:
        result = _combine(result, hop)
    return replace(result, hops=hops)


def _combine(left: ContractDiff, right: ContractDiff) -> ContractDiff:
    if left.to_version != right.from_version:
        raise PackDataError("diff composition requires consecutive boundaries")
    result = {fact.subject: fact for fact in left.facts}
    dependents: dict[str, set[str]] = {}
    for fact in left.facts:
        if fact.replacement is not None:
            dependents.setdefault(fact.replacement, set()).add(fact.subject)
    for incoming in right.facts:
        previous = result.get(incoming.subject)
        if previous is None:
            result[incoming.subject] = incoming
        else:
            conflict = (
                previous.result == "UNKNOWN_PROVIDER_CONTRACT"
                or incoming.result == "UNKNOWN_PROVIDER_CONTRACT"
                or previous.after != incoming.before
                or (previous.after is None and incoming.after is not None)
                or (
                    previous.replacement is not None
                    and incoming.replacement is not None
                    and previous.replacement != incoming.replacement
                )
            )
            result[incoming.subject] = replace(
                previous,
                after=incoming.after,
                change="ADDED"
                if previous.before is None
                else "REMOVED"
                if incoming.after is None
                else "CHANGED",
                replacement=incoming.replacement or previous.replacement,
                result="UNKNOWN_PROVIDER_CONTRACT" if conflict else "VALID",
                confidence="PROVEN"
                if not conflict and previous.confidence == incoming.confidence == "PROVEN"
                else "DOCUMENTED",
                reason="consecutive mapping conflicts"
                if conflict
                else "composed consecutive mapping",
            )
        for subject in tuple(dependents.get(incoming.subject, ())):
            mapped = result[subject]
            if subject == incoming.subject or mapped.replacement != incoming.subject:
                continue
            conflict = incoming.result == "UNKNOWN_PROVIDER_CONTRACT" or (
                incoming.after is None and incoming.replacement is None
            )
            replacement = incoming.replacement or mapped.replacement
            conflict = (
                conflict or replacement == subject or mapped.result == "UNKNOWN_PROVIDER_CONTRACT"
            )
            result[subject] = replace(
                mapped,
                replacement=replacement,
                result="UNKNOWN_PROVIDER_CONTRACT" if conflict else mapped.result,
                confidence="DOCUMENTED"
                if conflict or incoming.confidence == "DOCUMENTED"
                else mapped.confidence,
                reason="replacement chain conflicts"
                if conflict
                else "composed consecutive mapping",
            )
            if replacement is not None:
                dependents.setdefault(replacement, set()).add(subject)
        if incoming.replacement is not None:
            dependents.setdefault(incoming.replacement, set()).add(incoming.subject)
    facts = tuple(
        replace(
            fact,
            reason="consecutive mapping conflicts"
            if fact.result == "UNKNOWN_PROVIDER_CONTRACT"
            else "composed consecutive mapping",
        )
        for _, fact in sorted(result.items())
        if fact.before != fact.after
        or fact.replacement is not None
        or fact.result == "UNKNOWN_PROVIDER_CONTRACT"
    )
    return ContractDiff(
        left.from_version, right.to_version, content_id([left.pair_hash, right.pair_hash]), facts
    )
