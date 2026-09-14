from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from tests.corpus import seeding
from tests.corpus.shell import Budget, BudgetExhausted, Ran, run, shell_words
from tests.corpus.spec import (
    Asserted,
    CorpusError,
    Definitions,
    Family,
    Finding,
    FindingSet,
    Outcome,
    StageRecord,
    normalise_path,
    read_json,
)

PACK = "google_ads"
COVERAGE_CLAIMS = frozenset({"file_unscanned", "structure_unsupported", "ai_triage_residue"})
ACTIONABLE_STATUS = frozenset({"AFFECTED"})
UNKNOWN_STATUS = frozenset({"UNKNOWN", "HUMAN_REQUIRED"})
CLEAN_STATUS = frozenset(
    {"NOT_AFFECTED_WITH_EVIDENCE", "EXCLUDED_WITH_EVIDENCE", "PROVIDER_REFERENCE_DATA"}
)
SKIP_STATUS = frozenset({"UNSUPPORTED", "UNSCANNED"})
HUMAN_DECIDED_STATUS = frozenset({"HUMAN_ACCEPTED_RISK"})


@dataclass(frozen=True)
class ArmRun:
    findings: FindingSet
    stages: tuple[StageRecord, ...]
    runtime_seconds: float
    ledger_digest: str
    receipt_digest: str
    base_sha: str
    candidate_sha: str


def finding_id(candidate_id: str, claim_type: str, path: str, start: int, end: int) -> str:
    raw = f"{candidate_id}|{claim_type}|{path}|{start}|{end}".encode()
    return "F" + hashlib.sha256(raw).hexdigest()[:14]


def _evidence_index(ledger: dict[str, object]) -> dict[str, dict[str, object]]:
    raw = ledger.get("evidence")
    if not isinstance(raw, list):
        return {}
    out: dict[str, dict[str, object]] = {}
    for item in cast("list[object]", raw):
        if not isinstance(item, dict):
            continue
        record = {str(key): value for key, value in cast("dict[object, object]", item).items()}
        record_id = record.get("id")
        if isinstance(record_id, str):
            out[record_id] = record
    return out


def _obligation_findings(
    definitions: Definitions,
    evidence: dict[str, dict[str, object]],
    obligations: dict[str, object] | None,
    seen: set[str],
) -> list[Finding]:
    if obligations is None:
        return []
    raw = obligations.get("obligations")
    entries: list[object] = cast("list[object]", raw) if isinstance(raw, list) else []
    out: list[Finding] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        record = {str(k): v for k, v in cast("dict[object, object]", item).items()}
        obligation_id = str(record.get("id", ""))
        ids = record.get("evidence_ids")
        for key in (
            [str(value) for value in cast("list[object]", ids)] if isinstance(ids, list) else []
        ):
            source = evidence.get(key)
            if source is None:
                continue
            claim_type = str(source.get("claim_type", ""))
            if claim_type in COVERAGE_CLAIMS:
                continue
            path = normalise_path(str(source.get("path", "")))
            if not path:
                continue
            start = source.get("line_start")
            end = source.get("line_end")
            start_line = start if isinstance(start, int) and not isinstance(start, bool) else 0
            end_line = end if isinstance(end, int) and not isinstance(end, bool) else start_line
            subject = source.get("provider_subject")
            marker = finding_id(
                f"obligation:{obligation_id}", claim_type, path, start_line, end_line
            )
            if marker in seen:
                continue
            seen.add(marker)
            out.append(
                Finding(
                    finding_id=marker,
                    path=path,
                    start_line=start_line,
                    end_line=max(start_line, end_line),
                    mechanism=definitions.claim_type_to_mechanism.get(claim_type, "other"),
                    asserted="actionable",
                    subject=subject if isinstance(subject, str) else "",
                    reason=f"OBLIGATION:{record.get('status', '')!s}:{claim_type}",
                )
            )
    return out


