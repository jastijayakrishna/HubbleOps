from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from tests.corpus.mutate import SKIP_DIRS
from tests.corpus.spec import normalise_path, read_json

CONJUNCTS = (
    "migration_audit",
    "request_shape_differential",
    "response_consumer_check",
    "unknown_conservation",
)


@dataclass(frozen=True)
class Corruption:
    challenge_id: str
    name: str
    languages: tuple[str, ...]
    suffixes: tuple[str, ...]
    find: str
    replace: str
    intent: str
    should_be_caught_by: str


CHALLENGES: tuple[Corruption, ...] = (
    Corruption(
        "X01",
        "case_flipped_php_namespace",
        ("php",),
        (".php",),
        r"Google\\Ads\\GoogleAds\\V25",
        r"Google\\Ads\\GoogleAds\\v25",
        "spell the migrated namespace segment in the wrong case, which PHP resolves "
        "case-sensitively for class names and which a case-blind check would wave through",
        "migration_audit or a falsifier",
    ),
    Corruption(
        "X02",
        "env_default_reverted",
        ("php", "python", "typescript", "javascript"),
        (".php", ".py", ".ts", ".js"),
        r"""(['"]?GOOGLE_ADS_API_VERSION['"]?\s*[,=:]\s*['"])v25(['"])""",
        r"\1v23\2",
        "revert only the fallback default of an environment lookup, leaving the migrated "
        "constant beside it untouched",
        "migration_audit absence check",
    ),
    Corruption(
        "X03",
        "version_assembled_from_parts",
        ("python", "typescript", "javascript"),
        (".py", ".ts", ".js"),
        r"""(["'])v25(["'])""",
        r"\g<1>v2\g<1> + \g<2>4\g<2>",
        "smuggle a pre-target version past a literal scanner by splitting it across a "
        "concatenation, so no single token reads v24",
        "nothing textual; this is the escape case worth knowing about",
    ),
    Corruption(
        "X04",
        "dead_branch_carries_old_version",
        ("python",),
        (".py",),
        r"^(import |from )",
        r"HOPS_CHALLENGE_DEAD = 'google.ads.googleads.v22.services'\n\1",
        "hide a pre-target namespace inside code that never executes, which a runtime-only "
        "check would never reach",
        "the static scan, as an AFFECTED candidate and an obligation",
    ),
    Corruption(
        "X05",
        "comment_migrated_code_reverted",
        ("python", "typescript", "javascript"),
        (".py", ".ts", ".js"),
        r"""(googleads\.googleapis\.com/)v25""",
        r"\1v21",
        "revert a REST endpoint path while the surrounding prose still claims v25",
        "migration_audit absence check or the endpoint falsifier",
    ),
    Corruption(
        "X06",
        "zero_width_inside_version_token",
        ("python", "typescript", "javascript", "php"),
        (".py", ".ts", ".js", ".php"),
        r"""(["'])v25(["'])""",
        "\\1v\u200b25\\2",
        "break the version token with a zero-width space so it neither reads as v25 to a "
        "scanner nor changes how a human sees the line",
        "a fail-closed parse or an UNKNOWN, never a silent pass",
    ),
    Corruption(
        "X07",
        "manifest_pin_reverted",
        ("php", "python", "typescript", "javascript"),
        (".json", ".toml", ".txt"),
        r"""(google-ads[^\n]*?)(\^|>=|==)\s*3[0-9]+""",
        r"\g<1>\g<2>25",
        "revert the dependency pin below the target's client minimum while every call site "
        "stays migrated",
        "the SDK floor check and a dependency obligation",
    ),
)


@dataclass(frozen=True)
class Applied:
    challenge_id: str
    name: str
    applied: bool
    path: str
    line: int
    before: str
    after: str
    reason: str


def _candidates(repo: Path, suffixes: tuple[str, ...]) -> list[Path]:
    out: list[Path] = []
    for path in sorted(repo.rglob("*")):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        parts = path.relative_to(repo).parts
        if any(part in SKIP_DIRS for part in parts[:-1]):
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
        except OSError:
            continue
        out.append(path)
    return out


