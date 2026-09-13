from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from hubbleops.core.candidate import DECISION_REASON_PREFIX
from hubbleops.core.records import (
    as_line,
    as_mapping,
    as_sequence,
    as_text,
    is_mapping,
    parse_json,
)
from hubbleops.core.verification import (
    ORACLE_AUTHORITIES,
    CheckReport,
    OracleCheck,
    OracleOutcome,
    OracleView,
    request_identity,
)
from hubbleops.observe import Ledger

REQUEST_CLAIM_TYPE = "request_text"


@dataclass(frozen=True, slots=True)
class OracleReview:
    checks: tuple[OracleCheck, ...]
    available: bool
    unreachable: tuple[str, ...] = ()
    silent: bool = False
    decided: tuple[str, ...] = ()

    def accepted(self) -> tuple[OracleCheck, ...]:
        return tuple(item for item in self.checks if item.code == "VALID")

    def rejected(self) -> tuple[OracleCheck, ...]:
        return tuple(item for item in self.checks if item.code == "INVALID")

    def undecided(self) -> tuple[OracleCheck, ...]:
        return tuple(item for item in self.checks if item.code == "UNKNOWN_PROVIDER_CONTRACT")

    def authority(self) -> str:
        if not self.available:
            return "ORACLE_UNAVAILABLE"
        named = {item.authority for item in self.accepted() if item.authority is not None}
        if not named:
            return "ORACLE_UNAVAILABLE"
        return "CATALOG" if "CATALOG" in named else "LIVE"

    def report(self) -> CheckReport:
        rejected = self.rejected()
        return CheckReport(
            name="contract_oracle",
            passed=not rejected,
            reasons=tuple(
                f"provider rejected request {item.request_hash[:12]} "
                f"({item.service}.{item.method}): {item.reason}"
                for item in rejected
            ),
            unresolved=tuple(
                sorted(
                    [
                        f"oracle undecided for request {item.request_hash[:12]}: {item.reason}"
                        for item in self.undecided()
                    ]
                    + [
                        f"a request at {site} never reached the oracle, so its acceptance "
                        "is unproven rather than proven"
                        for site in self.unreachable
                    ]
                    + (
                        [
                            "the Change Pack changes provider subjects and not one request "
                            "reached the oracle, so oracle_all_accepted proves nothing"
                        ]
                        if self.silent
                        else []
                    )
                )
            ),
            detail={
                "available": self.available,
                "total": len(self.checks),
                "accepted": len(self.accepted()),
                "rejected": len(rejected),
                "undecided": len(self.undecided()),
                "unreachable": list(self.unreachable),
                "silent": self.silent,
                "decided": list(self.decided),
            },
        )


@dataclass(frozen=True, slots=True)
class RequestSites:
    requests: tuple[tuple[str, Mapping[str, Any]], ...]
    unreachable: tuple[str, ...]
    decided: tuple[str, ...] = ()


def requests_from(
    ledger: Ledger, captured: Sequence[Mapping[str, Any]]
) -> tuple[tuple[tuple[str, Mapping[str, Any]], ...], tuple[str, ...]]:
    sites = request_sites(ledger, captured)
    return sites.requests, sites.unreachable


def request_sites(ledger: Ledger, captured: Sequence[Mapping[str, Any]]) -> RequestSites:
    found: dict[str, Mapping[str, Any]] = {}
    unreachable: set[str] = set()
    decided: set[str] = set()
    resolved_spans: list[tuple[str, tuple[int, int]]] = []
    undecided: list[tuple[str, tuple[int, int]]] = []
    evidence = ledger.evidence_by_id()
    attached: dict[str, bool] = {}
    for candidate in ledger.candidates:
        by_decision = str(candidate.get("reason") or "").startswith(DECISION_REASON_PREFIX)
        for eid in candidate["evidence_ids"]:
            attached[str(eid)] = attached.get(str(eid), False) or by_decision
    for eid in sorted(attached):
        record = evidence.get(eid)
        if record is None or record["claim_type"] != REQUEST_CLAIM_TYPE:
            continue
        request = static_request(record)
        if request is not None:
            found.setdefault(request_identity(request), request)
            resolved_spans.append((str(record["path"]), _span(record)))
        elif attached[eid]:
            decided.add(f"{record['path']}:{_span(record)[0]}")
        else:
            undecided.append((str(record["path"]), _span(record)))
    for event in captured:
        request = _captured_request(event)
        if request is None:
            unreachable.add("a captured event carried no service, method or body")
        else:
            found.setdefault(request_identity(request), request)
    unreachable.update(
        f"{path}:{span[0]}" for path, span in undecided if not _covered(path, span, resolved_spans)
    )
    reached = {str(item["origin"]).removeprefix("static:") for item in found.values()}
    return RequestSites(
        requests=tuple((key, found[key]) for key in sorted(found)),
        unreachable=tuple(sorted(unreachable - reached)),
        decided=tuple(sorted(decided - reached)),
    )


