from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

Scope = Literal["line", "file", "subject"]
Verdict = Literal["ACTIONABLE", "CLEAN", "UNSETTLED"]
Kind = Literal["natural", "seeded"]
Asserted = Literal["actionable", "unknown", "clean"]
Outcome = Literal["COMPLETED", "HUMAN_REQUIRED", "SETUP_FAILED", "ENGINE_FAILED"]
Stage = Literal["environment", "discovery", "classification", "repair", "verification"]

OUTCOMES: tuple[Outcome, ...] = ("COMPLETED", "HUMAN_REQUIRED", "SETUP_FAILED", "ENGINE_FAILED")
STAGES: tuple[Stage, ...] = (
    "environment",
    "discovery",
    "classification",
    "repair",
    "verification",
)


class CorpusError(Exception):
    pass


def _obj(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CorpusError(f"{where}: expected an object, found {type(value).__name__}")
    return {str(key): item for key, item in cast("dict[object, object]", value).items()}


def _arr(value: object, where: str) -> list[object]:
    if not isinstance(value, list):
        raise CorpusError(f"{where}: expected an array, found {type(value).__name__}")
    return cast("list[object]", value)


def _text(source: dict[str, object], key: str, where: str, default: str | None = None) -> str:
    raw = source.get(key)
    if raw is None:
        if default is None:
            raise CorpusError(f"{where}: missing required field {key!r}")
        return default
    if not isinstance(raw, str):
        raise CorpusError(f"{where}.{key}: expected a string, found {type(raw).__name__}")
    return raw


def _whole(source: dict[str, object], key: str, where: str, default: int | None = None) -> int:
    raw = source.get(key)
    if raw is None:
        if default is None:
            raise CorpusError(f"{where}: missing required field {key!r}")
        return default
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise CorpusError(f"{where}.{key}: expected an integer, found {type(raw).__name__}")
    return raw


def _real(source: dict[str, object], key: str, where: str, default: float | None = None) -> float:
    raw = source.get(key)
    if raw is None:
        if default is None:
            raise CorpusError(f"{where}: missing required field {key!r}")
        return default
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise CorpusError(f"{where}.{key}: expected a number, found {type(raw).__name__}")
    return float(raw)


def _flag(source: dict[str, object], key: str, where: str, default: bool | None = None) -> bool:
    raw = source.get(key)
    if raw is None:
        if default is None:
            raise CorpusError(f"{where}: missing required field {key!r}")
        return default
    if not isinstance(raw, bool):
        raise CorpusError(f"{where}.{key}: expected a boolean, found {type(raw).__name__}")
    return raw


def _texts(source: dict[str, object], key: str, where: str) -> tuple[str, ...]:
    raw = source.get(key)
    if raw is None:
        return ()
    items = _arr(raw, f"{where}.{key}")
    out: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, str):
            raise CorpusError(f"{where}.{key}[{index}]: expected a string")
        out.append(item)
    return tuple(out)


def read_json(path: Path) -> dict[str, object]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise CorpusError(f"{path}: cannot be read ({error})") from error
    try:
        loaded: object = json.loads(raw)
    except json.JSONDecodeError as error:
        raise CorpusError(f"{path}: not valid JSON ({error})") from error
    return _obj(loaded, str(path))


def normalise_path(raw: str) -> str:
    text = raw.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


@dataclass(frozen=True)
class Definitions:
    version: int
    engine_scope: str
    source_versions: tuple[str, ...]
    target_version: str
    languages: tuple[str, ...]
    budget_minutes: int
    engineer_edits_allowed: int
    compatibility: dict[str, tuple[str, ...]]
    window_lines: int
    subject_mechanisms: tuple[str, ...]
    file_mechanisms: tuple[str, ...]
    universal_mechanisms: tuple[str, ...]
    claim_type_to_mechanism: dict[str, str]
    matching_version: int


@dataclass(frozen=True)
class Family:
    family_id: str
    repo: str
    sha: str
    clone_url: str
    members: tuple[str, ...]
    language: str
    source_api_version: str
    sdk_kind: str
    shape: str
    documented_install: tuple[str, ...]
    test_command: str
    independence: str
    eligible: bool


