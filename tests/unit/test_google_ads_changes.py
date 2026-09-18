import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from hubbleops.core.canonical import blob_hash, canonical_text
from hubbleops.core.errors import PackDataError
from hubbleops.packs.google_ads import refresh as source_refresh
from hubbleops.packs.google_ads.changes import CHANGES, DATA_ROOT, GoogleAdsChanges
from hubbleops.packs.google_ads.proto import symbols


def source(family: str, kind: str, attributes: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": "v25",
        "family": family,
        "kind": kind,
        "subject": "campaign.id",
        "attributes": attributes,
        "source_url": f"https://example.test/{family}",
        "retrieved_at": "2026-09-04T00:00:00Z",
        "sha256": "a" * 64,
    }


def test_crosscheck_keeps_agreement_documentation_and_disagreement_distinct() -> None:
    proto = source("proto", "field", {"data_type": "INT64", "repeated": False})
    field = source("field", "field", {"data_type": "INT64", "repeated": False, "selectable": True})
    fact = CHANGES.compile_version("v25", [proto, field])[0]
    assert (fact.confidence, fact.resolution) == ("PROVEN", "RESOLVED")
    assert fact.corroborating_sources
    for other in [
        {"data_type": "STRING", "repeated": False},
        {"data_type": "INT64", "repeated": True},
    ]:
        conflicting = CHANGES.compile_version("v25", [proto, {**field, "attributes": other}])[0]
        assert conflicting.resolution == "UNKNOWN_PROVIDER_CONTRACT"
    documented_only = CHANGES.compile_version("v25", [field])[0]
    assert (documented_only.resolution, documented_only.confidence) == ("RESOLVED", "DOCUMENTED")
    assert CHANGES.compile_version("v25", [proto])[0].resolution == "UNKNOWN_PROVIDER_CONTRACT"
    documented = CHANGES.compile_version(
        "v25", [source("docs", "documented_claim", {"claim": "Removed"})]
    )[0]
    assert documented.confidence == "DOCUMENTED"


def test_proto_parser_keeps_nested_field_and_enum_identities() -> None:
    package, parsed = symbols(
        "package test.v1; message A { string name = 1; message B { int64 name = 1; } "
        "B nested = 2; oneof choice { string other = 3; } enum E { UNKNOWN = 0; ON = 1; } }"
    )
    assert package == "test.v1"
    by_name = {item.name: item for item in parsed}
    assert by_name["A.name"].attributes["proto_type"] == "string"
    assert by_name["A.B.name"].attributes["proto_type"] == "int64"
    assert by_name["A.other"].attributes["number"] == 3
    assert by_name["A.E.ON"].attributes["number"] == 1
    with pytest.raises(ValueError):
        symbols("package test.v1; message A {")


