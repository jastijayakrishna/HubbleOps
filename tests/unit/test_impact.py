from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hubbleops.app import impact, migration
from hubbleops.core.candidate import candidate_identity, make_candidate
from hubbleops.core.evidence import make_evidence
from hubbleops.core.verification import ChangeSet, OracleOutcome
from hubbleops.obligations import ObligationInputs
from hubbleops.obligations import build as build_obligations
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


def ledger_of(records: list[dict[str, Any]], statuses: dict[str, tuple[str, str | None]]) -> Ledger:
    candidates: list[dict[str, Any]] = []
    for key, (status, close_with) in sorted(statuses.items()):
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
                close_with=close_with,
            )
        )
    return Ledger(
        provider="p",
        run_id=RUN,
        proof_scope_hash=SCOPE,
        evidence=tuple(records),
        candidates=tuple(candidates),
    )


def change_set(from_version: str, to_version: str) -> ChangeSet:
    return ChangeSet(
        from_version=from_version, to_version=to_version, pair_hash="b" * 64, changes=()
    )


def _fixture() -> tuple[Ledger, tuple[dict[str, Any], ...]]:
    records = [
        evidence("src/client.py", "call_version", {"literal": "v22"}, "v22"),
        evidence("requirements.txt", "sdk_installed", {"line": "google-ads==22.1.0"}),
        evidence("src/ambiguous.py", "call_version", {"literal": "v22"}, "v22"),
        evidence("src/ambiguous.py", "call_version", {"literal": "v24"}, "v24"),
        evidence("assets/logo.png", "surface_reference", {"pattern": "x"}),
        evidence("docs/readme.md", "surface_reference", {"pattern": "x"}),
        evidence("tests/fixtures/mock.json", "surface_reference", {"pattern": "x"}),
    ]
    ledger = ledger_of(
        records,
        {
            "src/client.py": ("AFFECTED", None),
            "requirements.txt": ("AFFECTED", None),
            "src/ambiguous.py": ("AFFECTED", None),
            "assets/logo.png": ("UNKNOWN", "attach a production observation of the icon call"),
            "docs/readme.md": ("UNKNOWN", "attach a production observation of the icon call"),
            "tests/fixtures/mock.json": (
                "UNSCANNED",
                "parse tests/fixtures/mock.json with a supported grammar",
            ),
        },
    )
    inputs = ObligationInputs(
        ledger=ledger,
        change_sets={"v22": change_set("v22", "v25")},
        oracle=StubOracle(),
        target="v25",
    )
    return ledger, build_obligations(inputs)


def test_affected_paths_hold_only_deterministic_and_human_work() -> None:
    ledger, obligations = _fixture()
    report = impact.from_obligations(
        ledger=ledger, obligations=obligations, provider="p", target="v25"
    )
    record = report.record
    assert record["affected_paths"] == ["requirements.txt", "src/client.py"]
    assert "assets/logo.png" not in record["affected_paths"]
    assert "docs/readme.md" not in record["affected_paths"]
    assert "tests/fixtures/mock.json" not in record["affected_paths"]
    assert "src/ambiguous.py" not in record["affected_paths"]


def test_unresolved_version_is_counted_apart_from_carried() -> None:
    ledger, obligations = _fixture()
    report = impact.from_obligations(
        ledger=ledger, obligations=obligations, provider="p", target="v25"
    )
    record = report.record
    assert record["unresolved_version_count"] == 1
    assert record["carried_counts"]["total"] == 3


def test_no_obligation_disappears_between_the_three_buckets() -> None:
    ledger, obligations = _fixture()
    report = impact.from_obligations(
        ledger=ledger, obligations=obligations, provider="p", target="v25"
    )
    record = report.record
    obligation_counts = record["obligation_counts"]
    accounted = (
        obligation_counts["deterministic"]
        + obligation_counts["human"]
        + record["unresolved_version_count"]
        + record["carried_counts"]["total"]
    )
    assert accounted == obligation_counts["total"] == len(obligations)


def test_carried_groups_sum_to_the_carried_count() -> None:
    ledger, obligations = _fixture()
    report = impact.from_obligations(
        ledger=ledger, obligations=obligations, provider="p", target="v25"
    )
    record = report.record
    assert (
        sum(group["sites"] for group in record["carried_groups"])
        == record["carried_counts"]["total"]
    )


def test_carried_obligations_sharing_a_closing_instruction_are_grouped() -> None:
    ledger, obligations = _fixture()
    report = impact.from_obligations(
        ledger=ledger, obligations=obligations, provider="p", target="v25"
    )
    groups = {group["instruction"]: group for group in report.record["carried_groups"]}
    assert groups["attach a production observation of the icon call"]["sites"] == 2
    assert sorted(groups["attach a production observation of the icon call"]["paths"]) == [
        "assets/logo.png",
        "docs/readme.md",
    ]
    assert groups["parse tests/fixtures/mock.json with a supported grammar"]["sites"] == 1


def test_render_lists_affected_paths_but_not_carried_paths() -> None:
    ledger, obligations = _fixture()
    report = impact.from_obligations(
        ledger=ledger, obligations=obligations, provider="p", target="v25"
    )
    rendered = impact.render(report)
    assert "src/client.py" in rendered
    assert "requirements.txt" in rendered
    header, _, tail = rendered.partition("carried UNKNOWN")
    assert "assets/logo.png" not in header
    assert "docs/readme.md" not in header
    assert "tests/fixtures/mock.json" not in header
    assert "assets/logo.png" in tail
    assert "docs/readme.md" in tail
    assert "tests/fixtures/mock.json" in tail


def test_render_groups_deterministic_edits_by_finding() -> None:
    ledger, obligations = _fixture()
    report = impact.from_obligations(
        ledger=ledger, obligations=obligations, provider="p", target="v25"
    )
    rendered = impact.render(report)
    assert "version:v22->v25  1 site" in rendered
    assert "dependency:v25  1 site" in rendered


def test_report_is_byte_identical_on_rebuild() -> None:
    ledger, obligations = _fixture()
    first = impact.from_obligations(
        ledger=ledger, obligations=obligations, provider="p", target="v25"
    )
    second = impact.from_obligations(
        ledger=ledger, obligations=obligations, provider="p", target="v25"
    )
    assert first.json_bytes() == second.json_bytes()


def test_partition_rejects_an_unclassified_repair_class() -> None:
    ledger, obligations = _fixture()
    projected = impact.project(ledger, obligations)
    tampered = [dict(item) for item in projected]
    tampered[0]["repair_class"] = "AGENT"
    try:
        impact.partition(tampered)
    except impact.ImpactInvalid:
        return
    raise AssertionError("partition must fail closed on an unclassified repair_class")


def test_the_edit_site_is_the_literal_when_the_obligation_names_where_it_is_written() -> None:
    obligation = {
        "current_state": (
            "src/api.ts:60 calls the provider at v22; the v22 literal is written at "
            "src/constants.ts:20"
        )
    }
    assert migration.edit_sites(obligation, ("src/api.ts", 60, "call_version")) == (
        ("src/constants.ts", 20),
    )
    plain = {"current_state": "src/api.ts:60 calls the provider at v22"}
    assert migration.edit_sites(plain, ("src/api.ts", 60, "call_version")) == (("src/api.ts", 60),)
