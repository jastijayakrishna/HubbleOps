from __future__ import annotations

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from hubbleops.core.canonical import blob_hash, content_id
from hubbleops.core.errors import PackDataError
from hubbleops.core.records import as_mapping, as_sequence, as_text, is_list, is_mapping
from hubbleops.packs._protocol import (
    Catalog,
    CatalogFact,
    Confidence,
    ContractDiff,
    DiffFact,
    Transport,
    ValidationResult,
)
from hubbleops.packs.google_ads.changes import DATA_ROOT, GoogleAdsChanges
from hubbleops.packs.google_ads.composition import compose

FIELD = re.compile(r"\b(?:[a-z][a-z0-9_]*\.)+[a-z][a-z0-9_]*\b")
SEARCH_METHODS = frozenset({"Search", "SearchStream"})
GAQL_SHAPE = re.compile(r"(?is)\bSELECT\b.+\bFROM\s+[a-z][a-z0-9_]*")


def _looks_like_gaql(request: Mapping[str, Any]) -> bool:
    query = as_text(as_mapping(request.get("request")).get("query")) or as_text(
        request.get("query")
    )
    return query is not None and GAQL_SHAPE.search(query) is not None


GOOGLE_ADS_SERVICE = "GoogleAdsService"
SINK_METHODS = {"search": "Search", "search_stream": "SearchStream", "mutate": "Mutate"}
MUTATE_REQUEST = "services.google_ads_service.MutateGoogleAdsRequest"
PROTO_SCALARS = frozenset(
    {
        "bool",
        "bytes",
        "double",
        "fixed32",
        "fixed64",
        "float",
        "int32",
        "int64",
        "sfixed32",
        "sfixed64",
        "sint32",
        "sint64",
        "string",
        "uint32",
        "uint64",
    }
)
PROTO_BOOL = frozenset({"bool", "google.protobuf.BoolValue"})
PROTO_STRING = frozenset(
    {
        "bytes",
        "string",
        "google.protobuf.BytesValue",
        "google.protobuf.Duration",
        "google.protobuf.FieldMask",
        "google.protobuf.StringValue",
        "google.protobuf.Timestamp",
    }
)
PROTO_FLOAT = frozenset(
    {"double", "float", "google.protobuf.DoubleValue", "google.protobuf.FloatValue"}
)
PROTO_SIGNED_32 = frozenset({"int32", "sfixed32", "sint32", "google.protobuf.Int32Value"})
PROTO_SIGNED_64 = frozenset({"int64", "sfixed64", "sint64", "google.protobuf.Int64Value"})
PROTO_UNSIGNED_32 = frozenset({"fixed32", "uint32", "google.protobuf.UInt32Value"})
PROTO_UNSIGNED_64 = frozenset({"fixed64", "uint64", "google.protobuf.UInt64Value"})
WELL_KNOWN_JSON_SCALARS: frozenset[str] = (
    PROTO_BOOL
    | PROTO_STRING
    | PROTO_FLOAT
    | PROTO_SIGNED_32
    | PROTO_SIGNED_64
    | PROTO_UNSIGNED_32
    | PROTO_UNSIGNED_64
) - PROTO_SCALARS
WELL_KNOWN_JSON_ANY = frozenset({"google.protobuf.NullValue", "google.protobuf.Value"})
WELL_KNOWN_JSON_OBJECTS = frozenset({"google.protobuf.Any", "google.protobuf.Struct"})
WELL_KNOWN_JSON_ARRAYS = frozenset({"google.protobuf.ListValue"})


