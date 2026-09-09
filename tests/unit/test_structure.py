from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hubbleops.app.cli import scan_repository
from hubbleops.app.registry import load_pack
from hubbleops.closure.source_closure import Classification
from hubbleops.closure.source_closure import build as build_closure
from hubbleops.core.errors import ToolingFailed, ToolingMissing, ToolingTimeout
from hubbleops.core.observer import StructuralRule
from hubbleops.graph.imports import AstGrep, SourceRange
from hubbleops.observe import structure
from hubbleops.observe.structure import ValuePath, rules_hash

FIXTURES = Path("tests/fixtures/phase3")


@pytest.fixture(scope="module")
def imported() -> Any:
    return scan_repository(FIXTURES / "python_imported_wrapper" / "repo", load_pack("google_ads"))


@pytest.fixture(scope="module")
def formatted() -> Any:
    return scan_repository(FIXTURES / "python_rest_query_wrapper" / "repo", load_pack("google_ads"))


@pytest.fixture(scope="module")
def typescript() -> Any:
    return scan_repository(FIXTURES / "typescript_version_symbol" / "repo", load_pack("google_ads"))


@pytest.fixture(scope="module")
def wrappers() -> Any:
    return scan_repository(FIXTURES / "wrapper_patterns" / "repo", load_pack("google_ads"))


@pytest.fixture(scope="module")
def language_scoped() -> Any:
    return scan_repository(FIXTURES / "language_scoped_carrier" / "repo", load_pack("google_ads"))


@pytest.fixture(scope="module")
def npm_cross_evidence() -> Any:
    return scan_repository(FIXTURES / "npm_lock_cross_evidence" / "repo", load_pack("google_ads"))


def structural(result: Any, claim_type: str) -> list[dict[str, Any]]:
    return [
        item
        for item in result.ledger.evidence
        if item["observer"] == "structure" and item["claim_type"] == claim_type
    ]


def test_imported_wrapper_resolves_query_and_endpoint_version(imported: Any) -> None:
    requests = structural(imported, "request_text")
    versions = structural(imported, "call_version")
    assert len(requests) == 1
    assert requests[0]["path"] == "scheduled_report.py"
    assert requests[0]["value"]["resolution"] == "CONTRACT_VALIDATION_DEFERRED"
    assert requests[0]["value"]["skeleton"] == {
        "fragments": ["SELECT campaign.id FROM campaign"],
        "holes": [],
    }
    assert requests[0]["value"]["wrapper_chain"] == [
        "sink requests.post argument 1 at provider_bridge.py:6",
        "call submit_report carries query at scheduled_report.py:5",
    ]
    assert {item["provider_subject"] for item in versions} == {"v24"}


def test_formatted_request_retains_named_hole_and_resolves_constant(formatted: Any) -> None:
    request = structural(formatted, "request_text")[0]
    assert request["value"]["resolution"] == "UNKNOWN_QUERY_HOLE"
    assert request["value"]["skeleton"]["holes"] == ["day_count"]
    assert {item["provider_subject"] for item in structural(formatted, "call_version")} == {"v19"}


def test_python_format_request_retains_a_named_hole(tmp_path: Path) -> None:
    (tmp_path / "formatted.py").write_text(
        "def run(field):\n"
        '    query = "SELECT {} FROM campaign".format(field)\n'
        "    return client.search(query)\n",
        encoding="utf-8",
    )
    result = scan_repository(tmp_path, load_pack("google_ads"))
    requests = structural(result, "request_text")
    assert len(requests) == 1
    assert requests[0]["value"]["skeleton"] == {
        "fragments": ["SELECT ", " FROM campaign"],
        "holes": ["format_0"],
    }
    assert requests[0]["value"]["resolution"] == "UNKNOWN_QUERY_HOLE"


@pytest.mark.parametrize(("extension", "declaration"), (("js", "field"), ("ts", "field: string")))
def test_javascript_family_template_request_retains_hole(
    tmp_path: Path, extension: str, declaration: str
) -> None:
    (tmp_path / f"template.{extension}").write_text(
        f"function run({declaration}) {{\n"
        "  const query = `SELECT campaign.id FROM campaign WHERE ${field}`;\n"
        "  return client.search(query);\n"
        "}\n",
        encoding="utf-8",
    )
    result = scan_repository(tmp_path, load_pack("google_ads"))
    requests = structural(result, "request_text")
    assert len(requests) == 1
    assert requests[0]["value"]["skeleton"] == {
        "fragments": ["SELECT campaign.id FROM campaign WHERE ", ""],
        "holes": ["field"],
    }
    assert requests[0]["value"]["resolution"] == "UNKNOWN_QUERY_HOLE"


