from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from hubbleops.core.candidate import claim_key as core_claim_key
from hubbleops.core.errors import PathNotInClosure, UnknownClaimType
from hubbleops.core.records import as_line, as_mapping, as_sequence, as_text
from hubbleops.core.surface import SurfaceSpec
from hubbleops.observe.deps import classify_manifest


def claim_key(record: Mapping[str, Any]) -> str:
    return core_claim_key(record)


CLAIM_PRECEDENCE: dict[str, tuple[str, ...]] = {
    "bulk_data_reference": ("text",),
    "sdk_installed": ("lock", "manifest"),
    "call_version": ("per_call", "client_init", "sdk_default", "UNKNOWN"),
    "production_version": ("telemetry", "sentinel", "dynamic", "static"),
    "request_text": ("dynamic", "structure", "text"),
    "surface_reference": ("structure", "text"),
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
    "adjacent_contract": ("text",),
    "contract_surface": ("structure", "text"),
}

LOCATION_BOUND_CLAIMS = frozenset(
    {
        "call_version",
        "config_reference",
        "endpoint_reference",
        "package_reference",
        "request_text",
        "surface_reference",
        "adjacent_contract",
        "contract_surface",
    }
)

NON_CODE_CARRIER_CLAIMS = frozenset(
    {
        "config_reference",
        "contract_surface",
        "endpoint_reference",
        "package_reference",
        "request_text",
        "surface_reference",
    }
)

COMMENT_NODE_KIND = "comment"
IMPORT_NODE_KIND = "import"
ENVIRONMENT_READ_NODE_KIND = "environment_read"
DEFERRED_VALIDATION = "CONTRACT_VALIDATION_DEFERRED"
DIRECT_CONTEXT = "direct"
NO_CONTEXT = "none"
LITERAL_TERMINAL = "LITERAL"

DOCUMENTATION_ROLE = "DOCUMENTATION"
STYLESHEET_ROLE = "STYLESHEET"
CONFIG_ROLE = "CONFIG"
LOCK_KIND = "lock"

TEXT_CLAIMS_A_STRUCTURAL_LINE_EXPLAINS = frozenset(
    {"config_reference", "endpoint_reference", "request_text", "surface_reference"}
)
CONTEXT_GATED_TEXT_CLAIMS = frozenset({"contract_surface", "request_text"})
GENERIC_SINK_CLAIMS = frozenset({"request_text"})
LATTICE_DECIDABLE_CLAIMS = frozenset(
    {
        "config_reference",
        "contract_surface",
        "package_reference",
        "request_text",
        "surface_reference",
    }
)
REQUEST_RESOURCE_KIND = "request_resource"
STABLE_DISPOSITION = "STABLE"
CHANGES_AT_PREFIX = "CHANGES_AT:"

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


@dataclass(frozen=True, slots=True)
class FileVersionEvidence:
    version: str
    sites: int
    example_id: str
    example_location: str


@dataclass(frozen=True, slots=True)
class ResolutionContext:
    surface: SurfaceSpec | None = None
    validations: Mapping[str, Mapping[str, Any]] = field(default_factory=dict[str, Any])
    file_contexts: Mapping[str, str] = field(default_factory=dict[str, str])
    structural_lines: Mapping[tuple[str, int], str] = field(
        default_factory=dict[tuple[str, int], str]
    )
    stability: Mapping[str, str] = field(default_factory=dict[str, str])
    lattice: tuple[str, ...] = ()

    def version_keys(self) -> frozenset[str]:
        return frozenset(self.surface.config_env_keys) if self.surface else frozenset()

    def direct_context(self, path: str) -> bool:
        return self.file_contexts.get(path) == DIRECT_CONTEXT

    def names_provider_surface(self, text: str) -> bool:
        if self.surface is None:
            return True
        lowered = text.lower()
        if any(host.lower() in lowered for host in self.surface.hosts):
            return True
        for carrier in self.surface.version_carriers:
            if re.search(carrier.regex, text):
                return True
        for language in self.surface.request_languages:
            if any(re.search(shape, text) for shape in language.shapes):
                return True
        return False


