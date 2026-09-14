from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import cast

from tests.corpus import arm_a, arm_c, challenge, decision, history, merge, mutate, typecheck
from tests.corpus.engine import check_scope, load_scope, materialise
from tests.corpus.matching import match
from tests.corpus.report import arm_section, render_text, unavailable_arm, write_scorecard
from tests.corpus.scoring import FamilyScore, score_family
from tests.corpus.shell import run
from tests.corpus.spec import (
    CorpusError,
    Definitions,
    Family,
    FindingSet,
    LabelSet,
    StageRecord,
    load_definitions,
    load_families,
    load_findings,
    load_labels,
    read_json,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENGINE_ROOT = REPO_ROOT / ".hubbleops" / "artifacts" / "corpus" / "engine-v0"
DEFAULT_OUT = REPO_ROOT / ".hubbleops" / "artifacts" / "corpus" / "runs"
DEFAULT_LABELS = REPO_ROOT / "dev" / "corpus" / "labels"
DEFINITIONS = REPO_ROOT / "dev" / "corpus" / "definitions.json"
MATCHING = REPO_ROOT / "dev" / "corpus" / "matching-rules.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hops corpus", description="run the labelled corpus")
    verbs = parser.add_subparsers(dest="verb", required=True)

    runner = verbs.add_parser("run", help="run one arm over every family")
    runner.add_argument("--arm", required=True, choices=["A", "C", "C-offline"])
    runner.add_argument("--engine", required=True, help="path to the frozen engine scope")
    runner.add_argument("--families", required=True, help="path to the pinned family list")
    runner.add_argument("--engine-root", default=str(DEFAULT_ENGINE_ROOT))
    runner.add_argument("--out", default=str(DEFAULT_OUT))
    runner.add_argument("--only", action="append", default=[])
    runner.add_argument("--budget-minutes", type=int, default=0)
    runner.add_argument("--repeat", type=int, default=1, help="run each family N times and compare")
    runner.add_argument("--variant", default="natural", choices=["natural", "seeded"])
    runner.add_argument("--labels", default=str(DEFAULT_LABELS))

    scorer = verbs.add_parser("score", help="score recorded findings against signed labels")
    scorer.add_argument("--families", required=True)
    scorer.add_argument("--labels", default=str(DEFAULT_LABELS))
    scorer.add_argument("--findings", default=str(DEFAULT_OUT))
    scorer.add_argument("--out", required=True)
    scorer.add_argument("--arm", action="append", default=[])
    scorer.add_argument("--label-kind", default="natural", choices=["natural", "seeded"])

    labeller = verbs.add_parser("label", help="build natural labels from repository history")
    labeller.add_argument("--families", required=True)
    labeller.add_argument("--labels", default=str(DEFAULT_LABELS))
    labeller.add_argument("--out", default=str(DEFAULT_OUT))
    labeller.add_argument("--only", action="append", default=[])

    challenger = verbs.add_parser(
        "challenge", help="run the external challenge suite against arm A"
    )
    challenger.add_argument("--engine", required=True)
    challenger.add_argument("--families", required=True)
    challenger.add_argument("--engine-root", default=str(DEFAULT_ENGINE_ROOT))
    challenger.add_argument("--out", default=str(DEFAULT_OUT))
    challenger.add_argument("--only", action="append", default=[])
    challenger.add_argument(
        "--report", default=str(REPO_ROOT / "dev" / "scorecards" / "challenge.json")
    )

    reporter = verbs.add_parser("decision-report", help="render a per-family decision report")
    reporter.add_argument("--families", required=True)
    reporter.add_argument("--out", default=str(DEFAULT_OUT))
    reporter.add_argument("--only", action="append", default=[])
    reporter.add_argument(
        "--reports", default=str(REPO_ROOT / ".hubbleops" / "artifacts" / "corpus" / "decisions")
    )

    rederiver = verbs.add_parser(
        "rederive", help="rebuild findings from the persisted ledger and obligations"
    )
    rederiver.add_argument("--families", required=True)
    rederiver.add_argument("--out", default=str(DEFAULT_OUT))
    rederiver.add_argument("--only", action="append", default=[])
    rederiver.add_argument("--variant", default="natural", choices=["natural", "seeded"])

    outcomes = verbs.add_parser(
        "outcomes", help="one line per family: outcome, failing command, exact error, stage"
    )
    outcomes.add_argument("--families", required=True)
    outcomes.add_argument("--out", default=str(DEFAULT_OUT))
    outcomes.add_argument("--arm", default="A")

    checker = verbs.add_parser("check", help="verify the engine scope and the fixed definitions")
    checker.add_argument("--engine", required=True)
    checker.add_argument("--engine-root", default=str(DEFAULT_ENGINE_ROOT))
    checker.add_argument("--families", default="")
    return parser


