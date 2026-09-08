from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from hubbleops.core.records import as_line
from hubbleops.core.verification import (
    CheckReport,
    Hunk,
    HunkMapping,
    ObligationView,
    TestRun,
)
from hubbleops.graph.imports import Definition, ImportGraph
from hubbleops.verify.gitdiff import Delta

MAX_REACH_HOPS = 5
AUTHORITY_PREFIXES = ("hubbleops/verify/", "hubbleops/proof/", "hubbleops/sandbox/verifier_image")
STATE_PREFIX = ".hubbleops/"
LOCKFILE_NAMES = frozenset(
    {
        "uv.lock",
        "poetry.lock",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "composer.lock",
        "Gemfile.lock",
        "go.sum",
        "Cargo.lock",
    }
)
IMPORT_PREFIXES = ("import ", "from ", "use ", "require(", "const ", "#include")


@dataclass(frozen=True, slots=True)
class Containment:
    mappings: tuple[HunkMapping, ...]

    def unexplained(self) -> tuple[HunkMapping, ...]:
        return tuple(item for item in self.mappings if item.disposition == "UNEXPLAINED")

    def collateral(self) -> tuple[HunkMapping, ...]:
        return tuple(item for item in self.mappings if item.disposition == "COLLATERAL")

    def discharged(self) -> tuple[HunkMapping, ...]:
        return tuple(item for item in self.mappings if item.disposition == "OBLIGATION")

    def report(self) -> CheckReport:
        unexplained = self.unexplained()
        return CheckReport(
            name="diff_containment",
            passed=not unexplained,
            reasons=tuple(
                f"unexplained hunk {item.hunk.identity()}: {item.reason}" for item in unexplained
            ),
            detail={
                "hunks": len(self.mappings),
                "obligations": len(self.discharged()),
                "collateral": len(self.collateral()),
                "unexplained": len(unexplained),
                "mappings": [item.to_mapping() for item in self.mappings],
            },
        )


@dataclass(frozen=True, slots=True)
class BlastRadius:
    changed_definitions: tuple[str, ...]
    reachable_modules: tuple[str, ...]
    covered_modules: tuple[str, ...]
    unknown_blast: tuple[str, ...]
    truncated: tuple[str, ...]
    uncoverable: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "changed_definitions": list(self.changed_definitions),
            "reachable_modules": list(self.reachable_modules),
            "covered_modules": list(self.covered_modules),
            "unknown_blast": list(self.unknown_blast),
            "reach_truncated": list(self.truncated),
            "coverage_unsupported": list(self.uncoverable),
        }


def contain(
    delta: Delta,
    obligations: Sequence[ObligationView],
    evidence: Sequence[Mapping[str, Any]],
    graph: ImportGraph,
) -> Containment:
    by_id = {item["id"]: item for item in evidence}
    sites: dict[str, list[tuple[str, int | None]]] = {}
    for obligation in obligations:
        located: list[tuple[str, int | None]] = [
            (str(by_id[eid]["path"]), as_line(by_id[eid].get("line_start")))
            for eid in obligation.evidence_ids
            if eid in by_id
        ]
        located.sort(key=lambda item: (item[1] is None, item[0], item[1] or 0))
        sites[obligation.id] = located

    mapped_paths = {path for locations in sites.values() for path, _ in locations}
    return Containment(
        mappings=tuple(_map_hunk(hunk, sites, mapped_paths, graph) for hunk in delta.hunks)
    )


def _map_hunk(
    hunk: Hunk,
    sites: dict[str, list[tuple[str, int | None]]],
    mapped_paths: set[str],
    graph: ImportGraph,
) -> HunkMapping:
    if _is_authority(hunk.path):
        return HunkMapping(
            hunk=hunk,
            disposition="UNEXPLAINED",
            obligation_id=None,
            reason="a candidate never edits the authority that judges it",
        )
    for obligation_id in sorted(sites):
        for path, line in sites[obligation_id]:
            if path != hunk.path:
                continue
            if line is None or hunk.spans(line) or _covers_definition(graph, hunk, line):
                return HunkMapping(
                    hunk=hunk,
                    disposition="OBLIGATION",
                    obligation_id=obligation_id,
                    reason=f"hunk covers the obligation site {path}:{line if line else '*'}",
                )
    collateral = _collateral_reason(hunk, mapped_paths)
    if collateral is not None:
        return HunkMapping(
            hunk=hunk, disposition="COLLATERAL", obligation_id=None, reason=collateral
        )
    return HunkMapping(
        hunk=hunk,
        disposition="UNEXPLAINED",
        obligation_id=None,
        reason="no obligation covers this hunk and no closed collateral rule explains it",
    )


def _covers_definition(graph: ImportGraph, hunk: Hunk, line: int) -> bool:
    for definition in graph.definitions_in(hunk.path):
        if definition.range.start_line <= line <= definition.range.end_line:
            span = range(definition.range.start_line, definition.range.end_line + 1)
            if any(hunk.spans(value) for value in span):
                return True
    return False


