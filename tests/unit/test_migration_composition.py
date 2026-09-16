from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from hubbleops.app import migration, registry
from hubbleops.closure import source_closure
from hubbleops.core.candidate import candidate_identity, make_candidate
from hubbleops.core.evidence import make_evidence
from hubbleops.core.verification import OracleOutcome
from hubbleops.obligations import ObligationInputs, build
from hubbleops.observe.ledger import Ledger
from hubbleops.packs._mock import MockContract, MockPack
from hubbleops.packs._protocol import ContractDiff
from hubbleops.repair import deterministic

SCOPE = "0" * 64
RUN = "1" * 64

CLIENT_SOURCE = """API_VERSION = "v1"
RETRYABLE_ERRORS = ("MockProvError",)


class MockProvClient:
    def __init__(self, token, version):
        self.token = token
        self.version = version


def build_client(token):
    return MockProvClient(token, version=API_VERSION)


def search_client(token):
    return MockProvClient(token, version=API_VERSION)
"""


class StubOracle:
    def validate(self, request: Mapping[str, Any], version: str) -> OracleOutcome:
        return OracleOutcome(code="VALID", reason="stub")


class ExplodingContract(MockContract):
    def diff(self, version_from: str, version_to: str) -> ContractDiff:
        raise RuntimeError("the catalog store is unreachable")


class ExplodingPack(MockPack):
    contract = ExplodingContract()


def mock_pack() -> registry.LoadedPack:
    return registry.load_pack("_mock")


def test_a_version_below_the_floor_is_recorded_with_its_reason_not_dropped() -> None:
    composition = migration.change_sets_for(mock_pack(), ["v2", "v0", "v1"], "v2")
    assert sorted(composition.sets) == ["v1"]
    assert composition.uncomposable == {"v0": "v0 is below this pack's lattice floor (v1)"}


def test_a_lattice_version_the_pack_refuses_to_compose_carries_the_packs_reason() -> None:
    composition = migration.change_sets_for(mock_pack(), ["v2"], "v1")
    assert composition.sets == {}
    assert composition.uncomposable == {
        "v2": "the pack composes no diff from v2 to v1: mock diffs support only v1 to v2"
    }


def test_a_version_not_shaped_like_the_lattice_carries_the_packs_reason_not_a_floor_claim() -> None:
    composition = migration.change_sets_for(mock_pack(), ["30.1.0", "V0"], "v2")
    assert composition.uncomposable == {
        "30.1.0": "the pack composes no diff from 30.1.0 to v2: mock diffs support only v1 to v2",
        "V0": "V0 is below this pack's lattice floor (v1)",
    }


def test_a_failure_that_is_not_the_packs_typed_error_propagates() -> None:
    loaded = replace(mock_pack(), implementation=ExplodingPack())
    with pytest.raises(RuntimeError, match="catalog store is unreachable"):
        migration.change_sets_for(loaded, ["v1"], "v2")


def test_composition_is_deterministic_across_input_order() -> None:
    first = migration.change_sets_for(mock_pack(), ["v0", "v1"], "v2")
    second = migration.change_sets_for(mock_pack(), ["v1", "v0"], "v2")
    assert first == second


def test_a_package_pin_is_never_detected_as_a_version_the_provider_runs() -> None:
    ledger = Ledger(
        provider="_mock",
        run_id=RUN,
        proof_scope_hash=SCOPE,
        evidence=(pinned_package("sdk_installed"), pinned_package("dependency_state"), reader(12)),
        candidates=(),
    )
    assert migration.detected_versions(ledger) == ("v1",)


def structural(path: str, line: int, claim_type: str, value: dict[str, Any], subject: str | None):
    return make_evidence(
        run_id=RUN,
        proof_scope_hash=SCOPE,
        claim_type=claim_type,
        observer="structure",
        repo_sha=None,
        path=path,
        line_start=line,
        line_end=line,
        source_hash="a" * 64,
        value=value,
        provider_subject=subject,
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="PROVEN",
    )


def pinned_package(claim_type: str) -> dict[str, Any]:
    return make_evidence(
        run_id=RUN,
        proof_scope_hash=SCOPE,
        claim_type=claim_type,
        observer="deps",
        repo_sha=None,
        path="setup.py",
        line_start=None,
        line_end=None,
        source_hash="a" * 64,
        value={
            "state": "PRESENT",
            "ecosystem": "python",
            "package": "mockprov-client",
            "version": "30.1.0",
            "spec": "==30.1.0",
            "source_kind": "manifest",
        },
        provider_subject="mockprov-client",
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="DOCUMENTED",
    )


