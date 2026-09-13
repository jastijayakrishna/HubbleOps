from __future__ import annotations

import re
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from hubbleops.app import migration, registry
from hubbleops.app.verification import InjectedOracle
from hubbleops.core.proof_scope import short_scope
from hubbleops.core.records import as_mapping, as_sequence, as_text
from hubbleops.obligations import ObligationInputs
from hubbleops.obligations import build as build_obligations
from hubbleops.observe.ledger import Ledger
from hubbleops.observe.telemetry import production_coverage
from hubbleops.verify import static_request

RULE = "─" * 56
LOCATION_WIDTH = 26
BODY_WIDTH = 100
DETAIL_INDENT = "    "
UNKNOWN_SITE_BUDGET = 10
FILE_BUDGET = 10
PATH_WIDTH = 46

DETERMINISTIC = "DETERMINISTIC"
HUMAN = "HUMAN"
PRESERVE_UNKNOWN = "PRESERVE_UNKNOWN"
CLASS_WORDS = {DETERMINISTIC: "deterministic", HUMAN: "human", PRESERVE_UNKNOWN: "preserved"}

GROUPED_CLAIMS = ("call_version", "endpoint_reference")
GROUP_LABELS = {
    "call_version": "explicit version literal",
    "endpoint_reference": "endpoint reference",
}

VERSION_PREFIX = "version:"
SUBJECT_PREFIX = "subject:"
DEPENDENCY_PREFIX = "dependency:"
CARRIED_PREFIX = "carried:"

VERSION_NUMBERS = re.compile(r"v?([0-9]+(?:\.[0-9]+){0,3})")

SINK_PROXIMITY = {
    "request_text": 0,
    "call_version": 1,
    "endpoint_reference": 2,
    "production_version": 3,
    "telemetry_state": 4,
    "sdk_installed": 5,
    "dependency_state": 6,
    "package_reference": 7,
    "config_reference": 8,
    "surface_reference": 9,
    "external_boundary": 10,
    "structure_unsupported": 11,
    "file_unscanned": 12,
    "ai_triage_residue": 13,
}


@dataclass(frozen=True, slots=True)
class ClosingGroup:
    instruction: str
    rank: int
    candidates: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class QueryVerdict:
    path: str
    line: int
    observer: str
    verdict: str
    detail: str
    evidence_id: str

    def site(self) -> str:
        return f"{self.path}:{self.line}" if self.line else self.path


QUERY_ACCEPTED = "ACCEPTED"
QUERY_REJECTED = "REJECTED"
QUERY_UNDECIDED = "UNDECIDED"
QUERY_HOLE = "HOLE"
QUERY_UNRESOLVED = "UNRESOLVED"
QUERY_TEXT_ONLY = "TEXT-ONLY"
QUERY_ORDER = (
    QUERY_REJECTED,
    QUERY_UNDECIDED,
    QUERY_HOLE,
    QUERY_UNRESOLVED,
    QUERY_TEXT_ONLY,
    QUERY_ACCEPTED,
)


@dataclass(frozen=True, slots=True)
class MigrationFindings:
    target: str
    obligations: tuple[Mapping[str, Any], ...]
    subject_changes: Mapping[str, str]
    minimums: Mapping[str, str]
    sunsets: Mapping[str, str]
    queries: tuple[QueryVerdict, ...] = ()


def findings(*, pack: registry.LoadedPack, ledger: Ledger, target: str) -> MigrationFindings:
    change_sets = migration.change_sets_for(pack, migration.detected_versions(ledger), target)
    obligations = build_obligations(
        ObligationInputs(
            ledger=ledger,
            change_sets=change_sets,
            oracle=InjectedOracle(pack.contract),
            target=target,
            sources={},
        )
    )
    return MigrationFindings(
        target=target,
        obligations=tuple(obligations),
        subject_changes={
            change.subject: change.change
            for version in sorted(change_sets)
            for change in change_sets[version].changes
        },
        minimums=_minimums(pack, target),
        sunsets={entry.id: entry.sunset_at for entry in pack.versions() if entry.sunset_at},
        queries=queries(pack=pack, ledger=ledger, target=target),
    )


