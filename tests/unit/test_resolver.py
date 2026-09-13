from __future__ import annotations

from typing import Any

import pytest

from hubbleops.core.canonical import EMPTY_SHA256, content_id
from hubbleops.core.errors import PathNotInClosure, UnknownClaimType
from hubbleops.core.evidence import make_evidence
from hubbleops.core.surface import SurfaceSpec
from hubbleops.observe import resolver
from hubbleops.observe.text import patterns_for

RUN_ID = content_id({"run": 1})
SCOPE_HASH = content_id({"scope": 1})

FROZEN_TABLES = {
    "sdk_installed": ("lock", "manifest"),
    "call_version": ("per_call", "client_init", "sdk_default", "UNKNOWN"),
    "production_version": ("telemetry", "sentinel", "dynamic", "static"),
    "request_text": ("dynamic", "structure", "text"),
}


CLOSURE_TREE = {
    "src/app.py": "INSIDE",
    "package.json": "INSIDE",
    "package-lock.json": "INSIDE",
    "vendor/sdk/composer.json": "VENDORED",
}


def record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "run_id": RUN_ID,
        "proof_scope_hash": SCOPE_HASH,
        "claim_type": "call_version",
        "observer": "text",
        "repo_sha": None,
        "path": "src/app.py",
        "line_start": 7,
        "line_end": 7,
        "source_hash": EMPTY_SHA256,
        "value": {"pattern": "carrier", "slot": "per_call"},
        "provider_subject": "v22",
        "dependency_context_hash": None,
        "derivation": "OBSERVED",
        "confidence": "RAW",
    }
    base.update(overrides)
    return make_evidence(**base)


def test_the_frozen_claim_tables_are_present_verbatim() -> None:
    for claim_type, table in FROZEN_TABLES.items():
        assert resolver.CLAIM_PRECEDENCE[claim_type] == table


def test_every_claim_type_has_its_own_table() -> None:
    assert set(resolver.CLAIM_PRECEDENCE) == set(resolver.HANDLERS) | {"production_version"}


def test_rank_reads_only_the_table_of_the_claim_it_is_given() -> None:
    installed = record(
        claim_type="sdk_installed",
        observer="text",
        value={
            "state": "PRESENT",
            "ecosystem": "python",
            "package": "sdk",
            "version": "1.0",
            "spec": None,
            "source_kind": "lock",
        },
    )
    assert resolver.rank(installed) == 0

    carried = record(value={"pattern": "c", "slot": "sdk_default", "source_kind": "lock"})
    assert resolver.rank(carried) == 2


def test_every_claim_type_the_observers_emit_has_a_rule(google_pack: Any) -> None:
    emitted = {pattern.claim_type for pattern in patterns_for(google_pack.surface)}
    emitted |= {"sdk_installed", "dependency_state", "file_unscanned", "external_boundary"}
    assert emitted <= set(resolver.CLAIM_PRECEDENCE)
    assert emitted <= set(resolver.HANDLERS)


def test_an_unregistered_claim_type_raises_rather_than_defaulting() -> None:
    unregistered = record(claim_type="invented_claim")
    with pytest.raises(UnknownClaimType):
        resolver.claim_key(unregistered)
    with pytest.raises(UnknownClaimType):
        resolver.resolve_claim("invented_claim", [unregistered], CLOSURE_TREE)


def test_a_lock_outranks_a_manifest_for_the_installed_sdk() -> None:
    manifest = record(
        claim_type="sdk_installed",
        observer="deps",
        path="package.json",
        provider_subject="sdk",
        value={
            "state": "PRESENT",
            "ecosystem": "javascript",
            "package": "sdk",
            "version": None,
            "spec": "^17.0.0",
            "source_kind": "manifest",
        },
    )
    lock = record(
        claim_type="sdk_installed",
        observer="deps",
        path="package-lock.json",
        provider_subject="sdk",
        value={
            "state": "PRESENT",
            "ecosystem": "javascript",
            "package": "sdk",
            "version": "17.1.0",
            "spec": None,
            "source_kind": "lock",
        },
    )
    resolution = resolver.resolve_claim("sdk_installed", [manifest, lock], CLOSURE_TREE)
    assert resolution.status == "AFFECTED"
    assert resolution.winner_id == lock["id"]
    assert "17.1.0" in resolution.reason


def test_a_per_call_override_outranks_an_sdk_default() -> None:
    default = record(value={"pattern": "namespace", "slot": "sdk_default"}, provider_subject="v21")
    override = record(
        line_start=7,
        line_end=7,
        value={"pattern": "call", "slot": "per_call"},
        provider_subject="v22",
    )
    resolution = resolver.resolve_claim("call_version", [default, override], CLOSURE_TREE)
    assert resolution.status == "AFFECTED"
    assert "v22" in resolution.reason


