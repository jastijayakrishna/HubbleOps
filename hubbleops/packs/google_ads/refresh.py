from __future__ import annotations

import argparse
import gzip
import html
import json
import re
import tarfile
import time
import urllib.request
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from pathlib import Path
from typing import Any

from hubbleops.core.canonical import blob_hash, canonical_text, export_bytes
from hubbleops.core.records import as_mapping, as_sequence, as_text
from hubbleops.packs.google_ads.proto import Symbol, resolve_type, symbols

RETRIEVED_AT = "2026-09-04T00:00:00Z"
PROTO_REFS = {
    "v18": "2032c33b2df0fd3b2dc9bfcb6fc747e139a0d87d",
    "v19": "a79ccb6aba44d1285d11840033956a0bced351a2",
    "v20": "00bb3db8a8cc88f0755e872856462b902d6dd8c4",
    "v21": "443c807dee1cb55542a03e004f17a3468830103a",
    "v22": "64aa30b277168edd20efee0c9ceb4ca01248931d",
    "v23": "64aa30b277168edd20efee0c9ceb4ca01248931d",
    "v24": "64aa30b277168edd20efee0c9ceb4ca01248931d",
    "v25": "64aa30b277168edd20efee0c9ceb4ca01248931d",
}
RELEASES = {
    "v19": ("2025-02-26", "2026-02-11"),
    "v20": ("2025-06-04", "2026-06-10"),
    "v21": ("2025-08-06", "2026-08-05"),
    "v22": ("2025-10-15", "2026-10"),
    "v23": ("2026-01-28", "2027-02"),
    "v24": ("2026-04-22", "2027-05"),
    "v25": ("2026-07-22", "2027-08"),
}
TAG = re.compile(r"<[^>]+>")
RESOURCE_LINK = re.compile(r"/google-ads/api/fields/(v[0-9]+)/([a-z][a-z0-9_]*)")
FIELD_HEADING = re.compile(r'<h2[^>]*\bid="([a-z][a-z0-9_.]+)"[^>]*>', re.I)
ROW = re.compile(
    r"<t[hd][^>]*>\s*{label}\s*</t[hd]>\s*<t[hd][^>]*>(.*?)</t[hd]>",
    re.I | re.S,
)
REPLACEMENT = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_.]+)\s+to replace\s+([A-Za-z][A-Za-z0-9_.]+)\b",
    re.I,
)
MIGRATION_HEADER = ("Initial state", "New state", "Change type", "Implementation guidance")
NEITHER_SIDE_BOUND = "neither side of this documented replacement binds to a single catalog subject"
REPLACED_SIDE_UNBOUND = (
    "the replaced side of this documented replacement binds to no single catalog subject"
)
REPLACEMENT_SIDE_UNBOUND = (
    "the replacement side of this documented replacement binds to no single catalog subject"
)
BOTH_SIDES_ONE_SUBJECT = (
    "both sides of this documented replacement bind to the same catalog subject"
)
SUBJECT_CHANGE_TYPE = re.compile(r"remov|renam|replac", re.I)
GUIDANCE_TARGET = re.compile(r"\b(?:use|replaced by)\s+(.*?)(?:\binstead\b|\.\s|\.$|$)", re.I)
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_.]*")
SUBJECT_KINDS = frozenset({"enum", "enum_value", "field", "message", "proto_field", "service"})
CODE_ITEM = re.compile(r"<code\b[^>]*>(.*?)</code>", re.S)
LIST_ITEM = re.compile(r"<li\b[^>]*>(.*?)</li>", re.S)
LINE_BREAK = re.compile(r"<br\b[^>]*>")
TABLE = re.compile(r"<table\b.*?</table>", re.S)
TABLE_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S)
TABLE_CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.S)
DOCUMENTED_CHANGE_KIND = "documented_change"
MIGRATION_ENTRY_KIND = "migration_entry"
UNRESOLVED_CHANGE_KIND = "unresolved_documented_change"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tar-root")
    parser.add_argument("--output", default=str(Path(__file__).resolve().parent / "data"))
    parser.add_argument("--field-workers", type=int, default=4)
    parser.add_argument("--config")
    parser.add_argument("--docs-only", action="store_true")
    args = parser.parse_args(argv)
    if args.docs_only:
        refresh_docs(Path(args.output))
        return 0
    if args.tar_root is None:
        parser.error("--tar-root is required unless --docs-only is given")
    config = None if args.config is None else as_mapping(json.loads(Path(args.config).read_bytes()))
    refresh(Path(args.tar_root), Path(args.output), args.field_workers, config)
    return 0


