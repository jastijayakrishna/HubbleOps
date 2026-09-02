from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from hubbleops.core.errors import UnknownClaimType
from hubbleops.core.records import as_mapping, as_sequence
from hubbleops.observe.deps import classify_manifest

CLAIM_PRECEDENCE: dict[str, tuple[str, ...]] = {
    "sdk_installed": ("lock", "manifest"),
    "call_version": ("per_call", "client_init", "sdk_default", "UNKNOWN"),
    "production_version": ("telemetry", "sentinel", "dynamic", "static"),
    "request_text": ("dynamic", "structure", "text"),
    "surface_reference": ("text",),
    "endpoint_reference": ("text",),
    "package_reference": ("text",),
    "config_reference": ("text",),
    "dependency_state": ("deps",),
    "file_unscanned": ("text",),
    "external_boundary": ("text",),
}

LOCATION_BOUND_CLAIMS = frozenset(
    {
        "call_version",
        "config_reference",
        "endpoint_reference",
        "package_reference",
        "request_text",
        "surface_reference",
    }
)

CLOSE_WITH_CALL_SITE = (
    "resolve the version at this call site: the structure observer's wrapper walk, "
    "dynamic capture of the test suite, or provider telemetry; or record it with "
    "`hops decide <candidate_id> --value <version> --by <name>`"
)
CLOSE_WITH_RUNTIME_CONFIG = (
    "the value of this key is chosen at runtime: close with a provider telemetry export, "
    "a sentinel event from production, dynamic capture; or record it with "
    "`hops decide <candidate_id> --value <version> --by <name>`"
)


@dataclass(frozen=True, slots=True)
class Resolution:
    status: str
    reason: str
    close_with: str | None
    winner_id: str


def claim_key(record: Mapping[str, Any]) -> str:
    claim_type = str(record["claim_type"])
    path = str(record["path"])
    line = record["line_start"]
    subject = record["provider_subject"]
    value = as_mapping(record["value"])
    if claim_type == "call_version":
        return f"{path}:{line}"
    if claim_type in ("surface_reference", "endpoint_reference", "package_reference"):
        return f"{path}:{line}:{subject}"
    if claim_type in ("config_reference", "request_text"):
        return f"{path}:{line}:{subject}"
    if claim_type == "sdk_installed":
        ecosystem = value.get("ecosystem")
        package = value.get("package")
        if value.get("state") == "ABSENT" or package is None:
            return f"{ecosystem}:*"
        return f"{ecosystem}:{str(package).lower().replace('_', '-')}"
    if claim_type == "dependency_state":
        return f"{value.get('state')}:{path}"
    if claim_type in ("file_unscanned", "external_boundary"):
        return path
    raise UnknownClaimType(claim_type)


def rank(record: Mapping[str, Any]) -> int:
    claim_type = str(record["claim_type"])
    table = CLAIM_PRECEDENCE.get(claim_type)
    if table is None:
        raise UnknownClaimType(claim_type)
    slot = _slot(record, claim_type)
    if slot not in table:
        return len(table)
    return table.index(slot)