def reader(line: int) -> dict[str, Any]:
    return structural(
        "src/client.py",
        line,
        "call_version",
        {
            "literal": "v1",
            "paths": [
                {
                    "terminal": "LITERAL",
                    "literal": "v1",
                    "path": "src/client.py",
                    "range": {"start_line": 1, "end_line": 1},
                }
            ],
        },
        "v1",
    )


def reference(line: int, subject: str) -> dict[str, Any]:
    return structural(
        "src/client.py",
        line,
        "surface_reference",
        {"node_kind": "identifier", "binding": subject},
        subject,
    )


def affected(key: str, claim_type: str, record: Mapping[str, Any]) -> dict[str, Any]:
    return make_candidate(
        candidate_id=candidate_identity("_mock", claim_type, key),
        run_id=RUN,
        proof_scope_hash=SCOPE,
        provider="_mock",
        evidence_ids=[str(record["id"])],
        status="AFFECTED",
        reason="fixture",
        close_with=None,
    )


def constant_reader_ledger() -> Ledger:
    records = {
        "src/client.py:2:MockProvError": reference(2, "MockProvError"),
        "src/client.py:5:MockProvClient": reference(5, "MockProvClient"),
        "src/client.py:12": reader(12),
        "src/client.py:16": reader(16),
    }
    return Ledger(
        provider="_mock",
        run_id=RUN,
        proof_scope_hash=SCOPE,
        evidence=tuple(records.values()),
        candidates=tuple(
            affected(key, str(record["claim_type"]), record) for key, record in records.items()
        ),
    )


def test_readers_of_a_rewritten_constant_are_discharged_as_satisfied_by_that_site(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "client.py").write_text(CLIENT_SOURCE, encoding="utf-8", newline="")
    pack = mock_pack()
    ledger = constant_reader_ledger()
    composition = migration.change_sets_for(pack, migration.detected_versions(ledger), "v2")
    assert composition.uncomposable == {}
    obligations = build(
        ObligationInputs(
            ledger=ledger,
            change_sets=composition.sets,
            oracle=StubOracle(),
            target="v2",
            uncomposable=composition.uncomposable,
        )
    )
    assert [item["repair_class"] for item in obligations] == ["DETERMINISTIC"] * 4
    assert all(
        item["current_state"].endswith("the v1 literal is written at src/client.py:1")
        for item in obligations
    )
    requests = migration.transform_requests(
        obligations=obligations,
        ledger=ledger,
        change_sets=composition.sets,
        root=tmp_path,
        target="v2",
    )
    assert {(item.path, item.line) for item in requests} == {("src/client.py", 1)}
    report = deterministic.run(transforms=pack.repair_transforms(), requests=requests)
    result = migration.MigrationResult(
        obligations=obligations, report=report, target="v2", written=()
    )
    assert sorted(item.result for item in report.outcomes) == [
        "APPLIED",
        "SATISFIED_BY",
        "SATISFIED_BY",
        "SATISFIED_BY",
    ]
    for outcome in report.outcomes:
        if outcome.result == "SATISFIED_BY":
            assert outcome.reason.startswith(
                "src/client.py:1 was rewritten in this run by mock-version-literal"
            )
    assert set(report.discharged()) == {item["id"] for item in obligations}
    assert result.open_for_human() == ()
    assert report.texts["src/client.py"].startswith('API_VERSION = "v2"\n')


def test_a_crlf_file_keeps_crlf_on_every_line_the_migration_did_not_edit(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "client.py"
    source.write_bytes(CLIENT_SOURCE.replace("\n", "\r\n").encode("utf-8"))
    before = source.read_bytes()

    result = migration.migrate(
        pack=mock_pack(),
        ledger=constant_reader_ledger(),
        closure=source_closure.build(tmp_path),
        root=tmp_path,
        target="v2",
        write=True,
    )

    assert result.written == ("src/client.py",)
    after = source.read_bytes()
    assert after.count(b"\r\n") == before.count(b"\r\n"), (
        "a migration rewrites the sites its obligations name and nothing else; rewriting every "
        "line terminator makes every line of the file a hunk no obligation contains"
    )
    assert after.split(b"\r\n")[0] == b'API_VERSION = "v2"'
    assert after.split(b"\r\n")[1:] == before.split(b"\r\n")[1:]