@dataclass(frozen=True)
class Label:
    label_id: str
    path: str
    line: int
    scope: Scope
    mechanism: str
    verdict: Verdict
    kind: Kind
    sources: tuple[str, ...]
    subject: str
    evidence: str


@dataclass(frozen=True)
class FileCoverage:
    path: str
    sources: tuple[str, ...]
    adjudicated: bool
    reason_if_not: str


@dataclass(frozen=True)
class LabelSet:
    family_id: str
    repo: str
    sha: str
    signed: bool
    signer: str
    eligible_files: int
    coverage: tuple[FileCoverage, ...]
    labels: tuple[Label, ...]

    def adjudicated_paths(self) -> frozenset[str]:
        return frozenset(entry.path for entry in self.coverage if entry.adjudicated)


@dataclass(frozen=True)
class Finding:
    finding_id: str
    path: str
    start_line: int
    end_line: int
    mechanism: str
    asserted: Asserted
    subject: str
    reason: str


@dataclass(frozen=True)
class StageRecord:
    name: str
    exit_code: int
    seconds: float
    detail: str


@dataclass(frozen=True)
class FindingSet:
    arm: str
    family_id: str
    sha: str
    outcome: Outcome
    verdict: str
    runtime_seconds: float
    human_minutes: float
    cost_usd: float
    engineer_edits: int
    findings: tuple[Finding, ...]
    repaired: tuple[str, ...]
    stages: tuple[StageRecord, ...]
    notes: str
    degraded: str = ""
    reasons: tuple[str, ...] = field(default_factory=tuple)


def load_definitions(definitions_path: Path, matching_path: Path) -> Definitions:
    head = read_json(definitions_path)
    where = str(definitions_path)
    handled = _obj(head.get("handled"), f"{where}.handled")
    match = read_json(matching_path)
    match_where = str(matching_path)
    predicate = _obj(match.get("match_predicate"), f"{match_where}.match_predicate")
    rules = _arr(predicate.get("rules"), f"{match_where}.match_predicate.rules")
    window = 0
    file_mechs: tuple[str, ...] = ()
    subject_mechs: tuple[str, ...] = ()
    for index, entry in enumerate(rules):
        rule = _obj(entry, f"{match_where}.match_predicate.rules[{index}]")
        rule_id = _text(rule, "id", f"{match_where}.rules[{index}]")
        if rule_id == "M2":
            window = _whole(rule, "window_lines", f"{match_where}.rules[{index}]")
        if rule_id == "M3":
            file_mechs = _texts(rule, "applies_to", f"{match_where}.rules[{index}]")
        if rule_id == "M4":
            subject_mechs = _texts(rule, "applies_to", f"{match_where}.rules[{index}]")
    if window <= 0:
        raise CorpusError(f"{match_where}: rule M2 must carry a positive window_lines")
    table_holder = _obj(
        match.get("mechanism_compatibility"), f"{match_where}.mechanism_compatibility"
    )
    table_raw = _obj(table_holder.get("table"), f"{match_where}.mechanism_compatibility.table")
    compatibility: dict[str, tuple[str, ...]] = {}
    for key, value in table_raw.items():
        items = _arr(value, f"{match_where}.table.{key}")
        names: list[str] = []
        for index, item in enumerate(items):
            if not isinstance(item, str):
                raise CorpusError(f"{match_where}.table.{key}[{index}]: expected a string")
            names.append(item)
        compatibility[key] = tuple(names)
    universal = _texts(
        table_holder, "universal_finding_mechanisms", f"{match_where}.mechanism_compatibility"
    )
    extraction = _obj(match.get("arm_finding_extraction"), f"{match_where}.arm_finding_extraction")
    arm_a = _obj(extraction.get("A"), f"{match_where}.arm_finding_extraction.A")
    claim_map_raw = _obj(
        arm_a.get("claim_type_to_mechanism"),
        f"{match_where}.arm_finding_extraction.A.claim_type_to_mechanism",
    )
    claim_map: dict[str, str] = {}
    for key, value in claim_map_raw.items():
        if not isinstance(value, str):
            raise CorpusError(f"{match_where}.claim_type_to_mechanism.{key}: expected a string")
        claim_map[key] = value
    return Definitions(
        version=_whole(head, "version", where),
        engine_scope=_text(head, "engine_scope", where),
        source_versions=_texts(handled, "source_versions", f"{where}.handled"),
        target_version=_text(handled, "target_version", f"{where}.handled"),
        languages=_texts(handled, "languages", f"{where}.handled"),
        budget_minutes=_whole(handled, "budget_minutes", f"{where}.handled"),
        engineer_edits_allowed=_whole(handled, "engineer_edits_allowed", f"{where}.handled"),
        compatibility=compatibility,
        window_lines=window,
        subject_mechanisms=subject_mechs,
        file_mechanisms=file_mechs,
        universal_mechanisms=universal,
        claim_type_to_mechanism=claim_map,
        matching_version=_whole(match, "version", match_where),
    )


