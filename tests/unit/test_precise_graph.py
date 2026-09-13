from __future__ import annotations

import json
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from hubbleops.app.cli import scan_repository
from hubbleops.app.registry import load_pack
from hubbleops.core.precise import Occurrence, SymbolIndex, read_index
from hubbleops.graph.imports import AstGrep, Definition, ImportGraph, build
from hubbleops.graph.indexers import restrict_to_first_party
from hubbleops.observe.structure import PreciseSite, adjudicate_line

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "precise" / "ts_symbol_binding"
REPO = FIXTURE / "repo"
PATHS = {
    "typescript": (
        "app/notify.ts",
        "app/report.ts",
        "lib/index.ts",
        "lib/mail.ts",
        "lib/transport.ts",
        "lib/version.ts",
    )
}
FAKE_SCRIPT = """
import shutil
import sys

if sys.argv[2:] == ["--version"]:
    print("0.4.0")
    sys.exit(0)
shutil.copyfile(sys.argv[1], sys.argv[sys.argv.index("--output") + 1])
"""


def load_index() -> SymbolIndex:
    return read_index((FIXTURE / "index.scip").read_bytes())


def definition_named(graph: ImportGraph, path: str, name: str) -> Definition:
    return next(item for item in graph.definitions if item.path == path and item.name == name)


def fake_indexer(directory: Path, fixture: Path = FIXTURE / "index.scip") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "fake_indexer.py"
    script.write_text(FAKE_SCRIPT, encoding="utf-8")
    if sys.platform == "win32":
        executable = directory / "fake.cmd"
        executable.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" "{fixture}" %*\r\n', encoding="utf-8"
        )
        return executable
    executable = directory / "fake"
    executable.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{script}" "{fixture}" "$@"\n', encoding="utf-8"
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable


def test_callers_are_bound_by_symbol_when_an_index_covers_the_definition() -> None:
    precise = build(REPO, PATHS, AstGrep(), (load_index(),))
    loose = build(REPO, PATHS, AstGrep())
    transport = definition_named(precise, "lib/transport.ts", "send")
    mail = definition_named(precise, "lib/mail.ts", "send")
    assert {item.path for item in loose.callers(transport)} == {"app/notify.ts", "app/report.ts"}
    assert {item.path for item in precise.callers(transport)} == {"app/report.ts"}
    assert {item.path for item in precise.callers(mail)} == {"app/notify.ts"}
    report = next(item for item in precise.callers(transport) if item.path == "app/report.ts")
    assert precise.caller_binding(transport, report) == "symbol"
    assert loose.caller_binding(transport, report) == "name"


def test_a_call_whose_symbol_is_unknown_stays_a_caller_by_name() -> None:
    index = load_index()
    trimmed = SymbolIndex(
        index.tool,
        index.tool_version,
        index.documents,
        tuple(
            item
            for item in index.occurrences
            if not (item.path == "app/notify.ts" and item.start_line == 3)
        ),
    )
    graph = build(REPO, PATHS, AstGrep(), (trimmed,))
    transport = definition_named(graph, "lib/transport.ts", "send")
    assert {item.path for item in graph.callers(transport)} == {"app/notify.ts", "app/report.ts"}


def test_a_barrel_import_resolves_to_its_definition_only_by_symbol() -> None:
    precise = build(REPO, PATHS, AstGrep(), (load_index(),))
    loose = build(REPO, PATHS, AstGrep())
    definition = precise.precise_definition("app/report.ts", "API_RELEASE")
    assert definition is not None
    assert (definition.path, definition.start_line) == ("lib/version.ts", 0)
    assert loose.precise_definition("app/report.ts", "API_RELEASE") is None


def test_the_graph_serialization_names_the_index_it_used() -> None:
    graph = build(REPO, PATHS, AstGrep(), (load_index(),))
    payload = json.loads(graph.serialize())
    assert payload["indexes"] == [
        {
            "definitions": 16,
            "documents": 6,
            "occurrences": 35,
            "references": 19,
            "tool": "scip-typescript",
            "tool_version": "0.4.0",
        }
    ]


def occurrence(path: str, line: int, column: int, name: str, symbol: str, roles: int) -> Occurrence:
    return Occurrence(path, line, column, line, column + len(name), symbol, roles)


def adjudicate(index: SymbolIndex, path: str, line: bytes, identifier: str) -> Any:
    surface = load_pack("google_ads").surface
    return adjudicate_line(
        identifier=identifier,
        line=line,
        line_start=0,
        comments=(),
        imports=(),
        aliases={},
        defined=frozenset(),
        language="python",
        surface=surface,
        site=PreciseSite(index, path, 0),
    )


def test_a_symbol_without_a_definition_in_the_index_adjudicates_nothing() -> None:
    line = 'p = "é"; client = GoogleAdsClient()'.encode()
    column = len('p = "é"; client = ')
    symbol = (
        "scip-python python google-ads 30.0.0 `google.ads.googleads.v22.services`/GoogleAdsClient#"
    )
    index = SymbolIndex(
        "scip-python",
        "0.6.6",
        ("app.py",),
        (occurrence("app.py", 0, column, "GoogleAdsClient", symbol, 8),),
    )
    assert adjudicate(index, "app.py", line, "GoogleAdsClient") is None


