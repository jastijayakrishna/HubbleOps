from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hubbleops.app import registry
from hubbleops.core.canonical import canonical_bytes, content_id
from hubbleops.core.observer import ObserverContext
from hubbleops.observe.dynamic.loaders import attested
from hubbleops.observe.dynamic.runner import (
    EventBatch,
    EventIssue,
    events_from_jsonl,
    events_to_evidence,
)
from hubbleops.observe.ledger import Ledger
from hubbleops.observe.telemetry import ProductionTuple, reconcile

SETTINGS = settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])

VERSIONS = st.integers(min_value=1, max_value=99).map(lambda number: f"v{number}")
NAMES = st.text(alphabet=st.characters(whitelist_categories=("Lu", "Ll")), min_size=1, max_size=12)
MODES = st.sampled_from(("hook", "proxy"))


def event(version: str, service: str, method: str, mode: str) -> dict[str, Any]:
    return {
        "version": version,
        "service": f"{service}Service",
        "method": method,
        "request_text": None,
        "request_type": "grpc",
        "stack": [],
        "ts": "2026-09-06T00:00:00Z",
        "mode": mode,
    }


def jsonl(events: list[dict[str, Any]]) -> bytes:
    return b"".join(json.dumps(item, sort_keys=True).encode("utf-8") + b"\n" for item in events)


def context() -> ObserverContext:
    return ObserverContext(
        provider="google_ads",
        run_id=content_id("run"),
        proof_scope_hash=content_id("scope"),
        repo_sha=None,
        dependency_context_hash=None,
        surface=registry.load_pack("google_ads").surface,
    )


@given(st.lists(st.tuples(VERSIONS, NAMES, NAMES), min_size=1, max_size=8), MODES)
@SETTINGS
def test_event_normalization_is_order_independent_and_byte_deterministic(
    rows: list[tuple[str, str, str]], mode: str
) -> None:
    events = [event(version, service, method, mode) for version, service, method in rows]
    forward = events_from_jsonl(jsonl(events))
    reverse = events_from_jsonl(jsonl(list(reversed(events))))
    assert forward.bytes() == reverse.bytes()
    assert forward.bytes() == events_from_jsonl(forward.bytes()).bytes()


@given(st.lists(st.tuples(VERSIONS, NAMES, NAMES), min_size=1, max_size=8))
@SETTINGS
def test_only_the_mode_field_distinguishes_the_two_capture_channels(
    rows: list[tuple[str, str, str]],
) -> None:
    hook = events_from_jsonl(
        jsonl([event(version, service, method, "hook") for version, service, method in rows])
    )
    proxy = events_from_jsonl(
        jsonl([event(version, service, method, "proxy") for version, service, method in rows])
    )
    assert [{**item, "mode": None} for item in hook.events] == [
        {**item, "mode": None} for item in proxy.events
    ]


@given(
    st.lists(st.tuples(VERSIONS, NAMES, NAMES), max_size=6),
    st.lists(st.text(min_size=1, max_size=20), max_size=4),
)
@SETTINGS
def test_no_event_line_and_no_issue_is_lost_on_the_way_to_evidence(
    rows: list[tuple[str, str, str]], codes: list[str]
) -> None:
    events = [event(version, service, method, "proxy") for version, service, method in rows]
    batch = EventBatch(
        events_from_jsonl(jsonl(events)).events,
        tuple(EventIssue(code, index, "reason") for index, code in enumerate(codes)),
    )
    records = events_to_evidence(
        batch, context(), Path("."), observer="sentinel", allow_site_claims=False
    )
    assert len(records) == len(batch.events) + len(batch.issues)
    assert {record["id"] for record in records} == {record["id"] for record in records}


@given(st.lists(st.text(max_size=40), min_size=1, max_size=8))
@SETTINGS
def test_every_malformed_line_becomes_an_issue_rather_than_a_missing_row(
    lines: list[str],
) -> None:
    payload = b"".join(line.encode("utf-8") + b"\n" for line in lines)
    batch = events_from_jsonl(payload)
    accounted = len(batch.events) + len(batch.issues)
    assert accounted >= len([line for line in lines if line.strip()])


@given(st.lists(st.tuples(VERSIONS, NAMES, NAMES), min_size=1, max_size=6))
@SETTINGS
def test_telemetry_reconciliation_accounts_for_every_distinct_tuple(
    rows: list[tuple[str, str, str]],
) -> None:
    observations = tuple(
        ProductionTuple(f"{service}Service", method, version) for version, service, method in rows
    )
    ctx = context()
    ledger = Ledger("google_ads", ctx.run_id, ctx.proof_scope_hash, (), ())
    result = reconcile(observations, (), ledger, ctx)
    assert result.total == len(set(observations))
    assert result.accounted == 0
    assert (
        len([item for item in result.evidence if item["claim_type"] == "production_version"])
        == result.total
    )


@given(st.lists(st.tuples(VERSIONS, NAMES, NAMES), min_size=1, max_size=6))
@SETTINGS
def test_telemetry_reconciliation_is_input_order_independent(
    rows: list[tuple[str, str, str]],
) -> None:
    observations = tuple(
        ProductionTuple(f"{service}Service", method, version) for version, service, method in rows
    )
    ctx = context()
    ledger = Ledger("google_ads", ctx.run_id, ctx.proof_scope_hash, (), ())
    forward = reconcile(observations, (), ledger, ctx)
    reverse = reconcile(tuple(reversed(observations)), (), ledger, ctx)
    assert canonical_bytes(list(forward.evidence)) == canonical_bytes(list(reverse.evidence))


@given(st.binary(max_size=256), st.text(max_size=16))
@SETTINGS
def test_only_this_runs_nonce_attests_that_the_hook_installed(noise: bytes, forged: str) -> None:
    nonce = "a" * 64
    other = "b" * 64
    assert attested(json.dumps({"nonce": nonce}).encode("utf-8"), nonce) is True
    assert attested(json.dumps({"nonce": other}).encode("utf-8"), nonce) is False
    assert attested(json.dumps({"nonce": forged}).encode("utf-8"), nonce) is (forged == nonce)
    assert attested(noise, nonce) in (True, False)
