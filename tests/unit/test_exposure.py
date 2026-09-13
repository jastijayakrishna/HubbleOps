from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

import pytest

from hubbleops.app import cli, exposure, registry
from hubbleops.core.candidate import candidate_identity, claim_key, make_candidate
from hubbleops.core.errors import HubbleOpsError
from hubbleops.core.evidence import make_evidence
from hubbleops.observe.ledger import Ledger

SCOPE = "0" * 64
RUN = "1" * 64
SOURCE = "a" * 64
FILES = 12
LITERAL_SITES = 40


@pytest.fixture(scope="module")
def mock_pack() -> registry.LoadedPack:
    return registry.load_pack("_mock")


def record(
    *,
    claim_type: str,
    path: str,
    line: int | None,
    subject: str | None,
    value: dict[str, Any],
) -> dict[str, Any]:
    return make_evidence(
        run_id=RUN,
        proof_scope_hash=SCOPE,
        claim_type=claim_type,
        observer="text",
        repo_sha=None,
        path=path,
        line_start=line,
        line_end=line,
        source_hash=SOURCE,
        value=value,
        provider_subject=subject,
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="RAW",
    )


def version_literal(index: int) -> dict[str, Any]:
    return record(
        claim_type="call_version",
        path=f"src/mod{index % FILES}.py",
        line=index + 1,
        subject="v1",
        value={
            "classification": "INSIDE",
            "kind": "version_carrier",
            "line": "client = Client(version='v1')",
            "matches": ["v1"],
            "pattern": "python_version_literal",
            "slot": "per_call",
        },
    )


def sdk_record(version: str) -> dict[str, Any]:
    return record(
        claim_type="sdk_installed",
        path="requirements.txt",
        line=3,
        subject="mockprov",
        value={
            "ecosystem": "python",
            "package": "mockprov",
            "source_kind": "lock",
            "spec": None,
            "state": "PRESENT",
            "version": version,
        },
    )


SUBJECT_CALL = record(
    claim_type="call_version",
    path="src/query.py",
    line=5,
    subject="v1",
    value={
        "classification": "INSIDE",
        "kind": "version_carrier",
        "line": "SELECT campaigns.legacy FROM campaign",
        "matches": ["v1"],
        "pattern": "python_version_literal",
        "slot": "per_call",
    },
)

SUBJECT_USE = record(
    claim_type="surface_reference",
    path="src/query.py",
    line=5,
    subject="campaigns.legacy",
    value={
        "classification": "INSIDE",
        "kind": "identifier",
        "line": "SELECT campaigns.legacy FROM campaign",
        "matches": ["campaigns.legacy"],
        "pattern": "campaigns.legacy",
    },
)


