from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hubbleops.core.candidate import candidate_identity, make_candidate
from hubbleops.core.evidence import make_evidence
from hubbleops.core.verification import ABSENT, ChangeSet, OracleOutcome, SubjectChange, method
from hubbleops.obligations import ObligationInputs, build
from hubbleops.observe.ledger import Ledger

SCOPE = "0" * 64
RUN = "1" * 64


class StubOracle:
    def validate(self, request: Mapping[str, Any], version: str) -> OracleOutcome:
        return OracleOutcome(code="VALID", reason="stub")


def evidence(
    path: str, claim_type: str, value: Any, provider_subject: str | None = None
) -> dict[str, Any]:
    return make_evidence(
        run_id=RUN,
        proof_scope_hash=SCOPE,
        claim_type=claim_type,
        observer="text",
        repo_sha=None,
        path=path,
        line_start=7,
        line_end=7,
        source_hash="a" * 64,
        value=value,
        provider_subject=provider_subject,
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="RAW",
    )


def ledger_of(records: list[dict[str, Any]], statuses: dict[str, str]) -> Ledger:
    candidates: list[dict[str, Any]] = []
    for key, status in sorted(statuses.items()):
        attached = [item["id"] for item in records if item["path"] == key]
        candidates.append(
            make_candidate(
                candidate_id=candidate_identity("p", "surface_reference", key),
                run_id=RUN,
                proof_scope_hash=SCOPE,
                provider="p",
                evidence_ids=attached,
                status=status,
                reason="fixture",
                close_with=(
                    "attach a production observation"
                    if status in ("UNKNOWN", "HUMAN_REQUIRED")
                    else None
                ),
            )
        )
    return Ledger(
        provider="p",
        run_id=RUN,
        proof_scope_hash=SCOPE,
        evidence=tuple(records),
        candidates=tuple(candidates),
    )


def change_set(from_version: str, to_version: str, *changes: SubjectChange) -> ChangeSet:
    return ChangeSet(
        from_version=from_version,
        to_version=to_version,
        pair_hash="b" * 64,
        changes=tuple(changes),
    )


def inputs_for(ledger: Ledger, sets: dict[str, ChangeSet], target: str) -> ObligationInputs:
    return ObligationInputs(ledger=ledger, change_sets=sets, oracle=StubOracle(), target=target)


def test_an_affected_call_site_earns_a_deterministic_version_obligation() -> None:
    record = evidence("src/client.py", "call_version", {"literal": "v22"}, "v22")
    ledger = ledger_of([record], {"src/client.py": "AFFECTED"})
    built = build(inputs_for(ledger, {"v22": change_set("v22", "v25")}, "v25"))
    assert len(built) == 1
    assert built[0]["repair_class"] == "DETERMINISTIC"
    assert built[0]["provider_change_id"] == "version:v22->v25"
    assert "v25" in built[0]["required_state"]


def test_a_removed_subject_without_a_replacement_is_human_not_guessed() -> None:
    records = [
        evidence("src/report.py", "call_version", {"literal": "v22"}, "v22"),
        evidence("src/report.py", "request_text", "SELECT campaign.legacy FROM campaign"),
    ]
    ledger = ledger_of(records, {"src/report.py": "AFFECTED"})
    removed = SubjectChange(
        subject="campaign.legacy",
        change="REMOVED",
        replacement=None,
        kind="PROVEN",
        reason="retired",
    )
    built = build(inputs_for(ledger, {"v22": change_set("v22", "v25", removed)}, "v25"))
    classes = {item["repair_class"] for item in built}
    assert "HUMAN" in classes
    human = next(item for item in built if item["repair_class"] == "HUMAN")
    assert "no announced replacement" in human["required_state"]


