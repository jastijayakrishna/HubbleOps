from __future__ import annotations

import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hubbleops.app import migration, registry
from hubbleops.app.exposure import UNKNOWN_SITE_BUDGET
from hubbleops.closure import SourceClosure
from hubbleops.core.canonical import export_bytes
from hubbleops.core.errors import HubbleOpsError
from hubbleops.observe import Ledger

DETERMINISTIC = "DETERMINISTIC"
HUMAN = "HUMAN"
PRESERVE_UNKNOWN = "PRESERVE_UNKNOWN"
REAL_WORK_CLASSES = (DETERMINISTIC, HUMAN)
EFFECTIVE_VERSION_UNRESOLVED = "EFFECTIVE_VERSION_UNRESOLVED"
FINDING_WIDTH = 96


class ImpactInvalid(HubbleOpsError):
    pass


@dataclass(frozen=True, slots=True)
class ImpactReport:
    record: dict[str, Any]

    def json_bytes(self) -> bytes:
        return export_bytes(self.record)


@dataclass(frozen=True, slots=True)
class Finding:
    change_id: str
    items: tuple[Mapping[str, Any], ...]

    @property
    def sites(self) -> int:
        return len(self.items)

    @property
    def files(self) -> tuple[str, ...]:
        return tuple(sorted({path for item in self.items for path in item["paths"]}))


@dataclass(frozen=True, slots=True)
class CarriedGroup:
    instruction: str
    items: tuple[Mapping[str, Any], ...]

    @property
    def sites(self) -> int:
        return len(self.items)


def build(
    *,
    pack: registry.LoadedPack,
    ledger: Ledger,
    closure: SourceClosure,
    root: Path,
    target: str,
) -> ImpactReport:
    result = migration.migrate(
        pack=pack,
        ledger=ledger,
        closure=closure,
        root=root,
        target=target,
        write=False,
    )
    return from_obligations(
        ledger=ledger, obligations=result.obligations, provider=pack.name, target=target
    )


def from_obligations(
    *,
    ledger: Ledger,
    obligations: Sequence[Mapping[str, Any]],
    provider: str,
    target: str,
) -> ImpactReport:
    projected = project(ledger, obligations)
    real_work, unresolved_version, carried = partition(projected)
    affected_paths = sorted({path for item in real_work for path in item["paths"]})
    groups = carried_groups(carried)
    counts = ledger.counts()
    return ImpactReport(
        {
            "schema_version": 2,
            "provider": provider,
            "target": target,
            "run_id": ledger.run_id,
            "proof_scope_hash": ledger.proof_scope_hash,
            "full_rescan": True,
            "candidate_counts": counts,
            "obligation_counts": {
                "total": len(projected),
                "deterministic": sum(item["repair_class"] == DETERMINISTIC for item in projected),
                "human": sum(item["repair_class"] == HUMAN for item in projected),
                "preserve_unknown": sum(
                    item["repair_class"] == PRESERVE_UNKNOWN for item in projected
                ),
            },
            "affected_paths": affected_paths,
            "unresolved_version_count": len(unresolved_version),
            "carried_counts": {
                "total": len(carried),
            },
            "carried_groups": [
                {
                    "instruction": group.instruction,
                    "sites": group.sites,
                    "obligation_ids": sorted(str(item["id"]) for item in group.items),
                    "paths": sorted({path for item in group.items for path in item["paths"]}),
                }
                for group in groups
            ],
            "obligations": list(projected),
        }
    )


def project(ledger: Ledger, obligations: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    evidence = ledger.evidence_by_id()
    candidates = tuple(ledger.ordered_candidates())
    projected: list[dict[str, Any]] = []
    for item in obligations:
        paths = sorted(
            {
                str(evidence[evidence_id]["path"])
                for evidence_id in item["evidence_ids"]
                if evidence_id in evidence
            }
        )
        projected.append(
            {
                "id": item["id"],
                "candidate_id": _candidate_id(item, candidates),
                "provider_change_id": item["provider_change_id"],
                "repair_class": item["repair_class"],
                "required_state": item["required_state"],
                "paths": paths,
            }
        )
    return tuple(projected)


def partition(
    projected: Sequence[Mapping[str, Any]],
) -> tuple[
    tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...]
]:
    real_work: list[Mapping[str, Any]] = []
    unresolved_version: list[Mapping[str, Any]] = []
    carried: list[Mapping[str, Any]] = []
    for item in projected:
        repair_class = str(item["repair_class"])
        if repair_class in REAL_WORK_CLASSES:
            real_work.append(item)
        elif repair_class == PRESERVE_UNKNOWN:
            if str(item["provider_change_id"]) == EFFECTIVE_VERSION_UNRESOLVED:
                unresolved_version.append(item)
            else:
                carried.append(item)
        else:
            raise ImpactInvalid(
                f"obligation {item['id']} has repair_class {repair_class!r}, which impact does "
                "not classify; every obligation must resolve to affected, unresolved, or carried"
            )
    return tuple(real_work), tuple(unresolved_version), tuple(carried)