def refresh(
    tar_root: Path, output: Path, field_workers: int, config: Mapping[str, Any] | None = None
) -> None:
    settings = config or {}
    refs = {
        key: str(value) for key, value in as_mapping(settings.get("proto_refs", PROTO_REFS)).items()
    }
    releases = {
        key: tuple(str(item) for item in as_sequence(value))
        for key, value in as_mapping(settings.get("releases", RELEASES)).items()
    }
    versions = tuple(sorted(releases, key=lambda value: int(value[1:])))
    retrieved_at = str(settings.get("retrieved_at", RETRIEVED_AT))
    emit_source = partial(write_source, retrieved_at=retrieved_at)
    sources = output / "sources"
    raw = sources / "raw"
    normalized = sources / "normalized"
    raw.mkdir(parents=True, exist_ok=True)
    normalized.mkdir(parents=True, exist_ok=True)
    cache = tar_root / ".phase2-field-cache"
    cache.mkdir(parents=True, exist_ok=True)
    manifest_sources: list[dict[str, Any]] = []
    proto_records: dict[str, list[dict[str, Any]]] = {}
    proto_inventories: dict[str, dict[str, Any]] = {}
    proto_field_indexes: dict[str, dict[str, tuple[str, bool, str, str]]] = {}
    for version in ("v18", *versions):
        tar_path = tar_root / f".phase2-{version}.tar"
        records, candidates, inventory = parse_proto(tar_path, version, refs[version])
        proto_records[version] = records
        proto_inventories[version] = inventory
        proto_field_indexes[version] = candidates
        manifest_sources.append(
            emit_source(
                sources,
                raw,
                normalized,
                version,
                "proto",
                records,
                inventory,
                baseline=version == "v18",
            )
        )
    field_subjects: dict[str, tuple[str, ...]] = {}
    for version in versions:
        field_records, inventory = fetch_fields(version, field_workers, cache)
        field_subjects[version] = tuple(str(item["subject"]) for item in field_records)
        proto_records[version].extend(
            crosscheck_records(version, field_records, proto_field_indexes[version])
        )
        manifest_sources = [
            item
            for item in manifest_sources
            if not (item["version"] == version and item["family"] == "proto")
        ]
        proto_inventory = proto_inventory_for(proto_records[version], proto_inventories[version])
        manifest_sources.append(
            emit_source(
                sources,
                raw,
                normalized,
                version,
                "proto",
                proto_records[version],
                proto_inventory,
            )
        )
        manifest_sources.append(
            emit_source(sources, raw, normalized, version, "field", field_records, inventory)
        )
    shared_pages = fetch_shared_pages(cache)
    for version in versions:
        previous = f"v{int(version[1:]) - 1}"
        docs = docs_records(
            version,
            shared_pages,
            releases,
            catalog_subjects(proto_records[previous], field_subjects.get(previous, ())),
            catalog_subjects(proto_records[version], field_subjects.get(version, ())),
        )
        compatibility = compatibility_records(version, shared_pages["sunset"])
        manifest_sources.append(
            {
                **emit_source(
                    sources,
                    raw,
                    normalized,
                    version,
                    "docs",
                    docs,
                    digest_inventory(shared_pages),
                ),
                "expected_replacements": replacement_accounting(docs),
            }
        )
        manifest_sources.append(
            emit_source(
                sources,
                raw,
                normalized,
                version,
                "compatibility",
                compatibility,
                digest_inventory({"sunset": shared_pages["sunset"]}),
            )
        )
    manifest_sources.sort(key=lambda item: (int(item["version"][1:]), item["family"]))
    retain_upstream_bytes(sources, cache, tar_root, manifest_sources)
    manifest = {
        "schema_version": 1,
        "expected_versions": list(versions),
        "versions": [
            {
                "id": version,
                "released_at": releases[version][0],
                "sunset_at": releases[version][1],
            }
            for version in versions
        ],
        "sources": manifest_sources,
    }
    (sources / "manifest.json").write_bytes(export_bytes(manifest))


def refresh_docs(output: Path) -> None:
    sources = output / "sources"
    manifest = as_mapping(json.loads((sources / "manifest.json").read_bytes()))
    versions = tuple(str(item) for item in as_sequence(manifest["expected_versions"]))
    releases = {
        str(as_mapping(item)["id"]): (
            str(as_mapping(item)["released_at"]),
            str(as_mapping(item).get("sunset_at") or ""),
        )
        for item in as_sequence(manifest["versions"])
    }
    entries = [dict(as_mapping(item)) for item in as_sequence(manifest["sources"])]
    subjects = {
        version: catalog_subjects(
            retained_records(sources, "proto", version),
            [str(item["subject"]) for item in retained_records(sources, "field", version)],
        )
        for version in ("v18", *versions)
    }
    for version in versions:
        previous = f"v{int(version[1:]) - 1}"
        pages = retained_pages(sources, version)
        docs = docs_records(version, pages, releases, subjects[previous], subjects.get(version, ()))
        entry = {
            **write_source(
                sources,
                sources / "raw",
                sources / "normalized",
                version,
                "docs",
                docs,
                digest_inventory(pages),
            ),
            "expected_replacements": replacement_accounting(docs),
        }
        previous_entry = next(
            item for item in entries if item["version"] == version and item["family"] == "docs"
        )
        if entry["raw_sha256"] != previous_entry["raw_sha256"]:
            raise ValueError(f"{version} docs sources changed while replaying retained bytes")
        entry["upstream_files"] = previous_entry["upstream_files"]
        entries[entries.index(previous_entry)] = entry
    (sources / "manifest.json").write_bytes(export_bytes({**manifest, "sources": entries}))