def test_a_renamed_subject_is_deterministic_because_the_diff_names_the_replacement() -> None:
    records = [
        evidence("src/report.py", "call_version", {"literal": "v22"}, "v22"),
        evidence("src/report.py", "request_text", "SELECT campaign.old FROM campaign"),
    ]
    ledger = ledger_of(records, {"src/report.py": "AFFECTED"})
    renamed = SubjectChange(
        subject="campaign.old",
        change="REMOVED",
        replacement="campaign.new",
        kind="PROVEN",
        reason="renamed",
    )
    built = build(inputs_for(ledger, {"v22": change_set("v22", "v25", renamed)}, "v25"))
    rename = next(item for item in built if item["provider_change_id"] == "subject:campaign.old")
    assert rename["repair_class"] == "DETERMINISTIC"
    assert "campaign.new" in rename["required_state"]


def test_a_subject_the_diff_cannot_map_is_preserved_never_forced_closed() -> None:
    records = [
        evidence("src/report.py", "call_version", {"literal": "v22"}, "v22"),
        evidence("src/report.py", "request_text", "SELECT campaign.murky FROM campaign"),
    ]
    ledger = ledger_of(records, {"src/report.py": "AFFECTED"})
    murky = SubjectChange(
        subject="campaign.murky",
        change="CHANGED",
        replacement=None,
        kind="UNKNOWN_PROVIDER_CONTRACT",
        reason="sources disagree",
    )
    built = build(inputs_for(ledger, {"v22": change_set("v22", "v25", murky)}, "v25"))
    entry = next(item for item in built if item["provider_change_id"] == "subject:campaign.murky")
    assert entry["repair_class"] == "PRESERVE_UNKNOWN"
    assert "must not be rewritten by guess" in entry["required_state"]


def test_an_unknown_candidate_becomes_a_preserved_obligation_carrying_its_instruction() -> None:
    record = evidence("legacy/config.py", "surface_reference", {"pattern": "x"})
    ledger = ledger_of([record], {"legacy/config.py": "UNKNOWN"})
    built = build(inputs_for(ledger, {}, "v25"))
    assert len(built) == 1
    assert built[0]["repair_class"] == "PRESERVE_UNKNOWN"
    assert built[0]["required_state"] == "attach a production observation"


def test_two_effective_versions_produce_two_obligation_sets_against_one_target() -> None:
    records = [
        evidence("src/a.py", "call_version", {"literal": "v22"}, "v22"),
        evidence("src/b.py", "call_version", {"literal": "v24"}, "v24"),
    ]
    ledger = ledger_of(records, {"src/a.py": "AFFECTED", "src/b.py": "AFFECTED"})
    sets = {"v22": change_set("v22", "v25"), "v24": change_set("v24", "v25")}
    built = build(inputs_for(ledger, sets, "v25"))
    assert {item["provider_change_id"] for item in built} == {
        "version:v22->v25",
        "version:v24->v25",
    }


def test_an_unresolvable_effective_version_is_preserved_not_assumed() -> None:
    records = [
        evidence("src/a.py", "call_version", {"literal": "v22"}, "v22"),
        evidence("src/a.py", "call_version", {"literal": "v24"}, "v24"),
    ]
    ledger = ledger_of(records, {"src/a.py": "AFFECTED"})
    built = build(inputs_for(ledger, {"v22": change_set("v22", "v25")}, "v25"))
    assert built[0]["repair_class"] == "PRESERVE_UNKNOWN"
    assert built[0]["provider_change_id"] == "EFFECTIVE_VERSION_UNRESOLVED"


def test_a_missing_composed_diff_is_preserved_never_guessed() -> None:
    record = evidence("src/a.py", "call_version", {"literal": "v19"}, "v19")
    ledger = ledger_of([record], {"src/a.py": "AFFECTED"})
    built = build(inputs_for(ledger, {"v22": change_set("v22", "v25")}, "v25"))
    assert built[0]["repair_class"] == "PRESERVE_UNKNOWN"
    assert built[0]["provider_change_id"] == "UNKNOWN_PROVIDER_CONTRACT"


