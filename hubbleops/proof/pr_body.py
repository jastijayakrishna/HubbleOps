from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from hubbleops.core.canonical import content_id
from hubbleops.core.records import as_mapping, as_sequence
from hubbleops.proof.receipt import Receipt
from hubbleops.proof.receipt import render as render_receipt


def render(document: Receipt) -> str:
    record = document.record
    scope = as_mapping(record["proof_scope"])
    blast = as_mapping(record["blast_radius"])
    unknown = {str(item) for item in as_sequence(blast.get("unknown_blast"))}
    dependents = tuple(str(item) for item in as_sequence(blast.get("reachable_modules")))
    tests = _covering_tests(as_mapping(record["frozen_baseline_tests"]), dependents)
    symbols = tuple(str(item) for item in as_sequence(blast.get("changed_definitions")))
    lines = [
        "# HubbleOps Proof Pack",
        "",
        f"**Verdict:** `{record['verdict']}`  ",
        f"**Oracle authority:** `{record['oracle_authority']}`",
        "",
        f"**Candidate SHA:** `{scope.get('repo_sha') or 'not recorded'}`  ",
        f"**ProofScope:** `{content_id(scope)}`",
        "",
        "## Blast-radius detail",
        "",
        "| Changed symbol | Dependents | Covering frozen tests | Status |",
        "|---|---|---|---|",
    ]
    if not symbols:
        lines.append("| _none_ | _none_ | _none_ | COVERED |")
    else:
        dependent_text = _cell(dependents)
        test_text = _cell(tests)
        status = "UNKNOWN" if unknown else "COVERED"
        lines.extend(
            f"| `{_escape(symbol)}` | {dependent_text} | {test_text} | {status} |"
            for symbol in symbols
        )
    lines.extend(
        ("", "## Complete receipt", "", "```text", render_receipt(document).rstrip(), "```")
    )
    return "\n".join(lines) + "\n"


ELIGIBLE_VERDICTS = ("VERIFIED_FOR_SCOPE", "HUMAN_REQUIRED")
PASSING_SECTIONS = (
    "migration_audit",
    "request_shape_differential",
    "response_consumer_check",
    "unknown_conservation",
)
FALSIFIER_RESULTS_THAT_HOLD = ("PASS", "SKIPPED")


def eligible(record: Mapping[str, Any]) -> bool:
    return record.get("verdict") in ELIGIBLE_VERDICTS and verdict_holds(record)


def verdict_holds(record: Mapping[str, Any]) -> bool:
    for name in PASSING_SECTIONS:
        section = as_mapping(record.get(name))
        if section.get("passed") is not True or as_sequence(section.get("unresolved")):
            return False
    frozen = as_mapping(record.get("frozen_baseline_tests"))
    if (
        frozen.get("executed") is not True
        or frozen.get("outcome") != "COMPLETED"
        or int(frozen.get("failed") or 0) != 0
        or int(frozen.get("passed") or 0) == 0
    ):
        return False
    if record.get("oracle_authority") not in ("CATALOG", "LIVE"):
        return False
    if any(
        as_mapping(item).get("code") != "VALID"
        for item in as_sequence(record.get("oracle_results"))
    ):
        return False
    if any(
        as_mapping(item).get("result") not in FALSIFIER_RESULTS_THAT_HOLD
        for item in as_sequence(record.get("falsifiers"))
    ):
        return False
    blast = as_mapping(record.get("blast_radius"))
    if int(as_mapping(blast.get("containment")).get("unexplained") or 0) != 0:
        return False
    if as_sequence(record.get("reasons")):
        return False
    blocked = bool(as_sequence(blast.get("unknown_blast")))
    return (record.get("verdict") == "HUMAN_REQUIRED") == blocked


def _covering_tests(frozen: Mapping[str, Any], dependents: Sequence[str]) -> tuple[str, ...]:
    paths = set(dependents)
    return tuple(
        sorted(
            str(test.get("name"))
            for raw in as_sequence(frozen.get("tests"))
            if (test := as_mapping(raw))
            and test.get("outcome") == "passed"
            and paths.intersection(str(path) for path in as_sequence(test.get("files")))
        )
    )


def _cell(values: Sequence[str]) -> str:
    return "<br>".join(f"`{_escape(value)}`" for value in values) if values else "_none_"


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("`", "\\`")


__all__ = ["eligible", "render"]