def finding_groups(items: Sequence[Mapping[str, Any]]) -> list[Finding]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for item in items:
        grouped.setdefault(str(item["provider_change_id"]), []).append(item)
    findings = [
        Finding(change_id=change_id, items=tuple(members)) for change_id, members in grouped.items()
    ]
    return sorted(findings, key=lambda finding: (-finding.sites, finding.change_id))


def finding_lines(items: Sequence[Mapping[str, Any]]) -> list[str]:
    if not items:
        return ["    none"]
    lines: list[str] = []
    for finding in finding_groups(items):
        label = f"{finding.sites} site" if finding.sites == 1 else f"{finding.sites} sites"
        files = finding.files
        suffix = "" if len(files) == finding.sites else f"  {len(files)} files"
        lines.append(f"    {finding.change_id}  {label}{suffix}")
        lines.extend(f"      {path}" for path in files)
    return lines


def carried_groups(items: Sequence[Mapping[str, Any]]) -> list[CarriedGroup]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for item in items:
        instruction = str(item["required_state"]) or "(no closing instruction recorded)"
        grouped.setdefault(instruction, []).append(item)
    groups = [
        CarriedGroup(instruction=instruction, items=tuple(members))
        for instruction, members in grouped.items()
    ]
    return sorted(groups, key=lambda group: (group.sites, group.instruction))


def carried_lines(items: Sequence[Mapping[str, Any]]) -> list[str]:
    if not items:
        return ["    none"]
    lines: list[str] = []
    shown = 0
    for group in carried_groups(items):
        if lines:
            lines.append("")
        expanded = shown + group.sites <= UNKNOWN_SITE_BUDGET
        label = f"{group.sites} site" if group.sites == 1 else f"{group.sites} sites"
        suffix = "" if expanded else "   [budget reached]"
        wrapped = textwrap.wrap(
            f"close with: {group.instruction}",
            width=FINDING_WIDTH,
            initial_indent="    ",
            subsequent_indent="      ",
        ) or ["    close with: (none recorded)"]
        lines.extend(wrapped)
        lines.append(f"      {label}{suffix}")
        if not expanded:
            continue
        shown += group.sites
        for path in sorted({path for item in group.items for path in item["paths"]}):
            lines.append(f"      {path}")
    return lines


def render(report: ImpactReport) -> str:
    record = report.record
    counts = record["candidate_counts"]
    obligations = record["obligation_counts"]
    open_discovery = sum(
        counts[name] for name in ("unknown", "human_required", "unsupported", "unscanned")
    )
    real_work, unresolved_version, carried = partition(record["obligations"])
    lines = [
        f"HubbleOps IMPACT  {record['provider']}  ->  {record['target']}",
        f"  ProofScope         {record['proof_scope_hash']}",
        "  analysis           full clean rescan",
        f"  affected           {counts['affected']}",
        f"  open discovery     {open_discovery}",
        f"  obligations        {obligations['total']}",
        f"    deterministic    {obligations['deterministic']}",
        f"    human            {obligations['human']}",
        f"    preserve UNKNOWN {obligations['preserve_unknown']}",
        f"      unresolved version {len(unresolved_version)}",
        f"      carried            {len(carried)}",
        f"  affected paths     {len(record['affected_paths'])}",
    ]
    lines.extend(finding_lines(real_work))
    lines.append("")
    lines.append(f"  carried UNKNOWN    {len(carried)}")
    lines.extend(carried_lines(carried))
    return "\n".join(lines) + "\n"


def _candidate_id(item: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]) -> str:
    evidence_ids = set(item["evidence_ids"])
    matches = [
        str(candidate["id"])
        for candidate in candidates
        if evidence_ids <= set(candidate["evidence_ids"])
    ]
    if len(matches) != 1:
        raise ImpactInvalid(
            f"obligation {item['id']} maps to {len(matches)} candidates; impact is ambiguous"
        )
    return matches[0]


__all__ = [
    "CarriedGroup",
    "Finding",
    "ImpactInvalid",
    "ImpactReport",
    "build",
    "carried_groups",
    "carried_lines",
    "finding_groups",
    "finding_lines",
    "from_obligations",
    "partition",
    "project",
    "render",
]