def test_a_version_below_the_lattice_floor_earns_a_human_obligation_never_silence() -> None:
    record = evidence("spikes/get_campaigns.py", "call_version", {"literal": "v9"}, "v9")
    ledger = ledger_of([record], {"spikes/get_campaigns.py": "AFFECTED"})
    built = build(
        ObligationInputs(
            ledger=ledger,
            change_sets={"v24": change_set("v24", "v25")},
            oracle=StubOracle(),
            target="v25",
            uncomposable={"v9": "v9 is below this pack's lattice floor (v19)"},
        )
    )
    assert len(built) == 1
    assert built[0]["repair_class"] == "HUMAN"
    assert built[0]["provider_change_id"] == "version:v9->v25"
    assert built[0]["current_state"] == "spikes/get_campaigns.py:7 calls the provider at v9"
    assert built[0]["required_state"] == (
        "spikes/get_campaigns.py:7 calls the provider at v25; v9 is below this pack's lattice "
        "floor (v19) so no deterministic transform applies: migrate by hand or retire the call"
    )
    assert built[0]["verification_method"] == method(ABSENT, "v9")


def test_every_affected_version_bearing_candidate_has_at_least_one_obligation() -> None:
    records = [
        evidence("src/composable.py", "call_version", {"literal": "v22"}, "v22"),
        evidence("src/floor.py", "call_version", {"literal": "v9"}, "v9"),
        evidence("src/at_target.py", "call_version", {"literal": "v25"}, "v25"),
        evidence("src/torn.py", "call_version", {"literal": "v22"}, "v22"),
        evidence("src/torn.py", "call_version", {"literal": "v24"}, "v24"),
        evidence("src/unmapped.py", "call_version", {"literal": "v21"}, "v21"),
        evidence("requirements.txt", "sdk_installed", {"line": "google-ads==22.1.0"}),
    ]
    bound = structural_reference(
        "src/bound.py", 3, {"node_kind": "identifier", "binding": "sdk", "binding_versions": ["V9"]}
    )
    paths = {str(item["path"]) for item in records}
    ledger = ledger_of(records, dict.fromkeys(sorted(paths), "AFFECTED"))
    ledger = Ledger(
        provider="p",
        run_id=RUN,
        proof_scope_hash=SCOPE,
        evidence=(*ledger.evidence, bound),
        candidates=(
            *ledger.candidates,
            candidate_of(
                "src/bound.py:3:ProviderClient", "surface_reference", [bound["id"]], "AFFECTED"
            ),
        ),
    )
    built = build(
        ObligationInputs(
            ledger=ledger,
            change_sets={"v22": change_set("v22", "v25"), "v25": change_set("v25", "v25")},
            oracle=StubOracle(),
            target="v25",
            uncomposable={"v9": "v9 is below this pack's lattice floor (v19)"},
        )
    )
    for candidate in ledger.candidates:
        assert candidate["status"] == "AFFECTED"
        attached = set(candidate["evidence_ids"])
        owned = [item for item in built if set(item["evidence_ids"]) <= attached]
        assert owned, f"{candidate['id']} has no obligation"
    floor = [item for item in built if item["provider_change_id"] == "version:v9->v25"]
    assert [item["repair_class"] for item in floor] == ["HUMAN", "HUMAN"]


def test_the_same_ledger_builds_byte_identical_obligations_twice() -> None:
    record = evidence("src/client.py", "call_version", {"literal": "v22"}, "v22")
    ledger = ledger_of([record], {"src/client.py": "AFFECTED"})
    first = build(inputs_for(ledger, {"v22": change_set("v22", "v25")}, "v25"))
    second = build(inputs_for(ledger, {"v22": change_set("v22", "v25")}, "v25"))
    assert first == second


def test_a_dependency_pin_earns_an_obligation_without_a_call_version() -> None:
    record = evidence("requirements.txt", "sdk_installed", {"line": "google-ads==22.1.0"})
    ledger = ledger_of([record], {"requirements.txt": "AFFECTED"})
    built = build(inputs_for(ledger, {}, "v25"))
    assert len(built) == 1
    assert built[0]["repair_class"] == "DETERMINISTIC"
    assert built[0]["provider_change_id"] == "dependency:v25"
    assert "supporting v25" in built[0]["required_state"]