def test_disagreeing_literals_at_one_location_stay_unknown() -> None:
    first = record(provider_subject="v22")
    second = record(provider_subject="v23", value={"pattern": "other", "slot": "per_call"})
    resolution = resolver.resolve_claim("call_version", [first, second], CLOSURE_TREE)
    assert resolution.status == "UNKNOWN"
    assert resolution.close_with


def test_a_version_carrier_without_a_literal_is_unknown_not_affected() -> None:
    resolution = resolver.resolve_claim(
        "call_version", [record(provider_subject=None)], CLOSURE_TREE
    )
    assert resolution.status == "UNKNOWN"
    assert resolution.close_with


def test_unscanned_and_unsupported_candidates_keep_specialized_open_states() -> None:
    unscanned = resolver.resolve_claim(
        "file_unscanned",
        [record(claim_type="file_unscanned", value={"reason": "binary"})],
        CLOSURE_TREE,
    )
    unsupported = resolver.resolve_claim(
        "structure_unsupported",
        [
            record(
                claim_type="structure_unsupported",
                observer="structure",
                value={"language": "ruby"},
            )
        ],
        CLOSURE_TREE,
    )
    assert (unscanned.status, unsupported.status) == ("UNSCANNED", "UNSUPPORTED")
    assert unscanned.close_with and unsupported.close_with


def test_non_inside_regions_are_excluded_with_the_classification_as_evidence() -> None:
    resolution = resolver.resolve_claim("call_version", [record()], {"src/app.py": "VENDORED"})
    assert resolution.status == "EXCLUDED_WITH_EVIDENCE"
    assert "VENDORED" in resolution.reason


def test_the_installed_sdk_is_still_claimed_inside_a_vendored_region() -> None:
    vendored = record(
        claim_type="sdk_installed",
        observer="deps",
        path="vendor/sdk/composer.json",
        provider_subject="sdk",
        value={
            "state": "PRESENT",
            "ecosystem": "php",
            "package": "sdk",
            "version": "22.1.0",
            "spec": None,
            "source_kind": "manifest",
        },
    )
    resolution = resolver.resolve_claim(
        "sdk_installed", [vendored], {"vendor/sdk/composer.json": "VENDORED"}
    )
    assert resolution.status == "AFFECTED"


def test_every_open_status_carries_a_closing_instruction() -> None:
    samples = [
        ("call_version", record(provider_subject=None)),
        ("file_unscanned", record(claim_type="file_unscanned", value={"reason": "binary"})),
        (
            "dependency_state",
            record(
                claim_type="dependency_state",
                observer="deps",
                value={"state": "NO_MANIFEST", "ecosystem": None},
            ),
        ),
        ("surface_reference", record(claim_type="surface_reference", provider_subject="sdk")),
        ("config_reference", record(claim_type="config_reference", provider_subject="KEY")),
        ("request_text", record(claim_type="request_text", provider_subject="lang")),
        ("external_boundary", record(claim_type="external_boundary", value={"reason": "symlink"})),
    ]
    for claim_type, sample in samples:
        resolution = resolver.resolve_claim(claim_type, [sample], CLOSURE_TREE)
        if resolution.status in ("UNKNOWN", "HUMAN_REQUIRED"):
            assert resolution.close_with, claim_type


def test_the_winner_is_stable_when_ranks_tie() -> None:
    first = record(provider_subject="v22")
    second = record(provider_subject="v22", value={"pattern": "z", "slot": "per_call"})
    assert resolver.winner([first, second]) == resolver.winner([second, first])


def test_a_location_bound_claim_off_the_closure_stops_rather_than_assuming_first_party() -> None:
    with pytest.raises(PathNotInClosure):
        resolver.resolve_claim("call_version", [record(path="ghost.py")], CLOSURE_TREE)


LATTICE = ("v19", "v25")
BARE_SURFACE = {
    "name": "bare",
    "identifiers": ["sdk"],
    "hosts": ["api.example.test"],
    "package_names": ["sdk"],
    "version_carriers": [],
    "request_languages": [],
    "sink_argument_positions": [],
    "config_env_keys": ["KEY"],
}


def stability_context(
    stability: dict[str, str], surface: SurfaceSpec | None = None
) -> resolver.ResolutionContext:
    return resolver.ResolutionContext(surface=surface, stability=stability, lattice=LATTICE)


def text_reference(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "claim_type": "surface_reference",
        "observer": "text",
        "provider_subject": "sdk",
        "value": {"pattern": "identifier", "kind": "identifier", "matches": ["sdk"]},
    }
    base.update(overrides)
    return record(**base)


def request_anchor(matches: list[str]) -> dict[str, Any]:
    return record(
        claim_type="request_text",
        observer="text",
        provider_subject="lang",
        value={"pattern": "lang#0", "kind": "request_language", "matches": matches},
    )