def test_typescript_imported_version_reaches_computed_carrier(typescript: Any) -> None:
    versions = structural(typescript, "call_version")
    assert {item["provider_subject"] for item in versions} == {"v24"}
    assert any(
        "import API_RELEASE" in " ".join(item["value"]["paths"][0]["hops"]) for item in versions
    )


def test_compositional_summaries_change_cost_but_never_a_single_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def evidence_of(scan: Any) -> list[dict[str, Any]]:
        return [record for record in scan.ledger.evidence if record["observer"] == "structure"]

    with_cache = evidence_of(
        scan_repository(FIXTURES / "wrapper_patterns" / "repo", load_pack("google_ads"))
    )

    def store_nothing(
        self: structure.ResolutionCache,
        key: tuple[str, int, str, str],
        hops: tuple[str, ...],
        values: tuple[ValuePath, ...],
    ) -> None:
        return None

    monkeypatch.setattr(structure.ResolutionCache, "store", store_nothing)
    without_cache = evidence_of(
        scan_repository(FIXTURES / "wrapper_patterns" / "repo", load_pack("google_ads"))
    )

    assert with_cache
    assert with_cache == without_cache


def test_a_summary_is_never_reused_for_a_cycle_or_an_exhausted_walk() -> None:
    cache = structure.ResolutionCache()
    source_range = SourceRange(0, 1, 1, 1)
    key = ("a.py", 0, "value", "owner")

    for terminal in ("AMBIGUOUS_CYCLE", "RESOLUTION_BUDGET_EXHAUSTED(20000)"):
        cache.store(key, (), (ValuePath("a.py", source_range, (), (), None, terminal, ()),))
        assert cache.lookup(key, ()) is None

    cache.store(key, (), (ValuePath("a.py", source_range, ("v22",), (), "v22", "LITERAL", ()),))
    assert cache.lookup(key, ()) is not None


def test_a_summary_whose_chain_repeats_its_own_entry_is_never_stored() -> None:
    cache = structure.ResolutionCache()
    source_range = SourceRange(0, 1, 1, 1)
    key = ("a.py", 0, "value", "owner")
    entry = ("carrier a.py:1",)
    doubled = ValuePath(
        "a.py",
        source_range,
        ("v22",),
        (),
        "v22",
        "LITERAL",
        (*entry, "left leg", *entry, "right leg"),
    )

    cache.store(key, entry, (doubled,))

    assert cache.lookup(key, ("a different entry",)) is None


def test_a_summary_whose_chain_does_not_extend_its_entry_is_never_stored() -> None:
    cache = structure.ResolutionCache()
    source_range = SourceRange(0, 1, 1, 1)
    key = ("a.py", 0, "value", "owner")
    unrelated = ValuePath(
        "a.py", source_range, ("v22",), (), "v22", "LITERAL", ("some other chain",)
    )

    cache.store(key, ("carrier a.py:1",), (unrelated,))

    assert cache.lookup(key, ("carrier a.py:1",)) is None


def test_a_reused_summary_carries_the_calling_wrapper_chain_not_the_cached_one() -> None:
    cache = structure.ResolutionCache()
    source_range = SourceRange(0, 1, 1, 1)
    key = ("a.py", 0, "value", "owner")
    stored = ValuePath(
        "a.py", source_range, ("v22",), (), "v22", "LITERAL", ("entry hop", "inner hop")
    )

    cache.store(key, ("entry hop",), (stored,))
    reused = cache.lookup(key, ("a different entry hop",))

    assert reused is not None
    assert reused[0].hops == ("a different entry hop", "inner hop")


def test_an_exhausted_resolution_budget_yields_unknown_and_never_a_resolved_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(structure, "RESOLUTION_BUDGET_STEPS", 1)
    result = scan_repository(FIXTURES / "wrapper_patterns" / "repo", load_pack("google_ads"))

    requests = [
        record
        for record in result.ledger.evidence
        if record["claim_type"] == "request_text" and record["observer"] == "structure"
    ]
    statuses = {
        candidate["status"]
        for candidate in result.ledger.candidates
        if any(record["id"] in candidate["evidence_ids"] for record in requests)
    }

    assert requests
    assert all(
        record["value"]["resolution"].startswith("RESOLUTION_BUDGET_EXHAUSTED")
        for record in requests
    )
    assert not any(
        record["value"]["resolution"] == "CONTRACT_VALIDATION_DEFERRED" for record in requests
    )
    assert "NOT_AFFECTED_WITH_EVIDENCE" not in statuses
    assert result.ledger.counts()["unexplained"] == 0