def test_a_first_party_symbol_is_found_behind_a_non_ascii_prefix() -> None:
    line = 'p = "é"; client = GoogleAdsClient()'.encode()
    column = len('p = "é"; client = ')
    symbol = "scip-python python repository 0.0.0 `lib.client`/GoogleAdsClient#"
    index = SymbolIndex(
        "scip-python",
        "0.6.6",
        ("app.py", "lib/client.py"),
        (
            occurrence("app.py", 0, column, "GoogleAdsClient", symbol, 8),
            occurrence("lib/client.py", 4, 6, "GoogleAdsClient", symbol, 1),
        ),
    )
    verdict = adjudicate(index, "app.py", line, "GoogleAdsClient")
    assert verdict is not None
    assert verdict.binding_target == "lib/client.py"
    assert verdict.binding_site == "app.py:1"


def test_external_symbol_occurrences_are_dropped_before_the_index_is_used() -> None:
    index = read_index((FIXTURE / "index.scip").read_bytes())
    external = [item for item in index.occurrences if "@types/node" in item.symbol]
    assert external
    kept, dropped = restrict_to_first_party(index, ("symbol-binding-fixture",))
    assert dropped == len(external) + sum(
        1 for item in index.occurrences if " typescript " in item.symbol
    )
    assert not [item for item in kept.occurrences if "@types/node" in item.symbol]
    assert kept.counts()["documents"] == 6
    assert kept.definition_of(external[0].symbol) is None
    unchanged, none = restrict_to_first_party(kept, ("symbol-binding-fixture",))
    assert none == 0 and unchanged is kept
    emptied, all_dropped = restrict_to_first_party(index, ("nobody",))
    assert all_dropped == len(index.occurrences)
    assert emptied.documents == () and not emptied.covers("app/report.ts")


DISPATCH = Path(__file__).resolve().parents[1] / "fixtures" / "precise" / "ts_dispatch"
DISPATCH_PATHS = {
    "typescript": (
        "app/alias.ts",
        "app/legacy.ts",
        "app/mail.ts",
        "app/modern.ts",
        "lib/port.ts",
        "lib/transport.ts",
    )
}


def test_a_call_through_an_interface_stays_a_caller_of_the_implementation() -> None:
    index = read_index((DISPATCH / "index.scip").read_bytes())
    precise = build(DISPATCH / "repo", DISPATCH_PATHS, AstGrep(), (index,))
    send = definition_named(precise, "lib/transport.ts", "send")
    callers = {(item.path, precise.caller_binding(send, item)) for item in precise.callers(send)}
    assert callers == {("app/legacy.ts", "name"), ("app/modern.ts", "symbol")}


def test_an_aliased_import_is_found_by_symbol_and_a_same_named_function_is_not() -> None:
    index = read_index((DISPATCH / "index.scip").read_bytes())
    precise = build(DISPATCH / "repo", DISPATCH_PATHS, AstGrep(), (index,))
    loose = build(DISPATCH / "repo", DISPATCH_PATHS, AstGrep())
    transport = definition_named(precise, "lib/transport.ts", "deliver")
    mail = definition_named(precise, "app/mail.ts", "deliver")
    assert {item.path for item in loose.callers(transport)} == {"app/mail.ts"}
    assert {item.path for item in precise.callers(transport)} == {"app/alias.ts"}
    assert {item.path for item in precise.callers(mail)} == {"app/mail.ts"}


def test_the_dispatch_fixture_keeps_both_versions_visible(tmp_path: Path) -> None:
    executable = fake_indexer(tmp_path / "indexer", DISPATCH / "index.scip")
    result = scan_repository(
        DISPATCH / "repo",
        load_pack("google_ads"),
        indexer_executables={"scip-typescript": str(executable)},
    )
    subjects = sorted(item["provider_subject"] for item in call_versions(result))
    assert "v22" in subjects and "v25" in subjects and "v24" in subjects
    hops = " ".join(
        hop
        for item in call_versions(result)
        for path in item["value"]["paths"]
        for hop in path["hops"]
    )
    assert "app/modern.ts:5 by symbol" in hops
    assert "app/legacy.ts:4" in hops and "app/legacy.ts:4 by symbol" not in hops


def test_a_first_party_symbol_binds_to_its_definition_file() -> None:
    line = b"client = GoogleAdsClient()"
    symbol = "scip-python python repository 0.0.0 `lib.client`/GoogleAdsClient#"
    index = SymbolIndex(
        "scip-python",
        "0.6.6",
        ("app.py", "lib/client.py"),
        (
            occurrence("app.py", 0, 9, "GoogleAdsClient", symbol, 8),
            occurrence("lib/client.py", 4, 6, "GoogleAdsClient", symbol, 1),
        ),
    )
    verdict = adjudicate(index, "app.py", line, "GoogleAdsClient")
    assert verdict is not None
    assert verdict.first_party_definition == "GoogleAdsClient"
    assert verdict.binding_target == "lib/client.py"
    assert verdict.binding_site == "app.py:1"
    assert verdict.versions == ()