def findings_from_ledger(
    definitions: Definitions,
    ledger: dict[str, object],
    obligations: dict[str, object] | None = None,
) -> tuple[Finding, ...]:
    evidence = _evidence_index(ledger)
    entries = ledger.get("candidates")
    if not isinstance(entries, list):
        raise CorpusError("ledger export carries no candidates array")
    seen: set[str] = set()
    out: list[Finding] = []
    for item in cast("list[object]", entries):
        if not isinstance(item, dict):
            continue
        wrapper = {str(key): value for key, value in cast("dict[object, object]", item).items()}
        raw_candidate = wrapper.get("candidate")
        if not isinstance(raw_candidate, dict):
            continue
        candidate = {
            str(key): value for key, value in cast("dict[object, object]", raw_candidate).items()
        }
        status = str(candidate.get("status", ""))
        if status in SKIP_STATUS:
            continue
        asserted: Asserted
        if status in ACTIONABLE_STATUS:
            asserted = "actionable"
        elif status in UNKNOWN_STATUS:
            asserted = "unknown"
        elif status in CLEAN_STATUS or status in HUMAN_DECIDED_STATUS:
            asserted = "clean"
        else:
            continue
        candidate_id = str(candidate.get("id", ""))
        ids = candidate.get("evidence_ids")
        evidence_ids = (
            [str(value) for value in cast("list[object]", ids)] if isinstance(ids, list) else []
        )
        records = [evidence[key] for key in evidence_ids if key in evidence]
        claim_types = {str(record.get("claim_type", "")) for record in records}
        if claim_types and claim_types <= COVERAGE_CLAIMS:
            continue
        for record in records:
            claim_type = str(record.get("claim_type", ""))
            if claim_type in COVERAGE_CLAIMS:
                continue
            path = normalise_path(str(record.get("path", "")))
            if path == "":
                continue
            start = record.get("line_start")
            end = record.get("line_end")
            start_line = start if isinstance(start, int) and not isinstance(start, bool) else 0
            end_line = end if isinstance(end, int) and not isinstance(end, bool) else start_line
            mechanism = definitions.claim_type_to_mechanism.get(claim_type, "other")
            subject = record.get("provider_subject")
            key = finding_id(candidate_id, claim_type, path, start_line, end_line)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                Finding(
                    finding_id=key,
                    path=path,
                    start_line=start_line,
                    end_line=max(start_line, end_line),
                    mechanism=mechanism,
                    asserted=asserted,
                    subject=subject if isinstance(subject, str) else "",
                    reason=f"{status}:{claim_type}",
                )
            )
    out.extend(_obligation_findings(definitions, evidence, obligations, seen))
    return tuple(
        sorted(
            out,
            key=lambda item: (
                item.path,
                item.start_line,
                item.end_line,
                item.mechanism,
                item.finding_id,
            ),
        )
    )


def repaired_paths(repo: Path, base_sha: str) -> tuple[str, ...]:
    listed = run(("git", "diff", "--name-only", base_sha), cwd=repo, timeout=120.0)
    if not listed.ok:
        return ()
    return tuple(
        sorted({normalise_path(line) for line in listed.stdout.splitlines() if line.strip()})
    )