def test_a_stable_surface_reference_is_not_affected_with_the_lattice_as_evidence() -> None:
    resolution = resolver.resolve_claim(
        "surface_reference",
        [text_reference()],
        CLOSURE_TREE,
        context=stability_context({"sdk": "STABLE"}),
    )
    assert resolution.status == "NOT_AFFECTED_WITH_EVIDENCE"
    assert "v19…v25" in resolution.reason
    assert resolution.close_with is None


def test_a_reference_that_changes_inside_the_lattice_stays_unknown_naming_the_boundary() -> None:
    resolution = resolver.resolve_claim(
        "surface_reference",
        [text_reference()],
        CLOSURE_TREE,
        context=stability_context({"sdk": "CHANGES_AT:v23"}),
    )
    assert resolution.status == "UNKNOWN"
    assert resolution.close_with is not None
    assert "v23" in resolution.close_with
    assert resolver.CLOSE_WITH_CALL_SITE in resolution.close_with


def test_without_a_stability_table_the_surface_reference_resolves_as_before() -> None:
    plain = resolver.resolve_claim("surface_reference", [text_reference()], CLOSURE_TREE)
    empty = resolver.resolve_claim(
        "surface_reference", [text_reference()], CLOSURE_TREE, context=stability_context({})
    )
    assert plain == empty
    assert plain.status == "UNKNOWN"
    assert plain.close_with == resolver.CLOSE_WITH_CALL_SITE


def test_an_undecided_or_absent_subject_leaves_the_surface_reference_unknown() -> None:
    for table in ({"sdk": "UNDECIDED"}, {"other": "STABLE"}):
        resolution = resolver.resolve_claim(
            "surface_reference", [text_reference()], CLOSURE_TREE, context=stability_context(table)
        )
        assert resolution.status == "UNKNOWN"
        assert resolution.close_with == resolver.CLOSE_WITH_CALL_SITE


def test_a_request_anchor_whose_fields_are_all_stable_is_not_affected() -> None:
    resolution = resolver.resolve_claim(
        "request_text",
        [request_anchor(["m.clicks", "m.cost"])],
        CLOSURE_TREE,
        context=stability_context({"m.clicks": "STABLE", "m.cost": "STABLE"}),
    )
    assert resolution.status == "NOT_AFFECTED_WITH_EVIDENCE"
    assert "m.clicks" in resolution.reason and "m.cost" in resolution.reason


def test_a_request_anchor_with_one_changing_field_stays_unknown_naming_the_boundary() -> None:
    resolution = resolver.resolve_claim(
        "request_text",
        [request_anchor(["m.clicks", "m.cost"])],
        CLOSURE_TREE,
        context=stability_context({"m.clicks": "STABLE", "m.cost": "CHANGES_AT:v22,v24"}),
    )
    assert resolution.status == "UNKNOWN"
    assert resolution.close_with is not None
    assert "'m.cost' changes at v22,v24" in resolution.close_with


def test_a_request_anchor_without_matches_is_never_closed_by_the_lattice() -> None:
    resolution = resolver.resolve_claim(
        "request_text",
        [request_anchor([])],
        CLOSURE_TREE,
        context=stability_context({"lang": "STABLE"}),
    )
    assert resolution.status == "UNKNOWN"


def test_a_version_selecting_config_key_is_untouched_by_the_stability_table() -> None:
    surface = SurfaceSpec.from_mapping(BARE_SURFACE)
    key = record(
        claim_type="config_reference",
        observer="text",
        provider_subject="KEY",
        value={"pattern": "KEY", "kind": "config_key", "matches": ["KEY"]},
    )
    resolution = resolver.resolve_claim(
        "config_reference",
        [key],
        CLOSURE_TREE,
        context=stability_context({"KEY": "STABLE"}, surface),
    )
    assert resolution.status == "UNKNOWN"
    assert resolution.close_with == resolver.CLOSE_WITH_RUNTIME_CONFIG


def test_a_structure_adjudicated_reference_is_untouched_by_the_stability_table() -> None:
    adjudicated = record(
        claim_type="surface_reference",
        observer="structure",
        provider_subject="sdk",
        value={"node_kind": "identifier", "binding": "sdk.Client"},
    )
    resolution = resolver.resolve_claim(
        "surface_reference",
        [adjudicated, text_reference()],
        CLOSURE_TREE,
        context=stability_context({"sdk": "STABLE"}),
    )
    assert resolution.status == "UNKNOWN"
    assert resolution.winner_id == adjudicated["id"]
    assert resolution.close_with == resolver.CLOSE_WITH_CALL_SITE


def test_the_resolution_context_carries_the_stability_table_and_lattice() -> None:
    context = resolver.resolution_context(
        [], None, None, stability={"sdk": "STABLE"}, lattice=LATTICE
    )
    assert context.stability == {"sdk": "STABLE"}
    assert context.lattice == LATTICE
    assert resolver.resolution_context([], None, None).stability == {}