def _load_fixed() -> Definitions:
    return load_definitions(DEFINITIONS, MATCHING)


def _selected(families: tuple[Family, ...], only: list[str]) -> tuple[Family, ...]:
    if not only:
        return families
    wanted = set(only)
    chosen = tuple(family for family in families if family.family_id in wanted)
    missing = wanted - {family.family_id for family in chosen}
    if missing:
        raise CorpusError(f"no such family: {', '.join(sorted(missing))}")
    return chosen


def command_check(args: argparse.Namespace) -> int:
    definitions = _load_fixed()
    scope = load_scope(Path(str(args.engine)))
    engine_root = Path(str(args.engine_root))
    problems = materialise(scope, REPO_ROOT, engine_root)
    for problem in problems:
        print(f"ENGINE {problem}")
    result = check_scope(scope, engine_root)
    print(f"ENGINE SCOPE {scope.tag}")
    print(f"  engine_tree  {result.engine_tree}")
    print(f"  pack_tree    {result.pack_tree}")
    print(f"  uv.lock      {result.uv_lock}")
    for name, value in sorted(result.tools.items()):
        print(f"  {name:12} {value or 'ABSENT'}")
    print(f"DEFINITIONS v{definitions.version}  MATCHING v{definitions.matching_version}")
    print(f"  target       {definitions.target_version}")
    print(f"  budget       {definitions.budget_minutes} minutes")
    print(f"  window       {definitions.window_lines} lines")
    families_path = str(args.families)
    if families_path:
        families = load_families(Path(families_path))
        eligible = [family for family in families if family.eligible]
        print(f"FAMILIES {len(families)} listed, {len(eligible)} eligible")
        for family in families:
            flag = "eligible" if family.eligible else "INELIGIBLE"
            print(
                f"  {family.family_id:24} {family.language:12} {family.source_api_version:6} "
                f"{flag:10} {family.repo}@{family.sha[:12]}"
            )
        if len(eligible) < 10:
            print(
                f"STOP fewer than ten independent eligible families exist: {len(eligible)}. "
                "The denominator is not filled."
            )
            return 2
    if not result.ok:
        for problem in result.problems:
            print(f"SCOPE MISMATCH {problem}")
        return 1
    print("SCOPE OK")
    return 0


