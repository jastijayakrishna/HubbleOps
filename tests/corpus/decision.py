from __future__ import annotations

from pathlib import Path
from typing import cast

from tests.corpus.spec import Family, FindingSet, read_json


def _wrap(text: str, width: int = 92, indent: str = "  ") -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = indent
    for word in words:
        if len(current) + len(word) + 1 > width and current.strip():
            lines.append(current.rstrip())
            current = indent
        current += word + " "
    if current.strip():
        lines.append(current.rstrip())
    return lines


def _obligation_rows(receipt: dict[str, object]) -> list[tuple[str, str]]:
    raw = receipt.get("obligations")
    if not isinstance(raw, list):
        return []
    rows: list[tuple[str, str]] = []
    for item in cast("list[object]", raw):
        if not isinstance(item, dict):
            continue
        record = {str(k): v for k, v in cast("dict[object, object]", item).items()}
        state = str(record.get("state", record.get("status", "")))
        reason = str(record.get("reason", ""))
        rows.append((state, reason))
    return rows


def next_action(findings: FindingSet, receipt: dict[str, object]) -> str:
    if findings.outcome == "SETUP_FAILED":
        failed = [
            stage
            for stage in findings.stages
            if stage.name.startswith("install") and stage.exit_code != 0
        ]
        which = failed[0].name if failed else "the documented install"
        return (
            f"Install the toolchain this repository documents, then rerun. {which} did not run "
            "on this machine, so nothing here has been verified and the version findings below "
            "come from the static scan alone."
        )
    open_reasons = [reason for reason in findings.reasons if " is OPEN" in reason]
    if open_reasons:
        return f"Make this edit, then rerun verify: {open_reasons[0]}"
    if findings.verdict == "VERIFIED_FOR_SCOPE":
        return "Nothing. Open the pull request; the Receipt is bound to this tree's ProofScope."
    if findings.reasons:
        return f"Resolve the first recorded reason, then rerun verify: {findings.reasons[0]}"
    return "Read the exposure map below and decide which sites to migrate first."


def render(
    family: Family,
    findings: FindingSet,
    receipt_path: Path,
    exposure_path: Path,
    telemetry: dict[str, object],
) -> str:
    receipt = read_json(receipt_path) if receipt_path.is_file() else {}
    lines: list[str] = []
    lines.append("=" * 94)
    lines.append(f"DECISION REPORT  {family.repo}  {family.source_api_version} -> v25")
    lines.append("=" * 94)
    lines.append("")
    lines.append("THE ONE THING TO DO NEXT")
    lines.extend(_wrap(next_action(findings, receipt)))
    lines.append("")
    lines.append("WHAT WAS RUN")
    lines.append(f"  repository      {family.repo} at {family.sha[:12]}")
    lines.append("  engine          engine-v0, scope dev/engine-v0.json")
    lines.append(f"  documented      {'; '.join(family.documented_install) or 'none'}")
    lines.append(
        f"  wall clock      {telemetry.get('runtime_seconds', '?')} s of a 30 minute budget"
    )
    lines.append(f"  outcome         {findings.outcome}")
    lines.append(f"  verdict         {findings.verdict or 'none produced'}")
    lines.append("")
    actionable = [item for item in findings.findings if item.asserted == "actionable"]
    unknown = [item for item in findings.findings if item.asserted == "unknown"]
    clean = [item for item in findings.findings if item.asserted == "clean"]
    lines.append("WHAT IT FOUND")
    lines.append(f"  {len(actionable):5} sites it says must change")
    lines.append(f"  {len(unknown):5} sites it cannot decide, each with a closing instruction")
    lines.append(f"  {len(clean):5} sites it explained and closed")
    lines.append(f"  {len(findings.repaired):5} files it edited")
    lines.append("")
    if actionable:
        lines.append("THE SITES THAT MUST CHANGE (first 12)")
        for item in actionable[:12]:
            lines.append(f"  {item.path}:{item.start_line}  {item.mechanism}  [{item.reason}]")
        if len(actionable) > 12:
            lines.append(f"  ... and {len(actionable) - 12} more")
        lines.append("")
    if findings.reasons:
        lines.append("WHY IT WILL NOT SAY VERIFIED")
        for reason in findings.reasons[:8]:
            lines.extend(_wrap(f"- {reason}"))
        if len(findings.reasons) > 8:
            lines.append(f"  ... and {len(findings.reasons) - 8} more reasons")
        lines.append("")
    rows = _obligation_rows(receipt)
    if rows:
        states: dict[str, int] = {}
        for state, _ in rows:
            states[state] = states.get(state, 0) + 1
        lines.append("OBLIGATIONS")
        lines.append(
            "  " + "  ".join(f"{state}={count}" for state, count in sorted(states.items()))
        )
        lines.append("")
    if unknown:
        lines.append("WHAT A HUMAN MUST DECIDE (first 8 unknowns)")
        for item in unknown[:8]:
            lines.append(f"  {item.path}:{item.start_line}  [{item.reason}]")
        if len(unknown) > 8:
            lines.append(f"  ... and {len(unknown) - 8} more")
        lines.append("")
    lines.append("HOW TO READ THIS")
    lines.extend(
        _wrap(
            "A verdict of VERIFIED_FOR_SCOPE is bound to this exact tree. Any new commit kills it "
            "and a new proof is required. HUMAN_REQUIRED and UNKNOWN are not failures: they are "
            "the engine declining to guess, and each one names what would close it. "
            "SETUP_FAILED means the repository's own documented install did not run here, so no "
            "verification was attempted at all."
        )
    )
    if exposure_path.is_file():
        lines.append("")
        lines.append("EXPOSURE MAP (as printed by hops exposure)")
        text = exposure_path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in text[:40]:
            lines.append("  " + line.rstrip()[:120])
        if len(text) > 40:
            lines.append(f"  ... {len(text) - 40} more lines")
    lines.append("")
    return "\n".join(lines)
