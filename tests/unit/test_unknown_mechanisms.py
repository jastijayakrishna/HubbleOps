from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from hubbleops.app import registry
from hubbleops.app.cli import scan_repository
from hubbleops.closure.source_closure import FileRole
from hubbleops.core.canonical import EMPTY_SHA256, content_id
from hubbleops.core.evidence import make_evidence
from hubbleops.observe import resolver

DUAL_DICTIONARY_SOURCE = "<?php\nnamespace Shop\\Ads;\n\n$label = 'google-ads';\n"

COMPOSER_MANIFEST = (
    json.dumps({"name": "shop/ads", "require": {"google-ads": "^22.0"}}, indent=2) + "\n"
)

COMPOSER_LOCK = (
    json.dumps(
        {
            "packages": [
                {"name": "googleads/google-ads-php", "version": "22.1.0"},
                {"name": "psr/log", "version": "1.1.4"},
            ]
        },
        indent=2,
    )
    + "\n"
)

DOCUMENTATION = "# Ads\n\nThe GoogleAdsClient is configured in the plugin settings.\n"

STYLESHEET = ".google-ads-banner {\n\tcolor: red;\n}\n"

SINGLE_VERSION_SOURCE = (
    "<?php\n"
    "namespace Shop\\Ads;\n"
    "\n"
    "use Google\\Ads\\GoogleAds\\V23\\Services\\SearchGoogleAdsRequest;\n"
    "use Vendor\\Sdk\\GoogleAdsClient;\n"
    "\n"
    "class Reporter {\n"
    "\tpublic function build() {\n"
    "\t\t$client = new GoogleAdsClient();\n"
    "\t\treturn new SearchGoogleAdsRequest();\n"
    "\t}\n"
    "}\n"
)

TWO_VERSION_SOURCE = (
    "<?php\n"
    "namespace Shop\\Ads;\n"
    "\n"
    "use Google\\Ads\\GoogleAds\\V23\\Services\\SearchGoogleAdsRequest;\n"
    "use Google\\Ads\\GoogleAds\\V22\\Services\\SearchGoogleAdsStreamRequest;\n"
    "use Vendor\\Sdk\\GoogleAdsClient;\n"
    "\n"
    "class Reporter {\n"
    "\tpublic function build() {\n"
    "\t\t$client = new GoogleAdsClient();\n"
    "\t}\n"
    "}\n"
)

UNPARSED_SINGLE_VERSION = (
    "#!/bin/sh\n"
    'curl "https://googleads.googleapis.com/v22/customers/123/googleAds:search"\n'
    'echo "googleads"\n'
)

ERROR_SHAPE_SOURCE = (
    "const api = require('google-ads-api');\n"
    "let errorCode;\n"
    "const denied = error.errorCode.authorizationError;\n"
)

ENVIRONMENT_SOURCE = (
    "const token = process.env.GOOGLE_ADS_DEVELOPER_TOKEN!;\n"
    "const release = process.env.GOOGLE_ADS_API_VERSION;\n"
    'export const GOOGLE_ADS_API_VERSION = "v22";\n'
    'const host = "googleads.googleapis.com";\n'
)

FIRST_PARTY_MODULE = 'export const ENDPOINT = "https://googleads.googleapis.com/v22";\n'

IMPORT_ONLY_SOURCE = (
    'import { ENDPOINT } from "../lib/google-ads/client";\n'
    'const path = "customers/123/conversionActions/signup";\n'
    "export function build() {\n"
    "  return `${ENDPOINT}/${path}`;\n"
    "}\n"
)

RUN_ID = content_id({"run": 1})
SCOPE_HASH = content_id({"scope": 1})