def _collateral_reason(hunk: Hunk, mapped_paths: set[str]) -> str | None:
    name = hunk.path.rsplit("/", 1)[-1]
    if name in LOCKFILE_NAMES:
        return f"{name} is regenerated by a dependency change"
    if hunk.path not in mapped_paths:
        return None
    if _import_only(hunk):
        return "import-only edit inside a file an obligation already maps"
    if _whitespace_only(hunk):
        return "formatting-only edit inside a file an obligation already maps"
    return None


def _import_only(hunk: Hunk) -> bool:
    lines = [line.strip() for line in (*hunk.added, *hunk.removed) if line.strip()]
    return bool(lines) and all(
        any(line.startswith(prefix) for prefix in IMPORT_PREFIXES) for line in lines
    )


def _whitespace_only(hunk: Hunk) -> bool:
    return _normalize(hunk.added) == _normalize(hunk.removed) and bool(hunk.added or hunk.removed)


def _normalize(lines: Sequence[str]) -> tuple[str, ...]:
    return tuple(item for item in ("".join(line.split()) for line in lines) if item)


def _is_authority(path: str) -> bool:
    return path.startswith(AUTHORITY_PREFIXES) or path.startswith(STATE_PREFIX)


def changed_definitions(delta: Delta, graph: ImportGraph) -> tuple[Definition, ...]:
    touched: list[Definition] = []
    for path in sorted({hunk.path for hunk in delta.hunks}):
        hunks = delta.hunks_in(path)
        for definition in graph.definitions_in(path):
            span = range(definition.range.start_line, definition.range.end_line + 1)
            if any(hunk.spans(line) for hunk in hunks for line in span):
                touched.append(definition)
    return tuple(sorted(set(touched), key=lambda item: item.id))


def reach(graph: ImportGraph, seeds: Sequence[Definition]) -> tuple[frozenset[str], frozenset[str]]:
    modules = {definition.path for definition in seeds}
    frontier = set(modules)
    incoming = _incoming_edges(graph)
    for _ in range(MAX_REACH_HOPS):
        following = {
            source for module in sorted(frontier) for source in incoming.get(module, frozenset())
        }
        new = following - modules
        if not new:
            return frozenset(modules), frozenset()
        modules |= new
        frontier = new
    remaining = {
        source for module in sorted(frontier) for source in incoming.get(module, frozenset())
    } - modules
    return frozenset(modules), frozenset(remaining)


def _incoming_edges(graph: ImportGraph) -> dict[str, frozenset[str]]:
    edges: dict[str, set[str]] = {}
    for binding in graph.imports:
        if binding.target_path is None:
            continue
        edges.setdefault(binding.target_path, set()).add(binding.path)
    for call in graph.calls:
        for definition in graph.definitions_named(call.callee):
            if call.path != definition.path:
                edges.setdefault(definition.path, set()).add(call.path)
    return {key: frozenset(value) for key, value in edges.items()}


def blast(
    delta: Delta,
    graph: ImportGraph,
    frozen: TestRun,
    uncoverable: Sequence[str] = (),
) -> BlastRadius:
    seeds = changed_definitions(delta, graph)
    reachable, truncated = reach(graph, seeds)
    covered: frozenset[str] = frozen.covered_files() if frozen.all_passed() else frozenset()
    unsupported = frozenset(uncoverable)
    unknown = (reachable - covered) | (reachable & unsupported) | truncated
    return BlastRadius(
        changed_definitions=tuple(sorted(definition.id for definition in seeds)),
        reachable_modules=tuple(sorted(reachable)),
        covered_modules=tuple(sorted(reachable & covered)),
        unknown_blast=tuple(sorted(unknown)),
        truncated=tuple(sorted(truncated)),
        uncoverable=tuple(sorted(reachable & unsupported)),
    )


def frozen_report(frozen: TestRun) -> CheckReport:
    if not frozen.executed:
        return CheckReport(
            name="frozen_baseline_tests",
            passed=False,
            reasons=(f"frozen baseline tests did not run: {frozen.reason}",),
            unresolved=(f"frozen_baseline_tests: {frozen.outcome}",),
            detail=frozen.to_mapping(),
        )
    if frozen.outcome != "COMPLETED":
        return CheckReport(
            name="frozen_baseline_tests",
            passed=False,
            reasons=(f"frozen baseline test run ended {frozen.outcome}: {frozen.reason}",),
            unresolved=(f"frozen_baseline_tests: {frozen.outcome}",),
            detail=frozen.to_mapping(),
        )
    return CheckReport(
        name="frozen_baseline_tests",
        passed=frozen.failed == 0,
        reasons=()
        if frozen.failed == 0
        else (f"{frozen.failed} frozen baseline test(s) fail against the candidate",),
        detail=frozen.to_mapping(),
    )


__all__ = [
    "MAX_REACH_HOPS",
    "BlastRadius",
    "Containment",
    "blast",
    "changed_definitions",
    "contain",
    "frozen_report",
    "reach",
]