def load_families(path: Path) -> tuple[Family, ...]:
    head = read_json(path)
    where = str(path)
    entries = _arr(head.get("families"), f"{where}.families")
    out: list[Family] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        body = _obj(entry, f"{where}.families[{index}]")
        spot = f"{where}.families[{index}]"
        rep = _obj(body.get("representative"), f"{spot}.representative")
        family_id = _text(body, "family_id", spot)
        if family_id in seen:
            raise CorpusError(f"{spot}: duplicate family_id {family_id!r}")
        seen.add(family_id)
        sha = _text(rep, "sha", f"{spot}.representative")
        if len(sha) != 40:
            raise CorpusError(f"{spot}.representative.sha: a family must pin a full 40-char SHA")
        out.append(
            Family(
                family_id=family_id,
                repo=_text(rep, "repo", f"{spot}.representative"),
                sha=sha,
                clone_url=_text(rep, "clone_url", f"{spot}.representative"),
                members=_texts(body, "members", spot),
                language=_text(body, "language", spot),
                source_api_version=_text(body, "source_api_version", spot),
                sdk_kind=_text(body, "sdk_kind", spot, ""),
                shape=_text(body, "shape", spot, ""),
                documented_install=_texts(body, "documented_install", spot),
                test_command=_text(body, "test_command", spot, ""),
                independence=_text(body, "independence", spot, ""),
                eligible=_flag(body, "eligible", spot, True),
            )
        )
    return tuple(sorted(out, key=lambda item: item.family_id))


def _scope(raw: str, where: str) -> Scope:
    if raw not in ("line", "file", "subject"):
        raise CorpusError(f"{where}.scope: {raw!r} is not line, file or subject")
    return raw


def _verdict(raw: str, where: str) -> Verdict:
    if raw not in ("ACTIONABLE", "CLEAN", "UNSETTLED"):
        raise CorpusError(f"{where}.verdict: {raw!r} is not ACTIONABLE, CLEAN or UNSETTLED")
    return raw


def _kind(raw: str, where: str) -> Kind:
    if raw not in ("natural", "seeded"):
        raise CorpusError(f"{where}.kind: {raw!r} is not natural or seeded")
    return raw


def _asserted(raw: str, where: str) -> Asserted:
    if raw not in ("actionable", "unknown", "clean"):
        raise CorpusError(f"{where}.asserted: {raw!r} is not actionable, unknown or clean")
    return raw


def _outcome(raw: str, where: str) -> Outcome:
    if raw not in OUTCOMES:
        raise CorpusError(f"{where}.outcome: {raw!r} is not one of {OUTCOMES}")
    return raw