def queries(*, pack: registry.LoadedPack, ledger: Ledger, target: str) -> tuple[QueryVerdict, ...]:
    oracle = InjectedOracle(pack.contract)
    structural_lines = {
        (str(record["path"]), int(record["line_start"] or 0))
        for record in ledger.evidence
        if record["claim_type"] == "request_text" and record["observer"] == "structure"
    }
    rows: list[QueryVerdict] = []
    for record in ledger.evidence:
        if record["claim_type"] != "request_text":
            continue
        path, line = str(record["path"]), int(record["line_start"] or 0)
        observer = str(record["observer"])
        evidence_id = str(record["id"])
        value = as_mapping(record["value"])
        if observer == "text":
            if (path, line) in structural_lines:
                continue
            resource = as_text(value.get("resource")) or as_text(record.get("provider_subject"))
            rows.append(
                QueryVerdict(
                    path,
                    line,
                    observer,
                    QUERY_TEXT_ONLY,
                    f"request-language text over {resource!r}; no skeleton was extracted",
                    evidence_id,
                )
            )
            continue
        skeleton = as_mapping(value.get("skeleton"))
        holes = [str(item) for item in as_sequence(skeleton.get("holes"))]
        resolution = as_text(value.get("resolution")) or ""
        if holes:
            rows.append(
                QueryVerdict(path, line, observer, QUERY_HOLE, ", ".join(holes), evidence_id)
            )
            continue
        request = static_request(record)
        if request is None:
            rows.append(
                QueryVerdict(path, line, observer, QUERY_UNRESOLVED, resolution, evidence_id)
            )
            continue
        outcome = oracle.validate(request, target)
        if outcome.code == "VALID":
            rows.append(
                QueryVerdict(
                    path,
                    line,
                    observer,
                    QUERY_ACCEPTED,
                    f"{outcome.authority or 'UNSTATED'} authority",
                    evidence_id,
                )
            )
        elif outcome.code == "INVALID":
            rows.append(
                QueryVerdict(path, line, observer, QUERY_REJECTED, outcome.reason, evidence_id)
            )
        else:
            rows.append(
                QueryVerdict(path, line, observer, QUERY_UNDECIDED, outcome.reason, evidence_id)
            )
    return tuple(sorted(rows, key=lambda row: (row.path, row.line, row.evidence_id)))


def render_queries(rows: Sequence[QueryVerdict], target: str, expand: bool) -> str:
    lines = [RULE, f"QUERIES  → {target}"]
    if not rows:
        lines.append("  no request site was observed")
        return "\n".join(lines) + "\n"
    counts = {verdict: 0 for verdict in QUERY_ORDER}
    for row in rows:
        counts[row.verdict] += 1
    lines.append(
        f"  {_plural(len(rows), 'request site')} · accepted {counts[QUERY_ACCEPTED]} · "
        f"rejected {counts[QUERY_REJECTED]} · undecided {counts[QUERY_UNDECIDED]} · "
        f"holes {counts[QUERY_HOLE]} · unresolved {counts[QUERY_UNRESOLVED]} · "
        f"text-only {counts[QUERY_TEXT_ONLY]}"
    )
    for verdict in QUERY_ORDER:
        chosen = [row for row in rows if row.verdict == verdict]
        if not chosen:
            continue
        shown = chosen if expand or verdict != QUERY_ACCEPTED else chosen[:UNKNOWN_SITE_BUDGET]
        for row in shown:
            lines.append(f"  {verdict:<10} {row.site()}")
            lines.extend(
                textwrap.wrap(
                    row.detail,
                    width=BODY_WIDTH,
                    initial_indent=" " * 13,
                    subsequent_indent=" " * 13,
                )
            )
        if len(chosen) > len(shown):
            lines.append(f"  [expand] {len(chosen) - len(shown)} more {verdict.lower()} sites")
    return "\n".join(lines) + "\n"


def _minimums(pack: registry.LoadedPack, target: str) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for fact in sorted(pack.contract.catalog(target).facts, key=lambda item: item.subject):
        if fact.kind != "client_compatibility":
            continue
        for ecosystem, value in sorted(as_mapping(fact.attributes.get("minimum_versions")).items()):
            text = as_text(value)
            if text:
                resolved[str(ecosystem)] = text
    return resolved


