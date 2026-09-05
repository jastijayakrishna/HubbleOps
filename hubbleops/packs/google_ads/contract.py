from __future__ import annotations

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from hubbleops.core.canonical import blob_hash, content_id
from hubbleops.core.errors import PackDataError
from hubbleops.core.records import as_mapping, as_sequence, as_text
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

    def validate(self, request: Mapping[str, Any], version: str) -> ValidationResult:
        try:
            normalized = self._version(version)
            catalog = self.catalog(normalized)
        except PackDataError as error:
            return ValidationResult(code="UNKNOWN_PROVIDER_CONTRACT", reason=str(error))
        service = as_text(request.get("service"))
        method = as_text(request.get("method"))
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
            if local is not None:
                return local
            body["query"] = query
            body["validate_only"] = True
            method = "Search"
        elif method == "Mutate":
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
                code="VALID", reason="provider validation accepted", response=response
            )
        if valid is False:
            return ValidationResult(
                code="INVALID", reason="provider validation rejected", response=response
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
                and left.resolution == right.resolution == "RESOLVED"
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
        for field in sorted(fields):
            fact = available.get(field)
            if fact is None:
                complete = any(
                    item.kind == "field_inventory" and item.attributes.get("complete") is True
                    for item in catalog.facts
                )
                return ValidationResult(
                    code="INVALID" if complete else "UNKNOWN_PROVIDER_CONTRACT",
                    reason=f"field {field} is absent from catalog",
                )
            if fact.resolution != "RESOLVED":
                return ValidationResult(
                    code="UNKNOWN_PROVIDER_CONTRACT",
                    reason=f"field {field} has conflicting provider sources",
                )
        return None

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
