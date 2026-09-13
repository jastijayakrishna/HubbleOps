from __future__ import annotations

import gzip
import json
import re
import tempfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from hubbleops.core.canonical import blob_hash, canonical_text, content_id, export_bytes
from hubbleops.core.errors import PackDataError
from hubbleops.core.records import as_mapping, as_sequence, as_text
from hubbleops.packs._protocol import BuildReport, CatalogFact, Confidence, Version

DATA_ROOT = Path(__file__).resolve().parent / "data"
SOURCE_ROOT = DATA_ROOT / "sources"
MANIFEST_PATH = SOURCE_ROOT / "manifest.json"
REQUIRED_FAMILIES = frozenset({"proto", "field", "docs", "compatibility"})


class GoogleAdsChanges:
    def __init__(self, data_root: Path = DATA_ROOT) -> None:
        self.data_root = data_root
        self.source_root = data_root / "sources"
        self.manifest_path = self.source_root / "manifest.json"

    @property
    def lattice_hash(self) -> str:
        return self._report_for_shipped().lattice_hash

    def build(self, output_dir: Path) -> BuildReport:
        manifest = self._manifest()
        versions = self._version_entries(manifest)
        required_versions = self._expected_versions(manifest)
        sources = self._source_entries(manifest)
        by_version: dict[str, list[dict[str, Any]]] = defaultdict(list)
        source_hashes: dict[str, str] = {}
        seen_families: dict[str, set[str]] = defaultdict(set)
        proto_by_version: dict[str, list[dict[str, Any]]] = {}
        for source in sources:
            relative = self._required_text(source, "path")
            version = self._required_text(source, "version")
            family = self._required_text(source, "family")
            baseline = source.get("baseline") is True
            if version not in required_versions and not (baseline and version == "v18"):
                raise PackDataError(f"source {relative} names unexpected version {version}")
            if family not in REQUIRED_FAMILIES:
                raise PackDataError(f"source {relative} names unexpected family {family}")
            path = (self.source_root / relative).resolve()
            if not path.is_relative_to(self.source_root.resolve()):
                raise PackDataError(f"source path escapes source root: {relative}")
            payload = path.read_bytes()
            actual_hash = blob_hash(payload)
            expected_hash = self._required_text(source, "sha256")
            if actual_hash != expected_hash:
                raise PackDataError(
                    f"source hash mismatch for {relative}: expected {expected_hash}, "
                    f"got {actual_hash}"
                )
            upstream_digests = self._verify_raw_source(source, relative)
            records = self._jsonl(payload, relative)
            expected_records = source.get("expected_records")
            if expected_records != len(records):
                raise PackDataError(
                    f"source inventory mismatch for {relative}: expected {expected_records}, "
                    f"parsed {len(records)}"
                )
            source_hashes[relative] = actual_hash
            if family == "proto":
                proto_by_version[version] = records
            for record in records:
                self._validate_source_record(record, relative, version, family)
                if record["sha256"] not in upstream_digests:
                    raise PackDataError(
                        f"{relative} fact cites a hash absent from its upstream inventory"
                    )
                if not baseline:
                    by_version[version].append(record)
            if not baseline:
                seen_families[version].add(family)
        for version in required_versions:
            missing = REQUIRED_FAMILIES - seen_families[version]
            if missing:
                raise PackDataError(
                    f"{version} is missing source families: {', '.join(sorted(missing))}"
                )
        output_dir.mkdir(parents=True, exist_ok=True)
        previous = "v18"
        for version in required_versions:
            if previous not in proto_by_version:
                raise PackDataError(f"missing proto comparison baseline {previous}")
            before = {
                str(item["subject"]): item
                for item in proto_by_version[previous]
                if item["kind"] != "field"
            }
            after = {
                str(item["subject"]): item
                for item in proto_by_version[version]
                if item["kind"] != "field"
            }
            deltas: list[dict[str, Any]] = []
            for subject in sorted(set(before) | set(after)):
                left, right = before.get(subject), after.get(subject)
                if (
                    left is not None
                    and right is not None
                    and left["attributes"] == right["attributes"]
                ):
                    continue
                deltas.append(
                    {
                        "subject": subject,
                        "change": "ADDED"
                        if left is None
                        else "REMOVED"
                        if right is None
                        else "CHANGED",
                        "before": left,
                        "after": right,
                        "confidence": "PROVEN",
                    }
                )
            (output_dir / f"proto_diff_{previous}_{version}.jsonl").write_bytes(
                "".join(canonical_text(item) + "\n" for item in deltas).encode()
            )
            previous = version
        catalog_hashes: dict[str, str] = {}
        fact_counts: dict[str, int] = {}
        for version in required_versions:
            facts = self.compile_version(version, by_version[version])
            payload = "".join(f"{canonical_text(fact.to_mapping())}\n" for fact in facts).encode()
            path = output_dir / f"catalog_{version}.jsonl"
            path.write_bytes(payload)
            catalog_hashes[version] = blob_hash(payload)
            fact_counts[version] = len(facts)
        version_map = {self._required_text(item, "id"): item for item in versions}
        lattice_hash = content_id(
            [
                {
                    "id": version,
                    "catalog_hash": catalog_hashes[version],
                    "released_at": self._required_text(version_map[version], "released_at"),
                    "sunset_at": version_map[version].get("sunset_at"),
                }
                for version in required_versions
            ]
        )
        report = BuildReport(
            source_hashes=dict(sorted(source_hashes.items())),
            catalog_hashes=catalog_hashes,
            fact_counts=fact_counts,
            lattice_hash=lattice_hash,
        )
        (output_dir / "lattice.json").write_bytes(
            export_bytes(
                {
                    "manifest_sha256": blob_hash(self.manifest_path.read_bytes()),
                    "versions": [
                        {**dict(version_map[version]), "catalog_hash": catalog_hashes[version]}
                        for version in required_versions
                    ],
                    "source_hashes": report.source_hashes,
                    "fact_counts": report.fact_counts,
                    "lattice_hash": lattice_hash,
                }
            )
        )
        return report

    def verify(self) -> BuildReport:
        with tempfile.TemporaryDirectory(prefix="hops-google-ads-") as directory:
            output = Path(directory)
            report = self.build(output)
            for version, expected_hash in report.catalog_hashes.items():
                shipped = self.data_root / f"catalog_{version}.jsonl"
                if not shipped.is_file():
                    raise PackDataError(f"shipped catalog is missing: {shipped.name}")
                actual = blob_hash(shipped.read_bytes())
                if actual != expected_hash:
                    raise PackDataError(
                        f"shipped catalog mismatch for {version}: expected {expected_hash}, "
                        f"got {actual}"
                    )
                if shipped.read_bytes() != (output / shipped.name).read_bytes():
                    raise PackDataError(f"shipped catalog is not byte-identical for {version}")
            if (self.data_root / "lattice.json").read_bytes() != (
                output / "lattice.json"
            ).read_bytes():
                raise PackDataError("shipped lattice metadata differs from rebuilt sources")
            for path in output.glob("proto_diff_*.jsonl"):
                shipped = self.data_root / path.name
                if not shipped.is_file() or shipped.read_bytes() != path.read_bytes():
                    raise PackDataError(f"computed proto diff mismatch: {path.name}")
            return report

    def versions(self) -> tuple[Version, ...]:
        entries = as_sequence(self._lattice().get("versions"))
        return tuple(
            Version(
                id=self._required_text(as_mapping(item), "id"),
                catalog_hash=self._required_text(as_mapping(item), "catalog_hash"),
                released_at=self._required_text(as_mapping(item), "released_at"),
                sunset_at=as_text(as_mapping(item).get("sunset_at")),
            )
            for item in entries
        )

    def _report_for_shipped(self) -> BuildReport:
        lattice = self._lattice()
        hashes: dict[str, str] = {}
        for version in self.versions():
            actual = blob_hash((self.data_root / f"catalog_{version.id}.jsonl").read_bytes())
            if actual != version.catalog_hash:
                raise PackDataError(f"shipped catalog hash mismatch for {version.id}")
            hashes[version.id] = actual
        return BuildReport(
            source_hashes={
                key: str(value) for key, value in as_mapping(lattice.get("source_hashes")).items()
            },
            catalog_hashes=hashes,
            fact_counts={
                key: int(value) for key, value in as_mapping(lattice.get("fact_counts")).items()
            },
            lattice_hash=self._required_text(lattice, "lattice_hash"),
        )

    def _lattice(self) -> Mapping[str, Any]:
        try:
            result = as_mapping(json.loads((self.data_root / "lattice.json").read_bytes()))
        except (OSError, ValueError) as error:
            raise PackDataError(f"cannot read lattice metadata: {error}") from error
        if result.get("manifest_sha256") != blob_hash(self.manifest_path.read_bytes()):
            raise PackDataError("lattice source manifest hash mismatch; rebuild required")
        entries = as_sequence(result.get("versions"))
        if content_id(entries) != result.get("lattice_hash"):
            raise PackDataError("lattice metadata hash mismatch")
        return result

    def _manifest(self) -> Mapping[str, Any]:
        if not self.manifest_path.is_file():
            raise PackDataError(f"source manifest is missing: {self.manifest_path}")
        try:
            raw: Any = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise PackDataError(f"source manifest cannot be read: {error}") from error
        manifest = as_mapping(raw)
        if manifest.get("schema_version") != 1:
            raise PackDataError("source manifest schema_version must be 1")
        self._expected_versions(manifest)
        return manifest

    def _expected_versions(self, manifest: Mapping[str, Any]) -> tuple[str, ...]:
        values = as_sequence(manifest.get("expected_versions"))
        if not values or any(
            not isinstance(item, str) or re.fullmatch(r"v[1-9][0-9]*", item) is None
            for item in values
        ):
            raise PackDataError("manifest must name a nonempty version lattice")
        versions = tuple(str(item) for item in values)
        numbers = tuple(int(item[1:]) for item in versions)
        if numbers != tuple(range(19, numbers[-1] + 1)):
            raise PackDataError("manifest lattice must be consecutive from v19")
        return versions

    def _version_entries(self, manifest: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
        entries = tuple(as_mapping(item) for item in as_sequence(manifest.get("versions")))
        ids = tuple(self._required_text(item, "id") for item in entries)
        expected = self._expected_versions(manifest)
        if ids != expected:
            raise PackDataError(f"version metadata must be ordered {expected}, got {ids}")
        return entries

    def _source_entries(self, manifest: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
        entries = tuple(as_mapping(item) for item in as_sequence(manifest.get("sources")))
        if not entries:
            raise PackDataError("source manifest contains no sources")
        paths = [self._required_text(item, "path") for item in entries]
        if len(paths) != len(set(paths)):
            raise PackDataError("source manifest contains duplicate paths")
        return entries

    def _jsonl(self, payload: bytes, relative: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for number, line in enumerate(payload.decode("utf-8").splitlines(), start=1):
            if not line:
                raise PackDataError(f"blank JSONL record in {relative}:{number}")
            try:
                raw: Any = json.loads(line)
            except json.JSONDecodeError as error:
                raise PackDataError(f"invalid JSON in {relative}:{number}: {error}") from error
            record = dict(as_mapping(raw))
            if not record:
                raise PackDataError(f"non-object JSONL record in {relative}:{number}")
            records.append(record)
        return records

    def _validate_source_record(
        self, record: Mapping[str, Any], relative: str, version: str, family: str
    ) -> None:
        required = ("subject", "kind", "attributes", "source_url", "retrieved_at", "sha256")
        missing = [key for key in required if key not in record]
        if missing:
            raise PackDataError(f"{relative} record is missing {', '.join(missing)}")
        if record.get("version") != version or record.get("family") != family:
            raise PackDataError(f"{relative} record version/family disagrees with its manifest")
        if len(self._required_text(record, "sha256")) != 64:
            raise PackDataError(f"{relative} record has invalid upstream sha256")
        if not as_mapping(record.get("attributes")):
            raise PackDataError(f"{relative} record has empty attributes")

    def compile_version(
        self, version: str, records: Sequence[dict[str, Any]]
    ) -> tuple[CatalogFact, ...]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            grouped[self._required_text(record, "subject")].append(record)
        facts = [self._reconcile(version, subject, grouped[subject]) for subject in sorted(grouped)]
        return tuple(facts)

    def _reconcile(
        self, version: str, subject: str, records: Sequence[dict[str, Any]]
    ) -> CatalogFact:
        proto_fields = [
            item for item in records if item["family"] == "proto" and item["kind"] == "field"
        ]
        field_records = [
            item for item in records if item["family"] == "field" and item["kind"] == "field"
        ]
        if proto_fields or field_records:
            return self._reconcile_field(version, subject, proto_fields, field_records)
        if len(records) != 1:
            raise PackDataError(f"{version} subject {subject} has duplicate non-field facts")
        record = records[0]
        family = self._required_text(record, "family")
        confidence = "PROVEN" if family == "proto" else "DOCUMENTED"
        return self._fact(record, confidence, (), "RESOLVED")

    def _reconcile_field(
        self,
        version: str,
        subject: str,
        proto_fields: Sequence[dict[str, Any]],
        field_records: Sequence[dict[str, Any]],
    ) -> CatalogFact:
        if len(proto_fields) > 1 or len(field_records) > 1:
            return self._unresolved_field(
                (proto_fields or field_records)[0],
                "a source family reports the field more than once",
            )
        if not proto_fields:
            field = field_records[0]
            if as_mapping(field["attributes"]).get("source_conflicts"):
                return self._unresolved_field(
                    field, "Query Builder sources disagree about the field"
                )
            return self._fact(field, "DOCUMENTED", (), "RESOLVED")
        if not field_records:
            return self._unresolved_field(
                proto_fields[0], "proto field has no Query Builder counterpart"
            )
        proto = proto_fields[0]
        field = field_records[0]
        proto_attributes = as_mapping(proto["attributes"])
        field_attributes = as_mapping(field["attributes"])
        proto_type = proto_attributes.get("data_type")
        field_type = field_attributes.get("data_type")
        types_agree = proto_type == field_type or (
            proto_type == "STRING" and field_type in {"DATE", "RESOURCE_NAME"}
        )
        if (
            not types_agree
            or proto_attributes.get("repeated") != field_attributes.get("repeated")
            or field_attributes.get("source_conflicts")
        ):
            attributes = {
                **dict(field_attributes),
                "proto_data_type": proto_attributes.get("data_type"),
                "conflict": "proto and field catalog type or repetition disagree",
            }
            return self._fact(
                {**field, "attributes": attributes},
                "DOCUMENTED",
                (self._source_ref(proto),),
                "UNKNOWN_PROVIDER_CONTRACT",
            )
        attributes = {**dict(field_attributes), "proto_data_type": proto_attributes["data_type"]}
        return self._fact(
            {**proto, "kind": "field", "attributes": attributes},
            "PROVEN",
            (self._source_ref(field),),
            "RESOLVED",
        )

    def _unresolved_field(self, record: Mapping[str, Any], conflict: str) -> CatalogFact:
        attributes = {**dict(as_mapping(record["attributes"])), "conflict": conflict}
        return self._fact(
            {**record, "attributes": attributes},
            "DOCUMENTED",
            (),
            "UNKNOWN_PROVIDER_CONTRACT",
        )

    def _fact(
        self,
        record: Mapping[str, Any],
        confidence: Confidence,
        corroborating: tuple[Mapping[str, str], ...],
        resolution: str,
    ) -> CatalogFact:
        return CatalogFact(
            subject=self._required_text(record, "subject"),
            kind=self._required_text(record, "kind"),
            attributes=dict(as_mapping(record["attributes"])),
            source_url=self._required_text(record, "source_url"),
            retrieved_at=self._required_text(record, "retrieved_at"),
            sha256=self._required_text(record, "sha256"),
            confidence=confidence,
            corroborating_sources=corroborating,
            resolution=resolution,
        )

    def _verify_raw_source(self, source: Mapping[str, Any], relative: str) -> frozenset[str]:
        raw_relative = self._required_text(source, "raw_path")
        raw_path = (self.source_root / raw_relative).resolve()
        if not raw_path.is_relative_to(self.source_root.resolve()):
            raise PackDataError(f"raw source path escapes source root: {raw_relative}")
        try:
            raw_payload = raw_path.read_bytes()
        except OSError as error:
            raise PackDataError(f"raw source {raw_relative} cannot be read: {error}") from error
        actual = blob_hash(raw_payload)
        expected = self._required_text(source, "raw_sha256")
        if actual != expected:
            raise PackDataError(
                f"raw source hash mismatch for {relative}: expected {expected}, got {actual}"
            )
        inventory = as_mapping(source.get("expected_inventory"))
        if not inventory:
            raise PackDataError(f"source {relative} has no expected_inventory")
        for _key, value in inventory.items():
            if not isinstance(value, int) or isinstance(value, bool):
                raise PackDataError(f"source {relative} has invalid expected_inventory")
        try:
            raw_record = as_mapping(json.loads(raw_payload.decode("utf-8")))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise PackDataError(f"raw inventory {raw_relative} is invalid: {error}") from error
        if as_mapping(raw_record.get("inventory")) != inventory:
            raise PackDataError(f"raw inventory disagrees with manifest for {relative}")
        upstream_files = as_sequence(source.get("upstream_files"))
        if not upstream_files:
            raise PackDataError(f"source {relative} has no retained upstream bytes")
        for item in upstream_files:
            descriptor = as_mapping(item)
            path = (self.source_root / self._required_text(descriptor, "path")).resolve()
            if not path.is_relative_to(self.source_root.resolve()):
                raise PackDataError("upstream path escapes source root")
            compressed = path.read_bytes()
            if blob_hash(compressed) != descriptor.get("sha256"):
                raise PackDataError(f"upstream compressed hash mismatch for {relative}")
            payload = gzip.decompress(compressed)
            if blob_hash(payload) != descriptor.get("upstream_sha256") or len(
                payload
            ) != descriptor.get("bytes"):
                raise PackDataError(f"upstream payload mismatch for {relative}")
        descriptors = [
            as_mapping(item)
            for key in ("files", "pages")
            for item in as_sequence(raw_record.get(key))
        ]
        descriptors.extend(
            as_mapping(raw_record[key]) for key in ("index", "overview") if key in raw_record
        )
        return frozenset(str(item["sha256"]) for item in descriptors)

    def _source_ref(self, record: Mapping[str, Any]) -> Mapping[str, str]:
        return {
            "source_url": self._required_text(record, "source_url"),
            "retrieved_at": self._required_text(record, "retrieved_at"),
            "sha256": self._required_text(record, "sha256"),
        }

    def _required_text(self, record: Mapping[str, Any], key: str) -> str:
        value = as_text(record.get(key))
        if value is None or not value:
            raise PackDataError(f"{key} must be a non-empty string")
        return value


CHANGES = GoogleAdsChanges()
