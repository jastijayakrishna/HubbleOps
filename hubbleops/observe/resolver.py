from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from hubbleops.core.candidate import claim_key as core_claim_key
from hubbleops.core.errors import PathNotInClosure, UnknownClaimType
from hubbleops.core.records import as_mapping, as_sequence
from hubbleops.observe.deps import classify_manifest


def claim_key(record: Mapping[str, Any]) -> str:
    return core_claim_key(record)


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
    "file_unscanned": ("structure", "text"),
    "structure_unsupported": ("structure",),
    "external_boundary": ("structure", "text"),
    "dynamic_state": ("dynamic",),
    "telemetry_state": ("telemetry",),
    "sentinel_state": ("sentinel",),
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
    "dynamic capture of the test suite, or provider telemetry"
)
CLOSE_WITH_RUNTIME_CONFIG = (
    "the value of this key is chosen at runtime: close with a provider telemetry export, "
    "a sentinel event from production, or dynamic capture"
)


@dataclass(frozen=True, slots=True)
class Resolution:
    status: str
    reason: str
    close_with: str | None
    winner_id: str


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
    handler = HANDLERS.get(claim_type)
    if handler is None:
        raise UnknownClaimType(claim_type)
    chosen = winner(records)
    if claim_type in LOCATION_BOUND_CLAIMS:
        path = str(chosen["path"])
        classification = classifications.get(path)
        if classification is None:
            raise PathNotInClosure(claim_type, path)
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
    unresolved = [
        record for record in records if record["provider_subject"] is None and rank(record) == best
    ]
    if versions and unresolved:
        return Resolution(
            status="UNKNOWN",
            reason=(
                f"{location} resolves to {', '.join(versions)} on some structural paths but "
                "other same-precedence paths retain runtime or ambiguous version state"
            ),
            close_with=(
                "resolve every same-precedence wrapper path with configuration evidence, dynamic "
                "capture, or provider telemetry"
            ),
            winner_id=str(chosen["id"]),
        )
    if len(versions) > 1:
        return Resolution(
            status="UNKNOWN",
            reason=(
                f"{location} carries more than one version literal at the same precedence: "
                f"{', '.join(versions)}"
            ),
            close_with=(
                "determine which literal executes here with dynamic capture or provider telemetry"
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
        close_with=(f"commit a {ecosystem} lock file so the installed version is resolvable"),
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
                "DEPENDENCY_STATE_UNKNOWN: no dependency manifest or lock file was found in the "
                "source closure, so the installed package set is unknown; a missing manifest is "
                "not evidence of absence"
            ),
            close_with=("add the dependency manifest or lock file the build actually uses"),
            winner_id=str(chosen["id"]),
        )
    if state == "UNRESOLVED_ECOSYSTEM":
        manifests = ", ".join(str(item) for item in as_sequence(value.get("manifests")))
        return Resolution(
            status="UNKNOWN",
            reason=(
                f"DEPENDENCY_STATE_UNKNOWN: {ecosystem} declares dependencies in {manifests} but "
                "ships no lock file, so the absence of a surface package cannot be proven"
            ),
            close_with=(f"commit a {ecosystem} lock file so absence can be resolved"),
            winner_id=str(chosen["id"]),
        )
    detail = value.get("detail")
    return Resolution(
        status="UNKNOWN",
        reason=(
            f"DEPENDENCY_STATE_UNKNOWN: {chosen['path']} could not be parsed as a {ecosystem} "
            f"manifest: {detail}"
        ),
        close_with=(f"repair or replace {chosen['path']} so the dependency state resolves"),
        winner_id=str(chosen["id"]),
    )


