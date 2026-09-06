from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from hubbleops.app.registry import load_pack
from hubbleops.core.candidate import claim_key
from hubbleops.core.errors import UnknownClaimType
from hubbleops.core.observer import ObserverContext
from hubbleops.observe.ai_triage import QUESTION, triage
from tests.unit.test_imports import imported_modules


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, str]]] = []

    def ask(self, question: str, files: Mapping[str, str]) -> Mapping[str, Any]:
        self.calls.append((question, files))
        return {"residue": "possible wrapper"}


def context() -> ObserverContext:
    return ObserverContext(
        provider="_mock",
        run_id="0" * 64,
        proof_scope_hash="1" * 64,
        repo_sha=None,
        dependency_context_hash="2" * 64,
        surface=load_pack("_mock").surface,
    )


def candidate(status: str = "UNKNOWN") -> dict[str, str]:
    return {"id": "3" * 64, "status": status}


def test_default_off_gate_never_calls_client() -> None:
    client = FakeClient()
    assert triage(candidate(), {"a.py": b"source"}, context(), client) == ()
    assert client.calls == []


def test_enabled_gate_asks_once_reads_at_most_two_files_and_only_derives_evidence() -> None:
    client = FakeClient()
    evidence = triage(
        candidate(),
        {"c.py": b"c", "a.py": b"a", "b.py": b"b"},
        context(),
        client,
        enabled=True,
    )
    assert len(client.calls) == 1
    assert client.calls[0][0] == QUESTION
    assert tuple(client.calls[0][1]) == ("a.py", "b.py")
    assert len(evidence) == 1
    assert evidence[0]["derivation"] == "DERIVED_AI_EVIDENCE"
    assert evidence[0]["value"]["candidate_id"] == candidate()["id"]
    assert "status" not in evidence[0]["value"]
    with pytest.raises(UnknownClaimType):
        claim_key(evidence[0])


def test_cli_has_no_ai_triage_import_or_route() -> None:
    modules = imported_modules(Path("hubbleops/app/cli.py"))
    assert "hubbleops.observe.ai_triage" not in modules


def test_closed_candidate_cannot_reach_client() -> None:
    client = FakeClient()
    assert (
        triage(
            candidate("AFFECTED"),
            {"a.py": b"source"},
            context(),
            client,
            enabled=True,
        )
        == ()
    )
    assert client.calls == []