def winner(records: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    return min(records, key=lambda record: (rank(record), str(record["id"])))


def resolve_claim(
    claim_type: str,
    records: Sequence[Mapping[str, Any]],
    classifications: Mapping[str, str],
) -> Resolution:
    handler = _HANDLERS.get(claim_type)
    if handler is None:
        raise UnknownClaimType(claim_type)
    chosen = winner(records)
    if claim_type in LOCATION_BOUND_CLAIMS:
        classification = classifications.get(str(chosen["path"]), "INSIDE")
        if classification != "INSIDE":
            return Resolution(
                status="EXCLUDED_WITH_EVIDENCE",
                reason=(
                    f"source closure classified {chosen['path']} as {classification}; "
                    "first-party repair does not apply to this region, and the version it "
                    "pins is claimed separately by the dependency observer"
                ),
                close_with=None,
                winner_id=str(chosen["id"]),
            )
    return handler(chosen, records)


def _slot(record: Mapping[str, Any], claim_type: str) -> str:
    value = as_mapping(record["value"])
    if claim_type == "sdk_installed":
        return str(value.get("source_kind", "manifest"))
    if claim_type == "call_version":
        return str(value.get("slot", "UNKNOWN"))
    if claim_type == "request_text":
        return str(record["observer"])
    return str(record["observer"])


def _call_version(chosen: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> Resolution:
    best = rank(chosen)
    versions = sorted(
        {
            str(record["provider_subject"])
            for record in records
            if record["provider_subject"] is not None and rank(record) == best
        }
    )
    location = _location(chosen)
    if len(versions) > 1:
        return Resolution(
            status="UNKNOWN",
            reason=(
                f"{location} carries more than one version literal at the same precedence: "
                f"{', '.join(versions)}"
            ),
            close_with=(
                "determine which literal executes here (dynamic capture or telemetry), then "
                "record it with `hops decide <candidate_id> --value <version> --by <name>`"
            ),
            winner_id=str(chosen["id"]),
        )
    if versions:
        pattern = _pattern_name(chosen)
        return Resolution(
            status="AFFECTED",
            reason=(
                f"explicit version literal {versions[0]} at {location} "
                f"via version carrier {pattern}"
            ),
            close_with=None,
            winner_id=str(chosen["id"]),
        )
    return Resolution(
        status="UNKNOWN",
        reason=f"{location} matched a version carrier but no version literal was resolvable",
        close_with=CLOSE_WITH_CALL_SITE,
        winner_id=str(chosen["id"]),
    )


def _sdk_installed(chosen: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> Resolution:
    value = as_mapping(chosen["value"])
    ecosystem = value.get("ecosystem")
    if value.get("state") == "ABSENT":
        locks = ", ".join(str(item) for item in as_sequence(value.get("locks")))
        return Resolution(
            status="NOT_AFFECTED_WITH_EVIDENCE",
            reason=(
                f"no surface package appears in the resolved {ecosystem} lock file(s): {locks}; "
                "absence is proven by a parsed lock, not assumed from a missing match"
            ),
            close_with=None,
            winner_id=str(chosen["id"]),
        )
    package = value.get("package")
    version = value.get("version")
    source_kind = value.get("source_kind")
    location = _location(chosen)
    if version:
        return Resolution(
            status="AFFECTED",
            reason=(
                f"{ecosystem} package {package} resolved to {version} "
                f"by {source_kind} at {location}"
            ),
            close_with=None,
            winner_id=str(chosen["id"]),
        )
    spec = value.get("spec")
    return Resolution(
        status="UNKNOWN",
        reason=(
            f"{ecosystem} package {package} is declared at {location} as {spec!r}, "
            "which does not resolve to a single installed version"
        ),
        close_with=(
            f"commit a {ecosystem} lock file so the installed version is resolvable, or record "
            "it with `hops decide <candidate_id> --value <version> --by <name>`"
        ),
        winner_id=str(chosen["id"]),
    )


def _dependency_state(
    chosen: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> Resolution:
    value = as_mapping(chosen["value"])
    state = value.get("state")
    ecosystem = value.get("ecosystem")
    if state == "NO_MANIFEST":
        return Resolution(
            status="UNKNOWN",
            reason=(
                "no dependency manifest or lock file was found in the source closure, so the "
                "installed package set is unknown; a missing manifest is not evidence of absence"
            ),
            close_with=(
                "add the dependency manifest or lock file the build actually uses, or record "
                "the installed provider SDK with `hops decide <candidate_id> --value <version> "
                "--by <name>`"
            ),
            winner_id=str(chosen["id"]),
        )
    if state == "UNRESOLVED_ECOSYSTEM":
        manifests = ", ".join(str(item) for item in as_sequence(value.get("manifests")))
        return Resolution(
            status="UNKNOWN",
            reason=(
                f"{ecosystem} declares dependencies in {manifests} but ships no lock file, so "
                "the absence of a surface package cannot be proven"
            ),
            close_with=(
                f"commit a {ecosystem} lock file so absence can be resolved, or record the "
                "installed provider SDK with `hops decide <candidate_id> --value <version> "
                "--by <name>`"
            ),
            winner_id=str(chosen["id"]),
        )
    detail = value.get("detail")
    return Resolution(
        status="UNKNOWN",
        reason=f"{chosen['path']} could not be parsed as a {ecosystem} manifest: {detail}",
        close_with=(
            f"repair or replace {chosen['path']} so the dependency state resolves, or record "
            "the installed provider SDK with `hops decide <candidate_id> --value <version> "
            "--by <name>`"
        ),
        winner_id=str(chosen["id"]),
    )


def _file_unscanned(chosen: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> Resolution:
    value = as_mapping(chosen["value"])
    return Resolution(
        status="UNKNOWN",
        reason=f"{chosen['path']} could not be scanned: {value.get('reason')}",
        close_with=(
            f"make {chosen['path']} readable and decodable and rescan, or record a deliberate "
            "exclusion with `hops decide <candidate_id> --value excluded --by <name>`"
        ),
        winner_id=str(chosen["id"]),
    )


def _external_boundary(
    chosen: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> Resolution:
    return Resolution(
        status="UNKNOWN",
        reason=f"{chosen['path']} leaves the closure root, so its contents are outside this proof",
        close_with=(
            f"scan the tree {chosen['path']} points at as its own ProofScope, or record a "
            "decision with `hops decide <candidate_id> --value <value> --by <name>`"
        ),
        winner_id=str(chosen["id"]),
    )


def _surface_reference(
    chosen: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> Resolution:
    return Resolution(
        status="UNKNOWN",
        reason=(
            f"recall-layer match for {chosen['provider_subject']!r} at {_location(chosen)}; "
            "the text observer proves the reference exists but carries no version"
        ),
        close_with=CLOSE_WITH_CALL_SITE,
        winner_id=str(chosen["id"]),
    )


def _endpoint_reference(
    chosen: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> Resolution:
    return Resolution(
        status="UNKNOWN",
        reason=(
            f"provider endpoint {chosen['provider_subject']!r} referenced at {_location(chosen)} "
            "without a resolvable version in the same line"
        ),
        close_with=CLOSE_WITH_CALL_SITE,
        winner_id=str(chosen["id"]),
    )


def _package_reference(
    chosen: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> Resolution:
    manifest = classify_manifest(str(chosen["path"]))
    if manifest is not None:
        ecosystem, source_kind = manifest
        return Resolution(
            status="UNKNOWN",
            reason=(
                f"package name {chosen['provider_subject']!r} appears at {_location(chosen)}, "
                f"a {ecosystem} {source_kind}; the text observer proves the name is written here "
                "but carries no installed version of its own"
            ),
            close_with=(
                f"read the installed version off the resolved {ecosystem} lock file, which the "
                "dependency observer claims as its own candidate; or record it with "
                "`hops decide <candidate_id> --value <version> --by <name>`"
            ),
            winner_id=str(chosen["id"]),
        )
    return Resolution(
        status="UNKNOWN",
        reason=(
            f"package name {chosen['provider_subject']!r} appears at {_location(chosen)} outside "
            "any recognised manifest, so it resolves to no installed version"
        ),
        close_with=(
            "confirm whether this location installs or pins the SDK (build script, container "
            "image, CI config); then record it with `hops decide <candidate_id> --value "
            "<version> --by <name>`"
        ),
        winner_id=str(chosen["id"]),
    )


def _config_reference(
    chosen: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> Resolution:
    return Resolution(
        status="UNKNOWN",
        reason=(
            f"configuration key {chosen['provider_subject']!r} read at {_location(chosen)}; "
            "its value is not statically decidable"
        ),
        close_with=CLOSE_WITH_RUNTIME_CONFIG,
        winner_id=str(chosen["id"]),
    )


def _request_text(chosen: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> Resolution:
    return Resolution(
        status="UNKNOWN",
        reason=(
            f"request-language anchor for {chosen['provider_subject']!r} at {_location(chosen)}; "
            "the text observer cannot tell which fields the built request selects"
        ),
        close_with=(
            "extract the request skeleton with the structure observer, or capture the executed "
            "request dynamically; or record it with `hops decide <candidate_id> --value "
            "<request> --by <name>`"
        ),
        winner_id=str(chosen["id"]),
    )


_HANDLERS = {
    "call_version": _call_version,
    "config_reference": _config_reference,
    "dependency_state": _dependency_state,
    "endpoint_reference": _endpoint_reference,
    "external_boundary": _external_boundary,
    "file_unscanned": _file_unscanned,
    "package_reference": _package_reference,
    "request_text": _request_text,
    "sdk_installed": _sdk_installed,
    "surface_reference": _surface_reference,
}


def _location(record: Mapping[str, Any]) -> str:
    line = record["line_start"]
    return f"{record['path']}:{line}" if line else str(record["path"])


def _pattern_name(record: Mapping[str, Any]) -> str:
    value = as_mapping(record["value"])
    return str(value.get("pattern", "unknown"))
