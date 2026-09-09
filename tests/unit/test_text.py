from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from hubbleops.app import registry
from hubbleops.app.cli import scan_repository
from hubbleops.closure import source_closure
from hubbleops.core.canonical import content_id
from hubbleops.core.errors import ToolingFailed
from hubbleops.core.observer import ObserverContext
from hubbleops.observe import text

SCRIPT_ONLY_MANIFEST = {
    "package.json": json.dumps(
        {
            "name": "app",
            "version": "1.0.0",
            "scripts": {"postinstall": "node ./scripts/mockprov-client-setup.js"},
            "dependencies": {"left-pad": "^1.3.0"},
        },
        indent=2,
    )
    + "\n",
    "package-lock.json": json.dumps(
        {
            "name": "app",
            "lockfileVersion": 3,
            "requires": True,
            "packages": {
                "": {"name": "app", "version": "1.0.0"},
                "node_modules/left-pad": {"version": "1.3.0"},
            },
        },
        indent=2,
    )
    + "\n",
}


def deny_read(path: Path) -> None:
    account = os.environ.get("USERNAME")
    if os.name != "nt" or not account:
        pytest.skip("read denial in this test is expressed with Windows ACLs")
    done = subprocess.run(
        ["icacls", str(path), "/deny", f"{account}:(R)"], capture_output=True, check=False
    )
    if done.returncode != 0:
        pytest.skip(f"icacls refused to deny read: {done.stderr.decode(errors='replace').strip()}")
    probe = subprocess.run(
        ["rg", "--line-number", "MockProvClient", str(path)], capture_output=True, check=False
    )
    if probe.returncode != 2:
        allow_read(path)
        pytest.skip("the current Windows token can still read a file with the deny ACL")


def allow_read(path: Path) -> None:
    account = os.environ.get("USERNAME", "")
    subprocess.run(["icacls", str(path), "/remove:d", account], capture_output=True, check=False)


def build(root: Path, files: dict[str, str]) -> source_closure.SourceClosure:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return source_closure.build(root)


def test_a_surface_name_written_only_in_a_manifest_still_raises_a_candidate(
    tmp_path: Path, mock_pack: registry.LoadedPack
) -> None:
    build(tmp_path, SCRIPT_ONLY_MANIFEST)
    book = scan_repository(tmp_path, mock_pack).ledger

    located = {
        (
            book.location_of(candidate).claim_type,
            book.location_of(candidate).display(),
            candidate["status"],
        )
        for candidate in book.candidates
    }
    assert ("package_reference", "package.json:5", "UNKNOWN") in located
    assert book.counts()["unknown"] >= 1
    assert book.counts()["unexplained"] == 0


def test_a_lock_proving_absence_never_stands_alone_over_a_named_surface(
    tmp_path: Path, mock_pack: registry.LoadedPack
) -> None:
    build(tmp_path, SCRIPT_ONLY_MANIFEST)
    book = scan_repository(tmp_path, mock_pack).ledger

    assert book.by_status("NOT_AFFECTED_WITH_EVIDENCE")
    assert [candidate["status"] for candidate in book.candidates] != ["NOT_AFFECTED_WITH_EVIDENCE"]
    named = {
        book.location_of(candidate).provider_subject for candidate in book.by_status("UNKNOWN")
    }
    assert "mockprov-client" in named


def test_a_manifest_hit_and_a_source_hit_produce_the_same_recall(
    tmp_path: Path, mock_pack: registry.LoadedPack
) -> None:
    manifest = build(tmp_path / "manifest", SCRIPT_ONLY_MANIFEST)
    source = build(
        tmp_path / "source",
        {"setup.js": 'require("./scripts/mockprov-client-setup.js");\n'},
    )
    ctx = ObserverContext(
        provider="_mock",
        run_id=content_id({"run": 1}),
        proof_scope_hash=content_id({"scope": 1}),
        repo_sha=None,
        dependency_context_hash=None,
        surface=mock_pack.surface,
    )

    def subjects(closure: source_closure.SourceClosure) -> set[str | None]:
        return {
            record["provider_subject"]
            for record in text.scan(closure, ctx)
            if record["claim_type"] in ("package_reference", "surface_reference")
        }

    assert subjects(manifest) == subjects(source)