def digest(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stage(name: str, ran: Ran, detail: str = "") -> StageRecord:
    return StageRecord(
        name=name,
        exit_code=124 if ran.timed_out else ran.exit_code,
        seconds=round(ran.seconds, 3),
        detail=detail or (ran.both_ends(1600) if not ran.ok else ran.tail(400)),
    )


def fetch_pinned(family: Family, repo: Path, budget: Budget) -> tuple[StageRecord, ...]:
    if repo.exists():
        shutil.rmtree(repo, ignore_errors=True)
    repo.mkdir(parents=True, exist_ok=True)
    stages: list[StageRecord] = []
    init = run(("git", "init", "--quiet"), cwd=repo, timeout=budget.claim("clone", 60.0))
    stages.append(_stage("git-init", init))
    if not init.ok:
        return tuple(stages)
    remote = run(
        ("git", "remote", "add", "origin", family.clone_url),
        cwd=repo,
        timeout=budget.claim("clone", 60.0),
    )
    stages.append(_stage("git-remote", remote))
    shallow = run(
        ("git", "fetch", "--depth", "1", "--quiet", "origin", family.sha),
        cwd=repo,
        timeout=budget.claim("clone", 900.0),
    )
    stages.append(_stage("git-fetch-pinned", shallow))
    if not shallow.ok:
        deep = run(
            ("git", "fetch", "--quiet", "origin"),
            cwd=repo,
            timeout=budget.claim("clone", 1200.0),
        )
        stages.append(_stage("git-fetch-all", deep))
        if not deep.ok:
            return tuple(stages)
    checkout = run(
        ("git", "checkout", "--quiet", "--detach", family.sha),
        cwd=repo,
        timeout=budget.claim("clone", 300.0),
    )
    stages.append(_stage("git-checkout", checkout))
    if checkout.ok:
        branch = run(
            ("git", "switch", "--quiet", "--create", "hubbleops-corpus-base"),
            cwd=repo,
            timeout=budget.claim("clone", 120.0),
        )
        stages.append(_stage("git-branch", branch))
    return tuple(stages)


def posix_path(path: Path) -> str:
    text = str(path).replace("\\", "/")
    if len(text) > 1 and text[1] == ":":
        return f"/{text[0].lower()}{text[2:]}"
    return text


def isolated_env(repo: Path, venv: Path, budget: Budget) -> tuple[str, StageRecord]:
    made = run(
        ("uv", "venv", "--seed", "--quiet", str(venv)),
        cwd=repo,
        timeout=budget.claim("venv", 300.0),
    )
    if not made.ok:
        return ("", _stage("venv", made))
    scripts = venv / ("Scripts" if (venv / "Scripts").is_dir() else "bin")
    prefix = (
        f'export PATH="{posix_path(scripts)}:$PATH"; '
        f'export VIRTUAL_ENV="{posix_path(venv)}"; '
        "export PIP_DISABLE_PIP_VERSION_CHECK=1; "
    )
    return (prefix, _stage("venv", made))


def install(family: Family, repo: Path, venv: Path, budget: Budget) -> tuple[StageRecord, ...]:
    stages: list[StageRecord] = []
    prefix, made = isolated_env(repo, venv, budget)
    stages.append(made)
    if not prefix:
        return tuple(stages)
    for index, line in enumerate(family.documented_install):
        allowed = budget.claim(f"install[{index}]", budget.remaining)
        ran = run(("bash", "-lc", prefix + line), cwd=repo, timeout=allowed)
        stages.append(_stage(f"install[{index}] {line}", ran))
        if not ran.ok:
            break
    return tuple(stages)


def _engine(
    engine_root: Path,
    args: tuple[str, ...],
    budget: Budget,
    name: str,
    want: float,
    env: dict[str, str] | None = None,
) -> Ran:
    allowed = budget.claim(name, want)
    return run(("uv", "run", "hops", *args), cwd=engine_root, timeout=allowed, env=env)


def read_verdict(receipt: Path) -> tuple[str, tuple[str, ...]]:
    if not receipt.is_file():
        return ("", ())
    body = read_json(receipt)
    verdict = body.get("verdict")
    raw = body.get("reasons")
    reasons: list[str] = []
    if isinstance(raw, list):
        for item in cast("list[object]", raw):
            reasons.append(str(item))
    return (str(verdict) if isinstance(verdict, str) else "", tuple(reasons))


def outcome_for(verdict: str, stages: tuple[StageRecord, ...]) -> tuple[Outcome, str]:
    install_failed = [
        entry for entry in stages if entry.name.startswith("install") and entry.exit_code != 0
    ]
    clone_failed = [
        entry for entry in stages if entry.name.startswith("git-") and entry.exit_code != 0
    ]
    if clone_failed and not any(
        entry.name.startswith("git-fetch-all") and entry.exit_code == 0 for entry in stages
    ):
        blocking = [entry for entry in clone_failed if entry.name != "git-fetch-pinned"]
        if blocking:
            return ("SETUP_FAILED", f"clone stage failed: {blocking[0].name}")
    if install_failed:
        return ("SETUP_FAILED", f"documented install failed: {install_failed[0].name}")
    if verdict == "VERIFIED_FOR_SCOPE":
        return ("COMPLETED", "")
    if verdict in ("HUMAN_REQUIRED", "UNKNOWN"):
        return ("HUMAN_REQUIRED", f"verdict {verdict}")
    if verdict == "FAILED":
        return (
            "HUMAN_REQUIRED",
            "verdict FAILED; every reason is recorded and awaits truth adjudication",
        )
    return ("ENGINE_FAILED", "no verdict was produced")


def run_family(
    definitions: Definitions,
    family: Family,
    engine_root: Path,
    work_root: Path,
    budget_seconds: float,
    variant: str = "natural",
    labels_path: Path | None = None,
) -> ArmRun:
    suffix = "repo" if variant == "natural" else f"repo-{variant}"
    repo = work_root / family.family_id / suffix
    state = work_root / family.family_id / f"state-{variant}"
    out = work_root / family.family_id / f"out-{variant}"
    for target in (state, out):
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
    ledger = out / "ledger.json"
    obligations = out / "obligations.json"
    receipt = out / "receipt.json"
    exposure_text = out / "exposure.txt"

    budget = Budget(budget_seconds)
    stages: list[StageRecord] = []
    findings: tuple[Finding, ...] = ()
    verdict = ""
    reasons: tuple[str, ...] = ()
    base_sha = family.sha
    candidate_sha = ""
    notes: list[str] = []

    try:
        stages.extend(fetch_pinned(family, repo, budget))
        if any(entry.exit_code != 0 for entry in stages if entry.name == "git-checkout"):
            raise BudgetExhausted("clone", budget.spent, budget.allowed)
        if variant == "seeded":
            placed, skipped = seeding.seed_tree(family, repo, work_root / family.family_id)
            seeded_sha = seeding.commit_seed(repo, placed)
            if seeded_sha:
                base_sha = seeded_sha
            if labels_path is not None:
                seeding.write_labels(
                    labels_path,
                    family,
                    base_sha,
                    placed,
                    skipped,
                    seeding.eligible_file_count(repo, family),
                )
            stages.append(
                StageRecord(
                    name="seed",
                    exit_code=0 if placed else 1,
                    seconds=0.0,
                    detail=f"{len(placed)} mechanisms seeded; {len(skipped)} not seeded",
                )
            )
        stages.extend(install(family, repo, work_root / family.family_id / "venv", budget))
        install_failed = any(
            entry.name.startswith("install") and entry.exit_code != 0 for entry in stages
        )
        dirty = run(("git", "status", "--porcelain"), cwd=repo, timeout=180.0)
        if dirty.ok and dirty.stdout.strip():
            touched = [line[3:].strip() for line in dirty.stdout.splitlines() if line.strip()]
            for path in touched:
                run(("git", "add", "--", path), cwd=repo, timeout=120.0)
            settled = run(
                ("git", "commit", "--quiet", "-m", "state after the documented install"),
                cwd=repo,
                timeout=300.0,
            )
            resolved = run(("git", "rev-parse", "HEAD"), cwd=repo, timeout=60.0)
            if settled.ok and resolved.ok:
                base_sha = resolved.stdout.strip()
            stages.append(
                StageRecord(
                    name="post-install-commit",
                    exit_code=settled.exit_code,
                    seconds=round(settled.seconds, 3),
                    detail=(
                        f"the documented install left {len(touched)} tracked path(s) modified; "
                        "the engine refuses to derive obligations from a tree that differs from "
                        "HEAD, so the installed state is committed and becomes the base SHA: "
                        + ", ".join(touched[:6])
                    ),
                )
            )
        if install_failed:
            notes.append(
                "the documented install did not complete, so the outcome is SETUP_FAILED; the "
                "SCAN plane needs no dependency and was run anyway, so discovery and "
                "classification are still measured"
            )
        if repo.is_dir():
            scan = _engine(
                engine_root,
                (
                    "scan",
                    str(repo),
                    "--pack",
                    PACK,
                    "--state-dir",
                    str(state),
                    "--export",
                    str(ledger),
                    "--target",
                    definitions.target_version,
                ),
                budget,
                "scan",
                1800.0,
            )
            stages.append(_stage("scan", scan))
            if ledger.is_file():
                findings = findings_from_ledger(definitions, read_json(ledger))
            exposure = _engine(
                engine_root,
                (
                    "exposure",
                    "--repo",
                    str(repo),
                    "--pack",
                    PACK,
                    "--state-dir",
                    str(state),
                    "--target",
                    definitions.target_version,
                    "--expand",
                ),
                budget,
                "exposure",
                600.0,
            )
            stages.append(_stage("exposure", exposure))
            exposure_text.write_text(exposure.stdout, encoding="utf-8")
            migrate = _engine(
                engine_root,
                (
                    "migrate",
                    str(repo),
                    "--pack",
                    PACK,
                    "--state-dir",
                    str(state),
                    "--target",
                    definitions.target_version,
                    "--obligations",
                    str(obligations),
                ),
                budget,
                "migrate",
                900.0,
            )
            stages.append(_stage("migrate", migrate))
            if ledger.is_file():
                findings = findings_from_ledger(
                    definitions,
                    read_json(ledger),
                    read_json(obligations) if obligations.is_file() else None,
                )
            changed = repaired_paths(repo, base_sha)
            if changed:
                for path in changed:
                    staged = run(
                        ("git", "add", "--", path),
                        cwd=repo,
                        timeout=budget.claim("commit", 60.0),
                    )
                    if not staged.ok:
                        stages.append(_stage(f"git-add {path}", staged))
                commit = run(
                    ("git", "commit", "--quiet", "-m", "hubbleops corpus candidate"),
                    cwd=repo,
                    timeout=budget.claim("commit", 120.0),
                )
                stages.append(_stage("git-commit", commit))
            resolved = run(("git", "rev-parse", "HEAD"), cwd=repo, timeout=60.0)
            candidate_sha = resolved.stdout.strip() if resolved.ok else ""
            if candidate_sha:
                verify = _engine(
                    engine_root,
                    (
                        "verify",
                        base_sha,
                        candidate_sha,
                        "--pack",
                        PACK,
                        "--repo",
                        str(repo),
                        "--to",
                        definitions.target_version,
                        "--obligations",
                        str(obligations),
                        "--state-dir",
                        str(state),
                        "--receipt",
                        str(receipt),
                    ),
                    budget,
                    "verify",
                    1200.0,
                )
                stages.append(_stage("verify", verify))
                verdict, reasons = read_verdict(receipt)
    except BudgetExhausted as exhausted:
        stages.append(
            StageRecord(
                name=f"budget:{exhausted.stage}",
                exit_code=124,
                seconds=round(exhausted.spent, 3),
                detail=str(exhausted),
            )
        )
        notes.append(f"the arm exceeded its own budget during {exhausted.stage}")

    frozen = tuple(stages)
    outcome, why = outcome_for(verdict, frozen)
    if any(entry.name.startswith("budget:") for entry in frozen) and outcome != "SETUP_FAILED":
        outcome = "ENGINE_FAILED"
        why = "the arm exceeded its own budget"
    if why:
        notes.append(why)
    return ArmRun(
        findings=FindingSet(
            arm="A",
            family_id=family.family_id,
            sha=family.sha,
            outcome=outcome,
            verdict=verdict,
            runtime_seconds=0.0,
            human_minutes=0.0,
            cost_usd=0.0,
            engineer_edits=0,
            findings=findings,
            repaired=repaired_paths(repo, base_sha) if candidate_sha else (),
            stages=(),
            notes="; ".join(notes),
            degraded="",
            reasons=reasons,
        ),
        stages=frozen,
        runtime_seconds=round(budget.spent, 3),
        ledger_digest=digest(ledger),
        receipt_digest=digest(receipt),
        base_sha=base_sha,
        candidate_sha=candidate_sha,
    )


def install_lines(family: Family) -> tuple[tuple[str, ...], ...]:
    return tuple(shell_words(line) for line in family.documented_install)


def write_findings(path: Path, record: FindingSet) -> None:
    body: dict[str, object] = {
        "schema": "hubbleops.corpus.findings/1",
        "arm": record.arm,
        "family_id": record.family_id,
        "sha": record.sha,
        "outcome": record.outcome,
        "verdict": record.verdict,
        "reasons": list(record.reasons),
        "engineer_edits": record.engineer_edits,
        "degraded": record.degraded,
        "notes": record.notes,
        "repaired": list(record.repaired),
        "findings": [
            {
                "id": item.finding_id,
                "path": item.path,
                "start_line": item.start_line,
                "end_line": item.end_line,
                "mechanism": item.mechanism,
                "asserted": item.asserted,
                "subject": item.subject,
                "reason": item.reason,
            }
            for item in record.findings
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
