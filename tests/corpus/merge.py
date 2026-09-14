from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from tests.corpus.spec import Label

RANK = {"ACTIONABLE": 2, "CLEAN": 1, "UNSETTLED": 0}


@dataclass(frozen=True)
class Disagreement:
    path: str
    line: int
    verdicts: tuple[str, ...]
    sources: tuple[str, ...]
    evidence: tuple[str, ...]


def merge_labels(
    groups: tuple[tuple[Label, ...], ...],
) -> tuple[tuple[Label, ...], tuple[Disagreement, ...]]:
    by_site: dict[tuple[str, int], list[Label]] = {}
    for group in groups:
        for label in group:
            by_site.setdefault((label.path, label.line), []).append(label)
    out: list[Label] = []
    clashes: list[Disagreement] = []
    for site in sorted(by_site):
        entries = sorted(by_site[site], key=lambda item: item.label_id)
        settled = [entry for entry in entries if entry.verdict != "UNSETTLED"]
        verdicts = {entry.verdict for entry in settled}
        if len(verdicts) > 1:
            clashes.append(
                Disagreement(
                    path=site[0],
                    line=site[1],
                    verdicts=tuple(sorted(verdicts)),
                    sources=tuple(
                        sorted({source for entry in entries for source in entry.sources})
                    ),
                    evidence=tuple(entry.evidence for entry in settled),
                )
            )
            head = entries[0]
            out.append(
                Label(
                    label_id=head.label_id,
                    path=head.path,
                    line=head.line,
                    scope=head.scope,
                    mechanism=head.mechanism,
                    verdict="UNSETTLED",
                    kind=head.kind,
                    sources=tuple(
                        sorted({source for entry in entries for source in entry.sources})
                    ),
                    subject=head.subject,
                    evidence=(
                        "sources disagree and the owner has not ruled: "
                        + " || ".join(entry.evidence for entry in settled)[:400]
                    ),
                )
            )
            continue
        chosen = max(entries, key=lambda item: (RANK.get(item.verdict, 0), item.label_id))
        out.append(
            Label(
                label_id=chosen.label_id,
                path=chosen.path,
                line=chosen.line,
                scope=chosen.scope,
                mechanism=chosen.mechanism,
                verdict=chosen.verdict,
                kind=chosen.kind,
                sources=tuple(sorted({source for entry in entries for source in entry.sources})),
                subject=chosen.subject,
                evidence=chosen.evidence,
            )
        )
    return (tuple(out), tuple(clashes))


def write(
    path: Path,
    family_id: str,
    repo: str,
    sha: str,
    labels: tuple[Label, ...],
    coverage: tuple[dict[str, object], ...],
    eligible_files: int,
    notes: tuple[str, ...],
    clashes: tuple[Disagreement, ...],
) -> None:
    body: dict[str, object] = {
        "schema": "hubbleops.corpus.labels/1",
        "family_id": family_id,
        "repo": repo,
        "sha": sha,
        "sources": ["S1", "S2"],
        "kind": "natural",
        "signed": False,
        "signer": "",
        "eligible_files": eligible_files,
        "notes": list(notes),
        "disagreements": [
            {
                "path": entry.path,
                "line": entry.line,
                "verdicts": list(entry.verdicts),
                "sources": list(entry.sources),
                "evidence": list(entry.evidence),
                "ruling": "",
            }
            for entry in clashes
        ],
        "disagreement_rule": (
            "Where two sources settle the same site differently the site is UNSETTLED and scores "
            "neither way until the owner rules. The ruling field is empty until then."
        ),
        "adjudicated_files": list(coverage),
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