def _span(record: Mapping[str, Any]) -> tuple[int, int]:
    start = as_line(record.get("line_start")) or 0
    return start, as_line(record.get("line_end")) or start


def _covered(
    path: str, span: tuple[int, int], resolved: Sequence[tuple[str, tuple[int, int]]]
) -> bool:
    return any(
        other == path and start <= span[0] and span[1] <= end for other, (start, end) in resolved
    )


def static_request(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = as_mapping(record.get("value"))
    dynamic = _request_from_text(value)
    if dynamic is not None:
        return {
            "service": as_text(value.get("service")) or "",
            "method": as_text(value.get("method")) or "",
            "request": dynamic,
            "origin": (
                "captured"
                if record.get("observer") in ("dynamic", "sentinel")
                else f"static:{record['path']}:{record['line_start'] or 0}"
            ),
        }
    skeleton = as_mapping(value.get("skeleton"))
    if as_sequence(skeleton.get("holes")):
        return None
    fragments = [str(item) for item in as_sequence(skeleton.get("fragments"))]
    text = as_text(value.get("text")) or as_text(value.get("query")) or " ".join(fragments)
    if not text.strip():
        return None
    sink = as_mapping(value.get("sink"))
    return {
        "service": as_text(value.get("service")) or "",
        "method": as_text(value.get("method")) or "",
        "request": {"query": text},
        "origin": f"static:{record['path']}:{record['line_start'] or 0}",
        **({"sink": dict(sink)} if sink else {}),
    }


def _captured_request(event: Mapping[str, Any]) -> Mapping[str, Any] | None:
    service = as_text(event.get("service"))
    method = as_text(event.get("method"))
    body = captured_body(event)
    if not service or not method or not body:
        return None
    return {
        "service": service,
        "method": method,
        "request": dict(body),
        "origin": "captured",
    }


def captured_body(event: Mapping[str, Any]) -> Mapping[str, Any] | None:
    body = event.get("request")
    if is_mapping(body) and body:
        return dict(as_mapping(body))
    return _request_from_text(event)


def _request_from_text(value: Mapping[str, Any]) -> Mapping[str, Any] | None:
    text = as_text(value.get("request_text"))
    if text is None or not text.strip():
        return None
    parsed = parse_json(text)
    if parsed.ok() and is_mapping(parsed.value) and parsed.value:
        return dict(as_mapping(parsed.value))
    method = as_text(value.get("method")) or ""
    if method in ("Search", "SearchStream"):
        return {"query": text}
    return None


def review(
    oracle: OracleView,
    requests: Sequence[tuple[str, Mapping[str, Any]]],
    target_version: str,
    now: datetime | None = None,
    unreachable: Sequence[str] = (),
    subjects_changed: bool = False,
    decided: Sequence[str] = (),
) -> OracleReview:
    stamp = (now or datetime.now(UTC)).isoformat()
    checks: list[OracleCheck] = []
    available = True
    for request_hash, request in requests:
        outcome = _validate(oracle, request, target_version)
        if outcome.code == "ORACLE_UNAVAILABLE":
            available = False
        checks.append(
            OracleCheck(
                request_hash=request_hash,
                service=str(request.get("service", "")),
                method=str(request.get("method", "")),
                version=target_version,
                origin=str(request.get("origin", "")),
                code=outcome.code,
                reason=outcome.reason,
                checked_at=stamp,
                authority=outcome.authority,
            )
        )
    return OracleReview(
        checks=tuple(checks),
        available=available,
        unreachable=tuple(sorted(set(unreachable))),
        silent=subjects_changed and not checks,
        decided=tuple(sorted(set(decided))),
    )


def _validate(oracle: OracleView, request: Mapping[str, Any], target_version: str) -> OracleOutcome:
    try:
        outcome: Any = oracle.validate(request, target_version)
    except Exception as error:
        return OracleOutcome(code="ORACLE_UNAVAILABLE", reason=str(error))
    if not isinstance(outcome, OracleOutcome) or outcome.code not in (
        "VALID",
        "INVALID",
        "UNKNOWN_PROVIDER_CONTRACT",
        "ORACLE_UNAVAILABLE",
    ):
        return OracleOutcome(
            code="UNKNOWN_PROVIDER_CONTRACT",
            reason="the injected oracle returned no valid outcome code",
        )
    if outcome.code == "VALID" and outcome.authority not in ORACLE_AUTHORITIES:
        return OracleOutcome(
            code="ORACLE_UNAVAILABLE",
            reason="the injected oracle accepted the request without naming an authority",
        )
    return outcome


__all__ = [
    "OracleReview",
    "RequestSites",
    "captured_body",
    "request_sites",
    "requests_from",
    "review",
    "static_request",
]