def test_two_offline_builds_are_byte_identical(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    assert CHANGES.build(first) == CHANGES.build(second)
    for path in first.iterdir():
        assert path.read_bytes() == (second / path.name).read_bytes()


@pytest.mark.parametrize(
    ("tamper", "named"),
    [
        ("normalized", "source hash mismatch for normalized/proto_v18.jsonl"),
        ("raw", "raw source hash mismatch for normalized/proto_v18.jsonl"),
        ("catalog", "shipped catalog mismatch for v25"),
        ("missing_family", "v25 is missing source families: field"),
        ("escape", "source path escapes source root: ../../outside.jsonl"),
    ],
)
def test_verification_rejects_tampered_inputs(tmp_path: Path, tamper: str, named: str) -> None:
    data = tmp_path / "data"
    shutil.copytree(DATA_ROOT, data)
    compiler = GoogleAdsChanges(data)
    manifest_path = data / "sources" / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    entry = manifest["sources"][0]
    if tamper in {"normalized", "raw"}:
        path = data / "sources" / entry["path" if tamper == "normalized" else "raw_path"]
        path.write_bytes(path.read_bytes() + b" ")
    elif tamper == "catalog":
        path = data / "catalog_v25.jsonl"
        path.write_bytes(path.read_bytes() + b" ")
    else:
        if tamper == "missing_family":
            manifest["sources"] = [
                item
                for item in manifest["sources"]
                if not (item["version"] == "v25" and item["family"] == "field")
            ]
        else:
            entry["path"] = "../../outside.jsonl"
        manifest_path.write_text(canonical_text(manifest), encoding="utf-8")
    with pytest.raises(PackDataError) as refusal:
        compiler.verify()
    assert named in str(refusal.value)


def test_catalogs_are_canonical_complete_and_provenanced() -> None:
    for version in CHANGES.versions():
        path = DATA_ROOT / f"catalog_{version.id}.jsonl"
        assert blob_hash(path.read_bytes()) == version.catalog_hash
        records = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
        assert len(records) > 10000
        assert [item["subject"] for item in records] == sorted(
            {item["subject"] for item in records}
        )
        assert any(
            item["kind"] == "field_inventory" and item["attributes"]["complete"] for item in records
        )
        assert all(
            item["source_url"] and item["retrieved_at"] and len(item["sha256"]) == 64
            for item in records
        )
        assert {item["confidence"] for item in records} == {"PROVEN", "DOCUMENTED"}


def test_configured_next_major_uses_the_same_refresh_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str]] = []

    def fake_proto(
        _path: Path, version: str, _commit: str
    ) -> tuple[list[dict[str, Any]], dict[str, tuple[str, bool, str, str]], dict[str, Any]]:
        seen.append(("proto", version))
        digest = "a" * 64
        records = [
            source_refresh.record(
                version,
                "proto",
                f"service.Example{version}",
                "service",
                {"source_path": "services/example.proto"},
                "https://example.test/proto",
                digest,
            )
        ]
        return records, {}, {"inventory": {"files": 1}, "tar_sha256": digest, "files": []}

    def fake_fields(
        version: str, _workers: int, _cache: Path
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        seen.append(("field", version))
        return [], {"inventory": {"pages": 1}, "pages": [{"sha256": "b" * 64}]}

    def fake_docs(
        version: str,
        _pages: Mapping[str, tuple[str, bytes, str]],
        _releases: Mapping[str, Any],
        _previous: tuple[str, ...],
        _current: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        seen.append(("docs", version))
        return [
            source_refresh.record(
                version,
                "docs",
                f"docs.release.{version}",
                "release_notes",
                {"major": version},
                "https://example.test/docs",
                "c" * 64,
            )
        ]

    def fake_compatibility(version: str, _page: tuple[str, bytes, str]) -> list[dict[str, Any]]:
        seen.append(("compatibility", version))
        return [
            source_refresh.record(
                version,
                "compatibility",
                f"compatibility.client_libraries.{version}",
                "client_compatibility",
                {"listed_in_current_table": False},
                "https://example.test/compatibility",
                "d" * 64,
            )
        ]

    pages = {"sunset": ("https://example.test", b"source", "https://example.test")}

    def fake_shared_pages(_cache: Path) -> dict[str, tuple[str, bytes, str]]:
        return pages

    def fake_inventory(
        _pages: Mapping[str, tuple[str, bytes, str]],
    ) -> dict[str, Any]:
        return {"inventory": {"pages": 1}, "pages": [{"sha256": "c" * 64}]}

    def fake_retain(
        _sources: Path, _cache: Path, _tar_root: Path, _entries: list[dict[str, Any]]
    ) -> None:
        return None

    monkeypatch.setattr(source_refresh, "parse_proto", fake_proto)
    monkeypatch.setattr(source_refresh, "fetch_fields", fake_fields)
    monkeypatch.setattr(source_refresh, "fetch_shared_pages", fake_shared_pages)
    monkeypatch.setattr(source_refresh, "docs_records", fake_docs)
    monkeypatch.setattr(source_refresh, "compatibility_records", fake_compatibility)
    monkeypatch.setattr(source_refresh, "digest_inventory", fake_inventory)
    monkeypatch.setattr(source_refresh, "retain_upstream_bytes", fake_retain)

    releases = {f"v{number}": ("2026-01-01", "2027-01") for number in range(19, 27)}
    refs = {"v18": "ref18", **{version: f"ref{version[1:]}" for version in releases}}
    output = tmp_path / "data"
    source_refresh.refresh(
        tmp_path,
        output,
        1,
        {"proto_refs": refs, "releases": releases, "retrieved_at": "2026-09-05T00:00:00Z"},
    )
    manifest = json.loads((output / "sources" / "manifest.json").read_bytes())
    assert manifest["expected_versions"][-1] == "v26"
    assert {family for family, version in seen if version == "v26"} == {
        "proto",
        "field",
        "docs",
        "compatibility",
    }