def resolution_context(
    evidence: Sequence[Mapping[str, Any]],
    surface: SurfaceSpec | None,
    validations: Mapping[str, Mapping[str, Any]] | None,
    stability: Mapping[str, str] | None = None,
    lattice: Sequence[str] = (),
) -> ResolutionContext:
    contexts: dict[str, str] = {}
    lines: dict[tuple[str, int], str] = {}
    for record in evidence:
        path = str(record["path"])
        value = as_mapping(record.get("value"))
        scope = as_text(value.get("file_context"))
        if record["observer"] == "text" and scope is not None:
            if scope == DIRECT_CONTEXT or path not in contexts:
                contexts[path] = scope
        if record["observer"] != "structure":
            continue
        line = as_line(record.get("line_start"))
        if line is None:
            continue
        resolved = value.get("resolution") == DEFERRED_VALIDATION or any(
            as_text(as_mapping(item).get("terminal")) == LITERAL_TERMINAL
            for item in as_sequence(value.get("paths"))
        )
        if resolved and str(record["claim_type"]) in ("call_version", "request_text"):
            key = (path, line)
            if key not in lines or str(record["id"]) < lines[key]:
                lines[key] = str(record["id"])
    return ResolutionContext(
        surface=surface,
        validations=dict(validations or {}),
        file_contexts=contexts,
        structural_lines=lines,
        stability=dict(stability or {}),
        lattice=tuple(lattice),
    )


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
    roles: Mapping[str, str] | None = None,
    path_versions: Mapping[str, FileVersionEvidence] | None = None,
    context: ResolutionContext | None = None,
) -> Resolution:
    handler = HANDLERS.get(claim_type)
    if handler is None:
        raise UnknownClaimType(claim_type)
    chosen = winner(records)
    ctx = context or ResolutionContext()
    if claim_type in LOCATION_BOUND_CLAIMS:
        path = str(chosen["path"])
        classification = classifications.get(path)
        if classification is None:
            raise PathNotInClosure(claim_type, path)
        if classification != "INSIDE":
            return _also_claimed(
                chosen,
                Resolution(
                    status="EXCLUDED_WITH_EVIDENCE",
                    reason=(
                        f"source closure classified {chosen['path']} as {classification}; "
                        "first-party repair does not apply to this region, and the version it "
                        "pins is claimed separately by the dependency observer"
                    ),
                    close_with=None,
                    winner_id=str(chosen["id"]),
                ),
            )
    carrier = (
        _non_code_carrier(chosen, roles or {}, ctx)
        if claim_type in NON_CODE_CARRIER_CLAIMS
        else None
    )
    if carrier is not None:
        return _also_claimed(chosen, carrier)
    resolution = _with_context(chosen, claim_type, handler(chosen, records), ctx)
    bound = (
        _file_version_binding(chosen, resolution, path_versions or {})
        if claim_type == "surface_reference"
        else None
    )
    return _also_claimed(chosen, bound or resolution)


def _with_context(
    chosen: Mapping[str, Any], claim_type: str, resolution: Resolution, ctx: ResolutionContext
) -> Resolution:
    if resolution.status != "UNKNOWN":
        return resolution
    if chosen["observer"] == "structure":
        if claim_type == "request_text":
            return _judged_skeleton(chosen, resolution, ctx)
        if claim_type == "surface_reference":
            return _environment_read(chosen, resolution, ctx)
        return resolution
    if chosen["observer"] != "text":
        return resolution
    explained = _structural_line(chosen, claim_type, ctx)
    if explained is not None:
        return explained
    if claim_type in CONTEXT_GATED_TEXT_CLAIMS:
        resolution = _without_direct_context(chosen, resolution, ctx)
    return _lattice_disposition(chosen, claim_type, resolution, ctx)