class UnavailableTransport:
    @property
    def available(self) -> bool:
        return False

    def validate(
        self,
        *,
        service: str,
        method: str,
        version: str,
        request: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        raise PackDataError("the shipped Google Ads transport performs no network I/O")


class GoogleAdsContract:
    def __init__(
        self,
        data_root: Path = DATA_ROOT,
        transport: Transport | None = None,
    ) -> None:
        self.data_root = data_root
        self.changes = GoogleAdsChanges(data_root)
        self.transport = transport or UnavailableTransport()
        self._catalogs: dict[str, Catalog] = {}
        self._diffs: dict[str, ContractDiff] = {}
        self._shape_indexes: dict[
            str,
            tuple[
                frozenset[str],
                frozenset[str],
                dict[str, dict[str, CatalogFact]],
            ],
        ] = {}

    def context_hash(self) -> str:
        credentials = getattr(self.transport, "credentials", None)
        authority = {
            "customer_id": getattr(credentials, "customer_id", None),
            "login_customer_id": getattr(credentials, "login_customer_id", None),
        }
        return content_id(
            {
                "authority": authority,
                "available": self.transport.available,
                "contract_implementation_sha256": blob_hash(Path(__file__).read_bytes()),
                "transport": (
                    f"{type(self.transport).__module__}.{type(self.transport).__qualname__}"
                ),
                "transport_implementation_sha256": blob_hash(
                    Path(__file__).with_name("transport.py").read_bytes()
                ),
            }
        )

    def catalog(self, version: str) -> Catalog:
        normalized = self._version(version)
        path = self.data_root / f"catalog_{normalized}.jsonl"
        if not path.is_file():
            raise PackDataError(f"catalog is missing for {normalized}")
        payload = path.read_bytes()
        expected = {item.id: item.catalog_hash for item in self.changes.versions()}[normalized]
        if blob_hash(payload) != expected:
            raise PackDataError(f"catalog hash mismatch for {normalized}")
        cached = self._catalogs.get(expected)
        if cached is not None:
            return deepcopy(cached)
        facts: list[CatalogFact] = []
        for number, line in enumerate(payload.decode("utf-8").splitlines(), start=1):
            if not line:
                raise PackDataError(f"blank catalog record in {path.name}:{number}")
            try:
                raw: Any = json.loads(line)
            except json.JSONDecodeError as error:
                raise PackDataError(f"invalid JSON in {path.name}:{number}: {error}") from error
            record = as_mapping(raw)
            confidence = record.get("confidence")
            if confidence not in ("PROVEN", "DOCUMENTED"):
                raise PackDataError(f"invalid confidence in {path.name}:{number}")
            corroborating = as_sequence(record.get("corroborating_sources"))
            if record.get("corroborating_sources") is not None and not isinstance(
                record.get("corroborating_sources"), list
            ):
                raise PackDataError(f"invalid corroborating_sources in {path.name}:{number}")
            facts.append(
                CatalogFact(
                    subject=self._text(record, "subject", path, number),
                    kind=self._text(record, "kind", path, number),
                    attributes=dict(as_mapping(record.get("attributes"))),
                    source_url=self._text(record, "source_url", path, number),
                    retrieved_at=self._text(record, "retrieved_at", path, number),
                    sha256=self._text(record, "sha256", path, number),
                    confidence=confidence,
                    corroborating_sources=tuple(dict(as_mapping(item)) for item in corroborating),
                    resolution=str(record.get("resolution", "RESOLVED")),
                )
            )
        subjects = [fact.subject for fact in facts]
        if subjects != sorted(subjects) or len(subjects) != len(set(subjects)):
            raise PackDataError(f"catalog {normalized} is not uniquely sorted by subject")
        result = Catalog(version=normalized, facts=tuple(facts), sha256=blob_hash(payload))
        if result.sha256 != expected:
            raise PackDataError(
                f"catalog hash mismatch for {normalized}: expected {expected}, got {result.sha256}"
            )
        self._catalogs[expected] = deepcopy(result)
        return result

    def diff(self, version_from: str, version_to: str) -> ContractDiff:
        source = self._version(version_from)
        target = self._version(version_to)
        lattice = tuple(item.id for item in self.changes.versions())
        start = lattice.index(source)
        end = lattice.index(target)
        if start >= end:
            raise PackDataError("contract diffs require an earlier source and later target")
        source_catalog = self.catalog(source)
        target_catalog = self.catalog(target)
        pair_hash = content_id([source_catalog.sha256, target_catalog.sha256])
        cache_key = (
            pair_hash
            if end == start + 1
            else content_id(
                {
                    "pair_hash": pair_hash,
                    "hops": [self.catalog(node).sha256 for node in lattice[start : end + 1]],
                }
            )
        )
        cached = self._diffs.get(cache_key)
        if cached is not None:
            return deepcopy(cached)
        hops = [self._adjacent(lattice[index], lattice[index + 1]) for index in range(start, end)]
        result = hops[0] if len(hops) == 1 else self._compose(source_catalog, target_catalog, hops)
        result = ContractDiff(
            from_version=source,
            to_version=target,
            pair_hash=pair_hash,
            facts=result.facts,
            hops=result.hops,
        )
        self._diffs[cache_key] = deepcopy(result)
        return result

    def _target(self, request: Mapping[str, Any]) -> tuple[str | None, str | None]:
        service = as_text(request.get("service"))
        method = as_text(request.get("method"))
        if service and method:
            return service, method
        sink = as_text(as_mapping(request.get("sink")).get("name"))
        resolved = SINK_METHODS.get(sink.rsplit(".", 1)[-1]) if sink is not None else None
        if resolved is None and _looks_like_gaql(request):
            resolved = "Search"
        if resolved is None:
            return service, method
        return service or GOOGLE_ADS_SERVICE, method or resolved

    def validate(self, request: Mapping[str, Any], version: str) -> ValidationResult:
        try:
            normalized = self._version(version)
            catalog = self.catalog(normalized)
        except PackDataError as error:
            return ValidationResult(code="UNKNOWN_PROVIDER_CONTRACT", reason=str(error))
        service, method = self._target(request)
        body = dict(as_mapping(request.get("request")))
        body.pop("validateOnly", None)
        if service != "GoogleAdsService" or not method:
            return ValidationResult(
                code="UNKNOWN_PROVIDER_CONTRACT",
                reason="validation requires a GoogleAdsService method and request mapping",
            )
        if method in SEARCH_METHODS:
            query = as_text(body.get("query")) or as_text(request.get("query"))
            if not query:
                return ValidationResult(
                    code="UNKNOWN_PROVIDER_CONTRACT",
                    reason="Search validation requires a literal GAQL query",
                )
            local = self._validate_fields(query, catalog)
            if local is not None and (local.code != "VALID" or not self.transport.available):
                return local
            body["query"] = query
            body["validate_only"] = True
            method = "Search"
        elif method == "Mutate":
            local = self._validate_mutate(body, catalog)
            if local is not None and (local.code != "VALID" or not self.transport.available):
                return local
            body["validate_only"] = True
        else:
            return ValidationResult(
                code="UNKNOWN_PROVIDER_CONTRACT",
                reason=f"{method} has no approved validation-only operation",
            )
        if not self.transport.available:
            return ValidationResult(
                code="ORACLE_UNAVAILABLE",
                reason="Google Ads validation transport is unavailable",
            )
        try:
            response = self.transport.validate(
                service=service,
                method=method,
                version=normalized,
                request=body,
            )
        except (OSError, PackDataError) as error:
            return ValidationResult(code="ORACLE_UNAVAILABLE", reason=str(error))
        valid = response.get("valid")
        if valid is True:
            return ValidationResult(
                code="VALID",
                reason="provider validation accepted",
                response=response,
                authority="LIVE",
            )
        if valid is False:
            provider_error = as_text(response.get("provider_error"))
            return ValidationResult(
                code="INVALID",
                reason=provider_error or "provider validation rejected",
                response=response,
            )
        return ValidationResult(
            code="ORACLE_UNAVAILABLE",
            reason="provider validation returned no boolean valid result",
            response=response,
        )

    def _adjacent(self, version_from: str, version_to: str) -> ContractDiff:
        source = self.catalog(version_from)
        target = self.catalog(version_to)
        pair_hash = content_id([source.sha256, target.sha256])
        cached = self._diffs.get(pair_hash)
        if cached is not None:
            return deepcopy(cached)
        before = {fact.subject: fact for fact in source.facts}
        after = {fact.subject: fact for fact in target.facts}
        docs = self._documented_changes(target)
        facts: list[DiffFact] = []
        for subject in sorted(set(before) | set(after)):
            left = before.get(subject)
            right = after.get(subject)
            if (
                left is not None
                and right is not None
                and left.attributes == right.attributes
                and subject not in docs
            ):
                continue
            documented = docs.get(subject, ())
            replacements = {
                replacement
                for item in documented
                for replacement in [as_text(item.attributes.get("replacement"))]
                if replacement
            }
            replacement = next(iter(replacements)) if len(replacements) == 1 else None
            conflict = any(item.resolution != "RESOLVED" for item in (left, right) if item)
            conflict = conflict or len(replacements) > 1
            conflict = conflict or (
                replacement is not None and (right is not None or replacement not in after)
            )
            if conflict:
                result = "UNKNOWN_PROVIDER_CONTRACT"
                reason = "catalog sources or documented replacements conflict"
                confidence: Confidence = "DOCUMENTED"
            else:
                result = "VALID"
                reason = "computed from consecutive normalized catalogs"
                confidence = self._confidence(left, right)
            change = "ADDED" if left is None else "REMOVED" if right is None else "CHANGED"
            facts.append(
                DiffFact(
                    subject=subject,
                    change=change,
                    before=None if left is None else left.attributes,
                    after=None if right is None else right.attributes,
                    replacement=replacement,
                    confidence=confidence,
                    result=result,
                    reason=reason,
                )
            )
        result_diff = ContractDiff(version_from, version_to, pair_hash, tuple(facts))
        self._diffs[pair_hash] = deepcopy(result_diff)
        return result_diff

    def _compose(
        self,
        source: Catalog,
        target: Catalog,
        hops: list[ContractDiff],
    ) -> ContractDiff:
        result = hops[0]
        for hop in hops[1:]:
            result = compose(result, hop)
        return ContractDiff(source.version, target.version, "", result.facts, result.hops)

    def _validate_fields(self, query: str, catalog: Catalog) -> ValidationResult | None:
        unquoted = re.sub(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"", " ", query)
        fields = {match.group(0) for match in FIELD.finditer(unquoted)}
        available = {fact.subject: fact for fact in catalog.facts if fact.kind == "field"}
        complete = any(
            item.kind == "field_inventory" and item.attributes.get("complete") is True
            for item in catalog.facts
        )
        for field in sorted(fields):
            fact = available.get(field)
            if fact is None:
                return ValidationResult(
                    code="INVALID" if complete else "UNKNOWN_PROVIDER_CONTRACT",
                    reason=f"field {field} is absent from catalog",
                )
            if fact.resolution != "RESOLVED":
                return ValidationResult(
                    code="UNKNOWN_PROVIDER_CONTRACT",
                    reason=f"field {field} has conflicting provider sources",
                )
        if not fields or not complete:
            return None
        return ValidationResult(
            code="VALID",
            reason=(
                f"every field resolves against the complete {catalog.version} field inventory; "
                "catalog authority does not cover selectability, filterability, segmentation or "
                "resource pairing"
            ),
            authority="CATALOG",
        )

    def _validate_mutate(
        self, body: Mapping[str, Any], catalog: Catalog
    ) -> ValidationResult | None:
        normalized, conflict = self._normalized_fields(body, {"operations": "mutate_operations"})
        if conflict is not None:
            return ValidationResult(code="UNKNOWN_PROVIDER_CONTRACT", reason=conflict)
        operations = normalized.get("mutate_operations")
        if operations is None or not is_list(operations) or not operations:
            if operations is not None and not is_list(operations):
                return ValidationResult(
                    code="INVALID",
                    reason="MutateGoogleAdsRequest.mutate_operations must be a repeated field",
                )
            return None
        checked = self._validate_message(
            body,
            MUTATE_REQUEST,
            catalog,
            aliases={"operations": "mutate_operations"},
            depth=0,
        )
        if checked is not None:
            return checked
        for number, operation in enumerate(operations, start=1):
            if not is_mapping(operation) or not operation:
                return ValidationResult(
                    code="UNKNOWN_PROVIDER_CONTRACT",
                    reason=f"mutate operation {number} has no catalog-provable operation shape",
                )
            selectors, selector_conflict = self._normalized_fields(as_mapping(operation), {})
            if selector_conflict is not None:
                return ValidationResult(code="UNKNOWN_PROVIDER_CONTRACT", reason=selector_conflict)
            if len(selectors) != 1:
                return ValidationResult(
                    code="UNKNOWN_PROVIDER_CONTRACT",
                    reason=(
                        f"mutate operation {number} selects {len(selectors)} operation messages; "
                        "the catalog does not encode oneof semantics"
                    ),
                )
        return ValidationResult(
            code="VALID",
            reason=(
                f"every supplied mutate field resolves through the {catalog.version} message and "
                "proto-field catalog; catalog authority proves protobuf JSON shape only and does "
                "not cover required fields, oneof semantics, resource constraints or provider "
                "business rules"
            ),
            authority="CATALOG",
        )

    def _validate_message(
        self,
        value: Mapping[str, Any],
        owner: str,
        catalog: Catalog,
        *,
        aliases: Mapping[str, str],
        depth: int,
    ) -> ValidationResult | None:
        if depth >= 64:
            return ValidationResult(
                code="UNKNOWN_PROVIDER_CONTRACT",
                reason="mutate request exceeds the 64-message validation depth",
            )
        messages, enums, fields_by_owner = self._shape_index(catalog)
        fields = fields_by_owner.get(owner)
        if owner not in messages or fields is None:
            return ValidationResult(
                code="UNKNOWN_PROVIDER_CONTRACT",
                reason=f"message {owner} is absent from the catalog",
            )
        normalized, conflict = self._normalized_fields(value, aliases)
        if conflict is not None:
            return ValidationResult(code="UNKNOWN_PROVIDER_CONTRACT", reason=conflict)
        for name, field_value in sorted(normalized.items()):
            fact = fields.get(name)
            if fact is None:
                return ValidationResult(
                    code="UNKNOWN_PROVIDER_CONTRACT",
                    reason=f"field {owner}.{name} is absent from the message catalog",
                )
            if fact.resolution != "RESOLVED":
                return ValidationResult(
                    code="UNKNOWN_PROVIDER_CONTRACT",
                    reason=f"field {owner}.{name} has conflicting provider sources",
                )
            repeated = fact.attributes.get("repeated") is True
            if repeated != is_list(field_value):
                expected = "a repeated field" if repeated else "a singular field"
                return ValidationResult(code="INVALID", reason=f"{owner}.{name} must be {expected}")
            values = as_sequence(field_value) if repeated else (field_value,)
            proto_type = as_text(fact.attributes.get("proto_type"))
            if proto_type is None:
                return ValidationResult(
                    code="UNKNOWN_PROVIDER_CONTRACT",
                    reason=f"field {owner}.{name} has no cataloged protobuf type",
                )
            for item in values:
                result = self._validate_typed_value(
                    item,
                    proto_type,
                    owner,
                    catalog,
                    enums,
                    messages,
                    depth + 1,
                )
                if result is not None:
                    return result
        return None

    def _validate_typed_value(
        self,
        value: Any,
        proto_type: str,
        owner: str,
        catalog: Catalog,
        enums: frozenset[str],
        messages: frozenset[str],
        depth: int,
    ) -> ValidationResult | None:
        normalized_type = proto_type.removeprefix(".")
        if value is None:
            return None
        if normalized_type in WELL_KNOWN_JSON_ANY:
            return None
        if normalized_type in WELL_KNOWN_JSON_OBJECTS:
            if not is_mapping(value):
                return ValidationResult(
                    code="INVALID", reason=f"protobuf message {normalized_type} must be an object"
                )
            return None
        if normalized_type in WELL_KNOWN_JSON_ARRAYS:
            if not is_list(value):
                return ValidationResult(
                    code="INVALID", reason=f"protobuf message {normalized_type} must be an array"
                )
            return None
        if normalized_type in PROTO_SCALARS or normalized_type in WELL_KNOWN_JSON_SCALARS:
            if not self._valid_scalar(normalized_type, value):
                return ValidationResult(
                    code="INVALID",
                    reason=f"value does not match protobuf JSON scalar {normalized_type}",
                )
            return None
        message = self._resolve_type(normalized_type, owner, messages)
        if message is not None:
            if not is_mapping(value):
                return ValidationResult(
                    code="INVALID", reason=f"protobuf message {message} must be an object"
                )
            return self._validate_message(
                as_mapping(value), message, catalog, aliases={}, depth=depth
            )
        if self._resolve_type(normalized_type, owner, enums) is not None:
            if not isinstance(value, str | int) or isinstance(value, bool):
                return ValidationResult(
                    code="INVALID", reason=f"protobuf enum {normalized_type} must be a scalar"
                )
            return None
        return ValidationResult(
            code="UNKNOWN_PROVIDER_CONTRACT",
            reason=f"protobuf type {normalized_type} cannot be resolved from the catalog",
        )

    def _valid_scalar(self, proto_type: str, value: Any) -> bool:
        if proto_type in PROTO_BOOL:
            return isinstance(value, bool)
        if proto_type in PROTO_STRING:
            return isinstance(value, str)
        if proto_type in PROTO_FLOAT:
            return (isinstance(value, int | float) and not isinstance(value, bool)) or value in (
                "NaN",
                "Infinity",
                "-Infinity",
            )
        if proto_type in PROTO_SIGNED_32:
            return self._bounded_integer(value, -(2**31), 2**31 - 1)
        if proto_type in PROTO_SIGNED_64:
            return self._bounded_integer(value, -(2**63), 2**63 - 1)
        if proto_type in PROTO_UNSIGNED_32:
            return self._bounded_integer(value, 0, 2**32 - 1)
        if proto_type in PROTO_UNSIGNED_64:
            return self._bounded_integer(value, 0, 2**64 - 1)
        return False

    def _bounded_integer(self, value: Any, minimum: int, maximum: int) -> bool:
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, str) and re.fullmatch(r"-?(?:0|[1-9][0-9]*)", value):
            parsed = int(value)
        else:
            return False
        return minimum <= parsed <= maximum

    def _shape_index(
        self, catalog: Catalog
    ) -> tuple[
        frozenset[str],
        frozenset[str],
        dict[str, dict[str, CatalogFact]],
    ]:
        cached = self._shape_indexes.get(catalog.sha256)
        if cached is not None:
            return cached
        messages = frozenset(
            fact.subject.removeprefix("message.")
            for fact in catalog.facts
            if fact.kind == "message"
        )
        enums = frozenset(
            fact.subject.removeprefix("enum.") for fact in catalog.facts if fact.kind == "enum"
        )
        fields_by_owner: dict[str, dict[str, CatalogFact]] = {}
        for fact in catalog.facts:
            if fact.kind != "proto_field":
                continue
            owner, separator, name = fact.subject.removeprefix("proto_field.").rpartition(".")
            if separator:
                fields_by_owner.setdefault(owner, {})[name] = fact
        result = messages, enums, fields_by_owner
        self._shape_indexes[catalog.sha256] = result
        return result

    def _resolve_type(self, proto_type: str, owner: str, available: frozenset[str]) -> str | None:
        type_path = proto_type.removeprefix("google.ads.googleads.VERSION.")
        owner_module = owner.rsplit(".", 1)[0]
        exact = f"{owner_module}.{type_path}"
        if exact in available:
            return exact
        family, separator, remainder = type_path.partition(".")
        candidates = sorted(
            item
            for item in available
            if item.endswith(f".{type_path}")
            or (separator and item.startswith(f"{family}.") and item.endswith(f".{remainder}"))
            or (not separator and item.endswith(f".{type_path}"))
        )
        return candidates[0] if len(candidates) == 1 else None

    def _normalized_fields(
        self, value: Mapping[str, Any], aliases: Mapping[str, str]
    ) -> tuple[dict[str, Any], str | None]:
        result: dict[str, Any] = {}
        for raw_name, field_value in cast(Mapping[object, Any], value).items():
            if not isinstance(raw_name, str) or not raw_name:
                return {}, "mutate request field names must be non-empty strings"
            name = aliases.get(self._snake_case(raw_name), self._snake_case(raw_name))
            if name in result:
                return {}, f"mutate request supplies more than one spelling of {name}"
            result[name] = field_value
        return result, None

    def _snake_case(self, value: str) -> str:
        leading = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
        return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", leading).lower()

    def _documented_changes(self, catalog: Catalog) -> dict[str, tuple[CatalogFact, ...]]:
        result: dict[str, list[CatalogFact]] = {}
        for fact in catalog.facts:
            subject = as_text(fact.attributes.get("change_subject"))
            if fact.kind == "documented_change" and subject:
                result.setdefault(subject, []).append(fact)
        return {key: tuple(value) for key, value in result.items()}

    def _confidence(self, left: CatalogFact | None, right: CatalogFact | None) -> Confidence:
        present = tuple(item for item in (left, right) if item is not None)
        return (
            "PROVEN"
            if present and all(item.confidence == "PROVEN" for item in present)
            else "DOCUMENTED"
        )

    def _version(self, value: str) -> str:
        normalized = value.lower()
        available = tuple(item.id for item in self.changes.versions())
        if normalized not in available:
            raise PackDataError(
                f"unsupported Google Ads version {value!r}; expected one of {available}"
            )
        return normalized

    def _text(self, record: Mapping[str, Any], key: str, path: Path, number: int) -> str:
        value = as_text(record.get(key))
        if value is None or not value:
            raise PackDataError(f"missing {key} in {path.name}:{number}")
        return value


CONTRACT = GoogleAdsContract()


def query_resources(data_root: Path = DATA_ROOT) -> tuple[str, ...]:
    resources: set[str] = set()
    for path in sorted(data_root.glob("catalog_v*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                record: object
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError as error:
                    raise PackDataError(
                        f"{path.name}:{number} is not valid JSON: {error}"
                    ) from error
                fact = as_mapping(record)
                if fact.get("kind") != "field":
                    continue
                subject = as_text(fact.get("subject"))
                if not subject:
                    raise PackDataError(f"{path.name}:{number} is a field fact without a subject")
                resources.add(subject.split(".", 1)[0].lower())
    if not resources:
        raise PackDataError(
            f"no field facts under {data_root}; the query-language resource set is derived from "
            "the catalog and an empty set would silently disable resource classification"
        )
    return tuple(sorted(resources))
