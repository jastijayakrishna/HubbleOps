from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hubbleops.app import registry
from hubbleops.app.cli import ScanResult
from tests.support import COVERAGE_ROOT

Scan = Callable[[Path, registry.LoadedPack], ScanResult]


def coverage_repos() -> list[Path]:
    return sorted(path for path in COVERAGE_ROOT.iterdir() if (path / "repo").is_dir())


def rows(scan: Scan, name: str, pack: registry.LoadedPack) -> list[dict[str, Any]]:
    book = scan(COVERAGE_ROOT / name / "repo", pack).ledger
    collected: list[dict[str, Any]] = []
    for candidate in book.ordered_candidates():
        location = book.location_of(candidate)
        collected.append(
            {
                "status": candidate["status"],
                "reason": candidate["reason"],
                "claim_type": location.claim_type,
                "location": location.display(),
                "path": location.display().split(":", 1)[0],
                "subject": location.provider_subject,
            }
        )
    return collected


def literals(scan: Scan, name: str, pack: registry.LoadedPack) -> set[str]:
    book = scan(COVERAGE_ROOT / name / "repo", pack).ledger
    evidence = book.evidence_by_id()
    found: set[str] = set()
    for candidate in book.ordered_candidates():
        for identifier in candidate["evidence_ids"]:
            record = evidence.get(identifier)
            if record is None or record["claim_type"] != "call_version":
                continue
            for path in record["value"].get("paths", ()):
                if path.get("literal"):
                    found.add(str(path["literal"]))
    return found


@pytest.mark.parametrize("fixture", coverage_repos(), ids=lambda path: path.name)
def test_no_coverage_fixture_leaves_an_unexplained_candidate(
    fixture: Path, google_pack: registry.LoadedPack, scan: Scan
) -> None:
    book = scan(fixture / "repo", google_pack).ledger
    assert book.counts()["unexplained"] == 0


def test_catalog_known_query_resource_outside_the_anchor_list_is_surfaced(
    google_pack: registry.LoadedPack, scan: Scan
) -> None:
    found = rows(scan, "gaql_resource_beyond_anchor_list", google_pack)
    conversion = [row for row in found if row["subject"] == "conversion_action"]
    assert conversion, (
        "a query over a resource the provider catalog knows produced no candidate; "
        "the recall surface has drifted from the catalog again"
    )
    assert all(row["claim_type"] == "request_text" for row in conversion)


def test_query_resource_absent_from_every_catalog_version_is_surfaced_as_unknown(
    google_pack: registry.LoadedPack, scan: Scan
) -> None:
    found = rows(scan, "gaql_resource_beyond_anchor_list", google_pack)
    future = [row for row in found if row["subject"] == "brand_guidelines"]
    assert future, "a query naming an unknown resource vanished instead of becoming UNKNOWN"
    site = future[0]["location"]
    skeletons = [
        row for row in found if row["location"] == site and row["claim_type"] == "request_text"
    ]
    undecided = [row for row in skeletons if row["status"] == "UNKNOWN"]
    assert undecided, f"no request candidate at {site} stayed UNKNOWN: {skeletons}"
    assert all("brand_guidelines" in row["reason"] for row in undecided)
    for row in future:
        assert row["status"] in ("UNKNOWN", "NOT_AFFECTED_WITH_EVIDENCE")
        if row["status"] == "NOT_AFFECTED_WITH_EVIDENCE":
            assert "structural layer already resolved" in row["reason"]


def test_a_tsx_import_of_a_first_party_module_is_adjudicated_not_recalled(
    google_pack: registry.LoadedPack, scan: Scan
) -> None:
    book = scan(COVERAGE_ROOT / "tsx_import" / "repo", google_pack).ledger
    settings = [
        candidate
        for candidate in book.ordered_candidates()
        if book.location_of(candidate).path == "ui/settings.tsx"
    ]
    assert settings, "the tsx file produced no candidate at all"
    evidence = book.evidence_by_id()
    for candidate in settings:
        attached = [evidence[eid] for eid in candidate["evidence_ids"]]
        assert any(
            record["observer"] == "structure" and record["value"].get("node_kind") == "import"
            for record in attached
        ), candidate["reason"]
        assert candidate["status"] == "NOT_AFFECTED_WITH_EVIDENCE"
    status = [
        candidate
        for candidate in book.ordered_candidates()
        if book.location_of(candidate).path == "ui/status.tsx"
        and book.location_of(candidate).claim_type == "call_version"
    ]
    assert [item["status"] for item in status] == ["AFFECTED"]