def _lattice_disposition(
    chosen: Mapping[str, Any], claim_type: str, resolution: Resolution, ctx: ResolutionContext
) -> Resolution:
    if resolution.status != "UNKNOWN" or claim_type not in LATTICE_DECIDABLE_CLAIMS:
        return resolution
    if not ctx.stability or not ctx.lattice:
        return resolution
    subjects = _named_subjects(chosen, claim_type)
    if not subjects or any(subject in ctx.version_keys() for subject in subjects):
        return resolution
    dispositions = {subject: ctx.stability.get(subject) for subject in subjects}
    changing = {
        subject: disposition[len(CHANGES_AT_PREFIX) :]
        for subject, disposition in dispositions.items()
        if disposition is not None and disposition.startswith(CHANGES_AT_PREFIX)
    }
    if changing:
        boundaries = "; ".join(
            f"{subject!r} changes at {versions}" for subject, versions in changing.items()
        )
        existing = f": {resolution.close_with}" if resolution.close_with else ""
        return replace(
            resolution,
            close_with=(
                f"{boundaries}; resolve the version at this site to decide which side it "
                f"runs on{existing}"
            ),
        )
    if any(disposition != STABLE_DISPOSITION for disposition in dispositions.values()):
        return resolution
    named = ", ".join(repr(subject) for subject in subjects)
    verbs = "carries no version and is" if len(subjects) == 1 else "carry no version and are"
    return Resolution(
        status="NOT_AFFECTED_WITH_EVIDENCE",
        reason=(
            f"{named} at {_location(chosen)} {verbs} stable across {ctx.lattice[0]}…"
            f"{ctx.lattice[-1]}; no migration inside this pack's lattice changes it"
        ),
        close_with=None,
        winner_id=str(chosen["id"]),
    )


def _named_subjects(chosen: Mapping[str, Any], claim_type: str) -> tuple[str, ...]:
    if claim_type != "request_text":
        subject = chosen["provider_subject"]
        return () if subject is None else (str(subject),)
    value = as_mapping(chosen["value"])
    named = {str(item) for item in as_sequence(value.get("matches"))}
    if value.get("kind") == REQUEST_RESOURCE_KIND:
        resource = as_text(value.get("resource")) or as_text(chosen["provider_subject"])
        if resource:
            named.add(resource)
    return tuple(sorted(named))


def _structural_line(
    chosen: Mapping[str, Any], claim_type: str, ctx: ResolutionContext
) -> Resolution | None:
    if claim_type not in TEXT_CLAIMS_A_STRUCTURAL_LINE_EXPLAINS:
        return None
    line = as_line(chosen.get("line_start"))
    if line is None:
        return None
    structural = ctx.structural_lines.get((str(chosen["path"]), line))
    if structural is None:
        return None
    return Resolution(
        status="NOT_AFFECTED_WITH_EVIDENCE",
        reason=(
            f"{chosen['provider_subject']!r} at {_location(chosen)} is a recall match on a line "
            f"the structural layer already resolved to a version or a request skeleton "
            f"(evidence {structural}); that structural candidate carries the site, so this "
            "text match is explained rather than counted twice"
        ),
        close_with=None,
        winner_id=str(chosen["id"]),
    )


def _without_direct_context(
    chosen: Mapping[str, Any], resolution: Resolution, ctx: ResolutionContext
) -> Resolution:
    scope = as_text(as_mapping(chosen["value"]).get("file_context"))
    if scope is None or scope == DIRECT_CONTEXT:
        return resolution
    return Resolution(
        status="NOT_AFFECTED_WITH_EVIDENCE",
        reason=(
            f"{chosen['provider_subject']!r} at {_location(chosen)} is a provider-shaped string "
            f"in a file with no provider context of its own (file_context={scope}: the only "
            "provider reference reaching it is a first-party import); a resource name, endpoint "
            "path or query without provider context in the same file is not a provider surface, "
            "and any provider call the imported module performs is claimed at that module's own "
            "sites"
        ),
        close_with=None,
        winner_id=str(chosen["id"]),
    )


def _environment_read(
    chosen: Mapping[str, Any], resolution: Resolution, ctx: ResolutionContext
) -> Resolution:
    value = as_mapping(chosen["value"])
    if str(value.get("node_kind")) != ENVIRONMENT_READ_NODE_KIND:
        return resolution
    key = str(chosen["provider_subject"])
    shape = as_text(value.get("environment_read_shape")) or "environment"
    if key in ctx.version_keys():
        return Resolution(
            status="UNKNOWN",
            reason=(
                f"{key!r} at {_location(chosen)} is read from {shape}; the pack names it a "
                "version-selecting key, so the executing version is chosen at runtime"
            ),
            close_with=CLOSE_WITH_RUNTIME_CONFIG,
            winner_id=str(chosen["id"]),
        )
    return Resolution(
        status="NOT_AFFECTED_WITH_EVIDENCE",
        reason=(
            f"{key!r} at {_location(chosen)} is read from {shape}: configuration, never a "
            "version; the pack names no version-selecting semantics for this key, so its "
            "runtime value cannot change which provider contract executes"
        ),
        close_with=None,
        winner_id=str(chosen["id"]),
    )