def write_tree(root: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def located(book: Any) -> dict[tuple[str, int | None, str, str | None], dict[str, Any]]:
    found: dict[tuple[str, int | None, str, str | None], dict[str, Any]] = {}
    for candidate in book.ordered_candidates():
        place = book.location_of(candidate)
        found[(place.path, place.line, place.claim_type, place.provider_subject)] = candidate
    return found


def text_records(book: Any, path: str, line: int, subject: str) -> list[dict[str, Any]]:
    return [
        record
        for record in book.evidence
        if record["observer"] == "text"
        and record["path"] == path
        and record["line_start"] == line
        and record["provider_subject"] == subject
    ]


def structure_record(path: str, **value: Any) -> dict[str, Any]:
    return make_evidence(
        run_id=RUN_ID,
        proof_scope_hash=SCOPE_HASH,
        claim_type="surface_reference",
        observer="structure",
        repo_sha=None,
        path=path,
        line_start=9,
        line_end=9,
        source_hash=EMPTY_SHA256,
        value=value,
        provider_subject="GoogleAdsClient",
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="RAW",
    )


def test_a_token_in_two_surface_dictionaries_becomes_one_candidate_carrying_both_claims(
    tmp_path: Path, google_pack: registry.LoadedPack
) -> None:
    root = write_tree(tmp_path / "dual", {"src/Note.php": DUAL_DICTIONARY_SOURCE})
    book = scan_repository(root, google_pack).ledger

    records = text_records(book, "src/Note.php", 4, "google-ads")
    assert len(records) == 1
    assert records[0]["claim_type"] == "surface_reference"
    assert records[0]["value"]["claims"] == ["package_reference", "surface_reference"]
    assert records[0]["value"]["kinds"] == ["identifier", "package"]
    assert records[0]["value"]["patterns"] == ["google-ads"]
    assert len(located(book)) == len(book.ordered_candidates())
    assert book.counts()["unexplained"] == 0


def test_the_merged_claim_is_the_package_name_when_the_line_is_a_manifest(
    tmp_path: Path, google_pack: registry.LoadedPack
) -> None:
    root = write_tree(tmp_path / "manifest", {"composer.json": COMPOSER_MANIFEST})
    book = scan_repository(root, google_pack).ledger

    records = text_records(book, "composer.json", 4, "google-ads")
    assert len(records) == 1
    assert records[0]["claim_type"] == "package_reference"
    assert records[0]["value"]["claims"] == ["package_reference", "surface_reference"]
    assert book.counts()["unexplained"] == 0


def test_merging_one_observation_is_byte_identical_across_runs(
    tmp_path: Path, google_pack: registry.LoadedPack
) -> None:
    root = write_tree(tmp_path / "stable", {"src/Note.php": DUAL_DICTIONARY_SOURCE})
    first = scan_repository(root, google_pack).ledger.export()
    second = scan_repository(root, google_pack).ledger.export()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_a_provider_name_in_documentation_is_explained_by_the_file_role(
    tmp_path: Path, google_pack: registry.LoadedPack
) -> None:
    root = write_tree(tmp_path / "docs", {"docs/guide.md": DOCUMENTATION})
    book = scan_repository(root, google_pack).ledger

    candidate = located(book)[("docs/guide.md", 3, "surface_reference", "GoogleAdsClient")]
    assert candidate["status"] == "NOT_AFFECTED_WITH_EVIDENCE"
    assert "DOCUMENTATION" in candidate["reason"]
    assert "docs/guide.md" in candidate["reason"]
    assert candidate["close_with"] is None
    assert book.counts()["unexplained"] == 0


def test_a_stylesheet_selector_naming_the_provider_is_explained_by_the_stylesheet_role(
    tmp_path: Path, google_pack: registry.LoadedPack
) -> None:
    root = write_tree(tmp_path / "styles", {"assets/app.scss": STYLESHEET})
    book = scan_repository(root, google_pack).ledger

    candidate = located(book)[("assets/app.scss", 1, "surface_reference", "google-ads")]
    assert candidate["status"] == "NOT_AFFECTED_WITH_EVIDENCE"
    assert "STYLESHEET" in candidate["reason"]
    assert candidate["close_with"] is None


def test_a_stylesheet_carries_the_stylesheet_role_in_the_closure(
    tmp_path: Path, google_pack: registry.LoadedPack
) -> None:
    root = write_tree(tmp_path / "roles", {"assets/app.scss": STYLESHEET})
    closure = scan_repository(root, google_pack).closure
    assert closure.by_path()["assets/app.scss"].role is FileRole.STYLESHEET


def test_a_lock_file_text_match_is_explained_while_the_dependency_claim_survives(
    tmp_path: Path, google_pack: registry.LoadedPack
) -> None:
    root = write_tree(tmp_path / "lock", {"composer.lock": COMPOSER_LOCK})
    book = scan_repository(root, google_pack).ledger

    carrier = located(book)[("composer.lock", 4, "package_reference", "googleads/google-ads-php")]
    assert carrier["status"] == "NOT_AFFECTED_WITH_EVIDENCE"
    assert "composer.lock" in carrier["reason"]
    assert carrier["close_with"] is None

    installed = [
        candidate
        for candidate in book.ordered_candidates()
        if book.location_of(candidate).claim_type == "sdk_installed"
    ]
    assert [candidate["status"] for candidate in installed] == ["AFFECTED"]
    assert "22.1.0" in installed[0]["reason"]


def test_an_adjudicated_site_in_a_file_whose_version_evidence_agrees_binds_to_that_version(
    tmp_path: Path, google_pack: registry.LoadedPack
) -> None:
    root = write_tree(tmp_path / "single", {"src/Reporter.php": SINGLE_VERSION_SOURCE})
    book = scan_repository(root, google_pack).ledger

    candidate = located(book)[("src/Reporter.php", 9, "surface_reference", "GoogleAdsClient")]
    assert candidate["status"] == "AFFECTED"
    assert "only version evidence is V23" in candidate["reason"]
    assert "src/Reporter.php:4" in candidate["reason"]

    attached = [record for record in book.evidence if record["id"] in candidate["evidence_ids"]]
    assert any(record["observer"] == "structure" for record in attached)
    carried = [
        record
        for record in book.evidence
        if record["claim_type"] == "call_version"
        and record["path"] == "src/Reporter.php"
        and record["provider_subject"] == "V23"
    ]
    assert [record["id"] in candidate["reason"] for record in carried] == [True]


def test_a_file_whose_version_evidence_disagrees_leaves_the_reference_unknown(
    tmp_path: Path, google_pack: registry.LoadedPack
) -> None:
    root = write_tree(tmp_path / "mixed", {"src/Reporter.php": TWO_VERSION_SOURCE})
    book = scan_repository(root, google_pack).ledger

    candidate = located(book)[("src/Reporter.php", 10, "surface_reference", "GoogleAdsClient")]
    assert candidate["status"] == "UNKNOWN"
    assert candidate["close_with"]


def test_a_site_the_structural_layer_never_adjudicated_never_borrows_the_file_version(
    tmp_path: Path, google_pack: registry.LoadedPack
) -> None:
    root = write_tree(tmp_path / "unparsed", {"bin/sync.sh": UNPARSED_SINGLE_VERSION})
    book = scan_repository(root, google_pack).ledger

    version = located(book)[("bin/sync.sh", 2, "call_version", "v22")]
    recall = located(book)[("bin/sync.sh", 3, "surface_reference", "googleads")]
    assert version["status"] == "AFFECTED"
    assert recall["status"] == "NOT_AFFECTED_WITH_EVIDENCE"
    assert "no migration inside this pack's lattice changes it" in recall["reason"]
    assert "v22" not in recall["reason"]


def test_a_first_party_binding_never_borrows_the_file_version(tmp_path: Path) -> None:
    first_party = structure_record(
        "src/Reporter.php",
        node_kind="bound_identifier",
        binding="Shop\\Ads\\GoogleAdsClient",
        binding_versions=[],
        first_party_definition="GoogleAdsClient",
        binding_target="src/GoogleAdsClient.php",
    )
    index = {
        "src/Reporter.php": resolver.FileVersionEvidence(
            version="V23",
            sites=1,
            example_id=EMPTY_SHA256,
            example_location="src/Reporter.php:4",
        )
    }
    resolution = resolver.resolve_claim(
        "surface_reference",
        [first_party],
        {"src/Reporter.php": "INSIDE"},
        {"src/Reporter.php": "SOURCE"},
        index,
    )
    assert resolution.status == "UNKNOWN"
    assert "V23" not in resolution.reason
    assert resolution.close_with


def test_the_error_shape_matches_a_provider_read_and_not_a_bare_declaration(
    google_pack: registry.LoadedPack,
) -> None:
    shape = next(
        item.shape
        for item in google_pack.surface.contract_surfaces
        if item.name == "ads_failure_detail"
    )
    compiled = re.compile(shape)
    assert compiled.search("let errorCode;") is None
    assert compiled.search("const code = response.errorCode;") is None
    assert compiled.search("error.errorCode.authorizationError") is not None
    assert compiled.search("failure.error_code.queryError") is not None
    assert compiled.search("const f = new GoogleAdsFailure();") is not None
    assert compiled.search("response.errors[0].errorCode") is not None
    assert compiled.search("payload?.errors?.[0]?.errorCode?.authorizationError") is not None


def test_a_bare_error_declaration_no_longer_raises_a_contract_surface_candidate(
    tmp_path: Path, google_pack: registry.LoadedPack
) -> None:
    root = write_tree(tmp_path / "errors", {"src/api.js": ERROR_SHAPE_SOURCE})
    book = scan_repository(root, google_pack).ledger

    lines = {
        book.location_of(candidate).line
        for candidate in book.ordered_candidates()
        if book.location_of(candidate).claim_type == "contract_surface"
    }
    assert lines == {3}
    assert book.counts()["unexplained"] == 0


def test_an_environment_read_is_configuration_and_a_literal_definition_is_a_version_site(
    tmp_path: Path, google_pack: registry.LoadedPack
) -> None:
    root = write_tree(tmp_path / "env", {"src/config.ts": ENVIRONMENT_SOURCE})
    book = scan_repository(root, google_pack).ledger
    found = located(book)

    token = found[("src/config.ts", 1, "surface_reference", "GOOGLE_ADS_DEVELOPER_TOKEN")]
    assert token["status"] == "NOT_AFFECTED_WITH_EVIDENCE"
    assert "process.env" in token["reason"]

    release = found[("src/config.ts", 2, "config_reference", "GOOGLE_ADS_API_VERSION")]
    assert release["status"] == "UNKNOWN"
    assert release["close_with"] == resolver.CLOSE_WITH_RUNTIME_CONFIG

    definition = found[("src/config.ts", 3, "call_version", "v22")]
    assert definition["status"] == "AFFECTED"
    assert ("src/config.ts", 3, "config_reference", "ADS_API_VERSION") not in found
    assert book.counts()["unexplained"] == 0


def test_a_file_whose_only_provider_link_is_a_first_party_import_has_no_direct_context(
    tmp_path: Path, google_pack: registry.LoadedPack
) -> None:
    root = write_tree(
        tmp_path / "ctx",
        {"lib/google-ads/client.ts": FIRST_PARTY_MODULE, "app/build.ts": IMPORT_ONLY_SOURCE},
    )
    book = scan_repository(root, google_pack).ledger
    contexts = {
        record["path"]: record["value"]["file_context"]
        for record in book.evidence
        if record["observer"] == "text" and "file_context" in record["value"]
    }
    assert contexts["lib/google-ads/client.ts"] == "direct"
    assert contexts["app/build.ts"] == "imported"
    surfaces = [
        candidate
        for candidate in book.ordered_candidates()
        if book.location_of(candidate).path == "app/build.ts"
        and book.location_of(candidate).claim_type == "contract_surface"
    ]
    assert surfaces
    assert {item["status"] for item in surfaces} == {"NOT_AFFECTED_WITH_EVIDENCE"}
    assert book.counts()["unexplained"] == 0


INSIDE = {"src/api.ts": "INSIDE", "apps/hooks/route.ts": "INSIDE", ".env.example": "INSIDE"}
SOURCE_ROLES = {"src/api.ts": "SOURCE", "apps/hooks/route.ts": "SOURCE", ".env.example": "CONFIG"}


def text_record(
    path: str, line: int, claim_type: str, subject: str, **value: Any
) -> dict[str, Any]:
    return make_evidence(
        run_id=RUN_ID,
        proof_scope_hash=SCOPE_HASH,
        claim_type=claim_type,
        observer="text",
        repo_sha=None,
        path=path,
        line_start=line,
        line_end=line,
        source_hash=EMPTY_SHA256,
        value={"kind": "identifier", "pattern": subject, **value},
        provider_subject=subject,
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="RAW",
    )


def skeleton_record(
    path: str, line: int, resolution: str, fragments: list[str], **extra: Any
) -> dict[str, Any]:
    return make_evidence(
        run_id=RUN_ID,
        proof_scope_hash=SCOPE_HASH,
        claim_type="request_text",
        observer="structure",
        repo_sha=None,
        path=path,
        line_start=line,
        line_end=line,
        source_hash=EMPTY_SHA256,
        value={
            "resolution": resolution,
            "sink": {"name": "searchStream", "site": f"{path}:{line}"},
            "skeleton": {"fragments": fragments, "holes": []},
            **extra,
        },
        provider_subject="gaql",
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="RAW",
    )


def resolve(
    claim_type: str, records: list[dict[str, Any]], context: resolver.ResolutionContext
) -> resolver.Resolution:
    return resolver.resolve_claim(claim_type, records, INSIDE, SOURCE_ROLES, {}, context)


def test_a_first_party_import_path_segment_is_first_party(
    google_pack: registry.LoadedPack,
) -> None:
    imported = structure_record(
        "src/api.ts",
        node_kind="import",
        binding="@/lib/integrations/google-ads/api",
        binding_versions=[],
        first_party_definition=None,
        binding_target="src/lib/integrations/google-ads/api.ts",
    )
    resolution = resolve(
        "surface_reference", [imported], resolver.ResolutionContext(surface=google_pack.surface)
    )
    assert resolution.status == "NOT_AFFECTED_WITH_EVIDENCE"
    assert "src/lib/integrations/google-ads/api.ts" in resolution.reason
    assert resolution.close_with is None


def test_a_bound_identifier_resolving_to_a_first_party_module_stays_unknown() -> None:
    bound = structure_record(
        "src/api.ts",
        node_kind="bound_identifier",
        binding="@/lib/integrations/google-ads/api",
        binding_versions=[],
        first_party_definition=None,
        binding_target="src/lib/integrations/google-ads/api.ts",
    )
    assert resolve("surface_reference", [bound], resolver.ResolutionContext()).status == "UNKNOWN"


def test_a_provider_shaped_string_without_direct_context_in_its_file_is_explained() -> None:
    surface = text_record(
        "apps/hooks/route.ts",
        20,
        "contract_surface",
        "customers/data",
        surface_kind="endpoint_path",
        file_context="imported",
    )
    resolution = resolve("contract_surface", [surface], resolver.ResolutionContext())
    assert resolution.status == "NOT_AFFECTED_WITH_EVIDENCE"
    assert "no provider context of its own" in resolution.reason


def test_the_same_string_with_direct_context_stays_unknown() -> None:
    surface = text_record(
        "src/api.ts",
        196,
        "contract_surface",
        "googleAds:searchStream",
        surface_kind="endpoint_path",
        file_context="direct",
    )
    resolution = resolve("contract_surface", [surface], resolver.ResolutionContext())
    assert resolution.status == "UNKNOWN"
    assert resolution.close_with


def test_a_string_the_observer_never_scoped_is_never_explained_by_absence() -> None:
    surface = text_record(
        "apps/hooks/route.ts",
        20,
        "contract_surface",
        "customers/data",
        surface_kind="endpoint_path",
    )
    assert resolve("contract_surface", [surface], resolver.ResolutionContext()).status == "UNKNOWN"


def test_an_environment_read_of_a_credential_key_is_configuration_never_a_version(
    google_pack: registry.LoadedPack,
) -> None:
    read = make_evidence(
        run_id=RUN_ID,
        proof_scope_hash=SCOPE_HASH,
        claim_type="surface_reference",
        observer="structure",
        repo_sha=None,
        path="src/api.ts",
        line_start=38,
        line_end=38,
        source_hash=EMPTY_SHA256,
        value={
            "node_kind": "environment_read",
            "binding": "GOOGLE_ADS_DEVELOPER_TOKEN",
            "binding_versions": [],
            "first_party_definition": None,
            "environment_read_shape": "process.env",
        },
        provider_subject="GOOGLE_ADS_DEVELOPER_TOKEN",
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="RAW",
    )
    context = resolver.ResolutionContext(surface=google_pack.surface)
    resolution = resolve("surface_reference", [read], context)
    assert resolution.status == "NOT_AFFECTED_WITH_EVIDENCE"
    assert "configuration, never a version" in resolution.reason


def test_an_environment_read_of_a_version_key_is_a_runtime_unknown(
    google_pack: registry.LoadedPack,
) -> None:
    read = make_evidence(
        run_id=RUN_ID,
        proof_scope_hash=SCOPE_HASH,
        claim_type="surface_reference",
        observer="structure",
        repo_sha=None,
        path="src/api.ts",
        line_start=2,
        line_end=2,
        source_hash=EMPTY_SHA256,
        value={
            "node_kind": "environment_read",
            "binding": "GOOGLE_ADS_API_VERSION",
            "binding_versions": [],
            "first_party_definition": None,
            "environment_read_shape": "process.env",
        },
        provider_subject="GOOGLE_ADS_API_VERSION",
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="RAW",
    )
    context = resolver.ResolutionContext(surface=google_pack.surface)
    resolution = resolve("surface_reference", [read], context)
    assert resolution.status == "UNKNOWN"
    assert resolution.close_with == resolver.CLOSE_WITH_RUNTIME_CONFIG


def test_a_config_file_declaration_of_a_non_version_key_is_explained_by_its_role(
    google_pack: registry.LoadedPack,
) -> None:
    declared = text_record(".env.example", 196, "surface_reference", "GOOGLE_ADS_CLIENT_ID")
    context = resolver.ResolutionContext(surface=google_pack.surface)
    assert resolve("surface_reference", [declared], context).status == "NOT_AFFECTED_WITH_EVIDENCE"
    version_key = text_record(
        ".env.example", 197, "config_reference", "GOOGLE_ADS_API_VERSION", kind="config_key"
    )
    assert resolve("config_reference", [version_key], context).status == "UNKNOWN"


def test_a_generic_sink_with_no_provider_surface_anywhere_on_its_chain_is_explained(
    google_pack: registry.LoadedPack,
) -> None:
    sink = skeleton_record(
        "apps/hooks/route.ts",
        22,
        "MISSING_ARGUMENT(discountCode)",
        [],
        paths=[
            {
                "path": "apps/hooks/helpers.ts",
                "skeleton": {"fragments": ["/webhook/orders"], "holes": ["payload"]},
                "terminal": "MISSING_ARGUMENT(discountCode)",
            }
        ],
    )
    context = resolver.ResolutionContext(
        surface=google_pack.surface, file_contexts={"apps/hooks/route.ts": "none"}
    )
    resolution = resolve("request_text", [sink], context)
    assert resolution.status == "NOT_AFFECTED_WITH_EVIDENCE"
    assert "apps/hooks/helpers.ts" in resolution.reason


def test_a_generic_sink_whose_fragment_names_the_provider_stays_unknown(
    google_pack: registry.LoadedPack,
) -> None:
    sink = skeleton_record(
        "apps/hooks/route.ts",
        22,
        "MISSING_ARGUMENT(customer)",
        ["https://googleads.googleapis.com/"],
    )
    context = resolver.ResolutionContext(surface=google_pack.surface)
    assert resolve("request_text", [sink], context).status == "UNKNOWN"


def test_a_generic_sink_in_a_file_with_direct_context_stays_unknown(
    google_pack: registry.LoadedPack,
) -> None:
    sink = skeleton_record("src/api.ts", 22, "UNRESOLVED_SYMBOL(path)", [])
    context = resolver.ResolutionContext(
        surface=google_pack.surface, file_contexts={"src/api.ts": "direct"}
    )
    assert resolve("request_text", [sink], context).status == "UNKNOWN"


def test_a_recall_match_on_a_structurally_resolved_line_is_explained_once() -> None:
    skeleton = skeleton_record("src/api.ts", 274, "CONTRACT_VALIDATION_DEFERRED", ["SELECT a"])
    recall = text_record(
        "src/api.ts",
        274,
        "request_text",
        "customer",
        kind="request_resource",
        resource="customer",
        resource_known=True,
        file_context="direct",
    )
    context = resolver.resolution_context([skeleton, recall], None, None)
    assert context.structural_lines == {("src/api.ts", 274): skeleton["id"]}
    resolution = resolve("request_text", [recall], context)
    assert resolution.status == "NOT_AFFECTED_WITH_EVIDENCE"
    assert skeleton["id"] in resolution.reason


def test_a_skeleton_the_oracle_accepts_in_the_lattice_is_affected() -> None:
    skeleton = skeleton_record("src/api.ts", 274, "CONTRACT_VALIDATION_DEFERRED", ["SELECT a"])
    context = resolver.ResolutionContext(
        validations={
            skeleton["id"]: {
                "accepted": ["v22", "v25"],
                "rejected": {},
                "undecided": {},
                "authority": "CATALOG",
            }
        }
    )
    resolution = resolve("request_text", [skeleton], context)
    assert resolution.status == "AFFECTED"
    assert "CATALOG authority in v22, v25" in resolution.reason


def test_a_skeleton_the_oracle_rejects_everywhere_stays_unknown_naming_the_reason() -> None:
    skeleton = skeleton_record("src/api.ts", 274, "CONTRACT_VALIDATION_DEFERRED", ["SELECT a"])
    context = resolver.ResolutionContext(
        validations={
            skeleton["id"]: {
                "accepted": [],
                "rejected": {"v25": "field campaign.gone is not selectable"},
                "undecided": {},
                "authority": None,
            }
        }
    )
    resolution = resolve("request_text", [skeleton], context)
    assert resolution.status == "UNKNOWN"
    assert "campaign.gone" in resolution.reason
    assert resolution.close_with


def test_a_skeleton_nobody_judged_keeps_the_deferred_instruction() -> None:
    skeleton = skeleton_record("src/api.ts", 274, "CONTRACT_VALIDATION_DEFERRED", ["SELECT a"])
    resolution = resolve("request_text", [skeleton], resolver.ResolutionContext())
    assert resolution.status == "UNKNOWN"
    assert "CONTRACT_VALIDATION_DEFERRED" in resolution.reason