def apply_one(repo: Path, corruption: Corruption, language: str) -> Applied:
    if language not in corruption.languages:
        return Applied(
            corruption.challenge_id,
            corruption.name,
            False,
            "",
            0,
            "",
            "",
            f"not applicable to {language}",
        )
    pattern = re.compile(corruption.find, re.MULTILINE)
    for path in _candidates(repo, corruption.suffixes):
        try:
            body = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            continue
        found = pattern.search(body)
        if found is None:
            continue
        updated = body[: found.start()] + pattern.sub(
            corruption.replace, body[found.start() :], count=1
        )
        if updated == body:
            continue
        line = body.count("\n", 0, found.start()) + 1
        before = body.splitlines()[line - 1] if line - 1 < len(body.splitlines()) else ""
        path.write_text(updated, encoding="utf-8")
        after_lines = updated.splitlines()
        after = after_lines[line - 1] if line - 1 < len(after_lines) else ""
        return Applied(
            corruption.challenge_id,
            corruption.name,
            True,
            normalise_path(str(path.relative_to(repo)).replace("\\", "/")),
            line,
            before.strip()[:180],
            after.strip()[:180],
            "",
        )
    return Applied(
        corruption.challenge_id,
        corruption.name,
        False,
        "",
        0,
        "",
        "",
        "no site in this family carries the shape this corruption needs",
    )


def _named(body: dict[str, object], key: str) -> tuple[bool | None, list[str]]:
    raw = body.get(key)
    if not isinstance(raw, dict):
        return (None, [])
    record = {str(k): v for k, v in cast("dict[object, object]", raw).items()}
    passed = record.get("passed")
    reasons_raw = record.get("reasons")
    reasons = (
        [str(item) for item in cast("list[object]", reasons_raw)]
        if isinstance(reasons_raw, list)
        else []
    )
    return (passed if isinstance(passed, bool) else None, reasons)


def signals(receipt: Path) -> dict[str, object]:
    if not receipt.is_file():
        return {"verdict": "", "reasons": [], "conjuncts": {}, "falsifiers": {}}
    body = read_json(receipt)
    conjuncts: dict[str, object] = {}
    for key in CONJUNCTS:
        passed, _ = _named(body, key)
        if passed is not None:
            conjuncts[key] = passed
    audit = body.get("migration_audit")
    if isinstance(audit, dict):
        record = {str(k): v for k, v in cast("dict[object, object]", audit).items()}
        value = record.get("passed")
        if isinstance(value, bool):
            conjuncts["migration_audit"] = value
    frozen = body.get("frozen_baseline_tests")
    if isinstance(frozen, dict):
        record = {str(k): v for k, v in cast("dict[object, object]", frozen).items()}
        conjuncts["frozen_baseline_tests"] = str(record.get("outcome", ""))
    falsifiers: dict[str, str] = {}
    raw = body.get("falsifiers")
    if isinstance(raw, list):
        for item in cast("list[object]", raw):
            if not isinstance(item, dict):
                continue
            record = {str(k): v for k, v in cast("dict[object, object]", item).items()}
            falsifiers[str(record.get("name", ""))] = str(record.get("result", ""))
    reasons_raw = body.get("reasons")
    reasons = (
        [str(item) for item in cast("list[object]", reasons_raw)]
        if isinstance(reasons_raw, list)
        else []
    )
    return {
        "verdict": str(body.get("verdict", "")),
        "reasons": reasons,
        "conjuncts": conjuncts,
        "falsifiers": falsifiers,
        "oracle_authority": str(body.get("oracle_authority", "")),
    }


def catching_stage(baseline: dict[str, object], corrupted: dict[str, object]) -> dict[str, object]:
    base_conj = cast("dict[str, object]", baseline.get("conjuncts", {}))
    bad_conj = cast("dict[str, object]", corrupted.get("conjuncts", {}))
    base_fals = cast("dict[str, str]", baseline.get("falsifiers", {}))
    bad_fals = cast("dict[str, str]", corrupted.get("falsifiers", {}))
    base_reasons = set(cast("list[str]", baseline.get("reasons", [])))
    bad_reasons = cast("list[str]", corrupted.get("reasons", []))

    newly: list[str] = []
    for key, value in bad_conj.items():
        if base_conj.get(key) is True and value is False:
            newly.append(f"conjunct:{key}")
    for name, result in bad_fals.items():
        if base_fals.get(name) == "PASS" and result != "PASS":
            newly.append(f"falsifier:{name}")
    new_reasons = [reason for reason in bad_reasons if reason not in base_reasons]
    if not newly and new_reasons:
        newly.append("reason:migration_audit")
    verdict = str(corrupted.get("verdict", ""))
    escaped = verdict == "VERIFIED_FOR_SCOPE" or (not newly and not new_reasons)
    return {
        "verdict": verdict,
        "caught": not escaped,
        "catching_stage": newly or ([] if escaped else ["reason-only"]),
        "new_reasons": new_reasons[:6],
    }


def write_report(path: Path, body: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