def _file_unscanned(chosen: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> Resolution:
    value = as_mapping(chosen["value"])
    channel = value.get("channel")
    close_with = (
        "restore compatible ast-grep structural scanning and rescan without --force"
        if channel == "structure"
        else f"make {chosen['path']} readable and decodable and rescan"
    )
    return Resolution(
        status="UNKNOWN",
        reason=f"{chosen['path']} could not be scanned: {value.get('reason')}",
        close_with=close_with,
        winner_id=str(chosen["id"]),
    )


def _external_boundary(
    chosen: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> Resolution:
    value = as_mapping(chosen["value"])
    if chosen["observer"] == "structure":
        return Resolution(
            status="UNKNOWN",
            reason=(
                f"captured payload {value.get('payload')} crosses {value.get('callee')} at "
                f"{_location(chosen)}; the receiving service is outside this source proof"
            ),
            close_with=(
                "scan the receiving service in its own ProofScope and bind the carried payload "
                "with dynamic capture or telemetry"
            ),
            winner_id=str(chosen["id"]),
        )
    return Resolution(
        status="UNKNOWN",
        reason=f"{chosen['path']} leaves the closure root, so its contents are outside this proof",
        close_with=(f"scan the tree {chosen['path']} points at as its own ProofScope"),
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
                "dependency observer claims as its own candidate"
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
            "make this location's SDK version explicit in a supported manifest or lock file, "
            "then rescan"
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
    if chosen["observer"] == "structure":
        value = as_mapping(chosen["value"])
        resolution = value.get("resolution")
        if resolution == "UNKNOWN_QUERY_HOLE":
            return Resolution(
                status="UNKNOWN",
                reason=(
                    f"UNKNOWN_QUERY_HOLE at {_location(chosen)}: the structural request "
                    "skeleton retains one or more runtime holes"
                ),
                close_with=(
                    "capture the executed request bound to this source hash or resolve every "
                    "named hole with deterministic evidence"
                ),
                winner_id=str(chosen["id"]),
            )
        if resolution != "CONTRACT_VALIDATION_DEFERRED":
            return Resolution(
                status="UNKNOWN",
                reason=f"{resolution} at {_location(chosen)} during the structural wrapper walk",
                close_with=(
                    "add deterministic evidence for the named missing path or capture the "
                    "executed request bound to this source hash"
                ),
                winner_id=str(chosen["id"]),
            )
        return Resolution(
            status="UNKNOWN",
            reason=(
                f"CONTRACT_VALIDATION_DEFERRED at {_location(chosen)}: structure resolved the "
                "request skeleton but Phase 3 does not validate provider fields"
            ),
            close_with=(
                "run the Phase 6 contract oracle against this source-bound request skeleton"
            ),
            winner_id=str(chosen["id"]),
        )
    return Resolution(
        status="UNKNOWN",
        reason=(
            f"request-language anchor for {chosen['provider_subject']!r} at {_location(chosen)}; "
            "the text observer cannot tell which fields the built request selects"
        ),
        close_with=(
            "extract the request skeleton with the structure observer, or capture the executed "
            "request dynamically"
        ),
        winner_id=str(chosen["id"]),
    )


def _structure_unsupported(
    chosen: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> Resolution:
    value = as_mapping(chosen["value"])
    return Resolution(
        status="UNKNOWN",
        reason=(
            f"STRUCTURE_UNSUPPORTED: {chosen['path']} is an INSIDE "
            f"{value.get('language')} file with no active structural rule bundle"
        ),
        close_with=(
            "add and proof-bind a tested structural rule bundle for this language, then rescan"
        ),
        winner_id=str(chosen["id"]),
    )


def _production_version(
    chosen: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> Resolution:
    value = as_mapping(chosen["value"])
    location = f"{value.get('service')}.{value.get('method')}@{value.get('version')}"
    mappings = as_sequence(value.get("candidate_ids"))
    if chosen["observer"] == "telemetry" and not mappings:
        return Resolution(
            status="UNKNOWN",
            reason=f"TELEMETRY_UNEXPLAINED: {location} maps to no explained candidate",
            close_with=(
                "capture this production operation with a source-bound stack or map it by "
                "recorded human decision"
            ),
            winner_id=str(chosen["id"]),
        )
    if chosen["observer"] == "telemetry" and len(mappings) > 1:
        reason = (
            f"{location} is production-accounted but TELEMETRY_SITE_AMBIGUOUS across "
            f"{len(mappings)} source candidates"
        )
    elif chosen["observer"] in ("dynamic", "sentinel") and not mappings:
        reason = f"OBSERVED_NOT_STATIC: {location} was observed by {chosen['observer']}"
    elif chosen["observer"] in ("dynamic", "sentinel"):
        reason = (
            f"{location} was observed by {chosen['observer']} and maps to "
            f"{len(mappings)} static candidate(s)"
        )
    else:
        reason = f"production operation {location} is explained by observed evidence"
    return Resolution(
        status="AFFECTED",
        reason=reason,
        close_with=None,
        winner_id=str(chosen["id"]),
    )


OBSERVER_STATE_CLOSURES = {
    "HOOK_NOT_INSTALLED": (
        "run capture in proxy mode, or fix the loader so the hook runs, then repeat capture in a "
        "new ProofScope; this run observed nothing and says nothing about usage"
    ),
    "UNKNOWN_DYNAMIC": (
        "exercise this call path from the test command, or capture the same tree in the other "
        "mode, then repeat capture in a new ProofScope"
    ),
    "CAPTURE_EXECUTION_FAILED": (
        "make the test command exit zero inside the sandbox, then repeat capture in a new "
        "ProofScope"
    ),
    "PROXY_INTERCEPTION_FAILED": (
        "make the workload trust the capture certificate, or capture in hook mode, then repeat "
        "capture in a new ProofScope; the requests on those connections were never seen"
    ),
    "PROXY_BYPASS_BLOCKED": (
        "allowlist the destination the workload needs, or capture in hook mode, then repeat "
        "capture in a new ProofScope"
    ),
    "UNKNOWN_WIRE_SIGNATURE": (
        "extend the pack wire signature to name this request, or capture in hook mode, then "
        "repeat capture in a new ProofScope"
    ),
    "STACK_TRUNCATED": (
        "reduce the observed call depth or raise the frame bound, then repeat capture in a new "
        "ProofScope"
    ),
}


def _observer_state(chosen: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> Resolution:
    value = as_mapping(chosen["value"])
    code = str(value.get("code"))
    reason = value.get("reason")
    return Resolution(
        status="UNKNOWN",
        reason=f"{code}: {reason}",
        close_with=OBSERVER_STATE_CLOSURES.get(
            code, "resolve the named observer failure and repeat capture in a new ProofScope"
        ),
        winner_id=str(chosen["id"]),
    )


HANDLERS = {
    "call_version": _call_version,
    "config_reference": _config_reference,
    "dependency_state": _dependency_state,
    "endpoint_reference": _endpoint_reference,
    "external_boundary": _external_boundary,
    "file_unscanned": _file_unscanned,
    "package_reference": _package_reference,
    "request_text": _request_text,
    "sdk_installed": _sdk_installed,
    "structure_unsupported": _structure_unsupported,
    "surface_reference": _surface_reference,
    "production_version": _production_version,
    "dynamic_state": _observer_state,
    "telemetry_state": _observer_state,
    "sentinel_state": _observer_state,
}


def _location(record: Mapping[str, Any]) -> str:
    line = record["line_start"]
    return f"{record['path']}:{line}" if line else str(record["path"])


def _pattern_name(record: Mapping[str, Any]) -> str:
    value = as_mapping(record["value"])
    return str(value.get("pattern", "unknown"))
