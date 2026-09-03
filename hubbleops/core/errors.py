from __future__ import annotations


class HubbleOpsError(Exception):
    pass


class ToolingMissing(HubbleOpsError):
    def __init__(self, tool: str, detail: str) -> None:
        super().__init__(f"TOOLING_MISSING: {tool}: {detail}")
        self.tool = tool
        self.detail = detail


class ToolingFailed(HubbleOpsError):
    def __init__(self, tool: str, detail: str) -> None:
        super().__init__(f"TOOLING_FAILED: {tool}: {detail}")
        self.tool = tool
        self.detail = detail


class ToolingTimeout(HubbleOpsError):
    def __init__(self, tool: str, seconds: float) -> None:
        super().__init__(f"UNKNOWN: {tool} exceeded its {seconds:g}s budget")
        self.tool = tool
        self.seconds = seconds


class SchemaViolation(HubbleOpsError):
    pass


class SurfaceSpecInvalid(HubbleOpsError):
    pass


class PackNotFound(HubbleOpsError):
    pass


class UnknownClaimType(HubbleOpsError):
    def __init__(self, claim_type: str) -> None:
        super().__init__(
            f"no resolution rule for claim_type {claim_type!r}; "
            "add one to observe/resolver.py rather than defaulting to a status"
        )
        self.claim_type = claim_type


class UnexplainedCandidates(HubbleOpsError):
    def __init__(self, count: int, detail: str) -> None:
        super().__init__(f"UNEXPLAINED_CANDIDATES={count} (law L1): {detail}")
        self.count = count
        self.detail = detail


class UnknownNotConserved(HubbleOpsError):
    def __init__(self, candidate_id: str, was: str, now: str) -> None:
        super().__init__(
            f"UNKNOWN_CONSERVATION (law L3): candidate {candidate_id} is {was} and this write "
            f"closes it to {now} without attaching evidence that was not already there. "
            "An UNKNOWN closes only with new evidence or a recorded human decision "
            "(`hops decide <candidate_id> --value <value> --by <name>`)."
        )
        self.candidate_id = candidate_id
        self.was = was
        self.now = now


class EvidenceNotFound(HubbleOpsError):
    def __init__(self, candidate_id: str, missing: tuple[str, ...]) -> None:
        super().__init__(
            f"EVIDENCE_NOT_FOUND (law L1): candidate {candidate_id} attaches {len(missing)} "
            f"evidence id(s) this run never persisted, starting with {missing[0]}. A candidate "
            "cites evidence that exists; an id that references nothing is not provenance and "
            "cannot carry a status or close an UNKNOWN."
        )
        self.candidate_id = candidate_id
        self.missing = missing


class ProvenanceDropped(HubbleOpsError):
    def __init__(self, candidate_id: str, missing: tuple[str, ...]) -> None:
        super().__init__(
            f"PROVENANCE_DROPPED (law L1): this write of candidate {candidate_id} omits "
            f"{len(missing)} evidence id(s) already attached to it, starting with "
            f"{missing[0]}. evidence_ids is append-only: deduplication attaches, it never "
            "discards provenance."
        )
        self.candidate_id = candidate_id
        self.missing = missing


class EvidenceIdentityMismatch(HubbleOpsError):
    def __init__(self, offered: str, derived: str) -> None:
        super().__init__(
            f"EVIDENCE_IDENTITY_MISMATCH: evidence offered under id {offered} hashes to "
            f"{derived}. An evidence id is the hash of its own content, so a record cannot be "
            "rewritten under an id it no longer matches, and no record may be persisted under an "
            "id that is not its own."
        )
        self.offered = offered
        self.derived = derived


class AiEvidenceAlone(HubbleOpsError):
    def __init__(self, candidate_id: str, was: str, now: str) -> None:
        super().__init__(
            f"AI_EVIDENCE_ALONE (law L10): candidate {candidate_id} is {was} and this write "
            f"closes it to {now} on evidence that is entirely DERIVED_AI_EVIDENCE. "
            "AI-derived evidence cannot alone change a candidate's status or close an UNKNOWN; "
            "attach an observation, or record a human decision "
            "(`hops decide <candidate_id> --value <value> --by <name>`)."
        )
        self.candidate_id = candidate_id
        self.was = was
        self.now = now


class PathNotInClosure(HubbleOpsError):
    def __init__(self, claim_type: str, path: str) -> None:
        super().__init__(
            f"PATH_NOT_IN_CLOSURE: {claim_type} evidence cites {path}, which the source closure "
            "never classified. Resolution stops rather than assuming the path is first-party "
            "source and repairable."
        )
        self.claim_type = claim_type
        self.path = path


class ProofScopeMismatch(HubbleOpsError):
    def __init__(self, kind: str, record_id: str, run_scope: str, record_scope: str) -> None:
        super().__init__(
            f"PROOF_SCOPE_MISMATCH (law L4): {kind} {record_id} carries proof_scope_hash "
            f"{record_scope} but run's scope is {run_scope}. A new scope needs a new run; "
            "evidence is never carried across a scope boundary."
        )
        self.kind = kind
        self.record_id = record_id
        self.run_scope = run_scope
        self.record_scope = record_scope


class StoreSchemaMismatch(HubbleOpsError):
    def __init__(self, path: str, found: int, expected: int) -> None:
        super().__init__(
            f"STORE_SCHEMA_MISMATCH: {path} was written by store schema v{found}, this build "
            f"expects v{expected}. Its rows are not covered by the constraints this build "
            "relies on. Delete the state directory and rescan."
        )
        self.path = path
        self.found = found
        self.expected = expected
