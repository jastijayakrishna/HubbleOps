from __future__ import annotations

import textwrap
from collections.abc import Mapping, Sequence
from typing import Any

from hubbleops.core.proof_scope import short_scope
from hubbleops.core.records import as_mapping, as_text
from hubbleops.observe.ledger import Ledger

RULE = "─" * 56
LOCATION_WIDTH = 26
BODY_WIDTH = 100
DETAIL_INDENT = "    "


def render(
    *,
    ledger: Ledger,
    pack_name: str,
    changes_hash: str,
    target: str,
    repository: str,
    repo_sha: str | None,
    structural_coverage: Mapping[str, Any] | None = None,
    expand_not_affected: bool = False,
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
    lines.append(f"  Human required          {counts['human_required']}")
    lines.append(f"  UNKNOWN                 {counts['unknown']}")
    lines.append(f"  Unexplained             {counts['unexplained']}")
    lines.append(
        "  Production services accounted for   no telemetry or sentinel observer in this ProofScope"
    )
    if structural_coverage:
        lines.append("  Structural coverage")
        for language, raw_counts in sorted(structural_coverage.items()):
            language_counts = as_mapping(raw_counts)
            lines.append(
                f"    {language:<12} supported={language_counts.get('supported', 0)} "
                f"unsupported={language_counts.get('unsupported', 0)} "
                f"unscanned={language_counts.get('unscanned', 0)}"
            )

    lines.append("")
    lines.append(RULE)
    lines.append("AFFECTED")
    lines.extend(_affected_block(ledger))

    lines.append("")
    lines.append(RULE)
    lines.append("UNKNOWN")
    lines.extend(_unknown_block(ledger))

    human_required = ledger.by_status("HUMAN_REQUIRED")
    if human_required:
        lines.append("")
        lines.append(RULE)
        lines.append("HUMAN REQUIRED")
        lines.extend(_unknown_block(ledger, status="HUMAN_REQUIRED"))

    lines.append("")
    lines.append(RULE)
    not_affected = ledger.by_status("NOT_AFFECTED_WITH_EVIDENCE")
    lines.append(f"NOT AFFECTED (with evidence)   {len(not_affected)}   [expand]")
    if expand_not_affected:
        lines.extend(_plain_block(ledger, not_affected))
    lines.append("")
    return "\n".join(lines) + "\n"


def _affected_block(ledger: Ledger) -> list[str]:
    candidates = ledger.by_status("AFFECTED")
    if not candidates:
        return ["  none"]
    index = ledger.evidence_by_id()
    lines: list[str] = []
    for candidate in candidates:
        location = ledger.location_of(candidate)
        attached = [index[eid] for eid in candidate["evidence_ids"] if eid in index]
        lines.extend(_headline(location.display(), str(candidate["reason"])))
        source = _source_line(attached)
        if source:
            lines.append(f"{DETAIL_INDENT}{source}")
        lines.append(f"{DETAIL_INDENT}{_provenance(attached)}")
    return lines


def _unknown_block(ledger: Ledger, status: str = "UNKNOWN") -> list[str]:
    candidates = ledger.by_status(status)
    if not candidates:
        return ["  none"]
    lines: list[str] = []
    for candidate in candidates:
        location = ledger.location_of(candidate)
        lines.extend(_headline(location.display(), str(candidate["reason"])))
        for wrapped in textwrap.wrap(
            f"close with: {candidate['close_with']}",
            width=BODY_WIDTH,
            initial_indent=DETAIL_INDENT,
            subsequent_indent=DETAIL_INDENT + "  ",
        ):
            lines.append(wrapped)
    return lines


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
