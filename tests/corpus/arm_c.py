from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from tests.corpus.arm_a import fetch_pinned, install, repaired_paths
from tests.corpus.shell import Budget, BudgetExhausted, run
from tests.corpus.spec import (
    Asserted,
    Definitions,
    Family,
    Finding,
    FindingSet,
    Outcome,
    StageRecord,
    normalise_path,
    read_json,
)

FINDINGS_FILE = "hubbleops-arm-c-findings.json"
PROMPT = """You are migrating this repository from the Google Ads API version it uses today to v25.

The repository is a fresh clone at a pinned commit, with its own documented install already run.
You have 30 minutes of wall clock and you may not ask anyone anything.

Do two things, in this order.

1. FIND every place in this repository that must change for v25. Be exhaustive: version literals,
   SDK namespaces, REST endpoint paths, client initialisation, configuration defaults and
   environment fallbacks, dependency pins in manifests and lockfiles, removed or renamed fields,
   enums and services read from responses, and GAQL that selects something v25 no longer has.

2. REPAIR what you are confident about by editing the files. Leave anything you are not confident
   about unedited, and say so.

Then write a file at the repository root called {findings_file} containing exactly this JSON shape
and nothing else:

{{
  "claim": "COMPLETE" or "HUMAN_REQUIRED",
  "source_version": "vNN",
  "findings": [
    {{"path": "repo/relative/path.py", "line": 42, "mechanism": "version_literal",
      "asserted": "actionable", "reason": "one short sentence", "repaired": true}}
  ],
  "human_work": ["one line per thing a human must still decide or do"],
  "human_minutes_estimate": 0
}}

Rules for that file:
- "claim" is COMPLETE only if you believe the repository now targets v25 and nothing else is
  needed. Otherwise HUMAN_REQUIRED.
- "mechanism" should be one of: version_literal, namespace_segment, endpoint_path, manifest_pin,
  lockfile_pin, config_version, removed_field_read, removed_enum_value, renamed_resource,
  removed_service_method, request_shape, generated_client_version. Use "other" if none fits.
- "asserted" is "actionable" for a site that must change, "unknown" for a site you cannot decide.
- One entry per site. Paths are relative to the repository root, with forward slashes.
- Write the file even if you found nothing.

You have the Google Ads API Developer Assistant plugin available. Use it."""


@dataclass(frozen=True)
class ArmCRun:
    findings: FindingSet
    stages: tuple[StageRecord, ...]
    runtime_seconds: float
    transcript: str
    cost_usd: float


def _claim_to_outcome(claim: str, install_failed: bool, ran_ok: bool) -> tuple[Outcome, str]:
    if install_failed:
        return ("SETUP_FAILED", "the documented install did not complete")
    if not ran_ok and not claim:
        return ("ENGINE_FAILED", "the arm exited without producing any verdict")
    if not claim:
        return ("ENGINE_FAILED", "the arm produced no claim and itemised no human work")
    if claim.upper() == "COMPLETE":
        return ("COMPLETED", "")
    return ("HUMAN_REQUIRED", "the arm itemised remaining human work")


def _asserted(raw: str) -> Asserted:
    lowered = raw.strip().lower()
    if lowered in ("actionable", "unknown", "clean"):
        return lowered
    return "unknown"


def edited_lines(repo: Path, base_sha: str) -> dict[str, int]:
    shown = run(("git", "diff", "-U0", "--no-color", base_sha), cwd=repo, timeout=300.0)
    out: dict[str, int] = {}
    if not shown.ok:
        return out
    current = ""
    for line in shown.stdout.splitlines():
        if line.startswith("+++ b/"):
            current = normalise_path(line[6:])
            continue
        if current and line.startswith("@@"):
            head = line.split("+", 1)
            if len(head) < 2:
                continue
            number = head[1].split(",")[0].split(" ")[0]
            if number.isdigit() and current not in out:
                out[current] = int(number)
    return out