def test_every_affected_candidate_earns_at_least_one_obligation() -> None:
    records = [
        evidence("src/a.py", "call_version", {"literal": "v25"}, "v25"),
        evidence("src/b.py", "call_version", {"literal": "v22"}, "v22"),
    ]
    ledger = ledger_of(records, {"src/a.py": "AFFECTED", "src/b.py": "AFFECTED"})
    sets = {"v22": change_set("v22", "v25"), "v25": change_set("v25", "v25")}
    built = build(inputs_for(ledger, sets, "v25"))
    covered = {item["id"] for item in built}
    assert len(covered) == 2


def structural_reference(path: str, line: int, value: dict[str, Any]) -> dict[str, Any]:
    return make_evidence(
        run_id=RUN,
        proof_scope_hash=SCOPE,
        claim_type="surface_reference",
        observer="structure",
        repo_sha=None,
        path=path,
        line_start=line,
        line_end=line,
        source_hash="a" * 64,
        value=value,
        provider_subject="ProviderClient",
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="PROVEN",
    )


def candidate_of(key: str, claim_type: str, ids: list[str], status: str) -> dict[str, Any]:
    return make_candidate(
        candidate_id=candidate_identity("p", claim_type, key),
        run_id=RUN,
        proof_scope_hash=SCOPE,
        provider="p",
        evidence_ids=ids,
        status=status,
        reason="fixture",
        close_with=None,
    )


def test_a_reference_bound_to_its_files_version_literal_names_that_literal_as_the_edit() -> None:
    literal = evidence("src/a.py", "call_version", {"literal": "v22"}, "v22")
    bound = structural_reference("src/a.py", 20, {"node_kind": "identifier", "binding": "sdk"})
    ledger = Ledger(
        provider="p",
        run_id=RUN,
        proof_scope_hash=SCOPE,
        evidence=(literal, bound),
        candidates=(
            candidate_of("src/a.py:7", "call_version", [literal["id"]], "AFFECTED"),
            candidate_of(
                "src/a.py:20:ProviderClient", "surface_reference", [bound["id"]], "AFFECTED"
            ),
        ),
    )
    built = build(inputs_for(ledger, {"v22": change_set("v22", "v25")}, "v25"))
    by_candidate = {item["evidence_ids"][0]: item for item in built}
    reference = by_candidate[bound["id"]]
    assert reference["provider_change_id"] == "version:v22->v25"
    assert reference["repair_class"] == "DETERMINISTIC"
    assert "src/a.py:7" in reference["current_state"]
    assert not any(item["provider_change_id"] == "EFFECTIVE_VERSION_UNRESOLVED" for item in built)


def test_a_file_bound_reference_names_where_the_literal_is_written_not_where_it_is_read() -> None:
    reader = make_evidence(
        run_id=RUN,
        proof_scope_hash=SCOPE,
        claim_type="call_version",
        observer="structure",
        repo_sha=None,
        path="src/a.py",
        line_start=425,
        line_end=425,
        source_hash="a" * 64,
        value={
            "literal": "v24",
            "paths": [
                {
                    "terminal": "LITERAL",
                    "literal": "v24",
                    "path": "src/a.py",
                    "range": {"start_line": 17, "end_line": 17},
                }
            ],
        },
        provider_subject="v24",
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="PROVEN",
    )
    bound = structural_reference("src/a.py", 9, {"node_kind": "identifier", "binding": "sdk"})
    ledger = Ledger(
        provider="p",
        run_id=RUN,
        proof_scope_hash=SCOPE,
        evidence=(reader, bound),
        candidates=(
            candidate_of("src/a.py:425", "call_version", [reader["id"]], "AFFECTED"),
            candidate_of(
                "src/a.py:9:ProviderClient", "surface_reference", [bound["id"]], "AFFECTED"
            ),
        ),
    )
    built = build(inputs_for(ledger, {"v24": change_set("v24", "v25")}, "v25"))
    by_candidate = {item["evidence_ids"][0]: item for item in built}
    assert by_candidate[bound["id"]]["current_state"] == (
        "src/a.py:9 is bound to the provider at v24; the v24 literal is written at src/a.py:17"
    )
    assert by_candidate[reader["id"]]["current_state"] == (
        "src/a.py:425 calls the provider at v24; the v24 literal is written at src/a.py:17"
    )


