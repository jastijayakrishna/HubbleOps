from __future__ import annotations

import json
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


def test_a_match_the_closure_marked_unscannable_is_left_to_the_closure_record(
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
    assert claims == {"file_unscanned"}


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