def completeness(
    ledger: Ledger,
    closure: Mapping[str, Any] | None,
    structural_coverage: Mapping[str, Any] | None,
) -> tuple[str, tuple[str, ...], dict[str, int]]:
    counts = ledger.counts()
    entries = int(as_mapping(closure or {}).get("entries") or 0)
    classifications = as_mapping(as_mapping(closure or {}).get("counts"))
    accounted = sum(int(value) for value in classifications.values())
    parser_failures = sum(
        1
        for record in ledger.evidence
        if record["observer"] == "structure" and record["claim_type"] == "file_unscanned"
    )
    exhausted = sum(
        1
        for record in ledger.evidence
        if record["claim_type"] == "request_text"
        and str(as_mapping(record["value"]).get("resolution", "")).startswith("RESOLUTION_BUDGET")
    )
    boundaries = sum(1 for record in ledger.evidence if record["claim_type"] == "external_boundary")
    indexed = 0
    unsupported = 0
    for raw_counts in (structural_coverage or {}).values():
        language_counts = as_mapping(raw_counts)
        indexed += int(language_counts.get("supported") or 0)
        unsupported += int(language_counts.get("unsupported") or 0)

    measures = {
        "entries": entries,
        "accounted": accounted,
        "indexed": indexed,
        "unsupported": unsupported,
        "parser_failures": parser_failures,
        "budget_exhausted": exhausted,
        "dynamic_boundaries": boundaries,
        "unknown": counts["unknown"],
        "unsupported_candidates": counts["unsupported"],
        "unscanned_candidates": counts["unscanned"],
        "unexplained": counts["unexplained"],
    }

    reasons: list[str] = []
    if entries != accounted:
        reasons.append(f"{entries - accounted} enumerated entries carry no classification")
    if counts["unexplained"]:
        reasons.append(f"{counts['unexplained']} unexplained candidates")
    if parser_failures:
        reasons.append(f"{parser_failures} source files could not be parsed")
    if exhausted:
        reasons.append(f"{exhausted} resolutions exhausted their budget before reaching a value")
    if counts["unknown"]:
        reasons.append(f"{counts['unknown']} provider-relevant UNKNOWN candidates remain")
    if counts["unsupported"]:
        reasons.append(f"{counts['unsupported']} provider-relevant candidates are unsupported")
    if counts["unscanned"]:
        reasons.append(f"{counts['unscanned']} provider-relevant candidates were not scanned")
    verdict = "DISCOVERY_COMPLETE" if not reasons else "DISCOVERY_INCOMPLETE"
    return verdict, tuple(reasons), measures


def _completeness_block(
    ledger: Ledger,
    closure: Mapping[str, Any] | None,
    structural_coverage: Mapping[str, Any] | None,
) -> list[str]:
    verdict, reasons, measures = completeness(ledger, closure, structural_coverage)
    roles = as_mapping(as_mapping(closure or {}).get("roles"))
    lines = [
        f"  {'Entries enumerated':<24}{measures['entries']}",
        f"  {'Entries accounted':<24}{measures['accounted']}",
        f"  {'Source indexed':<24}{measures['indexed']}",
        f"  {'Source unsupported':<24}{measures['unsupported']}",
        f"  {'Parser failures':<24}{measures['parser_failures']}",
        f"  {'Budget exhaustions':<24}{measures['budget_exhausted']}",
        f"  {'Dynamic boundaries':<24}{measures['dynamic_boundaries']}",
        f"  {'Unexplained':<24}{measures['unexplained']}",
    ]
    populated = {name: int(count) for name, count in roles.items() if int(count)}
    if populated:
        rendered = "  ".join(f"{name}={count}" for name, count in sorted(populated.items()))
        lines.append(f"  {'Roles':<24}{rendered}")
    lines.append(f"  {'Verdict':<24}{verdict}")
    for reason in reasons:
        lines.append(f"  {'':<24}because {reason}")
    return lines