def test_a_wrapper_chain_deeper_than_five_hops_resolves_instead_of_capping(
    wrappers: Any,
) -> None:
    requests = structural(wrappers, "request_text")

    assert requests
    assert not any(item["value"]["resolution"].startswith("MAX_DEPTH") for item in requests)
    assert not any(item["value"]["resolution"].startswith("RESOLUTION_BUDGET") for item in requests)
    chains = [" ".join(item["value"]["wrapper_chain"]) for item in requests]
    assert any("depth_6" in chain for chain in chains)


def test_wrapper_corpus_preserves_depth_configuration_and_boundary_unknowns(
    wrappers: Any,
) -> None:
    requests = structural(wrappers, "request_text")
    versions = structural(wrappers, "call_version")
    configuration = structural(wrappers, "config_reference")
    boundaries = structural(wrappers, "external_boundary")
    assert {item["provider_subject"] for item in versions} == {None, "v24"}
    version_candidate = next(
        candidate
        for candidate in wrappers.ledger.candidates
        if any(item["id"] in candidate["evidence_ids"] for item in versions)
    )
    assert version_candidate["status"] == "UNKNOWN"
    assert {item["provider_subject"] for item in configuration} == {"GOOGLE_ADS_API_VERSION"}
    chains = [" ".join(item["value"]["wrapper_chain"]) for item in requests]
    for wrapper in (
        "Gateway().send",
        "adapter.execute",
        "decorated_wrapper",
        "async_wrapper",
        "decorator traced",
        "factory factory_registry",
        "registration REGISTRY",
    ):
        assert any(wrapper in chain for chain in chains)
    assert len(boundaries) == 1
    assert boundaries[0]["value"]["callee"] == "publish"


def test_language_scoped_carrier_does_not_assign_python_version(
    language_scoped: Any,
) -> None:
    affected_versions = [
        candidate
        for candidate in language_scoped.ledger.by_status("AFFECTED")
        if language_scoped.ledger.location_of(candidate).claim_type == "call_version"
    ]
    assert affected_versions == []


def test_ambiguous_version_targets_stay_grouped_and_unknown(tmp_path: Path) -> None:
    source = (
        "def wrapped(version):\n"
        '    client.get_service("GoogleAdsService", version=version)\n\n'
        "def old_call():\n"
        '    wrapped("v22")\n\n'
        "def new_call():\n"
        '    wrapped("v24")\n'
    )
    (tmp_path / "ambiguous.py").write_text(source, encoding="utf-8")
    result = scan_repository(tmp_path, load_pack("google_ads"))
    versions = structural(result, "call_version")
    candidates = [
        candidate
        for candidate in result.ledger.candidates
        if any(item["id"] in candidate["evidence_ids"] for item in versions)
    ]
    assert len(candidates) == 1
    assert candidates[0]["status"] == "UNKNOWN"
    assert "v22, v24" in candidates[0]["reason"]


def test_equivalent_version_targets_resolve_without_false_ambiguity(tmp_path: Path) -> None:
    source = (
        "def wrapped(version):\n"
        '    client.get_service("GoogleAdsService", version=version)\n\n'
        "def first_call():\n"
        '    wrapped("v22")\n\n'
        "def second_call():\n"
        '    wrapped("v22")\n'
    )
    (tmp_path / "equivalent.py").write_text(source, encoding="utf-8")
    result = scan_repository(tmp_path, load_pack("google_ads"))
    versions = structural(result, "call_version")
    candidates = [
        candidate
        for candidate in result.ledger.candidates
        if any(item["id"] in candidate["evidence_ids"] for item in versions)
    ]
    assert len(candidates) == 1
    assert candidates[0]["status"] == "AFFECTED"


def test_lock_closes_manifest_range_but_not_target_compatibility(
    npm_cross_evidence: Any,
) -> None:
    sdk = [
        candidate
        for candidate in npm_cross_evidence.ledger.by_status("AFFECTED")
        if npm_cross_evidence.ledger.location_of(candidate).claim_type == "sdk_installed"
    ]
    assert len(sdk) == 1
    assert "24.1.0" in sdk[0]["reason"]
    assert npm_cross_evidence.closure_summary()["pack"]["target"].startswith("UNKNOWN")


