from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from hubbleops.core.canonical import content_id, export_bytes
from hubbleops.core.errors import HubbleOpsError
from hubbleops.core.records import MAX_JSON_BYTES, as_mapping, as_sequence
from hubbleops.core.schema import validate
from hubbleops.proof import guard, pr_body
from hubbleops.proof.receipt import Receipt
from hubbleops.store import write_atomic

GIT_TIMEOUT_SECONDS = 60.0


class MemoryInvalid(HubbleOpsError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedPullRequest:
    body: Path
    surface: Path
    bindings: Path
    decisions: Path
    retired: Path
    guard_workflow: Path
    verification_workflow: Path
    deferred: Mapping[str, tuple[str, ...]] = field(default_factory=dict[str, tuple[str, ...]])


def prepare(
    repository: Path,
    receipt_path: Path,
    body_path: Path,
    obligations: Path,
    command: str = "uv run hops",
    environment: Mapping[str, str] | None = None,
) -> PreparedPullRequest:
    root = repository.resolve()
    document = _receipt(receipt_path)
    if document.record.get("verdict") not in pr_body.ELIGIBLE_VERDICTS:
        raise MemoryInvalid(
            "only VERIFIED_FOR_SCOPE or HUMAN_REQUIRED receipts can prepare a pull request"
        )
    if not pr_body.verdict_holds(document.record):
        raise MemoryInvalid(
            "the receipt's verdict does not follow from the conjuncts it records, so the file "
            "was altered after hops verify wrote it; run hops verify again"
        )
    audit = as_mapping(document.record["migration_audit"])
    proof = as_mapping(document.record["proof_scope"])
    scope_hash = content_id(proof)
    head = _head(root)
    if head != _text(audit, "candidate_sha"):
        raise MemoryInvalid(
            f"the receipt is bound to candidate {_text(audit, 'candidate_sha')[:12]} but "
            f"{root} is at {head[:12]}; a new SHA kills the old proof, run hops verify on it"
        )
    provider = _text(audit, "provider")
    from_version = _text(audit, "from_version")
    to_version = _text(audit, "to_version")
    state = root / ".hubbleops"
    if not obligations.is_file():
        raise MemoryInvalid(
            f"{obligations} is required; run hops migrate before preparing the pull request, or "
            "name the list it wrote with --obligations"
        )
    surface = state / "surface.yml"
    bindings = state / "bindings.json"
    decisions = state / "decisions.yml"
    _initialize_yaml(surface, {"entries": [], "version": 1})
    _initialize_json(
        bindings,
        {
            "schema_version": 1,
            "proof_scope_hash": scope_hash,
            "bindings": [],
        },
    )
    _initialize_yaml(decisions, {"schema_version": 1, "decisions": []})
    patterns = tuple(str(item) for item in as_sequence(audit.get("retired_patterns")))
    still_present = guard.present(root, patterns)
    deferred = {pattern: sites for pattern, sites in still_present.items() if sites}
    retired = guard.write_retired(
        root,
        patterns=tuple(pattern for pattern in patterns if pattern not in deferred),
        provider=provider,
        proof_scope_hash=scope_hash,
    )
    guard_workflow = guard.install_workflow(root, command)
    verification_workflow = root / ".github" / "workflows" / "hubbleops-verify.yml"
    write_atomic(
        verification_workflow,
        workflow(command, provider, from_version, to_version, environment),
    )
    body = body_path.resolve()
    write_atomic(body, pr_body.render(document).encode("utf-8"))
    return PreparedPullRequest(
        body,
        surface,
        bindings,
        decisions,
        retired,
        guard_workflow,
        verification_workflow,
        deferred,
    )


def workflow(
    command: str,
    provider: str,
    from_version: str,
    to_version: str,
    environment: Mapping[str, str] | None = None,
) -> bytes:
    if re.fullmatch(r"[A-Za-z0-9._/ -]+", command) is None:
        raise MemoryInvalid("workflow command contains unsupported shell characters")
    for label, value in (
        ("provider", provider),
        ("from version", from_version),
        ("to version", to_version),
    ):
        if re.fullmatch(r"[A-Za-z0-9._-]+", value) is None:
            raise MemoryInvalid(f"{label} cannot be embedded safely in the workflow")
    bindings = dict(environment or {})
    for name, secret in bindings.items():
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", name) is None:
            raise MemoryInvalid(f"environment name {name!r} cannot be embedded safely")
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", secret) is None:
            raise MemoryInvalid(f"secret name {secret!r} cannot be embedded safely")
    environment_lines = "".join(
        f"      {name}: ${{{{ secrets.{bindings[name]} }}}}\n" for name in sorted(bindings)
    )
    base_sha_line = (
        "      BASE_SHA: "
        "${{ github.event.pull_request.base.sha || github.event.merge_group.base_sha }}\n"
    )
    text = f"""name: HubbleOps verification
on:
  pull_request:
  merge_group:
    types: [checks_requested]
permissions:
  contents: read
jobs:
  verify:
    runs-on: ubuntu-latest
    env:
{environment_lines}{base_sha_line}    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: astral-sh/setup-uv@v6
      - run: >-
          {command} verify "$BASE_SHA" "$GITHUB_SHA"
          --pack {provider}
          --from {from_version}
          --to {to_version}
          --obligations .hubbleops/obligations.json
          --decisions .hubbleops/decisions.yml
      - if: always()
        uses: actions/upload-artifact@v4
        with:
          name: hubbleops-proof-${{{{ github.sha }}}}
          path: .hubbleops/artifacts/**/receipt.*
"""
    return text.encode("utf-8")


def _head(repository: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MemoryInvalid(f"cannot read the HEAD of {repository}: {error}") from error
    head = completed.stdout.strip()
    if completed.returncode != 0 or len(head) != 40:
        raise MemoryInvalid(
            f"{repository} has no resolvable HEAD to bind the pull request to: "
            f"{completed.stderr.strip() or completed.returncode}"
        )
    return head


def _receipt(path: Path) -> Receipt:
    try:
        payload = path.resolve().read_bytes()
    except OSError as error:
        raise MemoryInvalid(f"receipt cannot be read: {error}") from error
    if len(payload) > MAX_JSON_BYTES:
        raise MemoryInvalid(f"receipt exceeds the {MAX_JSON_BYTES} byte input bound")
    try:
        raw: object = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MemoryInvalid(f"receipt is not valid JSON: {error}") from error
    return Receipt(validate("receipt", dict(as_mapping(raw))))


def _initialize_yaml(path: Path, document: dict[str, Any]) -> None:
    if path.exists():
        return
    write_atomic(
        path,
        yaml.safe_dump(document, allow_unicode=True, sort_keys=True).encode("utf-8"),
    )


def _initialize_json(path: Path, document: dict[str, Any]) -> None:
    if path.exists():
        return
    write_atomic(path, export_bytes(document))


def _text(document: Mapping[str, Any], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise MemoryInvalid(f"receipt migration audit has no {field}")
    return value


def receipt_provider(path: Path) -> str:
    document = _receipt(path)
    return _text(as_mapping(document.record["migration_audit"]), "provider")


__all__ = [
    "MemoryInvalid",
    "PreparedPullRequest",
    "prepare",
    "receipt_provider",
    "workflow",
]