def render(
    *,
    ledger: Ledger,
    pack_name: str,
    changes_hash: str,
    target: str,
    repository: str,
    repo_sha: str | None,
    structural_coverage: Mapping[str, Any] | None = None,
    precise_indexes: Mapping[str, Any] | None = None,
    closure: Mapping[str, Any] | None = None,
    findings: MigrationFindings | None = None,
    expand: bool = False,
) -> str:
    counts = ledger.counts()
    detected = _detected_versions(ledger)
    lines: list[str] = []
    lines.append(f"HubbleOps — {pack_name.replace('_', ' ').strip().upper()} EXPOSURE MAP")
    lines.append("")
    lines.append(
        f"Repository   {_fit(_repo_label(repository), 24)}"
        f"Commit  {repo_sha[:7] if repo_sha else 'not a git tree'}"
    )
    lines.append(f"Detected     {_fit(detected, 24)}Target  {target}")
    lines.append(
        f"ProofScope   {_fit(short_scope(ledger.proof_scope_hash), 24)}"
        f"Pack    {pack_name}@{changes_hash[:8]}"
    )
    lines.append("")
    lines.append(RULE)
    lines.append("DISCOVERY")
    lines.append(f"  Candidates found        {counts['total']}")
    lines.append(f"  Affected                {counts['affected']}")
    lines.append(f"  Not affected (evidence) {counts['not_affected_with_evidence']}")
    lines.append(f"  Excluded (evidence)     {counts['excluded_with_evidence']}")
    lines.append(f"  Provider reference data {counts['provider_reference_data']}")
    lines.append(f"  Unsupported             {counts['unsupported']}")
    lines.append(f"  Unscanned               {counts['unscanned']}")
    lines.append(f"  Human required          {counts['human_required']}")
    lines.append(f"  Human accepted risk     {counts['human_accepted_risk']}")
    lines.append(f"  UNKNOWN                 {counts['unknown']}")
    lines.append(f"  Unexplained             {counts['unexplained']}")
    production = production_coverage(ledger)
    if production is None:
        lines.append(
            "  Production services accounted for   "
            "no telemetry or sentinel observer in this ProofScope"
        )
    else:
        lines.append(f"  Production services accounted for   {production[0]}/{production[1]}")
    if structural_coverage:
        lines.append("  Structural coverage")
        statuses = as_mapping(precise_indexes)
        for language, raw_counts in sorted(structural_coverage.items()):
            language_counts = as_mapping(raw_counts)
            lines.append(
                f"    {language:<12} supported={language_counts.get('supported', 0)} "
                f"precise={language_counts.get('precise', 0)} "
                f"unsupported={language_counts.get('unsupported', 0)} "
                f"unscanned={language_counts.get('unscanned', 0)}"
            )
            status = statuses.get(language)
            if status is not None:
                lines.append(f"    {'':<12} {status}")

    lines.append("")
    lines.append(RULE)
    lines.append("DISCOVERY COMPLETENESS")
    lines.extend(_completeness_block(ledger, closure, structural_coverage))

    lines.append("")
    lines.append(RULE)
    lines.append(f"MIGRATION FINDINGS  → {target}")
    lines.extend(_migration_block(ledger, findings, target, expand))
    if findings is not None:
        lines.append("")
        lines.extend(render_queries(findings.queries, target, expand).rstrip("\n").split("\n"))

    lines.append("")
    lines.append(RULE)
    lines.append("AFFECTED")
    lines.extend(_affected_block(ledger, expand))

    lines.append("")
    lines.append(RULE)
    lines.append(f"UNKNOWN   {counts['unknown']}")
    lines.extend(_unknown_block(ledger, expand_all=expand))

    human_required = ledger.by_status("HUMAN_REQUIRED")
    if human_required:
        lines.append("")
        lines.append(RULE)
        lines.append(f"HUMAN REQUIRED   {len(human_required)}")
        lines.extend(_unknown_block(ledger, status="HUMAN_REQUIRED", expand_all=expand))

    for status, title in (("UNSUPPORTED", "UNSUPPORTED"), ("UNSCANNED", "UNSCANNED")):
        candidates = ledger.by_status(status)
        if candidates:
            lines.append("")
            lines.append(RULE)
            lines.append(f"{title}   {len(candidates)}")
            lines.extend(_unknown_block(ledger, status=status, expand_all=expand))

    for status, title in (
        ("PROVIDER_REFERENCE_DATA", "PROVIDER REFERENCE DATA"),
        ("HUMAN_ACCEPTED_RISK", "HUMAN ACCEPTED RISK"),
    ):
        candidates = ledger.by_status(status)
        if candidates:
            lines.append("")
            lines.append(RULE)
            lines.append(f"{title}   {len(candidates)}")
            lines.extend(_plain_block(ledger, candidates))

    lines.append("")
    lines.append(RULE)
    not_affected = ledger.by_status("NOT_AFFECTED_WITH_EVIDENCE")
    lines.append(f"NOT AFFECTED (with evidence)   {len(not_affected)}   [expand]")
    if expand:
        lines.extend(_plain_block(ledger, not_affected))
    lines.append("")
    return "\n".join(lines) + "\n"


