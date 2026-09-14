from __future__ import annotations

import json
from pathlib import Path

from tests.corpus import gaql, mutate
from tests.corpus.shell import run
from tests.corpus.spec import Family, Label

GAQL_TEMPLATE = {
    "python": 'HOPS_SEEDED_GAQL = "{query}"',
    "php": "$hopsSeededGaql = '{query}';",
    "typescript": 'export const hopsSeededGaql = "{query}";',
    "javascript": 'export const hopsSeededGaql = "{query}";',
}


def gaql_mutation(
    family: Family, repo: Path, work: Path
) -> tuple[mutate.Mutation | None, str, str]:
    source_venv = work / "s1" / "sdk-source"
    target_venv = work / "s1" / "sdk-target"
    if not source_venv.is_dir() or not target_venv.is_dir():
        return (
            None,
            "",
            "no removed-field GAQL mutation was planted: the two SDK environments S1 builds are "
            "absent, and the pack's own change data may not be used as a label source",
        )
    removed, why = gaql.find_removed(source_venv, target_venv, family.source_api_version, work)
    if removed is None:
        return (None, "", f"no removed-field GAQL mutation was planted: {why}")
    template = GAQL_TEMPLATE.get(family.language.strip().lower())
    if template is None:
        return (None, "", f"no GAQL template for language {family.language!r}")
    files = mutate.surface_files(repo, family.language)
    if not files:
        return (None, "", "no first-party file to carry a seeded GAQL query")
    target = files[len(files) // 2]
    return (
        mutate.Mutation(
            carrier="gaql_removed_field",
            mechanism="removed_field_read",
            path=target,
            line=0,
            text=template.format(query=removed.query()),
            version=family.source_api_version,
        ),
        removed.subject,
        why,
    )


def seed_tree(
    family: Family, repo: Path, work: Path | None = None
) -> tuple[tuple[mutate.Mutation, ...], tuple[str, ...]]:
    plan = mutate.plan(repo, family)
    wanted = list(plan.mutations)
    skipped = list(plan.skipped)
    subject = ""
    if work is not None:
        extra, subject, why = gaql_mutation(family, repo, work)
        if extra is None:
            skipped.append(why)
        else:
            wanted.append(extra)
            skipped.append(f"gaql_removed_field planted on {subject}: {why}")
    plan = mutate.MutationPlan(mutations=tuple(wanted), skipped=tuple(skipped))
    placed = mutate.apply(repo, plan.mutations)
    skipped = list(plan.skipped)
    for intended in plan.mutations:
        if not any(
            entry.path == intended.path and entry.carrier == intended.carrier for entry in placed
        ):
            skipped.append(f"{intended.carrier} could not be placed at {intended.path}")
    return placed, tuple(skipped)


def commit_seed(repo: Path, placed: tuple[mutate.Mutation, ...]) -> str:
    for path in sorted({entry.path for entry in placed}):
        run(("git", "add", "--", path), cwd=repo, timeout=120.0)
    run(
        ("git", "commit", "--quiet", "-m", "hubbleops corpus seeded mechanisms"),
        cwd=repo,
        timeout=180.0,
    )
    resolved = run(("git", "rev-parse", "HEAD"), cwd=repo, timeout=60.0)
    return resolved.stdout.strip() if resolved.ok else ""


def eligible_file_count(repo: Path, family: Family) -> int:
    return len(mutate.surface_files(repo, family.language))


def write_labels(
    path: Path,
    family: Family,
    sha: str,
    placed: tuple[mutate.Mutation, ...],
    skipped: tuple[str, ...],
    eligible_files: int,
) -> tuple[Label, ...]:
    labels = mutate.labels_for(placed)
    adjudicated = sorted({entry.path for entry in placed})
    body: dict[str, object] = {
        "schema": "hubbleops.corpus.labels/1",
        "family_id": family.family_id,
        "repo": family.repo,
        "sha": sha,
        "source": "S3",
        "kind": "seeded",
        "signed": False,
        "signer": "",
        "seed": mutate.SEED,
        "seeded_version": mutate.SEEDED_VERSION,
        "eligible_files": max(eligible_files, len(adjudicated)),
        "not_seeded": list(skipped),
        "coverage_note": (
            "S3 adjudicates only the sites it created. Every other file in this family is "
            "UNADJUDICATED by S3 and is never counted clean. Seeded numbers measure the pack's "
            "declared mechanism lattice, not the wild, and are never summed with natural labels."
        ),
        "adjudicated_files": [
            {"path": entry, "sources": ["S3"], "adjudicated": True, "reason_if_not": ""}
            for entry in adjudicated
        ],
        "labels": [
            {
                "id": label.label_id,
                "path": label.path,
                "line": label.line,
                "scope": label.scope,
                "mechanism": label.mechanism,
                "verdict": label.verdict,
                "kind": label.kind,
                "sources": list(label.sources),
                "subject": label.subject,
                "evidence": label.evidence,
            }
            for label in labels
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return labels