def affected(records: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    winner = records[0]
    return make_candidate(
        candidate_id=candidate_identity("_mock", str(winner["claim_type"]), claim_key(winner)),
        run_id=RUN,
        proof_scope_hash=SCOPE,
        provider="_mock",
        evidence_ids=[str(item["id"]) for item in records],
        status="AFFECTED",
        reason=reason,
        close_with=None,
    )


def ledger_of(sdk_version: str = "30.0.0") -> Ledger:
    literals = [version_literal(index) for index in range(LITERAL_SITES)]
    sdk = sdk_record(sdk_version)
    evidence = [*literals, sdk, SUBJECT_CALL, SUBJECT_USE]
    candidates = [
        affected(
            [item],
            f"explicit version literal v1 at {item['path']}:{item['line_start']} "
            "via version carrier python_version_literal",
        )
        for item in literals
    ]
    candidates.append(affected([sdk], "python package mockprov resolved by lock"))
    candidates.append(
        affected([SUBJECT_USE, SUBJECT_CALL], "'campaigns.legacy' at src/query.py:5 is in use")
    )
    return Ledger(
        provider="_mock",
        run_id=RUN,
        proof_scope_hash=SCOPE,
        evidence=tuple(evidence),
        candidates=tuple(candidates),
    )


def rendered(
    book: Ledger,
    found: exposure.MigrationFindings | None,
    expand: bool = False,
) -> str:
    return exposure.render(
        ledger=book,
        pack_name="_mock",
        changes_hash="c" * 64,
        target="v2",
        repository="/repo",
        repo_sha=None,
        findings=found,
        expand=expand,
    )


def section(text: str, title: str) -> str:
    body = text.split(f"\n{title}", 1)[1]
    return body.split("\n─", 1)[0]


def test_the_affected_group_counts_sum_to_the_affected_candidate_count(
    mock_pack: registry.LoadedPack,
) -> None:
    book = ledger_of()
    found = exposure.findings(pack=mock_pack, ledger=book, target="v2")
    body = section(rendered(book, found), "AFFECTED")
    grouped = sum(int(match) for match in re.findall(r"(\d+) sites? · \d+ files?", body))
    ungrouped = len(re.findall(r"evidence: ", body))
    literals = sum(
        book.location_of(candidate).claim_type == "call_version"
        for candidate in book.by_status("AFFECTED")
    )
    assert grouped + ungrouped == book.counts()["affected"] == LITERAL_SITES + 2
    assert grouped == literals
    assert f"explicit version literal V1 via python_version_literal   {literals} sites" in body
    assert "[expand]" in body


def test_expand_prints_every_affected_file(mock_pack: registry.LoadedPack) -> None:
    book = ledger_of()
    found = exposure.findings(pack=mock_pack, ledger=book, target="v2")
    body = section(rendered(book, found, expand=True), "AFFECTED")
    assert "[expand]" not in body
    for index in range(FILES):
        assert f"src/mod{index}.py" in body


def test_migration_findings_names_the_effective_version(
    mock_pack: registry.LoadedPack,
) -> None:
    book = ledger_of()
    found = exposure.findings(pack=mock_pack, ledger=book, target="v2")
    body = section(rendered(book, found), "MIGRATION FINDINGS")
    assert f"V1   {LITERAL_SITES + 1} sites · {FILES + 1} files" in body
    assert "version:v1->v2" in body
    assert "Deterministic edits" in body


def test_an_installed_client_below_the_target_floor_is_named(
    mock_pack: registry.LoadedPack,
) -> None:
    book = ledger_of()
    found = exposure.findings(pack=mock_pack, ledger=book, target="v2")
    below = replace(found, minimums={"python": "31.2.0"})
    body = section(rendered(book, below), "MIGRATION FINDINGS")
    assert "mockprov" in body
    assert "30.0.0" in body
    assert "v2 python minimum 31.2.0" in body
    assert "below floor" in body


def test_a_client_at_or_above_the_floor_meets_it(mock_pack: registry.LoadedPack) -> None:
    book = ledger_of("32.0.0")
    found = exposure.findings(pack=mock_pack, ledger=book, target="v2")
    body = section(
        rendered(book, replace(found, minimums={"python": "31.2.0"})), "MIGRATION FINDINGS"
    )
    assert "meets floor" in body


def test_a_client_version_the_pack_cannot_compare_never_meets_the_floor(
    mock_pack: registry.LoadedPack,
) -> None:
    book = ledger_of("dev-legacy-v32.1.0")
    found = exposure.findings(pack=mock_pack, ledger=book, target="v2")
    body = section(
        rendered(book, replace(found, minimums={"python": "31.2.0"})), "MIGRATION FINDINGS"
    )
    assert "floor unknown" in body
    assert "meets floor" not in body


def test_a_client_with_no_documented_minimum_reports_the_absence(
    mock_pack: registry.LoadedPack,
) -> None:
    book = ledger_of()
    found = exposure.findings(pack=mock_pack, ledger=book, target="v2")
    body = section(rendered(book, found), "MIGRATION FINDINGS")
    assert "no documented minimum" in body
    assert "floor unknown" in body


def test_a_removed_subject_in_use_is_a_finding(mock_pack: registry.LoadedPack) -> None:
    book = ledger_of()
    found = exposure.findings(pack=mock_pack, ledger=book, target="v2")
    body = section(rendered(book, found), "MIGRATION FINDINGS")
    assert "REMOVED campaigns.legacy   1 site" in body
    assert "src/query.py:5" in body


def test_the_sunset_of_the_detected_version_and_the_target_is_printed(
    mock_pack: registry.LoadedPack,
) -> None:
    book = ledger_of()
    found = exposure.findings(pack=mock_pack, ledger=book, target="v2")
    dated = replace(found, sunsets={"v1": "2026-02-11", "v2": "2027-08"})
    body = section(rendered(book, dated), "MIGRATION FINDINGS")
    assert "v1   2026-02-11" in body
    assert "v2   2027-08   target" in body


def test_the_closing_line_counts_every_obligation_class(
    mock_pack: registry.LoadedPack,
) -> None:
    book = ledger_of()
    found = exposure.findings(pack=mock_pack, ledger=book, target="v2")
    deterministic = sum(item["repair_class"] == "DETERMINISTIC" for item in found.obligations)
    human = sum(item["repair_class"] == "HUMAN" for item in found.obligations)
    preserved = sum(item["repair_class"] == "PRESERVE_UNKNOWN" for item in found.obligations)
    body = section(rendered(book, found), "MIGRATION FINDINGS")
    assert (
        f"Deterministic edits {deterministic} · Human decisions {human} "
        f"· Preserved UNKNOWN {preserved}" in body
    )


def test_the_target_is_the_header_target(mock_pack: registry.LoadedPack) -> None:
    book = ledger_of()
    found = exposure.findings(pack=mock_pack, ledger=book, target="v2")
    text = rendered(book, found)
    assert "Target  v2" in text
    assert "MIGRATION FINDINGS  → v2" in text


def test_the_rendering_is_byte_identical_on_two_renders(
    mock_pack: registry.LoadedPack,
) -> None:
    book = ledger_of()
    found = exposure.findings(pack=mock_pack, ledger=book, target="v2")
    assert rendered(book, found).encode() == rendered(book, found).encode()


def test_the_default_target_is_the_packs_latest_version(mock_pack: registry.LoadedPack) -> None:
    assert cli.chosen_target(mock_pack, None) == mock_pack.versions()[-1].id
    assert cli.chosen_target(mock_pack, "v1") == "v1"


def test_a_target_the_pack_does_not_publish_is_refused(mock_pack: registry.LoadedPack) -> None:
    with pytest.raises(HubbleOpsError, match="is not a version of _mock"):
        cli.chosen_target(mock_pack, "v99")


def test_a_rendering_without_a_contract_says_so(mock_pack: registry.LoadedPack) -> None:
    body = section(rendered(ledger_of(), None), "MIGRATION FINDINGS")
    assert "no provider contract was supplied to this rendering" in body


def request_record(
    path: str, line: int, observer: str, value: dict[str, Any], subject: str = "keyvalue"
) -> dict[str, Any]:
    return make_evidence(
        run_id=RUN,
        proof_scope_hash=SCOPE,
        claim_type="request_text",
        observer=observer,
        repo_sha=None,
        path=path,
        line_start=line,
        line_end=line,
        source_hash=SOURCE,
        value=value,
        provider_subject=subject,
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="RAW",
    )


def query_ledger() -> Ledger:
    accepted = request_record(
        "src/a.py",
        3,
        "structure",
        {
            "resolution": "CONTRACT_VALIDATION_DEFERRED",
            "service": "MockService",
            "method": "Search",
            "skeleton": {"fragments": ["resource=campaigns"], "holes": []},
        },
    )
    rejected = request_record(
        "src/b.py",
        4,
        "structure",
        {
            "resolution": "CONTRACT_VALIDATION_DEFERRED",
            "service": "MockService",
            "method": "Search",
            "request_text": '{"bad": true}',
            "skeleton": {"fragments": ['{"bad": true}'], "holes": []},
        },
    )
    holed = request_record(
        "src/c.py",
        5,
        "structure",
        {"resolution": "UNKNOWN_QUERY_HOLE", "skeleton": {"fragments": [], "holes": ["field"]}},
    )
    covered = request_record(
        "src/a.py", 3, "text", {"kind": "request_resource", "resource": "campaigns"}, "campaigns"
    )
    text_only = request_record(
        "src/d.py", 6, "text", {"kind": "request_resource", "resource": "accounts"}, "accounts"
    )
    records = [accepted, rejected, holed, covered, text_only]
    return Ledger(
        provider="_mock",
        run_id=RUN,
        proof_scope_hash=SCOPE,
        evidence=tuple(records),
        candidates=tuple(affected([item], "fixture") for item in records),
    )


def test_every_request_site_gets_exactly_one_query_verdict(mock_pack: registry.LoadedPack) -> None:
    rows = exposure.queries(pack=mock_pack, ledger=query_ledger(), target="v2")
    assert [(row.site(), row.verdict) for row in rows] == [
        ("src/a.py:3", "ACCEPTED"),
        ("src/b.py:4", "REJECTED"),
        ("src/c.py:5", "HOLE"),
        ("src/d.py:6", "TEXT-ONLY"),
    ]
    assert rows[0].detail == "CATALOG authority"
    assert rows[1].detail == "mock known-bad request"
    assert rows[2].detail == "field"


def test_the_queries_block_totals_add_up_and_render_byte_identically(
    mock_pack: registry.LoadedPack,
) -> None:
    rows = exposure.queries(pack=mock_pack, ledger=query_ledger(), target="v2")
    first = exposure.render_queries(rows, "v2", expand=False)
    assert first == exposure.render_queries(rows, "v2", expand=False)
    assert "4 request sites · accepted 1 · rejected 1 · undecided 0 · holes 1" in first
    assert "REJECTED   src/b.py:4" in first
    body = section(
        rendered(
            query_ledger(), exposure.findings(pack=mock_pack, ledger=query_ledger(), target="v2")
        ),
        "QUERIES",
    )
    assert "ACCEPTED   src/a.py:3" in body


def test_findings_never_build_a_transport_from_the_environment(
    mock_pack: registry.LoadedPack, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse() -> Any:
        raise AssertionError("scan and exposure must judge with the offline pack contract")

    monkeypatch.setattr(mock_pack.implementation, "verification_contract", refuse, raising=False)
    found = exposure.findings(pack=mock_pack, ledger=query_ledger(), target="v2")
    assert found.queries