def parse_findings(
    definitions: Definitions,
    repo: Path,
    repaired: tuple[str, ...],
    edit_lines: dict[str, int] | None = None,
) -> tuple[tuple[Finding, ...], str, tuple[str, ...], float]:
    target = repo / FINDINGS_FILE
    claim = ""
    human: list[str] = []
    minutes = 0.0
    out: list[Finding] = []
    seen: set[tuple[str, int, str]] = set()
    if target.is_file():
        body = read_json(target)
        claim = str(body.get("claim", ""))
        raw_human = body.get("human_work")
        if isinstance(raw_human, list):
            human = [str(item) for item in cast("list[object]", raw_human)]
        raw_minutes = body.get("human_minutes_estimate")
        if isinstance(raw_minutes, (int, float)) and not isinstance(raw_minutes, bool):
            minutes = float(raw_minutes)
        entries = body.get("findings")
        if isinstance(entries, list):
            for index, item in enumerate(cast("list[object]", entries)):
                if not isinstance(item, dict):
                    continue
                record = {str(k): v for k, v in cast("dict[object, object]", item).items()}
                path = normalise_path(str(record.get("path", "")))
                if not path:
                    continue
                raw_line = record.get("line")
                line = (
                    raw_line if isinstance(raw_line, int) and not isinstance(raw_line, bool) else 0
                )
                mechanism = str(record.get("mechanism", "other")).strip() or "other"
                if mechanism not in definitions.compatibility and mechanism not in (
                    definitions.universal_mechanisms
                ):
                    mechanism = "other"
                key = (path, line, mechanism)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    Finding(
                        finding_id=f"C{index + 1:05d}",
                        path=path,
                        start_line=line,
                        end_line=line,
                        mechanism=mechanism,
                        asserted=_asserted(str(record.get("asserted", "actionable"))),
                        subject=str(record.get("subject", "")),
                        reason=str(record.get("reason", ""))[:200],
                    )
                )
    lines = edit_lines or {}
    for index, path in enumerate(repaired):
        if path == FINDINGS_FILE:
            continue
        if any(entry.path == path for entry in out):
            continue
        at = lines.get(path, 0)
        out.append(
            Finding(
                finding_id=f"CE{index + 1:05d}",
                path=path,
                start_line=at,
                end_line=at,
                mechanism="other",
                asserted="actionable",
                subject="",
                reason="edited by arm C but not listed in its own findings file",
            )
        )
    return (tuple(out), claim, tuple(human), minutes)


def run_family(
    definitions: Definitions,
    family: Family,
    work_root: Path,
    budget_seconds: float,
    degraded: str,
) -> ArmCRun:
    repo = work_root / family.family_id / "repo"
    budget = Budget(budget_seconds)
    stages: list[StageRecord] = []
    transcript = ""
    cost = 0.0
    claim = ""
    human: tuple[str, ...] = ()
    minutes = 0.0
    findings: tuple[Finding, ...] = ()
    ran_ok = False
    install_failed = False
    try:
        stages.extend(fetch_pinned(family, repo, budget))
        stages.extend(install(family, repo, work_root / family.family_id / "venv", budget))
        install_failed = any(
            entry.name.startswith("install") and entry.exit_code != 0 for entry in stages
        )
        prompt = PROMPT.format(findings_file=FINDINGS_FILE)
        allowed = budget.claim("agent", budget.remaining)
        agent = run(
            (
                "claude",
                "-p",
                prompt,
                "--output-format",
                "json",
                "--permission-mode",
                "acceptEdits",
                "--allowedTools",
                "Read,Edit,Write,Grep,Glob,Bash",
            ),
            cwd=repo,
            timeout=allowed,
        )
        stages.append(
            StageRecord(
                name="claude -p",
                exit_code=124 if agent.timed_out else agent.exit_code,
                seconds=round(agent.seconds, 3),
                detail=agent.tail(400),
            )
        )
        ran_ok = agent.ok
        transcript = agent.stdout
        envelope: object = {}
        try:
            if agent.stdout.strip():
                envelope = json.loads(agent.stdout)
        except json.JSONDecodeError:
            envelope = {}
        if isinstance(envelope, dict):
            body = {str(k): v for k, v in cast("dict[object, object]", envelope).items()}
            raw_cost = body.get("total_cost_usd")
            if isinstance(raw_cost, (int, float)) and not isinstance(raw_cost, bool):
                cost = float(raw_cost)
        repaired = repaired_paths(repo, family.sha)
        findings, claim, human, minutes = parse_findings(
            definitions, repo, repaired, edited_lines(repo, family.sha)
        )
    except BudgetExhausted as exhausted:
        stages.append(
            StageRecord(
                name=f"budget:{exhausted.stage}",
                exit_code=124,
                seconds=round(exhausted.spent, 3),
                detail=str(exhausted),
            )
        )
    outcome, why = _claim_to_outcome(claim, install_failed, ran_ok)
    repaired_now = tuple(path for path in repaired_paths(repo, family.sha) if path != FINDINGS_FILE)
    notes = [why] if why else []
    if human:
        notes.append(f"{len(human)} item(s) of human work named: " + "; ".join(human[:3]))
    return ArmCRun(
        findings=FindingSet(
            arm="C-offline",
            family_id=family.family_id,
            sha=family.sha,
            outcome=outcome,
            verdict=claim or "none",
            runtime_seconds=0.0,
            human_minutes=minutes,
            cost_usd=cost,
            engineer_edits=0,
            findings=findings,
            repaired=repaired_now,
            stages=(),
            notes="; ".join(notes),
            degraded=degraded,
            reasons=human,
        ),
        stages=tuple(stages),
        runtime_seconds=round(budget.spent, 3),
        transcript=transcript,
        cost_usd=cost,
    )