def _migration_block(
    ledger: Ledger,
    found: MigrationFindings | None,
    target: str,
    expand: bool,
) -> list[str]:
    if found is None:
        return ["  no provider contract was supplied to this rendering"]
    lines = _effective_version_lines(ledger)
    lines.extend(_sdk_lines(ledger, found, target))
    lines.extend(_sunset_lines(ledger, found, target))
    lines.extend(_obligation_lines(ledger, found, expand))
    counts = _class_counts(found)
    lines.append(
        f"  Deterministic edits {counts[DETERMINISTIC]} "
        f"· Human decisions {counts[HUMAN]} "
        f"· Preserved UNKNOWN {counts[PRESERVE_UNKNOWN]}"
    )
    return lines


def _class_counts(found: MigrationFindings) -> dict[str, int]:
    counts = {DETERMINISTIC: 0, HUMAN: 0, PRESERVE_UNKNOWN: 0}
    for item in found.obligations:
        name = str(item["repair_class"])
        counts[name] = counts.get(name, 0) + 1
    return counts


def _version_sites(ledger: Ledger) -> dict[str, list[tuple[str, int | None]]]:
    sites: dict[str, list[tuple[str, int | None]]] = {}
    for record in ledger.evidence:
        if record["claim_type"] != "call_version":
            continue
        subject = as_text(record["provider_subject"])
        version = subject.upper() if subject else "UNKNOWN"
        sites.setdefault(version, []).append((str(record["path"]), record["line_start"]))
    return sites


def _effective_version_lines(ledger: Ledger) -> list[str]:
    sites = _version_sites(ledger)
    if not sites:
        return ["  Effective version   no version literal resolved"]
    lines = ["  Effective version"]
    for version in sorted(sites, key=lambda value: _version_key(value.lower())):
        found = sites[version]
        files = len({path for path, _ in found})
        lines.append(f"    {version}   {_plural(len(found), 'site')} · {_plural(files, 'file')}")
    if len(sites) > 1:
        lines.append(f"    mixed: {len(sites)} version literals resolve in this ProofScope")
    return lines


def _sdk_lines(ledger: Ledger, found: MigrationFindings, target: str) -> list[str]:
    index = ledger.evidence_by_id()
    entries: list[str] = []
    for candidate in ledger.by_status("AFFECTED"):
        location = ledger.location_of(candidate)
        if location.claim_type != "sdk_installed":
            continue
        attached = [index[eid] for eid in candidate["evidence_ids"] if eid in index]
        ecosystem, package, installed = _client_pin(attached)
        minimum = found.minimums.get(ecosystem)
        floor = (
            f"{target} {ecosystem} minimum {minimum}"
            if minimum
            else f"no documented minimum for {ecosystem} in {target}"
        )
        entries.append(
            f"    {location.display()} · {package} {installed} · {floor} "
            f"· {_floor_verdict(installed, minimum)}"
        )
    if not entries:
        return ["  SDK   no provider client library is installed in this ProofScope"]
    return ["  SDK", *entries]


def _client_pin(records: Sequence[Mapping[str, Any]]) -> tuple[str, str, str]:
    ecosystem = "unknown"
    package = "unknown"
    installed: str | None = None
    declared: str | None = None
    for record in records:
        value = as_mapping(record["value"])
        ecosystem = as_text(value.get("ecosystem")) or ecosystem
        package = as_text(value.get("package")) or package
        installed = installed or as_text(value.get("version"))
        declared = declared or as_text(value.get("spec"))
    return ecosystem, package, installed or declared or "version unresolved"


