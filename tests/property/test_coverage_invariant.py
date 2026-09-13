from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from hubbleops.app import registry
from hubbleops.app.cli import ScanResult
from hubbleops.closure.source_closure import Classification
from hubbleops.core.surface import SurfaceSpec
from hubbleops.observe.text import patterns_for
from hubbleops.packs.google_ads.contract import query_resources
from tests.integration.test_coverage_fixtures import coverage_repos
from tests.support import fixture_repos

Scan = Callable[[Path, registry.LoadedPack], ScanResult]

TEXT_SUFFIXES = frozenset(
    {".ts", ".tsx", ".js", ".jsx", ".py", ".php", ".go", ".java", ".cs", ".rb", ".json", ".yaml"}
)


UNCONDITIONAL_KINDS = frozenset(
    {"identifier", "host", "package", "config_key", "version_carrier", "adjacent_contract"}
)


def surface_hits(surface: SurfaceSpec, content: str) -> set[str]:
    found: set[str] = set()
    for pattern in patterns_for(surface):
        if pattern.kind not in UNCONDITIONAL_KINDS and not (
            pattern.kind == "contract_surface" and not pattern.gated
        ):
            continue
        if pattern.fixed:
            if pattern.pattern in content:
                found.add(pattern.name)
            continue
        try:
            compiled = re.compile(pattern.pattern)
        except re.error:
            continue
        if compiled.search(content):
            found.add(pattern.name)
    return found


@pytest.mark.parametrize(
    "fixture", [*coverage_repos(), *fixture_repos()], ids=lambda path: path.name
)
def test_no_file_matching_the_recall_surface_produces_zero_candidates(
    fixture: Path, google_pack: registry.LoadedPack, scan: Scan
) -> None:
    repo = fixture / "repo"
    result = scan(repo, google_pack)
    book = result.ledger
    covered = {
        book.location_of(candidate).display().split(":", 1)[0]
        for candidate in book.ordered_candidates()
    }
    silent: list[str] = []
    for entry in result.closure.entries:
        if entry.classification is not Classification.INSIDE:
            continue
        path = Path(entry.path)
        if path.suffix not in TEXT_SUFFIXES:
            continue
        source = repo / entry.path
        try:
            content = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        hits = surface_hits(google_pack.surface, content)
        if hits and entry.path not in covered:
            silent.append(f"{entry.path} matches {sorted(hits)} but produced no candidate")
    assert not silent, "\n".join(silent)


def test_a_path_beyond_the_legacy_windows_limit_is_still_read(tmp_path: Path) -> None:
    import os

    from hubbleops.closure import source_closure
    from hubbleops.closure.source_closure import system_path

    deep = tmp_path
    while len(str(deep)) < 300:
        deep = deep / "nested_directory_segment"
    os.makedirs(system_path(deep), exist_ok=True)
    target = deep / "client.ts"
    body = 'const HOST = "https://googleads.googleapis.com/v22";\n'
    with open(system_path(target), "w", encoding="utf-8") as handle:
        handle.write(body)
    relative = target.relative_to(tmp_path).as_posix()

    closure = source_closure.build(tmp_path)
    entry = closure.by_path().get(relative)
    assert entry is not None, (
        f"a {len(str(target))} character path was never enumerated; the closure and the "
        "searcher would disagree and the scan would stop"
    )
    assert entry.blob_sha, "the deep file was enumerated but never read"


@pytest.mark.parametrize("module", [".", "..", "...", "", ".sibling"])
def test_a_relative_import_never_crashes_module_resolution(module: str) -> None:
    from hubbleops.graph.imports import resolve_specifier

    assert resolve_specifier("pkg/mod.py", module, "python", ("pkg/mod.py",)) is None


def test_every_wire_carrier_is_language_independent(
    google_pack: registry.LoadedPack, mock_pack: registry.LoadedPack
) -> None:
    for pack in (google_pack, mock_pack):
        for carrier in pack.surface.version_carriers:
            if carrier.scope != "wire":
                continue
            assert carrier.languages == ("any",), (
                f"{pack.name}: wire carrier {carrier.name!r} is language-gated to "
                f"{list(carrier.languages)}"
            )
            assert carrier.applies_to("go")
            assert carrier.applies_to("typescript")


def test_a_wire_carrier_that_restricts_languages_is_rejected() -> None:
    from hubbleops.core.errors import SurfaceSpecInvalid

    with pytest.raises(SurfaceSpecInvalid, match="wire-scope"):
        SurfaceSpec.from_mapping(
            {
                "name": "probe",
                "identifiers": ["probe"],
                "hosts": ["api.probe.test"],
                "package_names": ["probe"],
                "version_carriers": [
                    {
                        "name": "namespace",
                        "regex": r"probe\.(?P<version>v[0-9]+)",
                        "slot": "sdk_default",
                        "languages": ["python"],
                        "scope": "wire",
                    }
                ],
                "request_languages": [],
                "sink_argument_positions": [],
                "config_env_keys": [],
            }
        )


def test_the_query_resource_set_is_derived_from_the_catalog(
    google_pack: registry.LoadedPack,
) -> None:
    languages = [item for item in google_pack.surface.request_languages if item.shapes]
    assert languages, "the pack declares no grammar-level request language shape"
    derived = set(query_resources())
    for language in languages:
        assert set(language.known_resources) == derived, (
            "the request-language resource set no longer equals the catalog's; a "
            "hand-maintained list has been reintroduced"
        )


def test_a_declared_request_shape_must_capture_its_resource() -> None:
    from hubbleops.core.errors import SurfaceSpecInvalid

    with pytest.raises(SurfaceSpecInvalid, match="resource"):
        SurfaceSpec.from_mapping(
            {
                "name": "probe",
                "identifiers": ["probe"],
                "hosts": ["api.probe.test"],
                "package_names": ["probe"],
                "version_carriers": [],
                "request_languages": [{"name": "q", "anchors": [], "shapes": [r"FROM\s+\w+"]}],
                "sink_argument_positions": [],
                "config_env_keys": [],
            }
        )


def test_every_shipped_pack_declares_its_adjacent_contracts(
    google_pack: registry.LoadedPack, mock_pack: registry.LoadedPack
) -> None:
    for pack in (google_pack, mock_pack):
        assert pack.surface.adjacent_contracts, (
            f"{pack.name} declares no adjacent contracts; a neighbouring API would be "
            "silently ignored rather than excluded with evidence"
        )
        assert pack.surface.contract_surfaces, (
            f"{pack.name} declares no contract surfaces; non-version contract surface would "
            "produce no candidate at all"
        )