def _judged_skeleton(
    chosen: Mapping[str, Any], resolution: Resolution, ctx: ResolutionContext
) -> Resolution:
    value = as_mapping(chosen["value"])
    resolution_code = as_text(value.get("resolution"))
    if resolution_code == DEFERRED_VALIDATION:
        judgement = ctx.validations.get(str(chosen["id"]))
        if judgement is None:
            return resolution
        return _oracle_resolution(chosen, judgement)
    if resolution_code == "UNKNOWN_QUERY_HOLE" or resolution_code is None:
        return resolution
    return _generic_sink(chosen, resolution, ctx)


def _oracle_resolution(chosen: Mapping[str, Any], judgement: Mapping[str, Any]) -> Resolution:
    accepted = [str(item) for item in as_sequence(judgement.get("accepted"))]
    rejected = as_mapping(judgement.get("rejected"))
    undecided = as_mapping(judgement.get("undecided"))
    authority = as_text(judgement.get("authority")) or "UNKNOWN"
    location = _location(chosen)
    if accepted:
        detail = ""
        if rejected:
            named = sorted(rejected)
            detail = f"; rejected in {', '.join(named)}: {rejected[named[0]]}"
        return Resolution(
            status="AFFECTED",
            reason=(
                f"request skeleton at {location} accepted at {authority} authority in "
                f"{', '.join(accepted)}{detail}"
            ),
            close_with=None,
            winner_id=str(chosen["id"]),
        )
    if rejected:
        named = sorted(rejected)
        return Resolution(
            status="UNKNOWN",
            reason=(
                f"request skeleton at {location} is rejected in every lattice version that "
                f"decided it ({', '.join(named)}): {rejected[named[0]]}"
            ),
            close_with=(
                "correct the request so the provider catalog accepts it, or record a human "
                "decision that this request does not reach the provider"
            ),
            winner_id=str(chosen["id"]),
        )
    named = sorted(undecided)
    reason = undecided[named[0]] if named else "the oracle returned no judgement"
    return Resolution(
        status="UNKNOWN",
        reason=f"request skeleton at {location} is undecided by the contract oracle: {reason}",
        close_with=(
            "capture the executed request bound to this source hash, or extend the provider "
            "catalog so the oracle can decide it"
        ),
        winner_id=str(chosen["id"]),
    )


def _generic_sink(
    chosen: Mapping[str, Any], resolution: Resolution, ctx: ResolutionContext
) -> Resolution:
    if ctx.surface is None:
        return resolution
    value = as_mapping(chosen["value"])
    chain_paths = {str(chosen["path"])} | {
        str(as_mapping(item).get("path"))
        for item in as_sequence(value.get("paths"))
        if as_mapping(item).get("path")
    }
    if any(ctx.file_contexts.get(path, NO_CONTEXT) != NO_CONTEXT for path in sorted(chain_paths)):
        return resolution
    fragments = [
        str(fragment)
        for item in as_sequence(value.get("paths"))
        for fragment in as_sequence(as_mapping(as_mapping(item).get("skeleton")).get("fragments"))
    ]
    fragments.extend(
        str(fragment)
        for fragment in as_sequence(as_mapping(value.get("skeleton")).get("fragments"))
    )
    if any(ctx.names_provider_surface(fragment) for fragment in fragments):
        return resolution
    sink = as_text(as_mapping(value.get("sink")).get("name")) or "sink"
    return Resolution(
        status="NOT_AFFECTED_WITH_EVIDENCE",
        reason=(
            f"{sink} at {_location(chosen)} is a generic request sink whose skeleton fragments "
            f"name no provider host, version carrier or request language, in a file with no "
            f"provider link, direct or imported, on a wrapper chain "
            f"({', '.join(sorted(chain_paths))}) whose files have none either; nothing "
            "observable ties this call to the provider"
        ),
        close_with=None,
        winner_id=str(chosen["id"]),
    )