def test_an_alias_binding_that_carries_a_version_is_never_replaced_by_a_versionless_symbol() -> (
    None
):
    line = b"client = GoogleAdsClient()"
    symbol = "scip-python python repository 0.0.0 `lib.client`/GoogleAdsClient#"
    index = SymbolIndex(
        "scip-python",
        "0.6.6",
        ("app.py", "lib/client.py"),
        (
            occurrence("app.py", 0, 9, "GoogleAdsClient", symbol, 8),
            occurrence("lib/client.py", 4, 6, "GoogleAdsClient", symbol, 1),
        ),
    )
    verdict = adjudicate_line(
        identifier="GoogleAdsClient",
        line=line,
        line_start=0,
        comments=(),
        imports=(),
        aliases={"GoogleAdsClient": "google.ads.googleads.v22.services"},
        defined=frozenset(),
        language="python",
        surface=load_pack("google_ads").surface,
        site=PreciseSite(index, "app.py", 0),
    )
    assert verdict is not None
    assert verdict.versions == ("v22",)
    assert verdict.binding == "google.ads.googleads.v22.services"


def test_a_site_with_no_symbol_falls_back_to_the_alias_table() -> None:
    index = SymbolIndex("scip-python", "0.6.6", ("app.py",), ())
    verdict = adjudicate(index, "app.py", b"client = GoogleAdsClient()", "GoogleAdsClient")
    assert verdict is None


def call_versions(result: Any) -> list[dict[str, Any]]:
    return [
        item
        for item in result.ledger.evidence
        if item["observer"] == "structure" and item["claim_type"] == "call_version"
    ]


def test_scan_resolves_the_version_through_the_barrel_only_with_a_precise_index(
    tmp_path: Path,
) -> None:
    executable = fake_indexer(tmp_path / "indexer")
    before = {path: path.read_bytes() for path in REPO.rglob("*") if path.is_file()}
    precise = scan_repository(
        REPO, load_pack("google_ads"), indexer_executables={"scip-typescript": str(executable)}
    )
    loose = scan_repository(
        REPO, load_pack("google_ads"), indexer_executables={"scip-typescript": "no-such-indexer"}
    )
    assert {path: path.read_bytes() for path in REPO.rglob("*") if path.is_file()} == before
    resolved = [item for item in call_versions(precise) if item["provider_subject"] == "v24"]
    assert resolved, [item["value"] for item in call_versions(precise)]
    chains = [" ".join(path["hops"]) for path in resolved[0]["value"]["paths"]]
    assert any(
        "app/report.ts:4 by symbol" in hops and "import API_RELEASE from lib/version.ts" in hops
        for hops in chains
    ), chains
    assert not [item for item in call_versions(loose) if item["provider_subject"] == "v24"]
    assert ";scip-typescript=0.4.0+sha256:" in precise.scanner_version
    assert "scip-typescript" not in loose.scanner_version
    assert precise.structural_coverage.to_mapping()["typescript"]["precise"] == 6
    assert loose.structural_coverage.to_mapping()["typescript"]["precise"] == 0
    assert precise.structural_coverage.indexes_mapping()["typescript"].startswith(
        "precise: scip-typescript 0.4.0+sha256:"
    )
    assert loose.structural_coverage.indexes_mapping()["typescript"].startswith(
        "recall-only: scip-typescript"
    )
    assert precise.proof_scope_hash != loose.proof_scope_hash


def test_a_present_indexer_that_fails_stops_the_scan_unless_forced(tmp_path: Path) -> None:
    directory = tmp_path / "broken"
    directory.mkdir()
    script = directory / "broken.py"
    script.write_text(
        'import sys\nprint("0.4.0") if sys.argv[1:] == ["--version"] else sys.exit(3)\n',
        encoding="utf-8",
    )
    if sys.platform == "win32":
        executable = directory / "broken.cmd"
        executable.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8"
        )
    else:
        executable = directory / "broken"
        executable.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8"
        )
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    with pytest.raises(Exception, match="TOOLING_FAILED: scip-typescript"):
        scan_repository(
            REPO, load_pack("google_ads"), indexer_executables={"scip-typescript": str(executable)}
        )
    forced = scan_repository(
        REPO,
        load_pack("google_ads"),
        force=True,
        indexer_executables={"scip-typescript": str(executable)},
    )
    assert forced.structural_coverage.to_mapping()["typescript"]["precise"] == 0
    assert forced.structural_coverage.indexes_mapping()["typescript"] == (
        "recall-only (forced): scip-typescript failed; every claim in its languages stays "
        "name-bound"
    )
    assert ";scip-typescript=0.4.0+sha256:" in forced.scanner_version
    assert forced.scanner_version.endswith("+forced-recall-only")
    assert "hops-precise" not in json.dumps(forced.closure_summary())
    assert shutil.which("no-such-indexer") is None
