from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from hubbleops.core.candidate import DECISION_REASON_PREFIX, OPEN_STATUSES, make_candidate
from hubbleops.core.canonical import content_id
from hubbleops.core.errors import HubbleOpsError
from hubbleops.core.records import MAX_JSON_BYTES, as_mapping, as_sequence, is_list, is_mapping
from hubbleops.observe import resolver
from hubbleops.observe.ledger import Ledger
from hubbleops.store.artifacts import write_atomic
from hubbleops.store.sqlite import Store

DECISION_VALUES = ("AFFECTED", "HUMAN_ACCEPTED_RISK")
REQUIRED_FIELDS = frozenset(
    {
        "id",
        "run_id",
        "proof_scope_hash",
        "candidate_id",
        "blob_hash",
        "path",
        "line_start",
        "claim_type",
        "value",
        "by",
    }
)


class DecisionInvalid(HubbleOpsError):
    pass


def record(
    book: Ledger,
    candidate_prefix: str,
    value: str,
    decided_by: str,
) -> dict[str, Any]:
    candidate = _candidate(book, candidate_prefix)
    if value not in DECISION_VALUES:
        raise DecisionInvalid(f"decision value must be one of: {', '.join(DECISION_VALUES)}")
    if not decided_by.strip():
        raise DecisionInvalid("--by must name the person accepting this decision")
    evidence = book.evidence_by_id()
    attached = [evidence[item] for item in candidate["evidence_ids"] if item in evidence]
    if not attached:
        raise DecisionInvalid("the candidate has no stored evidence to bind the decision")
    chosen = resolver.winner(attached)
    blob_hash = str(chosen.get("source_hash", ""))
    if len(blob_hash) != 64:
        raise DecisionInvalid("the candidate source location has no content hash")
    body: dict[str, Any] = {
        "run_id": book.run_id,
        "proof_scope_hash": book.proof_scope_hash,
        "candidate_id": str(candidate["id"]),
        "blob_hash": blob_hash,
        "path": str(chosen["path"]),
        "line_start": chosen["line_start"],
        "claim_type": str(chosen["claim_type"]),
        "value": value,
        "by": decided_by.strip(),
    }
    return {"id": content_id(body), **body}