def _non_code_carrier(
    chosen: Mapping[str, Any], roles: Mapping[str, str], ctx: ResolutionContext | None = None
) -> Resolution | None:
    path = str(chosen["path"])
    subject = chosen["provider_subject"]
    location = _location(chosen)
    role = roles.get(path)
    if (
        role == CONFIG_ROLE
        and ctx is not None
        and str(chosen["claim_type"]) in ("config_reference", "surface_reference")
        and str(subject) not in ctx.version_keys()
    ):
        return Resolution(
            status="NOT_AFFECTED_WITH_EVIDENCE",
            reason=(
                f"{subject!r} at {location} sits in {path}, whose source-closure role is "
                f"{CONFIG_ROLE}; a configuration declaration of a key the pack does not name as "
                "version-selecting carries no version, so the recall match is explained by the "
                "role of the file that carries it"
            ),
            close_with=None,
            winner_id=str(chosen["id"]),
        )
    if role == DOCUMENTATION_ROLE:
        return Resolution(
            status="NOT_AFFECTED_WITH_EVIDENCE",
            reason=(
                f"{subject!r} at {location} sits in {path}, whose source-closure role is "
                f"{DOCUMENTATION_ROLE}; prose that names the provider executes nothing, so the "
                "recall match is explained by the role of the file that carries it"
            ),
            close_with=None,
            winner_id=str(chosen["id"]),
        )
    if role == STYLESHEET_ROLE:
        return Resolution(
            status="NOT_AFFECTED_WITH_EVIDENCE",
            reason=(
                f"{subject!r} at {location} sits in {path}, whose source-closure role is "
                f"{STYLESHEET_ROLE}; a selector or property that names the provider styles a "
                "page and issues no request, so the recall match is explained by the role of "
                "the file that carries it"
            ),
            close_with=None,
            winner_id=str(chosen["id"]),
        )
    manifest = classify_manifest(path)
    if manifest is not None and manifest[1] == LOCK_KIND:
        ecosystem = manifest[0]
        return Resolution(
            status="NOT_AFFECTED_WITH_EVIDENCE",
            reason=(
                f"{subject!r} at {location} sits in {path}, the resolved {ecosystem} lock file; "
                "a lock records the dependency graph the installer produced, and the dependency "
                "observer claims that installed version from the parsed lock rather than from "
                "this text match"
            ),
            close_with=None,
            winner_id=str(chosen["id"]),
        )
    return None


def _file_version_binding(
    chosen: Mapping[str, Any],
    resolution: Resolution,
    path_versions: Mapping[str, FileVersionEvidence],
) -> Resolution | None:
    if resolution.status != "UNKNOWN" or chosen["observer"] != "structure":
        return None
    value = as_mapping(chosen["value"])
    if str(value.get("node_kind")) == COMMENT_NODE_KIND or not value.get("binding"):
        return None
    if value.get("first_party_definition") is not None:
        return None
    if as_sequence(value.get("binding_versions")):
        return None
    evidence = path_versions.get(str(chosen["path"]))
    if evidence is None:
        return None
    return Resolution(
        status="AFFECTED",
        reason=(
            f"{chosen['provider_subject']!r} at {_location(chosen)} is adjudicated as code bound "
            f"to {value.get('binding')}, which carries no version of its own, and sits in a file "
            f"whose only version evidence is {evidence.version} at {evidence.sites} version "
            f"site(s), e.g. {evidence.example_location}, evidence {evidence.example_id}"
        ),
        close_with=None,
        winner_id=str(chosen["id"]),
    )


def _also_claimed(chosen: Mapping[str, Any], resolution: Resolution) -> Resolution:
    kinds = [str(item) for item in as_sequence(as_mapping(chosen["value"]).get("kinds"))]
    if len(kinds) < 2:
        return resolution
    return replace(
        resolution,
        reason=(
            f"{resolution.reason}; this token matched the pack's {', '.join(kinds)} dictionaries "
            "on one line, and is claimed once rather than once per dictionary"
        ),
    )


def file_version_index(records: Sequence[Mapping[str, Any]]) -> dict[str, FileVersionEvidence]:
    carried: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        if str(record["claim_type"]) != "call_version" or record["provider_subject"] is None:
            continue
        carried.setdefault(str(record["path"]), []).append(record)
    index: dict[str, FileVersionEvidence] = {}
    for path, found in carried.items():
        versions = {str(record["provider_subject"]) for record in found}
        if len(versions) != 1:
            continue
        example = min(found, key=lambda record: (record["line_start"] or 0, str(record["id"])))
        index[path] = FileVersionEvidence(
            version=versions.pop(),
            sites=len(found),
            example_id=str(example["id"]),
            example_location=_location(example),
        )
    return index