def test_a_wire_identifier_carries_its_version_in_every_language(
    google_pack: registry.LoadedPack, scan: Scan
) -> None:
    found = rows(scan, "proto_namespace_outside_python", google_pack)
    carried = [row for row in found if row["claim_type"] == "call_version"]
    assert {row["path"] for row in carried} == {"service.go", "src/errors.ts"}, (
        "a wire-level protobuf identifier resolved its version in only some languages; "
        "the carrier has been language-gated again"
    )
    assert all(row["subject"] == "v22" for row in carried), (
        "the version sits on the line but no claim names it"
    )
    assert all("WIRE_NAMESPACE_WITHOUT_CALL_SITE" in row["reason"] for row in carried), (
        "a namespace readable as data in any language must name its version without being "
        "asserted as a proven call site"
    )


def test_a_rest_path_behind_a_local_wrapper_resolves_to_its_literal(
    google_pack: registry.LoadedPack, scan: Scan
) -> None:
    assert "customers:listAccessibleCustomers" in literals(
        scan, "rest_path_behind_local_wrapper", google_pack
    ), (
        "the wrapper walk lost the request path carried through a destructured parameter; "
        "the call disappeared behind the local wrapper again"
    )


def test_a_runtime_built_rest_path_behind_a_wrapper_stays_surfaced(
    google_pack: registry.LoadedPack, scan: Scan
) -> None:
    found = rows(scan, "rest_path_behind_local_wrapper", google_pack)
    assert any("googleAds:searchStream" in str(row["subject"]) for row in found)


def test_an_aliased_import_carries_provider_context_to_the_importing_file(
    google_pack: registry.LoadedPack, scan: Scan
) -> None:
    found = rows(scan, "path_alias_import", google_pack)
    assert any(row["path"] == "app/report.ts" for row in found), (
        "a file reaching the provider through a path-aliased import produced no candidate; "
        "the alias was treated as an external package again"
    )


def test_an_adjacent_contract_is_excluded_with_evidence_not_ignored(
    google_pack: registry.LoadedPack, scan: Scan
) -> None:
    found = rows(scan, "adjacent_contract_host", google_pack)
    adjacent = [row for row in found if row["claim_type"] == "adjacent_contract"]
    assert adjacent, "an adjacent contract host produced no record at all"
    assert all(row["status"] == "EXCLUDED_WITH_EVIDENCE" for row in adjacent)
    assert all(row["status"] != "AFFECTED" for row in adjacent)


def test_a_foreign_query_language_near_provider_code_is_not_claimed(
    google_pack: registry.LoadedPack, scan: Scan
) -> None:
    found = rows(scan, "foreign_query_language_nearby", google_pack)
    foreign = [
        row
        for row in found
        if row["subject"] in {"click_event", "warehouse_order"}
        or "UNRECOGNISED_RESOURCE" in row["reason"]
    ]
    assert not foreign, (
        "a query language that is not the provider's was claimed as a provider request because "
        f"the file merely imports provider code: {foreign}"
    )
    assert any(row["claim_type"] == "call_version" for row in found), (
        "the fixture no longer exercises real provider code, so it cannot guard anything"
    )


def test_provider_context_reaches_further_than_one_import_hop(
    google_pack: registry.LoadedPack, scan: Scan
) -> None:
    found = rows(scan, "context_beyond_one_import_hop", google_pack)
    assert any(row["path"] == "chain/level_a.ts" for row in found), (
        "a request path two import hops from the provider host produced no candidate; "
        "provider context stopped propagating"
    )


def test_an_unambiguous_resource_name_needs_no_surrounding_context(
    google_pack: registry.LoadedPack, scan: Scan
) -> None:
    found = rows(scan, "context_beyond_one_import_hop", google_pack)
    assert any(row["path"] == "isolated/config.ts" for row in found), (
        "a provider resource name in a file nothing imports produced no candidate; "
        "an unambiguous shape must not depend on the provider-context gate"
    )


def test_a_query_assembled_from_separate_literals_is_surfaced(
    google_pack: registry.LoadedPack, scan: Scan
) -> None:
    found = rows(scan, "split_query_assembly", google_pack)
    assert any(row["subject"] == "conversion_action" for row in found), (
        "a query split across two literals lost its resource"
    )


@pytest.mark.parametrize(
    "marker",
    [
        "https://www.googleapis.com/auth/adwords",
        '"developer-token"',
        '"login-customer-id"',
        "customers/${customerId}/conversionActions",
        "authorizationError",
    ],
)
def test_each_declared_contract_surface_reaches_the_ledger(
    marker: str, google_pack: registry.LoadedPack, scan: Scan
) -> None:
    found = rows(scan, "contract_surface_residue", google_pack)
    assert any(marker in str(row["subject"]) for row in found), (
        f"{marker!r} sits in a file with provider context and produced no candidate"
    )