def write(
    repository: Path,
    state_dir: Path,
    candidate_prefix: str,
    value: str,
    decided_by: str,
    run_id: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    with Store(state_dir) as store:
        row = store.run(run_id) if run_id else store.latest_run()
        if row is None:
            raise DecisionInvalid(f"no scan run exists in {state_dir}")
        book = Ledger(
            provider=row.provider,
            run_id=row.run_id,
            proof_scope_hash=row.proof_scope_hash,
            evidence=store.evidence_for(row.run_id),
            candidates=store.candidates_for(row.run_id),
        )
        item = record(book, candidate_prefix, value, decided_by)
        decided = apply(
            book,
            (item,),
            decided_run_id=row.run_id,
            decided_proof_scope_hash=row.proof_scope_hash,
        )
        moved = [
            held for held in decided.candidates if str(held["id"]) == str(item["candidate_id"])
        ]
        store.write_candidates(moved, decisions=(item,))
    destination = repository.resolve() / ".hubbleops" / "decisions.yml"
    existing = [
        held for held in load(destination) if held.get("candidate_id") != item["candidate_id"]
    ]
    records = tuple(sorted((*existing, item), key=lambda held: str(held["candidate_id"])))
    payload = yaml.safe_dump(
        {"schema_version": 1, "decisions": list(records)},
        allow_unicode=True,
        sort_keys=True,
    ).encode("utf-8")
    write_atomic(destination, payload)
    return destination, item


def load(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.is_file():
        return ()
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise DecisionInvalid(f"{path} cannot be read: {error}") from error
    if len(payload) > MAX_JSON_BYTES:
        raise DecisionInvalid(f"{path} exceeds the {MAX_JSON_BYTES} byte decision-file bound")
    try:
        loaded: object = yaml.safe_load(payload)
    except yaml.YAMLError as error:
        raise DecisionInvalid(f"{path} is not valid YAML: {error}") from error
    if not is_mapping(loaded):
        raise DecisionInvalid(f"{path} must contain a decision document")
    document = as_mapping(loaded)
    if document.get("schema_version") != 1 or not is_list(document.get("decisions")):
        raise DecisionInvalid(f"{path} must contain schema_version 1 and a decisions list")
    records = as_sequence(document.get("decisions"))
    if any(not is_mapping(item) for item in records):
        raise DecisionInvalid(f"{path} contains a non-object decision")
    return tuple(dict(as_mapping(item)) for item in records)


def bound_records(
    records: Sequence[Mapping[str, Any]],
    expected_scope_hash: str,
    expected_run_id: str,
    book: Ledger,
) -> tuple[dict[str, Any], ...]:
    candidates = {str(item["id"]): item for item in book.candidates}
    selected: list[dict[str, Any]] = []
    for raw in records:
        item = _validate(raw)
        candidate_id = str(item["candidate_id"])
        if candidate_id not in candidates:
            continue
        if item["proof_scope_hash"] != expected_scope_hash or item["run_id"] != expected_run_id:
            raise DecisionInvalid(
                f"decision {item['id']} targets a candidate in this run but is bound to a "
                "different ProofScope or run"
            )
        _validate_location(item, candidates[candidate_id], book)
        selected.append(item)
    if len({str(item["candidate_id"]) for item in selected}) != len(selected):
        raise DecisionInvalid("more than one decision targets the same candidate")
    return tuple(sorted(selected, key=lambda item: str(item["candidate_id"])))


def apply(
    book: Ledger,
    records: Sequence[Mapping[str, Any]],
    *,
    decided_run_id: str,
    decided_proof_scope_hash: str,
) -> Ledger:
    candidates_by_id = {str(item["id"]): item for item in book.candidates}
    validated = tuple(_validate(item) for item in records)
    if len({str(item["candidate_id"]) for item in validated}) != len(validated):
        raise DecisionInvalid("more than one decision targets the same candidate")
    for item in validated:
        if item["proof_scope_hash"] != decided_proof_scope_hash or item["run_id"] != decided_run_id:
            raise DecisionInvalid(
                f"decision {item['id']} is bound to a different ProofScope or run"
            )
        candidate_id = str(item["candidate_id"])
        candidate = candidates_by_id.get(candidate_id)
        if candidate is None:
            raise DecisionInvalid(
                f"decision {item['id']} has no matching candidate in the tree being judged"
            )
        _validate_location(item, candidate, book)
    selected = {str(item["candidate_id"]): item for item in validated}
    candidates: list[dict[str, Any]] = []
    for candidate in book.candidates:
        item = selected.get(str(candidate["id"]))
        if item is None:
            candidates.append(candidate)
            continue
        candidates.append(
            make_candidate(
                candidate_id=str(candidate["id"]),
                run_id=str(candidate["run_id"]),
                proof_scope_hash=str(candidate["proof_scope_hash"]),
                provider=str(candidate["provider"]),
                evidence_ids=[str(value) for value in candidate["evidence_ids"]],
                status=str(item["value"]),
                reason=(
                    f"{DECISION_REASON_PREFIX}{item['id']} by {item['by']} for source blob "
                    f"{item['blob_hash']}"
                ),
                close_with=None,
            )
        )
    return Ledger(
        provider=book.provider,
        run_id=book.run_id,
        proof_scope_hash=book.proof_scope_hash,
        evidence=book.evidence,
        candidates=tuple(candidates),
    )


def _candidate(book: Ledger, prefix: str) -> Mapping[str, Any]:
    matches = [
        item
        for item in book.candidates
        if str(item["id"]).startswith(prefix) and _adjudicable(item)
    ]
    if len(matches) != 1:
        detail = "no open candidate matches" if not matches else "candidate prefix is ambiguous"
        raise DecisionInvalid(f"{detail}: {prefix}")
    return matches[0]


def _adjudicable(item: Mapping[str, Any]) -> bool:
    if item["status"] in OPEN_STATUSES:
        return True
    return str(item.get("reason") or "").startswith(DECISION_REASON_PREFIX)


def _validate(raw: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(raw)
    if frozenset(item) != REQUIRED_FIELDS:
        raise DecisionInvalid("decision fields do not match the version 1 contract")
    expected = content_id({key: value for key, value in item.items() if key != "id"})
    if item["id"] != expected:
        raise DecisionInvalid(f"decision {item.get('id')} has an invalid content identity")
    if item["value"] not in DECISION_VALUES:
        raise DecisionInvalid(f"decision {item['id']} has an unsupported value")
    if not isinstance(item["by"], str) or not item["by"].strip():
        raise DecisionInvalid(f"decision {item['id']} has no human author")
    return item


def _validate_location(item: Mapping[str, Any], candidate: Mapping[str, Any], book: Ledger) -> None:
    evidence = book.evidence_by_id()
    attached = [evidence[value] for value in candidate["evidence_ids"] if value in evidence]
    matching = [
        record
        for record in attached
        if record.get("source_hash") == item["blob_hash"]
        and record.get("path") == item["path"]
        and record.get("line_start") == item["line_start"]
        and record.get("claim_type") == item["claim_type"]
    ]
    if not matching:
        raise DecisionInvalid(
            f"decision {item['id']} is not keyed to evidence for candidate {candidate['id']}"
        )


__all__ = [
    "DECISION_VALUES",
    "DecisionInvalid",
    "apply",
    "bound_records",
    "load",
    "record",
    "write",
]
