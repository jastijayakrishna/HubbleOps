from __future__ import annotations

import re
from typing import Any

from hubbleops.app import exposure
from hubbleops.core.candidate import candidate_identity, make_candidate
from hubbleops.core.evidence import make_evidence
from hubbleops.observe.ledger import Ledger

SCOPE = "0" * 64
RUN = "1" * 64


def evidence(path: str, claim_type: str) -> dict[str, Any]:
    return make_evidence(
        run_id=RUN,
        proof_scope_hash=SCOPE,
        claim_type=claim_type,
        observer="text",
        repo_sha=None,
        path=path,
        line_start=1,
        line_end=1,
        source_hash="a" * 64,
        value={"pattern": "x"},
        provider_subject=None,
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="RAW",
    )


def ledger_of(sites: list[tuple[str, str, str]]) -> Ledger:
    records = [evidence(path, claim) for path, claim, _ in sites]
    candidates = [
        make_candidate(
            candidate_id=candidate_identity("p", "surface_reference", path),
            run_id=RUN,
            proof_scope_hash=SCOPE,
            provider="p",
            evidence_ids=[record["id"]],
            status="UNKNOWN",
            reason=f"{path} is undecided",
            close_with=instruction,
        )
        for record, (path, _, instruction) in zip(records, sites, strict=True)
    ]
    return Ledger(
        provider="p",
        run_id=RUN,
        proof_scope_hash=SCOPE,
        evidence=tuple(records),
        candidates=tuple(candidates),
    )


def render(book: Ledger, expand: bool = False) -> str:
    return exposure.render(
        ledger=book,
        pack_name="p",
        changes_hash="c" * 64,
        target="v25",
        repository="/repo",
        repo_sha=None,
        expand_not_affected=expand,
    )


def site_counts(rendered: str) -> list[int]:
    inside = False
    counts: list[int] = []
    for line in rendered.splitlines():
        if line.startswith("UNKNOWN   "):
            inside = True
            continue
        if inside and line.startswith("─"):
            break
        if inside:
            match = re.match(r"\s+(\d+) sites?(\s|$)", line)
            if match:
                counts.append(int(match.group(1)))
    return counts


def test_grouping_never_loses_a_candidate() -> None:
    sites = [(f"docs/file{index}.md", "surface_reference", "read the docs") for index in range(40)]
    sites.append(("src/client.py", "call_version", "run hops decide"))
    book = ledger_of(sites)
    rendered = render(book)
    assert sum(site_counts(rendered)) == len(book.by_status("UNKNOWN")) == 41


def test_the_nearest_sink_group_leads_and_the_long_tail_collapses() -> None:
    sites = [(f"docs/file{index}.md", "surface_reference", "read the docs") for index in range(40)]
    sites.append(("src/client.py", "call_version", "run hops decide"))
    rendered = render(ledger_of(sites))
    unknown = rendered.split("UNKNOWN   41", 1)[1]
    assert unknown.index("run hops decide") < unknown.index("read the docs")
    assert "[expand]" in unknown
    assert "src/client.py:1" in unknown
    assert "docs/file0.md" not in unknown


def test_expand_prints_every_site() -> None:
    sites = [(f"docs/file{index}.md", "surface_reference", "read the docs") for index in range(40)]
    sites.append(("src/client.py", "call_version", "run hops decide"))
    rendered = render(ledger_of(sites), expand=True)
    assert "[expand]" not in rendered.split("UNKNOWN   41", 1)[1].split("─")[0]
    assert "docs/file0.md" in rendered


def test_every_closing_instruction_still_reaches_the_reader() -> None:
    sites = [
        ("src/a.py", "call_version", "resolve the call site"),
        ("deps/b.json", "dependency_state", "commit a lock file"),
        ("docs/c.md", "surface_reference", "read the docs"),
    ]
    rendered = render(ledger_of(sites))
    for _, _, instruction in sites:
        assert instruction in rendered


def test_the_rendering_is_deterministic() -> None:
    sites = [(f"src/f{index}.py", "call_version", f"instruction {index % 3}") for index in range(9)]
    book = ledger_of(sites)
    assert render(book) == render(book)