def command_run(args: argparse.Namespace) -> int:
    definitions = _load_fixed()
    scope = load_scope(Path(str(args.engine)))
    engine_root = Path(str(args.engine_root))
    arm = str(args.arm)
    out_root = Path(str(args.out)) / arm
    families = _selected(load_families(Path(str(args.families))), list(args.only))
    budget_minutes = int(args.budget_minutes) or definitions.budget_minutes
    repeat = max(1, int(args.repeat))

    work_root = Path(str(args.out)) / "work-c" if arm != "A" else Path(str(args.out)) / "work"
    if arm == "C":
        print(
            "ARM C requires the Google Ads MCP sidecar with validation-only credentials in "
            "~/google-ads.yaml. That file is absent, so arm C cannot run as specified."
        )
        print("Run --arm C-offline for the credential-free degradation, recorded as such.")
        return 3
    if arm == "C-offline":
        families_c = _selected(load_families(Path(str(args.families))), list(args.only))
        degraded = (
            "arm C without credentials: the Developer Assistant plugin v4.0.0 is installed and "
            "its offline skills work, but every live-API skill is unavailable because no "
            "developer token exists. This is NOT arm C and its numbers are not arm C's."
        )
        for family in families_c:
            outcome = arm_c.run_family(
                definitions, family, work_root, float(budget_minutes) * 60.0, degraded
            )
            target = out_root / family.family_id
            target.mkdir(parents=True, exist_ok=True)
            arm_a.write_findings(target / "findings.json", outcome.findings)
            (target / "transcript.json").write_text(outcome.transcript, encoding="utf-8")
            (target / "telemetry-0.json").write_text(
                json.dumps(
                    {
                        "schema": "hubbleops.corpus.telemetry/1",
                        "arm": arm,
                        "family_id": family.family_id,
                        "runtime_seconds": outcome.runtime_seconds,
                        "cost_usd": outcome.cost_usd,
                        "budget_minutes": budget_minutes,
                        "degraded": degraded,
                        "stages": [
                            {
                                "name": stage.name,
                                "exit_code": stage.exit_code,
                                "seconds": stage.seconds,
                                "detail": stage.detail,
                            }
                            for stage in outcome.stages
                        ],
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            print(
                f"{family.family_id:24} outcome={outcome.findings.outcome:14} "
                f"claim={outcome.findings.verdict:16} "
                f"findings={len(outcome.findings.findings):5} "
                f"edits={len(outcome.findings.repaired):3} "
                f"runtime={outcome.runtime_seconds:8.1f}s cost=${outcome.cost_usd:.2f}"
            )
        return 0

    problems = materialise(scope, REPO_ROOT, engine_root)
    for problem in problems:
        print(f"ENGINE {problem}")
        return 1
    result = check_scope(scope, engine_root)
    if not result.ok:
        for problem in result.problems:
            print(f"SCOPE MISMATCH {problem}")
        print("Arm A refuses to run outside its frozen scope.")
        return 1

    variant = str(args.variant)
    labels_root = Path(str(args.labels))
    exit_code = 0
    for family in families:
        digests: list[str] = []
        for attempt in range(repeat):
            outcome = arm_a.run_family(
                definitions,
                family,
                engine_root,
                work_root,
                float(budget_minutes) * 60.0,
                variant,
                labels_root / f"{family.family_id}.seeded.json" if variant == "seeded" else None,
            )
            target = out_root / (
                f"{family.family_id}.seeded" if variant == "seeded" else family.family_id
            )
            findings_path = target / "findings.json"
            arm_a.write_findings(findings_path, outcome.findings)
            telemetry = {
                "schema": "hubbleops.corpus.telemetry/1",
                "arm": arm,
                "family_id": family.family_id,
                "attempt": attempt,
                "runtime_seconds": outcome.runtime_seconds,
                "budget_minutes": budget_minutes,
                "base_sha": outcome.base_sha,
                "candidate_sha": outcome.candidate_sha,
                "ledger_sha256": outcome.ledger_digest,
                "receipt_sha256": outcome.receipt_digest,
                "engine_tree": result.engine_tree,
                "stages": [
                    {
                        "name": stage.name,
                        "exit_code": stage.exit_code,
                        "seconds": stage.seconds,
                        "detail": stage.detail,
                    }
                    for stage in outcome.stages
                ],
            }
            (target / f"telemetry-{attempt}.json").write_text(
                json.dumps(telemetry, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            digests.append(arm_a.digest(findings_path))
            print(
                f"{family.family_id:24} attempt={attempt} outcome={outcome.findings.outcome:14} "
                f"verdict={outcome.findings.verdict or '-':18} "
                f"findings={len(outcome.findings.findings):5} "
                f"runtime={outcome.runtime_seconds:8.1f}s findings_sha256={digests[-1][:16]}"
            )
        if len(set(digests)) > 1:
            print(f"NONDETERMINISTIC {family.family_id}: {sorted(set(digests))}")
            exit_code = 1
    return exit_code


def _label_path(labels_root: Path, family: Family, kind: str) -> Path:
    if kind == "seeded":
        return labels_root / f"{family.family_id}.seeded.json"
    return labels_root / f"{family.family_id}.json"


def _findings_path(findings_root: Path, arm: str, family: Family, kind: str) -> Path:
    name = f"{family.family_id}.seeded" if kind == "seeded" else family.family_id
    return findings_root / arm / name / "findings.json"


def command_score(args: argparse.Namespace) -> int:
    definitions = _load_fixed()
    families = load_families(Path(str(args.families)))
    labels_root = Path(str(args.labels))
    findings_root = Path(str(args.findings))
    kind = str(args.label_kind)
    arms = list(args.arm) or ["A"]
    body: dict[str, object] = {
        "schema": "hubbleops.corpus.scorecard/1",
        "definitions_version": definitions.version,
        "matching_version": definitions.matching_version,
        "engine_scope": definitions.engine_scope,
        "label_kind": kind,
        "families_pinned": len(families),
        "families_eligible": sum(1 for family in families if family.eligible),
        "harness_parameters": {
            "note": (
                "Every bound the harness imposes that the fixed definitions do not. Stated here "
                "because a cap that is not declared is a cap that reads as a property of the arm."
            ),
            "budget_minutes": definitions.budget_minutes,
            "install_timeout": (
                "the whole remaining budget. An earlier 900 second per-install cap was removed "
                "once it was found to be deciding an outcome: klosk/adloop's `uv sync "
                "--all-extras` was killed at 900.1s and recorded SETUP_FAILED at the harness's "
                "cap rather than at the definition's budget. Any family whose install stage "
                "reports exit 124 at 900s was measured under that cap."
            ),
            "scan_timeout_seconds": 1800,
            "verify_timeout_seconds": 1200,
            "unsettled_line_cap": 3000,
            "s1_languages": ["python"],
            "s1_unavailable": {
                "php": "no php and no composer on this machine",
                "typescript": "no google-ads-api major carries v25",
            },
        },
        "arms": [],
    }
    sections: list[dict[str, object]] = []
    rendered: list[str] = []
    for arm in arms:
        rows: list[FamilyScore] = []
        signed = True
        missing: list[str] = []
        degraded = ""
        for family in families:
            label_file = _label_path(labels_root, family, kind)
            findings_file = _findings_path(findings_root, arm, family, kind)
            if not label_file.is_file():
                missing.append(f"labels missing for {family.family_id}")
                continue
            if not findings_file.is_file():
                missing.append(
                    f"{arm} produced no findings file for {family.family_id}; counted as "
                    "ENGINE_FAILED so the family stays in the denominator"
                )
                empty = FindingSet(
                    arm=arm,
                    family_id=family.family_id,
                    sha=family.sha,
                    outcome="ENGINE_FAILED",
                    verdict="",
                    runtime_seconds=0.0,
                    human_minutes=0.0,
                    cost_usd=0.0,
                    engineer_edits=0,
                    findings=(),
                    repaired=(),
                    stages=(),
                    notes="the arm wrote no findings file",
                )
                label_missing: LabelSet = load_labels(label_file)
                rows.append(
                    score_family(
                        definitions,
                        family,
                        label_missing,
                        empty,
                        match(definitions, label_missing, arm, ()),
                        "seeded" if kind == "seeded" else "natural",
                    )
                )
                continue
            label_set: LabelSet = load_labels(label_file)
            findings: FindingSet = load_findings(findings_file)
            telemetry_file = findings_file.parent / "telemetry-0.json"
            if telemetry_file.is_file():
                telemetry = read_json(telemetry_file)
                seconds = telemetry.get("runtime_seconds")
                spend = telemetry.get("cost_usd")
                findings = replace(
                    findings,
                    runtime_seconds=(
                        float(seconds)
                        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool)
                        else 0.0
                    ),
                    cost_usd=(
                        float(spend)
                        if isinstance(spend, (int, float)) and not isinstance(spend, bool)
                        else findings.cost_usd
                    ),
                )
            if not label_set.signed:
                signed = False
            if findings.degraded:
                degraded = findings.degraded
            report = match(definitions, label_set, arm, findings.findings)
            rows.append(
                score_family(
                    definitions,
                    family,
                    label_set,
                    findings,
                    report,
                    "seeded" if kind == "seeded" else "natural",
                )
            )
        if not rows:
            sections.append(
                unavailable_arm(
                    arm,
                    "no family produced both a label set and a findings file",
                    tuple(missing),
                )
            )
            rendered.append(
                f"ARM {arm}  ARM_UNAVAILABLE  no family produced both a label set and a "
                "findings file\n" + "\n".join(f"    missing: {entry}" for entry in missing)
            )
            continue
        section = arm_section(arm, kind, tuple(rows), signed, degraded, tuple(missing))
        sections.append(section.body())
        rendered.append(render_text(section))
    body["arms"] = sections
    write_scorecard(Path(str(args.out)), body)
    for text in rendered:
        print(text)
    print(f"wrote {args.out}")
    return 0


def command_label(args: argparse.Namespace) -> int:
    families = _selected(load_families(Path(str(args.families))), list(args.only))
    labels_root = Path(str(args.labels))
    work_root = Path(str(args.out)) / "work"
    total = 0
    for family in families:
        repo = work_root / family.family_id / "repo"
        if not (repo / ".git").is_dir():
            print(f"{family.family_id:24} NO TREE at {repo}; run the natural arm first")
            continue
        if not history.unshallow(repo):
            print(f"{family.family_id:24} could not unshallow; S2 cannot mine this history")
        labels, adjudicated, notes = history.build(repo, family)
        eligible = len(mutate.surface_files(repo, family.language))
        coverage: list[dict[str, object]] = [
            {"path": entry, "sources": ["S2"], "adjudicated": True, "reason_if_not": ""}
            for entry in adjudicated
        ]
        s1 = typecheck.build(repo, family, work_root / family.family_id / "s1")
        merged_notes = list(notes)
        if s1.available:
            merged_notes.extend(s1.notes)
            by_path = {str(row["path"]): row for row in coverage}
            for row in s1.coverage:
                key = str(row["path"])
                if key in by_path:
                    existing = by_path[key]
                    sources = cast("list[str]", existing.get("sources", []))
                    existing["sources"] = sorted({*sources, "S1"})
                    existing["s1"] = row
                else:
                    coverage.append(row)
                    by_path[key] = row
        else:
            merged_notes.append(f"S1 did not run: {s1.reason}")
        combined, clashes = merge.merge_labels((labels, s1.labels))
        merge.write(
            labels_root / f"{family.family_id}.json",
            family.family_id,
            family.repo,
            family.sha,
            combined,
            tuple(coverage),
            max(eligible, len(coverage)),
            tuple(merged_notes),
            clashes,
        )
        total += len(combined)
        print(
            f"{family.family_id:24} labels={len(combined):5} S2={len(labels):5} "
            f"S1={len(s1.labels):4} files={len(coverage):4} eligible={eligible:5} "
            f"clashes={len(clashes):3} s1={'ok' if s1.available else 'unavailable'}"
        )
    print(f"wrote natural labels for {len(families)} families, {total} labels")
    return 0


def command_challenge(args: argparse.Namespace) -> int:
    definitions = _load_fixed()
    scope = load_scope(Path(str(args.engine)))
    engine_root = Path(str(args.engine_root))
    result = check_scope(scope, engine_root)
    if not result.ok:
        for problem in result.problems:
            print(f"SCOPE MISMATCH {problem}")
        return 1
    families = _selected(load_families(Path(str(args.families))), list(args.only))
    work_root = Path(str(args.out)) / "work"
    rows: list[dict[str, object]] = []
    for family in families:
        repo = work_root / family.family_id / "repo"
        out = work_root / family.family_id / "out"
        state = work_root / family.family_id / "state"
        receipt = out / "receipt.json"
        obligations = out / "obligations.json"
        if not (repo / ".git").is_dir() or not receipt.is_file():
            print(f"{family.family_id:24} SKIPPED: no arm A candidate tree and receipt")
            continue
        head = run(("git", "rev-parse", "HEAD"), cwd=repo, timeout=60.0)
        candidate_sha = head.stdout.strip()
        baseline = challenge.signals(receipt)
        for corruption in challenge.CHALLENGES:
            run(("git", "reset", "--hard", "--quiet", candidate_sha), cwd=repo, timeout=120.0)
            applied = challenge.apply_one(repo, corruption, family.language)
            if not applied.applied:
                rows.append(
                    {
                        "family_id": family.family_id,
                        "language": family.language,
                        "challenge_id": corruption.challenge_id,
                        "name": corruption.name,
                        "applied": False,
                        "reason": applied.reason,
                        "intent": corruption.intent,
                    }
                )
                print(f"{family.family_id:24} {corruption.challenge_id} n/a  {applied.reason[:50]}")
                continue
            run(("git", "add", "--", applied.path), cwd=repo, timeout=120.0)
            run(
                ("git", "commit", "--quiet", "-m", f"challenge {corruption.challenge_id}"),
                cwd=repo,
                timeout=120.0,
            )
            resolved = run(("git", "rev-parse", "HEAD"), cwd=repo, timeout=60.0)
            corrupted_sha = resolved.stdout.strip()
            bad_receipt = out / f"receipt-{corruption.challenge_id}.json"
            verified = run(
                (
                    "uv",
                    "run",
                    "hops",
                    "verify",
                    family.sha,
                    corrupted_sha,
                    "--pack",
                    "google_ads",
                    "--repo",
                    str(repo),
                    "--to",
                    definitions.target_version,
                    "--obligations",
                    str(obligations),
                    "--state-dir",
                    str(state),
                    "--receipt",
                    str(bad_receipt),
                ),
                cwd=engine_root,
                timeout=1200.0,
            )
            corrupted = challenge.signals(bad_receipt)
            outcome = challenge.catching_stage(baseline, corrupted)
            rows.append(
                {
                    "family_id": family.family_id,
                    "language": family.language,
                    "challenge_id": corruption.challenge_id,
                    "name": corruption.name,
                    "applied": True,
                    "site": f"{applied.path}:{applied.line}",
                    "before": applied.before,
                    "after": applied.after,
                    "intent": corruption.intent,
                    "expected_catcher": corruption.should_be_caught_by,
                    "verify_exit": verified.exit_code,
                    **outcome,
                }
            )
            mark = "CAUGHT " if outcome["caught"] else "ESCAPED"
            stage = ",".join(cast("list[str]", outcome["catching_stage"])) or "-"
            print(
                f"{family.family_id:24} {corruption.challenge_id} {mark} "
                f"verdict={outcome['verdict'] or '-':18} by={stage[:54]}"
            )
        run(("git", "reset", "--hard", "--quiet", candidate_sha), cwd=repo, timeout=120.0)
    applied_rows = [row for row in rows if row.get("applied")]
    caught = [row for row in applied_rows if row.get("caught")]
    challenge.write_report(
        Path(str(args.report)),
        {
            "schema": "hubbleops.corpus.challenge/1",
            "arm": "A",
            "engine_scope": definitions.engine_scope,
            "external": (
                "These corruptions are authored for this run and are not the tests/adversarial "
                "corpus the engine was built against. The catching stage is the conjunct, "
                "falsifier or audit reason that newly fires against the uncorrupted candidate's "
                "own receipt, so a family whose baseline verdict is already FAILED still "
                "isolates each corruption's own signal."
            ),
            "applied": len(applied_rows),
            "caught": len(caught),
            "escaped": len(applied_rows) - len(caught),
            "rows": rows,
        },
    )
    print(
        f"\nchallenges applied {len(applied_rows)}, caught {len(caught)}, "
        f"escaped {len(applied_rows) - len(caught)}"
    )
    print(f"wrote {args.report}")
    return 0


def command_decision_report(args: argparse.Namespace) -> int:
    families = _selected(load_families(Path(str(args.families))), list(args.only))
    work_root = Path(str(args.out)) / "work"
    findings_root = Path(str(args.out)) / "A"
    reports = Path(str(args.reports))
    reports.mkdir(parents=True, exist_ok=True)
    written = 0
    for family in families:
        findings_file = findings_root / family.family_id / "findings.json"
        if not findings_file.is_file():
            print(f"{family.family_id:24} no arm A findings; skipped")
            continue
        findings = load_findings(findings_file)
        telemetry_file = findings_root / family.family_id / "telemetry-0.json"
        telemetry = read_json(telemetry_file) if telemetry_file.is_file() else {}
        raw_stages = telemetry.get("stages")
        if isinstance(raw_stages, list):
            recorded: list[StageRecord] = []
            for item in cast("list[object]", raw_stages):
                if not isinstance(item, dict):
                    continue
                entry = {str(k): v for k, v in cast("dict[object, object]", item).items()}
                code = entry.get("exit_code")
                recorded.append(
                    StageRecord(
                        name=str(entry.get("name", "")),
                        exit_code=code
                        if isinstance(code, int) and not isinstance(code, bool)
                        else 0,
                        seconds=0.0,
                        detail="",
                    )
                )
            findings = replace(findings, stages=tuple(recorded))
        text = decision.render(
            family,
            findings,
            work_root / family.family_id / "out" / "receipt.json",
            work_root / family.family_id / "out" / "exposure.txt",
            telemetry,
        )
        target = reports / f"{family.family_id}.txt"
        target.write_text(text, encoding="utf-8")
        written += 1
        print(f"{family.family_id:24} wrote {target}")
    print(f"{written} decision report(s) in {reports}")
    return 0


def command_rederive(args: argparse.Namespace) -> int:
    definitions = _load_fixed()
    families = _selected(load_families(Path(str(args.families))), list(args.only))
    variant = str(args.variant)
    work_root = Path(str(args.out)) / "work"
    findings_root = Path(str(args.out)) / "A"
    changed = 0
    for family in families:
        name = f"{family.family_id}.seeded" if variant == "seeded" else family.family_id
        candidates = (
            [work_root / family.family_id / "out-natural", work_root / family.family_id / "out"]
            if variant == "natural"
            else [work_root / family.family_id / f"out-{variant}"]
        )
        out_dir = next(
            (entry for entry in candidates if (entry / "ledger.json").is_file()), candidates[0]
        )
        ledger = out_dir / "ledger.json"
        obligations = out_dir / "obligations.json"
        target = findings_root / name / "findings.json"
        if not ledger.is_file() or not target.is_file():
            print(f"{family.family_id:24} no persisted ledger or findings; skipped")
            continue
        recorded = load_findings(target)
        rebuilt = arm_a.findings_from_ledger(
            definitions,
            read_json(ledger),
            read_json(obligations) if obligations.is_file() else None,
        )
        before = arm_a.digest(target)
        arm_a.write_findings(target, replace(recorded, findings=rebuilt))
        after = arm_a.digest(target)
        actionable = sum(1 for item in rebuilt if item.asserted == "actionable")
        print(
            f"{family.family_id:24} findings {len(recorded.findings):5} -> {len(rebuilt):5} "
            f"actionable={actionable:5} {'changed' if before != after else 'unchanged'}"
        )
        if before != after:
            changed += 1
    print(f"{changed} findings file(s) changed")
    return 0


ENGINE_STAGE = {
    "scan": "discovery",
    "exposure": "classification",
    "migrate": "repair",
    "verify": "verification",
    "post-install-commit": "environment",
    "venv": "environment",
}


def _stage_of(name: str) -> str:
    head = name.split(" ")[0].split("[")[0]
    if head.startswith("git-") or head in ("install", "venv", "post-install-commit"):
        return "environment"
    return ENGINE_STAGE.get(head, "verification")


def command_outcomes(args: argparse.Namespace) -> int:
    families = load_families(Path(str(args.families)))
    arm = str(args.arm)
    root = Path(str(args.out)) / arm
    counts: dict[str, int] = {}
    print(f"OUTCOME COLUMN, arm {arm}, one line per family\n")
    for family in sorted(families, key=lambda item: item.family_id):
        findings_file = root / family.family_id / "findings.json"
        telemetry_file = root / family.family_id / "telemetry-0.json"
        if not findings_file.is_file():
            print(f"{family.family_id:24} NO FINDINGS FILE — the arm wrote nothing")
            counts["NO_OUTPUT"] = counts.get("NO_OUTPUT", 0) + 1
            continue
        record = load_findings(findings_file)
        telemetry = read_json(telemetry_file) if telemetry_file.is_file() else {}
        counts[record.outcome] = counts.get(record.outcome, 0) + 1
        seconds = telemetry.get("runtime_seconds")
        elapsed = f"{float(seconds):.0f}s" if isinstance(seconds, (int, float)) else "?"
        print(
            f"{family.family_id:24} {family.language:11} {family.source_api_version:4} "
            f"{record.outcome:14} verdict={record.verdict or '-':18} {elapsed:>7} "
            f"outcome_stage={'environment' if record.outcome == 'SETUP_FAILED' else 'see below'}"
        )
        if record.outcome in ("COMPLETED",):
            continue
        raw = telemetry.get("stages")
        stages = cast("list[object]", raw) if isinstance(raw, list) else []
        shown = 0
        for item in stages:
            if not isinstance(item, dict):
                continue
            entry = {str(k): v for k, v in cast("dict[object, object]", item).items()}
            code = entry.get("exit_code")
            if not isinstance(code, int) or code == 0:
                continue
            name = str(entry.get("name", ""))
            detail = " ".join(str(entry.get("detail", "")).split())
            print(f"    stage={_stage_of(name):14} command: {name}")
            print(f"    {'':14}      exit={code}  error: {detail[:220]}")
            shown += 1
            if shown >= 3:
                break
        if not shown:
            print(f"    stage={'verification':14} no non-zero command; {record.notes[:160]}")
    print("\nOUTCOMES  " + "  ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    completed = counts.get("COMPLETED", 0)
    print(
        f"COMPLETED {completed} of {len(families)}. Every other family is reported with the stage "
        "that stopped it; none is counted as a pass."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.verb == "run":
            return command_run(args)
        if args.verb == "score":
            return command_score(args)
        if args.verb == "check":
            return command_check(args)
        if args.verb == "label":
            return command_label(args)
        if args.verb == "challenge":
            return command_challenge(args)
        if args.verb == "decision-report":
            return command_decision_report(args)
        if args.verb == "rederive":
            return command_rederive(args)
        if args.verb == "outcomes":
            return command_outcomes(args)
    except CorpusError as error:
        print(f"CORPUS ERROR {error}")
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