def _floor_verdict(installed: str, minimum: str | None) -> str:
    if minimum is None:
        return "floor unknown"
    left = _version_numbers(installed)
    right = _version_numbers(minimum)
    if left is None or right is None:
        return "floor unknown"
    return "meets floor" if left >= right else "below floor"


def _version_numbers(value: str) -> tuple[int, ...] | None:
    match = VERSION_NUMBERS.fullmatch(value)
    if match is None:
        return None
    parts = tuple(int(item) for item in match[1].split("."))
    return parts + (0,) * (4 - len(parts))


def _sunset_lines(ledger: Ledger, found: MigrationFindings, target: str) -> list[str]:
    versions = {version.lower() for version in _version_sites(ledger)}
    versions.add(target)
    dated = [version for version in sorted(versions, key=_version_key) if version in found.sunsets]
    if not dated:
        return []
    lines = ["  Sunset"]
    for version in dated:
        suffix = "   target" if version == target else ""
        lines.append(f"    {version}   {found.sunsets[version]}{suffix}")
    return lines


def _obligation_lines(ledger: Ledger, found: MigrationFindings, expand: bool) -> list[str]:
    index = ledger.evidence_by_id()
    open_groups: dict[str, list[tuple[str, int | None]]] = {}
    classes: dict[str, str] = {}
    preserved: dict[str, int] = {}
    for item in found.obligations:
        label = _obligation_label(item, found)
        if str(item["repair_class"]) == PRESERVE_UNKNOWN:
            preserved[label] = preserved.get(label, 0) + 1
            continue
        site = _obligation_site(item, index)
        open_groups.setdefault(label, [])
        classes[label] = str(item["repair_class"])
        if site is not None:
            open_groups[label].append(site)
    lines = ["  Required changes"]
    if not open_groups:
        lines.append("    none")
    for label in sorted(open_groups, key=lambda name: (-len(open_groups[name]), name)):
        sites = sorted(set(open_groups[label]))
        files = len({path for path, _ in sites})
        scale = _plural(len(sites), "site")
        if files > 1:
            scale = f"{scale} · {_plural(files, 'file')}"
        word = CLASS_WORDS.get(classes[label], classes[label].lower())
        lines.append(f"    {label}   {scale}   {word}")
        lines.extend(_site_lines(sites, expand, "      "))
    if preserved:
        lines.append("  Preserved UNKNOWN")
        order = sorted(preserved, key=lambda name: (-preserved[name], name))
        shown = order if expand else order[:UNKNOWN_SITE_BUDGET]
        for label in shown:
            lines.append(f"    {label}   {preserved[label]}")
        if len(order) > len(shown):
            lines.append(f"    [expand] {len(order) - len(shown)} more kinds")
    return lines


def _site_lines(sites: Sequence[tuple[str, int | None]], expand: bool, indent: str) -> list[str]:
    shown = list(sites) if expand else list(sites[:UNKNOWN_SITE_BUDGET])
    lines = [f"{indent}{_site(path, line)}" for path, line in shown]
    if len(sites) > len(shown):
        lines.append(f"{indent}[expand] {len(sites) - len(shown)} more sites")
    return lines


def _site(path: str, line: int | None) -> str:
    return f"{path}:{line}" if line else path


def _obligation_label(item: Mapping[str, Any], found: MigrationFindings) -> str:
    identifier = str(item["provider_change_id"])
    preserved = str(item["repair_class"]) == PRESERVE_UNKNOWN
    if identifier.startswith(CARRIED_PREFIX):
        return f"carried {identifier[len(CARRIED_PREFIX) :]}"
    if identifier.startswith(SUBJECT_PREFIX):
        subject = identifier[len(SUBJECT_PREFIX) :]
        if preserved:
            return f"unmapped subject {subject}"
        return f"{found.subject_changes.get(subject, 'CHANGED')} {subject}"
    if identifier.startswith(DEPENDENCY_PREFIX):
        return "dependency"
    if identifier.startswith(VERSION_PREFIX):
        return identifier
    return identifier