def test_a_reference_whose_binding_carries_a_version_uses_that_version() -> None:
    bound = structural_reference(
        "src/b.py", 3, {"node_kind": "identifier", "binding": "sdk", "binding_versions": ["V22"]}
    )
    ledger = Ledger(
        provider="p",
        run_id=RUN,
        proof_scope_hash=SCOPE,
        evidence=(bound,),
        candidates=(
            candidate_of(
                "src/b.py:3:ProviderClient", "surface_reference", [bound["id"]], "AFFECTED"
            ),
        ),
    )
    built = build(inputs_for(ledger, {"v22": change_set("v22", "v25")}, "v25"))
    assert [item["provider_change_id"] for item in built] == ["version:v22->v25"]
    assert built[0]["repair_class"] == "DETERMINISTIC"


def test_a_bound_reference_names_the_import_line_that_carries_its_version_as_the_edit() -> None:
    bound = structural_reference(
        "src/b.py",
        40,
        {
            "node_kind": "bound_identifier",
            "binding": "sdk.V22.Client",
            "binding_versions": ["V22"],
            "binding_site": "src/b.py:3",
        },
    )
    ledger = Ledger(
        provider="p",
        run_id=RUN,
        proof_scope_hash=SCOPE,
        evidence=(bound,),
        candidates=(
            candidate_of(
                "src/b.py:40:ProviderClient", "surface_reference", [bound["id"]], "AFFECTED"
            ),
        ),
    )
    built = build(inputs_for(ledger, {"v22": change_set("v22", "v25")}, "v25"))
    assert built[0]["current_state"].endswith("the v22 literal is written at src/b.py:3")


def query_record(path: str, fragments: list[str]) -> dict[str, Any]:
    return make_evidence(
        run_id=RUN,
        proof_scope_hash=SCOPE,
        claim_type="request_text",
        observer="structure",
        repo_sha=None,
        path=path,
        line_start=12,
        line_end=12,
        source_hash="a" * 64,
        value={
            "resolution": "CONTRACT_VALIDATION_DEFERRED",
            "skeleton": {"fragments": fragments, "holes": []},
        },
        provider_subject="gaql",
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="RAW",
    )


def test_an_accepted_query_whose_fields_did_not_change_needs_nothing() -> None:
    query = query_record("src/q.py", ["SELECT campaign.id FROM campaign"])
    ledger = Ledger(
        provider="p",
        run_id=RUN,
        proof_scope_hash=SCOPE,
        evidence=(query,),
        candidates=(candidate_of("src/q.py:12:gaql", "request_text", [query["id"]], "AFFECTED"),),
    )
    built = build(inputs_for(ledger, {"v22": change_set("v22", "v25")}, "v25"))
    assert built == ()


def test_a_query_selecting_a_removed_field_earns_the_subject_obligation() -> None:
    query = query_record("src/q.py", ["SELECT campaign.legacy FROM campaign"])
    ledger = Ledger(
        provider="p",
        run_id=RUN,
        proof_scope_hash=SCOPE,
        evidence=(query,),
        candidates=(candidate_of("src/q.py:12:gaql", "request_text", [query["id"]], "AFFECTED"),),
    )
    removed = SubjectChange(
        subject="campaign.legacy", change="REMOVED", replacement=None, kind="RESOLVED", reason=""
    )
    built = build(inputs_for(ledger, {"v22": change_set("v22", "v25", removed)}, "v25"))
    assert [item["provider_change_id"] for item in built] == ["subject:campaign.legacy"]
    assert built[0]["repair_class"] == "HUMAN"
