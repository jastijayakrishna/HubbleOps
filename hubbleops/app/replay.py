from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from hubbleops.core.errors import HubbleOpsError
from hubbleops.core.proof_scope import proof_scope_hash, run_id_for
from hubbleops.store.sqlite import ArtifactRow, RunRow, Store


class ReplayInvalid(HubbleOpsError):
    pass


@dataclass(frozen=True, slots=True)
class ReplayResult:
    run: RunRow
    artifacts: tuple[ArtifactRow, ...]


def verify(state_dir: Path, run_id: str) -> ReplayResult:
    with Store(state_dir.resolve()) as store:
        row = store.run(run_id)
        if row is None:
            raise ReplayInvalid(f"REPLAY_INVALID: run {run_id} does not exist")
        if row.finished_at is None:
            raise ReplayInvalid(f"REPLAY_INVALID: run {run_id} is unfinished")
        derived_scope = proof_scope_hash(row.proof_scope)
        if derived_scope != row.proof_scope_hash:
            raise ReplayInvalid(
                f"REPLAY_INVALID: run {run_id} ProofScope hashes to {derived_scope}, "
                f"not {row.proof_scope_hash}"
            )
        derived_run = run_id_for(
            scope_hash=derived_scope,
            provider=row.provider,
            verb=row.verb,
            target=row.target,
        )
        if derived_run != row.run_id:
            raise ReplayInvalid(
                f"REPLAY_INVALID: stored run id {row.run_id} derives as {derived_run}"
            )
        artifacts = store.artifacts_for(run_id)
    if not artifacts:
        raise ReplayInvalid(f"REPLAY_INVALID: run {run_id} has no recorded artifacts")
    for artifact in artifacts:
        _verify_artifact(artifact, state_dir, row.proof_scope_hash)
    return ReplayResult(row, artifacts)


def _verify_artifact(
    artifact: ArtifactRow, state_dir: Path, expected_proof_scope_hash: str
) -> None:
    if artifact.proof_scope_hash != expected_proof_scope_hash:
        raise ReplayInvalid(
            f"REPLAY_INVALID: artifact {artifact.kind} is bound to a different ProofScope"
        )
    path = Path(artifact.path)
    if not path.is_absolute():
        path = state_dir.resolve() / path
    if not path.is_file():
        raise ReplayInvalid(f"REPLAY_INVALID: artifact {artifact.kind} is missing at {path}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(65_536):
            digest.update(chunk)
            size += len(chunk)
    if size != artifact.size or digest.hexdigest() != artifact.sha256:
        raise ReplayInvalid(
            f"REPLAY_INVALID: artifact {artifact.kind} at {path} no longer matches "
            f"sha256:{artifact.sha256} size:{artifact.size}"
        )


def render(result: ReplayResult) -> str:
    lines = [
        f"HubbleOps REPLAY VERIFIED  {result.run.run_id}",
        f"  verb        {result.run.verb}",
        f"  provider    {result.run.provider}",
        f"  target      {result.run.target}",
        f"  ProofScope  {result.run.proof_scope_hash}",
        f"  artifacts   {len(result.artifacts)}",
    ]
    lines.extend(
        f"    {item.kind:<24} sha256:{item.sha256}  {item.size} bytes  {item.path}"
        for item in result.artifacts
    )
    return "\n".join(lines) + "\n"


__all__ = ["ReplayInvalid", "ReplayResult", "render", "verify"]