def _slot(record: Mapping[str, Any], claim_type: str) -> str:
    value = as_mapping(record["value"])
    if claim_type == "sdk_installed":
        return str(value.get("source_kind", "manifest"))
    if claim_type == "call_version":
        return str(value.get("slot", "UNKNOWN"))
    if claim_type == "request_text":
        return str(record["observer"])
    return str(record["observer"])


def _only_wire_namespace_text(records: Sequence[Mapping[str, Any]], best: int) -> bool:
    carrying = [
        record
        for record in records
        if record["provider_subject"] is not None and rank(record) == best
    ]
    if not carrying:
        return False
    for record in carrying:
        value = as_mapping(record["value"])
        if record["observer"] != "text":
            return False
        if value.get("scope") != "wire" or value.get("slot") != "sdk_default":
            return False
    return True


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
    if versions and _only_wire_namespace_text(records, best):
        return Resolution(
            status="UNKNOWN",
            reason=(
                f"WIRE_NAMESPACE_WITHOUT_CALL_SITE at {location}: the text observer proves the "
                f"reference carries {versions[0]}, but a wire namespace is readable as data in "
                "any language and no observer proved an executable call site here"
            ),
            close_with=(
                "bind this namespace to a call site with the structure observer, dynamic capture, "
                "or provider telemetry, or record a human decision that it is inert data"
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
        status="UNSCANNED",
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
    if chosen["observer"] == "structure":
        return _adjudicated_reference(chosen)
    return Resolution(
        status="UNKNOWN",
        reason=(
            f"recall-layer match for {chosen['provider_subject']!r} at {_location(chosen)}; "
            "the text observer proves the reference exists but carries no version"
        ),
        close_with=CLOSE_WITH_CALL_SITE,
        winner_id=str(chosen["id"]),
    )


def _adjudicated_reference(chosen: Mapping[str, Any]) -> Resolution:
    value = as_mapping(chosen["value"])
    node_kind = str(value.get("node_kind"))
    subject = chosen["provider_subject"]
    location = _location(chosen)
    binding = value.get("binding")
    versions = [str(item) for item in as_sequence(value.get("binding_versions"))]
    if node_kind == "comment":
        return Resolution(
            status="NOT_AFFECTED_WITH_EVIDENCE",
            reason=(
                f"{subject!r} at {location} is inside a comment node proved by the structural "
                "parse, so it is not executable and cannot carry a provider call. The text "
                "observer's recall match is explained, not dismissed"
            ),
            close_with=None,
            winner_id=str(chosen["id"]),
        )
    if versions:
        return Resolution(
            status="AFFECTED",
            reason=(
                f"{subject!r} at {location} binds to {binding}, whose namespace carries "
                f"version {', '.join(versions)}"
            ),
            close_with=None,
            winner_id=str(chosen["id"]),
        )
    first_party = value.get("first_party_definition")
    if first_party is not None:
        return Resolution(
            status="UNKNOWN",
            reason=(
                f"{subject!r} at {location} binds to {binding}, which this repository defines "
                f"itself as {first_party}; the name resembles the provider's but the symbol is "
                "first-party, and whether it reaches the provider is not yet resolved"
            ),
            close_with=(
                f"follow {first_party} to the provider call it performs, or prove it performs "
                "none, and bind this site to that result"
            ),
            winner_id=str(chosen["id"]),
        )
    target = as_text(as_mapping(chosen["value"]).get("binding_target"))
    if target and node_kind == IMPORT_NODE_KIND:
        return Resolution(
            status="NOT_AFFECTED_WITH_EVIDENCE",
            reason=(
                f"{subject!r} at {location} is a path segment of the import specifier "
                f"{binding}, which this repository resolves to its own file {target}; the token "
                "names a first-party module, not the provider, and any provider call that module "
                "performs is claimed at its own sites"
            ),
            close_with=None,
            winner_id=str(chosen["id"]),
        )
    if target:
        return Resolution(
            status="UNKNOWN",
            reason=(
                f"{subject!r} at {location} binds to {binding}, which this repository resolves to "
                f"{target}; the module carries no version of its own"
            ),
            close_with=(
                f"resolve the version inside {target} and bind this site to that result, or "
                "capture the executed request"
            ),
            winner_id=str(chosen["id"]),
        )
    return Resolution(
        status="UNKNOWN",
        reason=(
            f"{subject!r} at {location} binds to {binding}, which carries no version and is "
            "defined outside this repository"
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
    value = as_mapping(chosen["value"])
    if value.get("kind") == "request_resource":
        resource = as_text(value.get("resource")) or str(chosen["provider_subject"])
        if value.get("resource_known"):
            return Resolution(
                status="UNKNOWN",
                reason=(
                    f"request-language query over {resource!r} at {_location(chosen)}; the "
                    "resource is in the provider catalog but the text observer cannot tell "
                    "which of its fields the built request selects"
                ),
                close_with=(
                    "extract the request skeleton with the structure observer, or capture the "
                    "executed request dynamically"
                ),
                winner_id=str(chosen["id"]),
            )
        return Resolution(
            status="UNKNOWN",
            reason=(
                f"UNRECOGNISED_RESOURCE {resource!r} at {_location(chosen)}: a request-language "
                "query in a file that carries provider context names a resource absent from "
                "every catalog version in this pack's lattice"
            ),
            close_with=(
                "ingest the provider version that introduces this resource, or record a human "
                "decision that the query does not reach this provider"
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


def _adjacent_contract(
    chosen: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> Resolution:
    value = as_mapping(chosen["value"])
    return Resolution(
        status="EXCLUDED_WITH_EVIDENCE",
        reason=(
            f"{chosen['provider_subject']} at {_location(chosen)} belongs to the adjacent "
            f"contract {as_text(value.get('pattern'))!r}, which carries its own version lattice "
            "and is not the contract under migration"
        ),
        close_with=None,
        winner_id=str(chosen["id"]),
    )


def _contract_surface(
    chosen: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> Resolution:
    value = as_mapping(chosen["value"])
    kind = as_text(value.get("surface_kind")) or "contract surface"
    return Resolution(
        status="UNKNOWN",
        reason=(
            f"CONTRACT_SURFACE_UNRESOLVED at {_location(chosen)}: a {kind} "
            f"({chosen['provider_subject']!r}) sits in a file that carries provider context, and "
            "no observer resolved it to a versioned contract subject"
        ),
        close_with=(
            "bind this surface to a catalog subject with the contract oracle, or capture the "
            "executed request that carries it"
        ),
        winner_id=str(chosen["id"]),
    )


def _structure_unsupported(
    chosen: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> Resolution:
    value = as_mapping(chosen["value"])
    return Resolution(
        status="UNSUPPORTED",
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


def _bulk_data_reference(
    chosen: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> Resolution:
    value = as_mapping(chosen["value"])
    role = value.get("role")
    matches = value.get("match_count")
    lines = value.get("line_count")
    subjects = as_sequence(value.get("subjects"))
    shown = ", ".join(str(item) for item in subjects[:5])
    return Resolution(
        status="PROVIDER_REFERENCE_DATA",
        reason=(
            f"{chosen['path']} carries the {role} role: {matches} surface matches on {lines} "
            f"lines ({shown}). A record-stream or snapshot file is provider reference data, not "
            "first-party code, so it is excluded from code candidacy and no call version is "
            "claimed from it. Every match is counted here rather than dropped"
        ),
        close_with=(
            "if first-party code loads this file as configuration that selects a provider "
            "version or field, record that read as a resource-loading edge so the data becomes "
            "a call site rather than reference data"
        ),
        winner_id=str(chosen["id"]),
    )


HANDLERS = {
    "bulk_data_reference": _bulk_data_reference,
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
    "adjacent_contract": _adjacent_contract,
    "contract_surface": _contract_surface,
}


def _location(record: Mapping[str, Any]) -> str:
    line = record["line_start"]
    return f"{record['path']}:{line}" if line else str(record["path"])


def _pattern_name(record: Mapping[str, Any]) -> str:
    value = as_mapping(record["value"])
    return str(value.get("pattern", "unknown"))