def load_labels(path: Path) -> LabelSet:
    head = read_json(path)
    where = str(path)
    coverage_entries = _arr(head.get("adjudicated_files"), f"{where}.adjudicated_files")
    coverage: list[FileCoverage] = []
    for index, entry in enumerate(coverage_entries):
        body = _obj(entry, f"{where}.adjudicated_files[{index}]")
        spot = f"{where}.adjudicated_files[{index}]"
        coverage.append(
            FileCoverage(
                path=normalise_path(_text(body, "path", spot)),
                sources=_texts(body, "sources", spot),
                adjudicated=_flag(body, "adjudicated", spot),
                reason_if_not=_text(body, "reason_if_not", spot, ""),
            )
        )
    label_entries = _arr(head.get("labels"), f"{where}.labels")
    labels: list[Label] = []
    seen: set[str] = set()
    for index, entry in enumerate(label_entries):
        body = _obj(entry, f"{where}.labels[{index}]")
        spot = f"{where}.labels[{index}]"
        label_id = _text(body, "id", spot)
        if label_id in seen:
            raise CorpusError(f"{spot}: duplicate label id {label_id!r}")
        seen.add(label_id)
        scope = _scope(_text(body, "scope", spot), spot)
        line = _whole(body, "line", spot, 0)
        if scope == "line" and line <= 0:
            raise CorpusError(f"{spot}: a line-scoped label needs a positive line")
        labels.append(
            Label(
                label_id=label_id,
                path=normalise_path(_text(body, "path", spot)),
                line=line,
                scope=scope,
                mechanism=_text(body, "mechanism", spot),
                verdict=_verdict(_text(body, "verdict", spot), spot),
                kind=_kind(_text(body, "kind", spot), spot),
                sources=_texts(body, "sources", spot),
                subject=_text(body, "subject", spot, ""),
                evidence=_text(body, "evidence", spot, ""),
            )
        )
    return LabelSet(
        family_id=_text(head, "family_id", where),
        repo=_text(head, "repo", where),
        sha=_text(head, "sha", where),
        signed=_flag(head, "signed", where, False),
        signer=_text(head, "signer", where, ""),
        eligible_files=_whole(head, "eligible_files", where, len(coverage)),
        coverage=tuple(sorted(coverage, key=lambda item: item.path)),
        labels=tuple(
            sorted(labels, key=lambda item: (item.path, item.line, item.mechanism, item.label_id))
        ),
    )


def load_findings(path: Path) -> FindingSet:
    head = read_json(path)
    where = str(path)
    entries = _arr(head.get("findings"), f"{where}.findings")
    findings: list[Finding] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        body = _obj(entry, f"{where}.findings[{index}]")
        spot = f"{where}.findings[{index}]"
        finding_id = _text(body, "id", spot)
        if finding_id in seen:
            raise CorpusError(f"{spot}: duplicate finding id {finding_id!r}")
        seen.add(finding_id)
        start = _whole(body, "start_line", spot, 0)
        findings.append(
            Finding(
                finding_id=finding_id,
                path=normalise_path(_text(body, "path", spot)),
                start_line=start,
                end_line=_whole(body, "end_line", spot, start),
                mechanism=_text(body, "mechanism", spot),
                asserted=_asserted(_text(body, "asserted", spot), spot),
                subject=_text(body, "subject", spot, ""),
                reason=_text(body, "reason", spot, ""),
            )
        )
    stage_entries = _arr(head.get("stages"), f"{where}.stages") if "stages" in head else []
    stages: list[StageRecord] = []
    for index, entry in enumerate(stage_entries):
        body = _obj(entry, f"{where}.stages[{index}]")
        spot = f"{where}.stages[{index}]"
        stages.append(
            StageRecord(
                name=_text(body, "name", spot),
                exit_code=_whole(body, "exit_code", spot, 0),
                seconds=_real(body, "seconds", spot, 0.0),
                detail=_text(body, "detail", spot, ""),
            )
        )
    return FindingSet(
        arm=_text(head, "arm", where),
        family_id=_text(head, "family_id", where),
        sha=_text(head, "sha", where),
        outcome=_outcome(_text(head, "outcome", where), where),
        verdict=_text(head, "verdict", where, ""),
        runtime_seconds=_real(head, "runtime_seconds", where, 0.0),
        human_minutes=_real(head, "human_minutes", where, 0.0),
        cost_usd=_real(head, "cost_usd", where, 0.0),
        engineer_edits=_whole(head, "engineer_edits", where, 0),
        findings=tuple(
            sorted(
                findings,
                key=lambda item: (
                    item.path,
                    item.start_line,
                    item.end_line,
                    item.mechanism,
                    item.finding_id,
                ),
            )
        ),
        repaired=_texts(head, "repaired", where),
        stages=tuple(stages),
        notes=_text(head, "notes", where, ""),
        degraded=_text(head, "degraded", where, ""),
        reasons=_texts(head, "reasons", where),
    )