def test_a_match_the_closure_never_enumerated_stops_the_scan(
    tmp_path: Path, mock_pack: registry.LoadedPack, monkeypatch: pytest.MonkeyPatch
) -> None:
    closure = build(tmp_path, {"app.js": 'const c = "mockprov";\n'})
    ctx = ObserverContext(
        provider="_mock",
        run_id=content_id({"run": 1}),
        proof_scope_hash=content_id({"scope": 1}),
        repo_sha=None,
        dependency_context_hash=None,
        surface=mock_pack.surface,
    )

    def stray(*_: object) -> Iterator[text.TextHit]:
        yield text.TextHit(path="ghost/app.js", line_number=1, line_text='const c = "mockprov";')

    monkeypatch.setattr(text, "_search", stray)
    with pytest.raises(ToolingFailed) as raised:
        text.scan(closure, ctx)
    assert "never enumerated" in str(raised.value)


def test_the_searcher_never_reaches_a_directory_the_closure_did_not_enumerate(
    tmp_path: Path, mock_pack: registry.LoadedPack
) -> None:
    closure = build(
        tmp_path,
        {
            "app.js": 'const c = "mockprov";\n',
            ".venv/lib/site-packages/dep/client.py": 'CLIENT = "mockprov"\n',
            ".hubbleops/artifacts/run/ledger.json": '{"surface": "mockprov"}\n',
        },
    )
    ctx = ObserverContext(
        provider="_mock",
        run_id=content_id({"run": 1}),
        proof_scope_hash=content_id({"scope": 1}),
        repo_sha=None,
        dependency_context_hash=None,
        surface=mock_pack.surface,
    )

    records = text.scan(closure, ctx)

    assert ".venv" in closure.search_exclusions()
    assert ".hubbleops/artifacts" in closure.search_exclusions()
    reached = sorted({str(record["path"]) for record in records})
    assert not any(path.startswith((".venv/", ".hubbleops/artifacts/")) for path in reached), (
        f"the searcher walked a directory the closure pruned: {reached}"
    )


def test_a_match_in_a_closure_marked_unscannable_file_is_not_discarded(
    tmp_path: Path, mock_pack: registry.LoadedPack, monkeypatch: pytest.MonkeyPatch
) -> None:
    closure = build(tmp_path, {"blob.bin": "mockprov\x00\n"})
    unscannable = [entry.path for entry in closure.entries if not entry.carries_source()]
    assert unscannable == ["blob.bin"]
    ctx = ObserverContext(
        provider="_mock",
        run_id=content_id({"run": 1}),
        proof_scope_hash=content_id({"scope": 1}),
        repo_sha=None,
        dependency_context_hash=None,
        surface=mock_pack.surface,
    )

    def stray(*_: object) -> Iterator[text.TextHit]:
        yield text.TextHit(path="blob.bin", line_number=1, line_text="mockprov")

    monkeypatch.setattr(text, "_search", stray)
    claims = {record["claim_type"] for record in text.scan(closure, ctx)}
    assert claims == {"file_unscanned", "package_reference", "surface_reference"}


def test_a_media_suffix_never_removes_a_file_from_the_ledger(
    tmp_path: Path, mock_pack: registry.LoadedPack
) -> None:
    (tmp_path / "logo.png").write_bytes(b"mockprov MockProvClient\x00 in a png")
    (tmp_path / "blob.dat").write_bytes(b"opaque\x00")
    book = scan_repository(tmp_path, mock_pack).ledger

    accounted = {record["path"] for record in book.evidence}
    assert {"logo.png", "blob.dat"} <= accounted, (
        "a suffix is not evidence about content; every unscanned path stays in the ledger"
    )
    unscanned = {
        book.location_of(candidate).display(): candidate["status"]
        for candidate in book.candidates
        if book.location_of(candidate).claim_type == "file_unscanned"
    }
    assert unscanned == {"logo.png": "UNKNOWN", "blob.dat": "UNKNOWN"}
    assert book.counts()["unexplained"] == 0
    for candidate in book.candidates:
        if book.location_of(candidate).claim_type == "file_unscanned":
            assert candidate["close_with"]