def _obligation_site(
    item: Mapping[str, Any], index: Mapping[str, dict[str, Any]]
) -> tuple[str, int | None] | None:
    attached = [index[eid] for eid in item["evidence_ids"] if eid in index]
    if not attached:
        return None
    chosen = min(
        attached,
        key=lambda record: (
            _sink_rank(str(record["claim_type"])),
            str(record["path"]),
            int(record["line_start"] or 0),
        ),
    )
    return str(chosen["path"]), chosen["line_start"]


def _affected_block(ledger: Ledger, expand: bool) -> list[str]:
    candidates = ledger.by_status("AFFECTED")
    if not candidates:
        return ["  none"]
    index = ledger.evidence_by_id()
    keys: list[tuple[str, str, str] | None] = []
    grouped: dict[tuple[str, str, str], list[tuple[str, int | None]]] = {}
    attachments: list[list[dict[str, Any]]] = []
    for candidate in candidates:
        location = ledger.location_of(candidate)
        attached = [index[eid] for eid in candidate["evidence_ids"] if eid in index]
        attachments.append(attached)
        key = _finding_key(location.claim_type, location.provider_subject, attached)
        keys.append(key)
        if key is not None:
            grouped.setdefault(key, []).append((location.path, location.line))
    lines: list[str] = []
    emitted: set[tuple[str, str, str]] = set()
    spaced = False
    for candidate, attached, key in zip(candidates, attachments, keys, strict=True):
        if key is None:
            if spaced:
                lines.append("")
                spaced = False
            lines.extend(
                _headline(ledger.location_of(candidate).display(), str(candidate["reason"]))
            )
            source = _source_line(attached)
            if source:
                lines.append(f"{DETAIL_INDENT}{source}")
            lines.append(f"{DETAIL_INDENT}{_provenance(attached)}")
            continue
        if key in emitted:
            continue
        emitted.add(key)
        if lines:
            lines.append("")
        lines.extend(_finding_group(key, grouped[key], expand))
        spaced = True
    return lines


def _finding_key(
    claim_type: str, subject: str | None, attached: Sequence[Mapping[str, Any]]
) -> tuple[str, str, str] | None:
    if claim_type not in GROUPED_CLAIMS:
        return None
    return claim_type, (subject or "UNKNOWN").upper(), _carrier(attached, claim_type)


def _carrier(records: Sequence[Mapping[str, Any]], claim_type: str) -> str:
    for record in records:
        if str(record["claim_type"]) != claim_type:
            continue
        pattern = as_text(as_mapping(record["value"]).get("pattern"))
        if pattern:
            return pattern
    return ""


def _finding_group(
    key: tuple[str, str, str], sites: Sequence[tuple[str, int | None]], expand: bool
) -> list[str]:
    claim_type, subject, carrier = key
    label = f"{GROUP_LABELS[claim_type]} {subject}"
    if carrier:
        label = f"{label} via {carrier}"
    by_file: dict[str, list[int | None]] = {}
    for path, line in sites:
        by_file.setdefault(path, []).append(line)
    lines = [f"  {label}   {_plural(len(sites), 'site')} · {_plural(len(by_file), 'file')}"]
    order = sorted(by_file, key=lambda path: (-len(by_file[path]), path))
    shown = order if expand else order[:FILE_BUDGET]
    for path in shown:
        numbers = sorted(line for line in by_file[path] if line is not None)
        listed = ", ".join(str(line) for line in numbers[:UNKNOWN_SITE_BUDGET])
        if len(numbers) > UNKNOWN_SITE_BUDGET:
            listed = f"{listed}, …"
        detail = f"(lines {listed})" if listed else ""
        lines.append(
            f"    {_fit(path, PATH_WIDTH)}{_plural(len(by_file[path]), 'site')} {detail}".rstrip()
        )
    if len(order) > len(shown):
        lines.append(f"    [expand] {len(order) - len(shown)} more files")
    return lines


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _unknown_block(ledger: Ledger, status: str = "UNKNOWN", expand_all: bool = False) -> list[str]:
    candidates = ledger.by_status(status)
    if not candidates:
        return ["  none"]
    groups = _closing_groups(ledger, candidates)
    lines: list[str] = []
    shown = 0
    for group in groups:
        if lines:
            lines.append("")
        expanded = expand_all or shown + len(group.candidates) <= UNKNOWN_SITE_BUDGET
        lines.extend(_group_header(group, expanded))
        if not expanded:
            continue
        shown += len(group.candidates)
        for candidate in group.candidates:
            location = ledger.location_of(candidate)
            lines.extend(_headline(location.display(), str(candidate["reason"])))
            lines.append(f"{DETAIL_INDENT}candidate: {candidate['id']}")
    return lines


