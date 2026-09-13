from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from hubbleops.core.canonical import content_id
from hubbleops.core.evidence import make_evidence
from hubbleops.core.verification import (
    OBLIGATION_METHODS,
    ChangeSet,
    ObligationView,
    SubjectChange,
    method,
    parse_method,
)
from hubbleops.observe import Ledger
from hubbleops.verify import audit

ENGINE = Path("hubbleops/obligations/engine.py")
RUN = content_id({"run": "method-grammar"})
SCOPE = content_id({"scope": "method-grammar"})


def _ledger() -> Ledger:
    record = make_evidence(
        run_id=RUN,
        proof_scope_hash=SCOPE,
        claim_type="call_version",
        observer="text",
        repo_sha=None,
        path="src/report.py",
        line_start=3,
        line_end=3,
        source_hash="b" * 64,
        value={"line": "client = Client(version='v25')"},
        provider_subject="v25",
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="RAW",
    )
    candidate: dict[str, Any] = {
        "id": content_id({"candidate": "one"}),
        "provider": "mock",
        "claim_type": "call_version",
        "claim_key": "src/report.py:3",
        "status": "AFFECTED",
        "reason": "a version literal resolves here",
        "close_with": None,
        "evidence_ids": [record["id"]],
        "run_id": RUN,
        "proof_scope_hash": SCOPE,
    }
    return Ledger(
        provider="mock",
        run_id=RUN,
        proof_scope_hash=SCOPE,
        evidence=(record,),
        candidates=(candidate,),
    )


def _changes() -> ChangeSet:
    return ChangeSet(
        from_version="v22",
        to_version="v25",
        pair_hash="c" * 64,
        changes=(
            SubjectChange(
                subject="campaign.legacy",
                change="REMOVED",
                replacement=None,
                kind="PROVEN",
                reason="removed in v25",
            ),
        ),
    )


def _argument_for(kind: str, ledger: Ledger) -> str:
    if kind == "conserved":
        return str(ledger.candidates[0]["id"])
    if kind == "supports":
        return "v25"
    if kind == "version":
        return "v25"
    return "campaign.legacy"


def _obligation(kind: str, ledger: Ledger) -> ObligationView:
    return ObligationView(
        id=content_id({"obligation": kind}),
        provider_change_id=f"probe:{kind}",
        evidence_ids=(str(ledger.evidence[0]["id"]),),
        current_state="src/report.py:3 is in its pre-migration state",
        required_state="src/report.py:3 is in its post-migration state",
        repair_class="DETERMINISTIC",
        verification_method=method(kind, _argument_for(kind, ledger)),
        status="OPEN",
    )


@pytest.mark.parametrize("kind", OBLIGATION_METHODS)
def test_every_grammar_member_round_trips(kind: str) -> None:
    parsed = parse_method(method(kind, "campaign.legacy"))
    assert parsed is not None
    assert parsed.kind == kind
    assert parsed.argument == "campaign.legacy"
    assert parsed.intent()


@pytest.mark.parametrize("kind", OBLIGATION_METHODS)
def test_the_audit_executes_every_method_the_grammar_admits(kind: str) -> None:
    ledger = _ledger()
    result = audit.run(
        ledger,
        _changes(),
        [_obligation(kind, ledger)],
        supported_targets=("v25",),
    )
    statuses = {item.status for item in result.reconciliations}
    assert audit.UNRECONCILABLE_STATUS not in statuses, (
        f"the {kind} method the engine may emit is not one the audit executes"
    )


def test_a_method_outside_the_grammar_is_refused_at_construction() -> None:
    with pytest.raises(ValueError):
        method("rescan", "campaign.legacy")
    with pytest.raises(ValueError):
        method("absent", "campaign:legacy")
    with pytest.raises(ValueError):
        method("absent", "")


def test_prose_is_not_a_method() -> None:
    assert parse_method("migration audit finds no reference to the retired version") is None
    assert parse_method("absent:") is None


def test_the_engine_never_writes_a_verification_method_as_a_literal() -> None:
    tree = ast.parse(ENGINE.read_text(encoding="utf-8"))
    literals = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.keyword)
        and node.arg == "verification_method"
        and not isinstance(node.value, ast.Call)
    ]
    assert literals == [], (
        "every verification_method the engine emits must be built by the shared grammar "
        f"constructor, not written as a literal; offending lines: {literals}"
    )
