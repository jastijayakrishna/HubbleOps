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