def _group_header(group: ClosingGroup, expanded: bool) -> list[str]:
    sites = len(group.candidates)
    label = f"{sites} site" if sites == 1 else f"{sites} sites"
    suffix = "" if expanded else "   [expand]"
    lines = textwrap.wrap(
        f"close with: {group.instruction}",
        width=BODY_WIDTH,
        initial_indent="  ",
        subsequent_indent="    ",
    ) or ["  close with: (none recorded)"]
    lines[0] = f"{lines[0]}"
    lines.append(f"    {label}{suffix}")
    return lines


def _closing_groups(ledger: Ledger, candidates: Sequence[Mapping[str, Any]]) -> list[ClosingGroup]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for candidate in candidates:
        instruction = as_text(candidate.get("close_with")) or "(no closing instruction recorded)"
        grouped.setdefault(instruction, []).append(candidate)
    groups = [
        ClosingGroup(
            instruction=instruction,
            rank=min(_sink_rank(ledger.location_of(item).claim_type) for item in members),
            candidates=tuple(members),
        )
        for instruction, members in grouped.items()
    ]
    return sorted(groups, key=lambda group: (group.rank, len(group.candidates), group.instruction))


def _sink_rank(claim_type: str) -> int:
    return SINK_PROXIMITY.get(claim_type, len(SINK_PROXIMITY))


def _plain_block(ledger: Ledger, candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    lines: list[str] = []
    for candidate in candidates:
        location = ledger.location_of(candidate)
        lines.extend(_headline(location.display(), str(candidate["reason"])))
    return lines


def _headline(location: str, reason: str) -> list[str]:
    body = textwrap.wrap(reason, width=BODY_WIDTH - LOCATION_WIDTH - 2) or [""]
    if len(location) > LOCATION_WIDTH:
        lines = [f"  {location}"]
        remaining = body
    else:
        lines = [f"  {location.ljust(LOCATION_WIDTH)}{body[0]}"]
        remaining = body[1:]
    for extra in remaining:
        lines.append(f"  {' ' * LOCATION_WIDTH}{extra}")
    return lines


def _source_line(records: Sequence[Mapping[str, Any]]) -> str:
    for record in records:
        line = as_text(as_mapping(record["value"]).get("line"))
        if line is not None:
            return line.strip()[: BODY_WIDTH - len(DETAIL_INDENT)]
    return ""


def _provenance(records: Sequence[Mapping[str, Any]]) -> str:
    claims = sorted({str(record["claim_type"]) for record in records})
    observers = sorted({str(record["observer"]) for record in records})
    confidences = sorted({str(record["confidence"]) for record in records})
    return (
        f"evidence: {'+'.join(claims)} · observer: {'+'.join(observers)} "
        f"· confidence: {'+'.join(confidences)}"
    )


def _detected_versions(ledger: Ledger) -> str:
    counts: dict[str, int] = {}
    unknown = 0
    for record in ledger.evidence:
        if record["claim_type"] != "call_version":
            continue
        version = as_text(record["provider_subject"])
        if version is None:
            unknown += 1
            continue
        normalized = version.lower()
        counts[normalized] = counts.get(normalized, 0) + 1
    parts = [f"{version} ({counts[version]})" for version in sorted(counts, key=_version_key)]
    if unknown:
        parts.append(f"UNKNOWN ({unknown})")
    return ", ".join(parts) if parts else "no version literal resolved"


def _version_key(value: str) -> tuple[int, str]:
    suffix = value[1:] if value.startswith("v") else ""
    return (int(suffix), value) if suffix.isdigit() else (10**9, value)


def _repo_label(target: str) -> str:
    parts = [part for part in target.replace("\\", "/").split("/") if part]
    return "/".join(parts[-2:]) if len(parts) > 1 else (parts[0] if parts else target)


def _fit(text: str, width: int) -> str:
    if len(text) >= width:
        return f"{text}  "
    return text.ljust(width)