def retained_records(sources: Path, family: str, version: str) -> list[dict[str, Any]]:
    path = sources / "normalized" / f"{family}_{version}.jsonl"
    if not path.is_file():
        return []
    return [
        dict(as_mapping(json.loads(line)))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def retained_pages(sources: Path, version: str) -> dict[str, tuple[str, bytes, str]]:
    inventory = as_mapping(json.loads((sources / "raw" / f"docs_{version}.json").read_bytes()))
    pages: dict[str, tuple[str, bytes, str]] = {}
    for item in as_sequence(inventory["pages"]):
        page = as_mapping(item)
        digest = str(page["sha256"])
        payload = gzip.decompress((sources / "upstream" / f"{digest}.gz").read_bytes())
        if blob_hash(payload) != digest:
            raise ValueError(f"retained upstream page {digest} does not match its own hash")
        pages[str(page["name"])] = (str(page["url"]), payload, str(page["resolved_url"]))
    return pages


def retain_upstream_bytes(
    sources: Path, cache: Path, tar_root: Path, entries: list[dict[str, Any]]
) -> None:
    upstream = sources / "upstream"
    upstream.mkdir(exist_ok=True)
    cached: dict[str, Path] = {}
    for path in cache.rglob("*.bin"):
        cached[blob_hash(path.read_bytes())] = path
    for entry in entries:
        inventory = as_mapping(json.loads((sources / str(entry["raw_path"])).read_bytes()))
        if entry["family"] == "proto":
            path = tar_root / f".phase2-{entry['version']}.tar"
            digests = {str(inventory["tar_sha256"])}
            cached[next(iter(digests))] = path
        else:
            descriptors = [as_mapping(item) for item in as_sequence(inventory.get("pages"))]
            descriptors.extend(
                as_mapping(inventory[key]) for key in ("index", "overview") if key in inventory
            )
            digests = {str(item["sha256"]) for item in descriptors}
        retained: list[dict[str, Any]] = []
        for digest in sorted(digests):
            payload = cached[digest].read_bytes()
            if blob_hash(payload) != digest:
                raise ValueError("upstream cache changed during source retention")
            compressed = gzip.compress(payload, mtime=0)
            destination = upstream / f"{digest}.gz"
            destination.write_bytes(compressed)
            retained.append(
                {
                    "path": destination.relative_to(sources).as_posix(),
                    "sha256": blob_hash(compressed),
                    "upstream_sha256": digest,
                    "bytes": len(payload),
                }
            )
        entry["upstream_files"] = retained


def parse_proto(
    tar_path: Path, version: str, commit: str
) -> tuple[list[dict[str, Any]], dict[str, tuple[str, bool, str, str]], dict[str, Any]]:
    tar_hash = blob_hash(tar_path.read_bytes())
    records: list[dict[str, Any]] = []
    candidates: dict[str, tuple[str, bool, str, str]] = {}
    file_digests: list[dict[str, Any]] = []
    known: dict[str, Symbol] = {}
    fields: dict[str, list[tuple[Symbol, str, str]]] = {}
    roots: dict[str, str] = {}
    with tarfile.open(tar_path) as archive:
        members = sorted(
            (
                member
                for member in archive.getmembers()
                if member.isfile() and member.name.endswith(".proto")
            ),
            key=lambda member: member.name,
        )
        for member in members:
            handle = archive.extractfile(member)
            if handle is None:
                raise ValueError(f"cannot read {member.name}")
            payload = handle.read()
            source_hash = blob_hash(payload)
            file_digests.append({"path": member.name, "sha256": source_hash, "size": len(payload)})
            package, declarations = symbols(payload.decode("utf-8"))
            relative = member.name.split(f"/{version}/", 1)[1]
            url = (
                "https://github.com/googleapis/googleapis/blob/"
                f"{commit}/google/ads/googleads/{version}/{relative}"
            )
            for symbol in declarations:
                kind, name = symbol.kind, symbol.name
                known[f"{package}.{name}"] = symbol
                subject = f"{kind}.{relative.removesuffix('.proto').replace('/', '.')}.{name}"
                if kind == "service":
                    subject = f"service.{name}"
                attributes = {
                    "source_path": relative,
                    **symbol.attributes,
                }
                if "proto_type" in attributes:
                    attributes["proto_type"] = re.sub(
                        r"googleads\.v\d+\.", "googleads.VERSION.", str(attributes["proto_type"])
                    )
                records.append(
                    record(version, "proto", subject, kind, attributes, url, source_hash)
                )
                prefix = field_prefix(relative)
                if prefix is not None and kind == "message" and "." not in name:
                    roots[prefix] = f"{package}.{name}"
                if kind == "proto_field":
                    owner = f"{package}.{name.rsplit('.', 1)[0]}"
                    fields.setdefault(owner, []).append((symbol, url, source_hash))

    def walk(owner: str, prefix: str, visited: frozenset[str]) -> None:
        if owner in visited:
            return
        for symbol, url, digest in fields.get(owner, []):
            name = symbol.name.rsplit(".", 1)[1]
            subject = f"{prefix}.{name}"
            proto_type = resolve_type(str(symbol.attributes["proto_type"]), owner, known)
            candidates[subject] = (proto_type, symbol.attributes["repeated"] is True, url, digest)
            if proto_type in fields:
                walk(proto_type, subject, visited | {owner})

    for prefix, owner in sorted(roots.items()):
        walk(owner, prefix, frozenset())
    inventory = {
        "inventory": {
            "proto_files": len(file_digests),
            "proto_bytes": sum(item["size"] for item in file_digests),
            "normalized_records": len(records),
        },
        "commit": commit,
        "tar_sha256": tar_hash,
        "files": file_digests,
    }
    return records, candidates, inventory


def proto_inventory_for(
    records: Sequence[Mapping[str, Any]], original: Mapping[str, Any]
) -> dict[str, Any]:
    kinds: dict[str, int] = {}
    for item in records:
        kind = str(item["kind"])
        kinds[kind] = kinds.get(kind, 0) + 1
    return {
        **original,
        "inventory": {
            **as_mapping(original["inventory"]),
            "normalized_records": len(records),
            **dict(sorted(kinds.items())),
        },
    }


def fetch_fields(
    version: str, workers: int, cache: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if version == "v19":
        return fetch_archived_fields(version, cache, workers)
    return fetch_query_builder_fields(version, workers, cache)


def fetch_archived_fields(
    version: str, cache: Path, workers: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    official = f"https://developers.google.com/google-ads/api/fields/{version}/"
    cdx_url = f"https://web.archive.org/cdx/search/cdx?url=developers.google.com/google-ads/api/fields/{version}/*&output=json&filter=statuscode:200&filter=mimetype:text/html&collapse=digest"
    payload, resolved = fetch(cdx_url, cache / version / "archive_index")
    rows = as_sequence(json.loads(payload))
    captures: dict[str, list[tuple[str, str]]] = {}
    for row in rows[1:]:
        columns = as_sequence(row)
        original = str(columns[2])
        slug = original.removeprefix(official)
        if re.fullmatch(r"[a-z][a-z0-9_]*", slug) and not slug.endswith(
            ("query_builder", "query_validator")
        ):
            captures.setdefault(slug, []).append((str(columns[1]), original))
    overview, overview_page = fetch_archived_resource(
        version, "overview", captures.get("overview", []), cache
    )
    overview_text = overview.decode("utf-8")
    resources = sorted(
        {
            slug
            for found, slug in RESOURCE_LINK.findall(overview_text)
            if found == version
            and slug != "overview"
            and not slug.endswith(("query_builder", "query_validator"))
        }
    )
    if not resources:
        raise ValueError("archived field overview has no resource inventory")
    records: list[dict[str, Any]] = []
    pages = [overview_page]
    with ThreadPoolExecutor(max_workers=min(workers, 3)) as executor:
        futures = {
            executor.submit(
                fetch_archived_resource, version, slug, captures.get(slug, []), cache
            ): slug
            for slug in resources
        }
        for future in as_completed(futures):
            body, page = future.result()
            records.extend(
                parse_field_page(
                    body.decode("utf-8"),
                    version,
                    str(page["url"]),
                    str(page["resolved_url"]),
                    str(page["sha256"]),
                )
            )
            pages.append(page)
    records = merge_field_records(
        sorted(records, key=lambda item: (str(item["subject"]), str(item["source_url"]))), version
    )
    records.append(
        record(
            version,
            "field",
            f"field_inventory.{version}",
            "field_inventory",
            {"complete": True, "resources": len(resources), "fields": len(records)},
            str(overview_page["url"]),
            str(overview_page["sha256"]),
        )
    )
    return records, {
        "inventory": {
            "index_pages": 1,
            "resource_pages": len(resources),
            "fields": len(records) - 1,
        },
        "index": {"url": cdx_url, "resolved_url": resolved, "sha256": blob_hash(payload)},
        "pages": sorted(pages, key=lambda item: str(item["url"])),
    }


def fetch_archived_resource(
    version: str, slug: str, captures: Sequence[tuple[str, str]], cache: Path
) -> tuple[bytes, dict[str, Any]]:
    errors: list[str] = []
    ordered = sorted(
        captures,
        key=lambda item: (
            not (cache / version / f"archive_{slug}_{item[0]}.bin").is_file(),
            item[0],
        ),
    )
    for stamp, original in ordered:
        url = f"https://web.archive.org/web/{stamp}id_/{original}"
        try:
            payload, resolved = fetch(url, cache / version / f"archive_{slug}_{stamp}")
            text = payload.decode("utf-8")
            if "</html>" not in text:
                raise ValueError("truncated archive response")
            if slug != "overview":
                canonical = re.search(r'<link\b[^>]*rel="canonical"[^>]*href="([^"]+)"', text)
                if canonical is None or canonical[1] != original:
                    raise ValueError("canonical source URL does not match the requested version")
                fields = parse_field_page(text, version, original, resolved, blob_hash(payload))
                if not fields:
                    raise ValueError("no parsed fields")
            return payload, {
                "url": original,
                "resolved_url": resolved,
                "sha256": blob_hash(payload),
                "size": len(payload),
            }
        except (ValueError, OSError) as error:
            errors.append(f"{stamp}: {error}")
    raise ValueError(f"{version}/{slug} has no complete capture: {errors}")


def fetch_query_builder_fields(
    version: str, workers: int, cache: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    index_url = f"https://gaql-query-builder.uc.r.appspot.com/schemas/{version}/fields_index.json"
    index_payload, index_resolved = fetch(index_url, cache / version / "fields_index")
    loaded: Any = json.loads(index_payload)
    index = as_mapping(loaded)
    if not index:
        raise ValueError(f"{version} Query Builder field index is empty")
    resources = sorted(
        {
            resource
            for entry in index.values()
            for resource in as_sequence(as_mapping(entry).get("selectable_with"))
            if isinstance(resource, str) and "." not in resource
        }
    )
    if not resources:
        raise ValueError(f"{version} Query Builder field index names no resources")
    records: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_query_builder_resource, version, resource, cache, index): resource
            for resource in resources
        }
        for future in as_completed(futures):
            page_records, page = future.result()
            records.extend(page_records)
            pages.append(page)
    records = merge_field_records(
        sorted(records, key=lambda item: (str(item["subject"]), str(item["source_url"]))), version
    )
    record_subjects = {str(item["subject"]) for item in records}
    missing = sorted(set(index) - record_subjects)
    if missing:
        raise ValueError(
            f"{version} Query Builder schemas omit {len(missing)} indexed fields, "
            f"first {missing[0]}"
        )
    index_digest = blob_hash(index_payload)
    records.append(
        record(
            version,
            "field",
            f"field_inventory.{version}",
            "field_inventory",
            {"complete": True, "resources": len(resources), "fields": len(index)},
            index_url,
            index_digest,
        )
    )
    pages.sort(key=lambda item: str(item["url"]))
    return records, {
        "inventory": {
            "index_pages": 1,
            "resource_pages": len(pages),
            "resources": len(resources),
            "fields": len(index),
        },
        "index": {
            "url": index_url,
            "resolved_url": index_resolved,
            "sha256": index_digest,
            "size": len(index_payload),
        },
        "pages": pages,
    }


def fetch_query_builder_resource(
    version: str,
    resource: str,
    cache: Path,
    index: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    url = f"https://gaql-query-builder.uc.r.appspot.com/schemas/{version}/{resource}.json"
    payload, resolved = fetch(url, cache / version / f"schema_{resource}")
    loaded: Any = json.loads(payload)
    schema = as_mapping(loaded)
    fields = as_mapping(schema.get("fields"))
    if not fields:
        raise ValueError(f"{url} contains no fields")
    digest = blob_hash(payload)
    records: list[dict[str, Any]] = []
    for subject, wrapper in fields.items():
        details = as_mapping(as_mapping(wrapper).get("field_details"))
        if not details:
            raise ValueError(f"{url} has no field_details for {subject}")
        name = as_text(details.get("name"))
        if name != subject:
            raise ValueError(f"{url} field key and name disagree for {subject}")
        data_type = as_text(details.get("data_type"))
        category = as_text(details.get("category"))
        flags = tuple(details.get(key) for key in ("filterable", "selectable", "sortable"))
        repeated = details.get("is_repeated")
        if (
            data_type is None
            or category is None
            or not all(isinstance(item, bool) for item in flags)
        ):
            raise ValueError(f"{url} has incomplete metadata for {subject}")
        if not isinstance(repeated, bool):
            raise ValueError(f"{url} has invalid repetition metadata for {subject}")
        selectable_with = tuple(
            item
            for item in as_sequence(as_mapping(index.get(subject)).get("selectable_with"))
            if isinstance(item, str)
        )
        records.append(
            record(
                version,
                "field",
                subject,
                "field",
                {
                    "category": category,
                    "data_type": data_type,
                    "filterable": flags[0],
                    "selectable": flags[1],
                    "sortable": flags[2],
                    "repeated": repeated,
                    "selectable_with": list(selectable_with),
                },
                url,
                digest,
            )
        )
    return records, {
        "url": url,
        "resolved_url": resolved,
        "sha256": digest,
        "size": len(payload),
        "fields": len(records),
    }


def parse_field_page(
    text: str, version: str, official_url: str, resolved_url: str, digest: str
) -> list[dict[str, Any]]:
    headings = list(FIELD_HEADING.finditer(text))
    records: list[dict[str, Any]] = []
    for index, heading in enumerate(headings):
        subject = heading[1]
        if "." not in subject:
            continue
        stop = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        block = text[heading.end() : stop]
        values = {
            label: row_value(block, label)
            for label in (
                "Category",
                "Data Type",
                "Filterable",
                "Selectable",
                "Sortable",
                "Repeated",
            )
        }
        if any(value is None for value in values.values()):
            raise ValueError(f"{official_url} has incomplete metadata for {subject}")
        selectable_with = selectable_with_values(block)
        attributes = {
            "category": values["Category"],
            "data_type": values["Data Type"],
            "filterable": boolean(values["Filterable"]),
            "selectable": boolean(values["Selectable"]),
            "sortable": boolean(values["Sortable"]),
            "repeated": boolean(values["Repeated"]),
            "selectable_with": selectable_with,
        }
        records.append(record(version, "field", subject, "field", attributes, official_url, digest))
    return records


def crosscheck_records(
    version: str,
    field_records: Sequence[Mapping[str, Any]],
    candidates: Mapping[str, tuple[str, bool, str, str]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for field in field_records:
        subject = str(field["subject"])
        candidate = candidates.get(subject)
        if candidate is None:
            continue
        proto_type, repeated, url, digest = candidate
        records.append(
            record(
                version,
                "proto",
                subject,
                "field",
                {
                    "data_type": normalized_proto_type(proto_type, subject),
                    "proto_type": proto_type,
                    "repeated": repeated,
                },
                url,
                digest,
            )
        )
    return records


def merge_field_records(records: Sequence[Mapping[str, Any]], version: str) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in records:
        subject = str(item["subject"])
        existing = merged.get(subject)
        if existing is None:
            merged[subject] = dict(item)
            continue
        left = dict(existing["attributes"])
        right = dict(item["attributes"])
        left.pop("selectable_with", None)
        right.pop("selectable_with", None)
        left.pop("resolved_url", None)
        right.pop("resolved_url", None)
        if left != right:
            conflicts = as_sequence(existing["attributes"].get("source_conflicts"))
            existing["attributes"]["source_conflicts"] = [
                *conflicts,
                {"source_url": item["source_url"], "sha256": item["sha256"], "attributes": right},
            ]
        values = set(existing["attributes"].get("selectable_with", []))
        values.update(item["attributes"].get("selectable_with", []))
        existing["attributes"] = {**existing["attributes"], "selectable_with": sorted(values)}
    return [merged[subject] for subject in sorted(merged)]


def fetch_shared_pages(cache: Path) -> dict[str, tuple[str, bytes, str]]:
    urls = {
        "release": "https://developers.google.com/google-ads/api/docs/release-notes",
        "archive": "https://developers.google.com/google-ads/api/docs/archived-release-notes",
        "upgrade": "https://developers.google.com/google-ads/api/docs/upgrade",
        "upgrade_archive": "https://web.archive.org/web/20250827000000id_/https://developers.google.com/google-ads/api/docs/upgrade",
        "sunset": "https://developers.google.com/google-ads/api/docs/sunset-dates",
        "sunset_v19": "https://ads-developers.googleblog.com/2025/12/google-ads-api-v19-sunset-reminder.html",
        "sunset_v20": "https://ads-developers.googleblog.com/2026/04/google-ads-api-v20-sunset-reminder.html",
        "sunset_v21": "https://ads-developers.googleblog.com/2026/06/google-ads-api-v21-sunset-reminder.html",
    }
    result: dict[str, tuple[str, bytes, str]] = {}
    for name, url in urls.items():
        payload, resolved = fetch(url, cache / "shared" / name)
        result[name] = (url, payload, resolved)
    return result


def docs_records(
    version: str,
    pages: Mapping[str, tuple[str, bytes, str]],
    releases: Mapping[str, Sequence[str]] = RELEASES,
    previous_subjects: Sequence[str] = (),
    current_subjects: Sequence[str] = (),
) -> list[dict[str, Any]]:
    release_url, release, _ = pages[
        "release" if f'id="{version}-top"'.encode() in pages["release"][1] else "archive"
    ]
    upgrade_url, upgrade, _ = pages["upgrade"]
    guide_sections = version_sections(upgrade.decode("utf-8"), version)
    if not guide_sections:
        upgrade_url, upgrade, _ = pages["upgrade_archive"]
        guide_sections = version_sections(upgrade.decode("utf-8"), version)
    if not guide_sections:
        raise ValueError(f"upgrade guide has no {version} section")
    sunset_url, sunset, _ = pages.get(f"sunset_{version}", pages["sunset"])
    text = release.decode("utf-8")
    sections = [
        item[0]
        for item in re.finditer(r"<h2\b[^>]*>.*?(?=<h2\b|$)", text, re.S)
        if re.match(rf"{version}(?:\b|\.)", clean(item[0].split("</h2>", 1)[0]))
    ]
    if not sections:
        raise ValueError(f"release notes have no {version} section")
    occurrences: dict[str, int] = {}
    table_changes = migration_table_changes(
        version,
        sections,
        previous_subjects,
        current_subjects,
        release_url,
        blob_hash(release),
        occurrences,
    )
    records = [
        record(
            version,
            "docs",
            f"docs.lifecycle.{version}",
            "lifecycle",
            {"sunset_at": releases[version][1]},
            sunset_url,
            blob_hash(sunset),
        ),
        record(
            version,
            "docs",
            f"docs.upgrade.{version}",
            "upgrade_guide",
            {"from_version": f"v{int(version[1:]) - 1}", "to_version": version},
            upgrade_url,
            blob_hash(upgrade),
        ),
        record(
            version,
            "docs",
            f"docs.release.{version}",
            "release_notes",
            {
                "major": version,
                "released_at": releases[version][0],
                "minor_updates_folded": True,
            },
            release_url,
            blob_hash(release),
        ),
    ]
    prose_claims = 0
    paragraphs = re.findall(r"<li\b[^>]*>(.*?)</li>", "".join(sections), re.S)
    for paragraph in paragraphs:
        claim = clean(paragraph)
        action = re.search(r"\b(Added|Removed|Deprecated|Renamed|Updated|Changed)\b", claim, re.I)
        if action is None:
            continue
        prose_claims += len(replacement_claims(claim))
        identifiers = sorted(
            set(clean(value) for value in re.findall(r"<code\b[^>]*>(.*?)</code>", paragraph, re.S))
        )
        records.append(
            record(
                version,
                "docs",
                f"docs.claim.{blob_hash(claim.encode())}",
                "documented_claim",
                {"action": action[1].upper(), "identifiers": identifiers, "claim": claim},
                release_url,
                blob_hash(release),
            )
        )
        records.extend(
            documented_replacements(
                version,
                claim,
                previous_subjects,
                current_subjects,
                release_url,
                blob_hash(release),
                occurrences,
            )
        )
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", "".join(guide_sections), re.S):
        cells = [clean(cell) for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, re.S)]
        if len(cells) < 2 or not any(cells):
            continue
        guidance = " ".join(cells)
        prose_claims += len(replacement_claims(guidance))
        records.append(
            record(
                version,
                "docs",
                f"docs.upgrade_entry.{blob_hash(canonical_text(cells).encode())}",
                "upgrade_guidance",
                {
                    "from_version": f"v{int(version[1:]) - 1}",
                    "to_version": version,
                    "topic": cells[0],
                    "guidance": cells[1:],
                },
                upgrade_url,
                blob_hash(upgrade),
            )
        )
        records.extend(
            documented_replacements(
                version,
                guidance,
                previous_subjects,
                current_subjects,
                upgrade_url,
                blob_hash(upgrade),
                occurrences,
            )
        )
    records.extend(table_changes)
    rows = migration_rows(sections)
    accounting = replacement_accounting(records)
    stated = (
        sum(len(stated_replacements(row)) for row in rows if states_replacement(row)) + prose_claims
    )
    if accounting["stated_pairs"] != stated:
        raise ValueError(
            f"{version} documents {stated} stated replacements but recorded "
            f"{accounting['stated_pairs']}; a documented replacement left no record"
        )
    if accounting["migration_rows"] != tabulated_rows(sections):
        raise ValueError(
            f"{version} release notes tabulate {tabulated_rows(sections)} migration rows but "
            f"{accounting['migration_rows']} were recognised; a documented row left no record"
        )
    return records


def replacement_accounting(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    pairs: dict[str, set[str]] = {DOCUMENTED_CHANGE_KIND: set(), UNRESOLVED_CHANGE_KIND: set()}
    counts: dict[str, int] = {DOCUMENTED_CHANGE_KIND: 0, MIGRATION_ENTRY_KIND: 0}
    replacement_rows = 0
    for item in records:
        kind = str(item["kind"])
        attributes = as_mapping(item["attributes"])
        if kind in counts:
            counts[kind] += 1
        if kind == MIGRATION_ENTRY_KIND:
            replacement_rows += attributes["states_replacement"] is True
        if kind in pairs:
            pairs[kind].add(str(attributes["stated_pair"]))
    bound = pairs[DOCUMENTED_CHANGE_KIND]
    unresolved = pairs[UNRESOLVED_CHANGE_KIND]
    if bound & unresolved:
        raise ValueError("a stated replacement is recorded as both bound and unresolved")
    return {
        "bindings": counts[DOCUMENTED_CHANGE_KIND],
        "bound_pairs": len(bound),
        "migration_rows": counts[MIGRATION_ENTRY_KIND],
        "replacement_rows": replacement_rows,
        "stated_pairs": len(bound | unresolved),
        "unresolved_pairs": len(unresolved),
    }


def migration_rows(sections: Sequence[str]) -> list[tuple[str, ...]]:
    rows: list[tuple[str, ...]] = []
    for table in TABLE.findall("".join(sections)):
        cells = [tuple(TABLE_CELL.findall(row)) for row in TABLE_ROW.findall(table)]
        if not cells or tuple(clean(item) for item in cells[0]) != MIGRATION_HEADER:
            continue
        rows.extend(row for row in cells[1:] if len(row) == len(MIGRATION_HEADER))
    return rows


def tabulated_rows(sections: Sequence[str]) -> int:
    return sum(
        sum(1 for row in TABLE_ROW.findall(table)[1:] if len(TABLE_CELL.findall(row)) == 4)
        for table in TABLE.findall("".join(sections))
    )


def states_replacement(row: Sequence[str]) -> bool:
    return SUBJECT_CHANGE_TYPE.search(clean(row[2])) is not None


def stated_items(cell: str) -> tuple[str, ...]:
    return tuple(found for item in CODE_ITEM.findall(cell) for found in [clean(item)] if found)


def list_items(cell: str) -> tuple[str, ...]:
    chunks = LIST_ITEM.findall(cell) or LINE_BREAK.split(cell)
    if len(chunks) < 2:
        return ()
    found = [stated_items(chunk) for chunk in chunks]
    if any(len(item) != 1 for item in found):
        return ()
    return tuple(item[0] for item in found)


def stated_replacements(row: Sequence[str]) -> tuple[tuple[str, str], ...]:
    replaced = list_items(row[0])
    replacing = list_items(row[1])
    if replaced and len(replaced) == len(replacing):
        return tuple(zip(replaced, replacing, strict=True))
    return ((clean(row[0]), clean(row[1])),)


def migration_table_changes(
    version: str,
    sections: Sequence[str],
    previous_subjects: Sequence[str],
    current_subjects: Sequence[str],
    source_url: str,
    digest: str,
    occurrences: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    counted = {} if occurrences is None else occurrences
    for row in migration_rows(sections):
        records.append(migration_entry(version, row, source_url, digest, counted))
        if not states_replacement(row):
            continue
        claim = " ".join(clean(cell) for cell in row)
        guidance = " ".join(item[1] for item in GUIDANCE_TARGET.finditer(clean(row[3])))
        for stated_subject, stated_replacement in stated_replacements(row):
            records.extend(
                replacement_records(
                    version,
                    claim,
                    stated_subject,
                    stated_replacement,
                    guidance,
                    previous_subjects,
                    current_subjects,
                    source_url,
                    digest,
                    counted,
                )
            )
    return records


def migration_entry(
    version: str,
    row: Sequence[str],
    source_url: str,
    digest: str,
    occurrences: dict[str, int],
) -> dict[str, Any]:
    cells = [clean(cell) for cell in row]
    position = canonical_text([version, cells])
    ordinal = occurrences.get(position, 0)
    occurrences[position] = ordinal + 1
    return record(
        version,
        "docs",
        f"docs.migration_entry.{blob_hash(canonical_text([position, ordinal]).encode())}",
        MIGRATION_ENTRY_KIND,
        {
            "change_type": cells[2],
            "from_version": f"v{int(version[1:]) - 1}",
            "guidance": cells[3],
            "initial_state": cells[0],
            "new_state": cells[1],
            "states_replacement": states_replacement(row),
            "to_version": version,
        },
        source_url,
        digest,
    )


def replacement_records(
    version: str,
    claim: str,
    stated_subject: str,
    stated_replacement: str,
    guidance: str,
    previous_subjects: Sequence[str],
    current_subjects: Sequence[str],
    source_url: str,
    digest: str,
    occurrences: dict[str, int],
) -> list[dict[str, Any]]:
    position = canonical_text([version, claim, stated_subject, stated_replacement])
    ordinal = occurrences.get(position, 0)
    occurrences[position] = ordinal + 1
    pair = blob_hash(canonical_text([position, ordinal]).encode())
    bindings = replacement_bindings(
        stated_subject, stated_replacement, previous_subjects, current_subjects
    )
    change_subject: str | None = None
    replacement: str | None = None
    if not bindings:
        change_subject = documented_row_subject(stated_subject, previous_subjects)
        replacement = documented_row_subject(stated_replacement, current_subjects, stated_subject)
        if replacement is None and guidance:
            replacement = documented_row_subject(guidance, current_subjects, stated_subject)
        if change_subject is not None and replacement is not None and change_subject != replacement:
            bindings = ((change_subject, replacement),)
    if not bindings:
        return [
            unresolved_replacement(
                version,
                claim,
                stated_subject,
                stated_replacement,
                change_subject,
                replacement,
                pair,
                source_url,
                digest,
            )
        ]
    return [
        record(
            version,
            "docs",
            f"docs.change.{blob_hash(canonical_text([subject, target, claim, pair]).encode())}",
            DOCUMENTED_CHANGE_KIND,
            {
                "change_kind": "REPLACED",
                "change_subject": subject,
                "replacement": target,
                "claim": claim,
                "stated_pair": pair,
            },
            source_url,
            digest,
        )
        for subject, target in bindings
    ]


def replacement_bindings(
    stated_subject: str,
    stated_replacement: str,
    previous_subjects: Sequence[str],
    current_subjects: Sequence[str],
) -> tuple[tuple[str, str], ...]:
    replaced = container_index(stated_subject, previous_subjects)
    replacing = container_index(stated_replacement, current_subjects)
    return tuple(
        (replaced[container], replacing[container])
        for container in sorted(set(replaced) & set(replacing))
        if replaced[container] != replacing[container]
    )


def container_index(identifier: str, subjects: Sequence[str]) -> dict[str, str]:
    lowered = identifier.lower()
    leaf = identifier.rpartition(".")[2]
    grouped: dict[str, list[str]] = {}
    for subject in subjects:
        folded = subject.lower()
        if folded != lowered and not folded.endswith(f".{lowered}"):
            continue
        if not subject.endswith(leaf):
            continue
        container = subject[: len(subject) - len(identifier)].rstrip(".")
        grouped.setdefault(container, []).append(subject)
    return {container: found[0] for container, found in grouped.items() if len(found) == 1}


def catalog_subjects(
    proto_records: Sequence[Mapping[str, Any]], field_subjects: Sequence[str]
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            [str(item["subject"]) for item in proto_records]
            + [str(item) for item in field_subjects]
        )
    )


def unresolved_replacement(
    version: str,
    claim: str,
    stated_subject: str,
    stated_replacement: str,
    change_subject: str | None,
    replacement: str | None,
    identity: str,
    source_url: str,
    digest: str,
) -> dict[str, Any]:
    previous = f"v{int(version[1:]) - 1}"
    if change_subject is None and replacement is None:
        conflict = NEITHER_SIDE_BOUND
        close_with = (
            f"name the single {previous} catalog subject the replaced side means and the single "
            f"{version} subject the replacement side means, each already a fact in "
            f"catalog_{previous}.jsonl and catalog_{version}.jsonl, or record a human decision "
            "that this claim names no catalog subject"
        )
    elif change_subject is None:
        conflict = REPLACED_SIDE_UNBOUND
        close_with = (
            f"name the single {previous} catalog subject the replaced side means, already a fact "
            f"in catalog_{previous}.jsonl, or record a human decision that this claim names no "
            "catalog subject"
        )
    elif replacement is None:
        conflict = REPLACEMENT_SIDE_UNBOUND
        close_with = (
            f"name the single {version} catalog subject the replacement side means, already a "
            f"fact in catalog_{version}.jsonl, or record a human decision that this claim "
            "documents a removal with no replacement"
        )
    else:
        conflict = BOTH_SIDES_ONE_SUBJECT
        close_with = (
            f"name the {version} catalog subject the replacement side means distinct from "
            f"{change_subject}, already a fact in catalog_{version}.jsonl, or record a human "
            "decision that this claim states no replacement"
        )
    attributes: dict[str, Any] = {
        "claim": claim,
        "close_with": close_with,
        "conflict": conflict,
        "from_version": previous,
        "stated_pair": identity,
        "stated_replacement": stated_replacement,
        "stated_subject": stated_subject,
        "to_version": version,
    }
    if change_subject is not None:
        attributes["change_subject"] = change_subject
    if replacement is not None:
        attributes["replacement"] = replacement
    return record(
        version,
        "docs",
        f"docs.unresolved_change.{blob_hash(identity.encode())}",
        UNRESOLVED_CHANGE_KIND,
        attributes,
        source_url,
        digest,
    )


def api_identifiers(phrase: str) -> list[str]:
    found = [item.strip(".") for item in IDENTIFIER.findall(phrase)]
    return [item for item in found if item and ("_" in item or "." in item or not item.islower())]


def subject_identity(subject: str) -> str:
    kind, _, rest = subject.partition(".")
    return rest if kind in SUBJECT_KINDS and rest else subject


def documented_row_subject(phrase: str, subjects: Sequence[str], context: str = "") -> str | None:
    names = api_identifiers(phrase)
    qualifiers = names + api_identifiers(context)
    candidates = {
        found
        for name in set(names) | {f"{a}.{b}" for a in qualifiers for b in names if a != b}
        for found in [documented_subject(name, subjects)]
        if found is not None
    }
    resolved = {
        value
        for value in candidates
        if not any(
            other != value and subject_identity(other).startswith(f"{subject_identity(value)}.")
            for other in candidates
        )
    }
    return next(iter(resolved)) if len(resolved) == 1 else None


def replacement_claims(claim: str) -> tuple[tuple[str, str], ...]:
    return tuple((match[2], match[1]) for match in REPLACEMENT.finditer(claim))


def documented_replacements(
    version: str,
    claim: str,
    previous_subjects: Sequence[str],
    current_subjects: Sequence[str],
    source_url: str,
    digest: str,
    occurrences: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    counted = {} if occurrences is None else occurrences
    return [
        item
        for stated_subject, stated_replacement in replacement_claims(claim)
        for item in replacement_records(
            version,
            claim,
            stated_subject,
            stated_replacement,
            "",
            previous_subjects,
            current_subjects,
            source_url,
            digest,
            counted,
        )
    ]


def documented_subject(identifier: str, subjects: Sequence[str]) -> str | None:
    matches = [
        subject
        for subject in subjects
        if subject == identifier or subject.endswith(f".{identifier}")
    ]
    return matches[0] if len(matches) == 1 else None


def version_sections(text: str, version: str) -> list[str]:
    sections: list[str] = []
    for item in re.finditer(r"<h2\b[^>]*>.*?(?=<h2\b|$)", text, re.S):
        title = clean(item[0].split("</h2>", 1)[0])
        if title == version or re.fullmatch(rf"v[0-9]+ to {version}", title):
            sections.append(item[0])
    return sections


def compatibility_records(version: str, page: tuple[str, bytes, str]) -> list[dict[str, Any]]:
    url, payload, _ = page
    minima: dict[str, str] = {}
    maxima: dict[str, str | None] = {}
    for table in re.findall(r"<table\b.*?</table>", payload.decode("utf-8"), re.S):
        value = clean(table)
        language = re.search(r"Client library for (Java|\.NET|PHP|Python|Ruby|Perl)\b", value)
        if language is None:
            continue
        row = re.search(rf"\b{version}\s+Min:\s*([\d.]+)\s+Max:\s*([\d.-]+)", value)
        if row:
            key = language[1].lower().replace(".net", "dotnet")
            minima[key] = row[1]
            maxima[key] = None if row[2] == "-" else row[2]
    supported = bool(minima)
    attributes: dict[str, Any] = {"listed_in_current_table": supported}
    if supported:
        attributes["minimum_versions"] = minima
        attributes["maximum_versions"] = maxima
    return [
        record(
            version,
            "compatibility",
            f"compatibility.client_libraries.{version}",
            "client_compatibility",
            attributes,
            url,
            blob_hash(payload),
        )
    ]


def write_source(
    sources: Path,
    raw: Path,
    normalized: Path,
    version: str,
    family: str,
    records: Sequence[Mapping[str, Any]],
    inventory: Mapping[str, Any],
    *,
    baseline: bool = False,
    retrieved_at: str = RETRIEVED_AT,
) -> dict[str, Any]:
    raw_path = raw / f"{family}_{version}.json"
    normalized_path = normalized / f"{family}_{version}.jsonl"
    raw_payload = export_bytes(inventory)
    normalized_payload = "".join(
        f"{canonical_text({**item, 'retrieved_at': retrieved_at})}\n" for item in records
    ).encode()
    raw_path.write_bytes(raw_payload)
    normalized_path.write_bytes(normalized_payload)
    result: dict[str, Any] = {
        "path": normalized_path.relative_to(sources).as_posix(),
        "raw_path": raw_path.relative_to(sources).as_posix(),
        "version": version,
        "family": family,
        "sha256": blob_hash(normalized_payload),
        "raw_sha256": blob_hash(raw_payload),
        "expected_records": len(records),
        "expected_inventory": dict(inventory["inventory"]),
    }
    if baseline:
        result["baseline"] = True
    return result


def record(
    version: str,
    family: str,
    subject: str,
    kind: str,
    attributes: Mapping[str, Any],
    source_url: str,
    digest: str,
) -> dict[str, Any]:
    return {
        "version": version,
        "family": family,
        "subject": subject,
        "kind": kind,
        "attributes": dict(attributes),
        "source_url": source_url,
        "retrieved_at": RETRIEVED_AT,
        "sha256": digest,
    }


def fetch(url: str, cache_path: Path | None = None) -> tuple[bytes, str]:
    if cache_path is not None and cache_path.with_suffix(".bin").is_file():
        payload = cache_path.with_suffix(".bin").read_bytes()
        resolved = cache_path.with_suffix(".url").read_text(encoding="utf-8")
        return payload, resolved
    request = urllib.request.Request(
        url,
        headers={"Accept-Encoding": "gzip", "User-Agent": "HubbleOps/0.1 source refresh"},
    )
    payload = b""
    resolved = url
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = response.read()
                resolved = response.geturl()
                if response.headers.get("Content-Encoding") == "gzip":
                    payload = gzip.decompress(payload)
            break
        except (OSError, EOFError):
            if attempt == 3:
                raise
            time.sleep(2**attempt)
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.with_suffix(".bin").write_bytes(payload)
        cache_path.with_suffix(".url").write_text(resolved, encoding="utf-8")
    return payload, resolved


def row_value(block: str, label: str) -> str | None:
    match = re.search(ROW.pattern.format(label=re.escape(label)), block, ROW.flags)
    return clean(match[1]) if match is not None else None


def selectable_with_values(block: str) -> list[str]:
    marker = re.search(r"Selectable with", block, re.I)
    if marker is None:
        return []
    tail = block[marker.end() :]
    return sorted(set(re.findall(r"<code[^>]*>([a-z][a-z0-9_.]+)</code>", tail, re.I)))


def boolean(value: str | None) -> bool:
    if value == "True":
        return True
    if value == "False":
        return False
    raise ValueError(f"expected True or False, got {value!r}")


def clean(value: str) -> str:
    return " ".join(html.unescape(TAG.sub(" ", value)).split())


def field_prefix(relative: str) -> str | None:
    path = Path(relative)
    if path.parent.as_posix() == "resources":
        return path.stem
    if relative in ("common/metrics.proto", "common/segments.proto"):
        return path.stem
    return None


def normalized_proto_type(proto_type: str, subject: str) -> str:
    scalar = proto_type.removeprefix(".")
    if scalar == "string":
        return "STRING"
    if scalar in {"int32", "int64", "uint32", "uint64", "sint32", "sint64"}:
        return "INT64"
    if scalar in {"double", "float"}:
        return "DOUBLE"
    if scalar == "bool":
        return "BOOLEAN"
    if scalar == "bytes":
        return "STRING"
    return "ENUM" if ".enums." in scalar or scalar.endswith("Enum") else "MESSAGE"


def digest_inventory(pages: Mapping[str, tuple[str, bytes, str]]) -> dict[str, Any]:
    items = [
        {
            "name": name,
            "url": url,
            "resolved_url": resolved,
            "sha256": blob_hash(payload),
            "size": len(payload),
        }
        for name, (url, payload, resolved) in sorted(pages.items())
    ]
    total_bytes = sum(len(payload) for _, payload, _ in pages.values())
    return {"inventory": {"pages": len(items), "bytes": total_bytes}, "pages": items}


if __name__ == "__main__":
    raise SystemExit(main())