def test_two_boundary_payloads_in_one_file_have_distinct_candidates(tmp_path: Path) -> None:
    (tmp_path / "bridge.py").write_text(
        "def send():\n"
        '    publish("SELECT campaign.id FROM campaign")\n'
        '    publish("SELECT customer.id FROM customer")\n',
        encoding="utf-8",
    )
    result = scan_repository(tmp_path, load_pack("google_ads"))
    evidence = structural(result, "external_boundary")
    assert len(evidence) == 2
    identities = {
        candidate["id"]
        for candidate in result.ledger.candidates
        if any(item["id"] in candidate["evidence_ids"] for item in evidence)
    }
    assert len(identities) == 2


def test_generated_markers_are_artifact_header_scoped() -> None:
    closure = build_closure(FIXTURES / "generated_marker_scope" / "repo")
    entries = closure.by_path()
    assert entries["usage_notes.md"].classification is Classification.INSIDE
    assert entries["emitted/api_types.ts"].classification is Classification.GENERATED


def test_rule_hash_is_order_independent_and_changes_with_bytes(tmp_path: Path) -> None:
    first = tmp_path / "first.yml"
    second = tmp_path / "second.yml"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    one = StructuralRule("a", "python", first, hashlib.sha256(b"first").hexdigest())
    two = StructuralRule("b", "typescript", second, hashlib.sha256(b"second").hexdigest())
    assert rules_hash((one, two)) == rules_hash((two, one))
    changed = StructuralRule("a", "python", first, hashlib.sha256(b"changed").hexdigest())
    assert rules_hash((one, two)) != rules_hash((changed, two))


def test_missing_ast_grep_fails_closed_without_force(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    with pytest.raises(ToolingMissing, match="TOOLING_MISSING"):
        scan_repository(
            tmp_path,
            load_pack("_mock"),
            ast_grep_executable="definitely-not-an-ast-grep-binary",
        )


def test_incompatible_ast_grep_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    completed = subprocess.CompletedProcess(
        args=["ast-grep", "--version"],
        returncode=0,
        stdout="ast-grep 0.44.0\n",
        stderr="",
    )

    def incompatible(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return completed

    monkeypatch.setattr(subprocess, "run", incompatible)
    with pytest.raises(ToolingMissing, match="incompatible"):
        AstGrep().version()


def test_force_accounts_for_every_inside_file(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "extensionless").write_text("provider residue\n", encoding="utf-8")
    result = scan_repository(
        tmp_path,
        load_pack("_mock"),
        force=True,
        ast_grep_executable="definitely-not-an-ast-grep-binary",
    )
    inside = [entry for entry in result.closure.entries if entry.classification.value == "INSIDE"]
    forced = structural(result, "file_unscanned")
    assert {item["path"] for item in forced} == {entry.path for entry in inside}
    assert result.structural_coverage.to_mapping()["python"] == {
        "supported": 0,
        "unscanned": 1,
        "unsupported": 0,
    }


def test_parse_failure_unscans_only_the_broken_file(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text("if if\n", encoding="utf-8")
    (tmp_path / "valid.py").write_text(
        'client.search("SELECT campaign.id FROM campaign")\n',
        encoding="utf-8",
    )
    result = scan_repository(tmp_path, load_pack("google_ads"))
    unscanned = structural(result, "file_unscanned")
    assert {item["path"] for item in unscanned} == {"broken.py"}
    assert result.structural_coverage.to_mapping()["python"] == {
        "supported": 1,
        "unscanned": 1,
        "unsupported": 0,
    }


def test_unknown_and_extensionless_languages_are_explicit(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "extensionless").write_text("provider residue\n", encoding="utf-8")
    (tmp_path / "service.rb").write_text("puts 'provider residue'\n", encoding="utf-8")
    result = scan_repository(tmp_path, load_pack("_mock"))
    unsupported = structural(result, "structure_unsupported")
    assert {item["path"] for item in unsupported} == {"extensionless", "service.rb"}
    assert result.structural_coverage.to_mapping()["unknown"]["unsupported"] == 1
    assert result.structural_coverage.to_mapping()["ruby"]["unsupported"] == 1


def test_malformed_ast_grep_json_is_rejected(tmp_path: Path) -> None:
    runner = AstGrep()
    with pytest.raises(ToolingFailed, match="malformed JSON"):
        runner.decode("not-json", tmp_path, "call")


def test_ast_grep_timeout_is_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired("ast-grep", 30)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(ToolingTimeout, match="UNKNOWN"):
        AstGrep().version()