def test_the_paths_ripgrep_could_not_read_are_taken_from_its_stderr() -> None:
    stderr = (
        "rg: .\\src\\locked.py: Access is denied. (os error 5)\n"
        "rg: src/other.py: Permission denied (os error 13)\n"
        "rg: something with no path\n"
    )
    assert text.unreadable_paths(stderr) == ("src/locked.py", "src/other.py")


def test_a_file_ripgrep_cannot_read_does_not_throw_the_whole_scan_away(
    tmp_path: Path, mock_pack: registry.LoadedPack
) -> None:
    build(tmp_path, {"src/seen.py": 'MockProvClient(version="v22")\n'})
    locked = tmp_path / "src" / "locked.py"
    locked.write_text('MockProvClient(version="v22")\n', encoding="utf-8")
    deny_read(locked)

    try:
        book = scan_repository(tmp_path, mock_pack).ledger
    finally:
        allow_read(locked)

    located = {book.location_of(candidate).display() for candidate in book.candidates}
    assert any(display.startswith("src/seen.py") for display in located)
    assert "src/locked.py" in located
    assert book.counts()["unexplained"] == 0


def test_a_record_stream_dense_with_surface_names_becomes_one_accounted_candidate(
    tmp_path: Path, mock_pack: registry.LoadedPack
) -> None:
    rows = "".join(f'{{"client": "MockProvClient", "row": {index}}}\n' for index in range(500))
    build(tmp_path, {"data/catalog.jsonl": rows})

    book = scan_repository(tmp_path, mock_pack).ledger
    bulk = [
        candidate
        for candidate in book.candidates
        if book.location_of(candidate).claim_type == "bulk_data_reference"
    ]

    assert len(bulk) == 1
    assert bulk[0]["status"] == "EXCLUDED_WITH_EVIDENCE"
    assert book.location_of(bulk[0]).path == "data/catalog.jsonl"
    assert book.counts()["unexplained"] == 0


def test_collapsing_a_bulk_file_never_loses_a_match(
    tmp_path: Path, mock_pack: registry.LoadedPack
) -> None:
    rows = "".join(f'{{"client": "MockProvClient", "row": {index}}}\n' for index in range(500))
    build(tmp_path, {"data/catalog.jsonl": rows})

    book = scan_repository(tmp_path, mock_pack).ledger
    record = next(item for item in book.evidence if item["claim_type"] == "bulk_data_reference")

    assert record["value"]["match_count"] >= 500
    assert record["value"]["line_count"] == 500
    assert "MockProvClient" in record["value"]["subjects"]


def test_a_source_file_with_many_surface_names_is_never_collapsed(
    tmp_path: Path, mock_pack: registry.LoadedPack
) -> None:
    lines = "".join(f'client_{index} = MockProvClient(version="v22")\n' for index in range(300))
    build(tmp_path, {"src/wide.py": lines})

    book = scan_repository(tmp_path, mock_pack).ledger
    claim_types = {book.location_of(candidate).claim_type for candidate in book.candidates}
    located = [
        candidate
        for candidate in book.candidates
        if book.location_of(candidate).path == "src/wide.py"
    ]

    assert "bulk_data_reference" not in claim_types
    assert len(located) > 100
    assert book.counts()["unexplained"] == 0


def test_a_call_site_ripgrep_quarantines_as_binary_still_raises_a_candidate(
    tmp_path: Path, mock_pack: registry.LoadedPack
) -> None:
    padding = "".join(f"# padding line {index:05d}\n" for index in range(700)).encode()
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "late.py").write_bytes(padding + b'\x00\nMockProvClient(version="v22")\n')

    book = scan_repository(tmp_path, mock_pack).ledger

    unscanned = {
        book.location_of(candidate).display(): candidate["status"]
        for candidate in book.candidates
        if book.location_of(candidate).claim_type == "file_unscanned"
    }
    assert unscanned["src/late.py"] == "UNKNOWN"
    assert book.counts()["unexplained"] == 0
