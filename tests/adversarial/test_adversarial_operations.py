from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hubbleops.app import cli, migration, registry
from hubbleops.closure import source_closure
from hubbleops.core.candidate import candidate_identity, make_candidate
from hubbleops.core.evidence import make_evidence
from hubbleops.observe.ledger import Ledger
from hubbleops.proof.receipt import Receipt
from hubbleops.verify import conserve
from tests.adversarial.test_adversarial_candidates import corruptions
from tests.phase5_support import Repository, build_repository, write_obligations

BY_NAME = {item.name: item for item in corruptions()}
SCOPE = "0" * 64
RUN = "1" * 64
CANDIDATE_SCOPE = "2" * 64
CANDIDATE_RUN = "3" * 64

CRLF_SOURCE = """API_VERSION = "v1"
RETRYABLE = ("MockProvError",)


def build(token):
    return (token, API_VERSION)
"""


def _observation(source_hash: str, run_id: str, proof_scope_hash: str) -> dict[str, Any]:
    return make_evidence(
        run_id=run_id,
        proof_scope_hash=proof_scope_hash,
        claim_type="surface_reference",
        observer="text",
        repo_sha=None,
        path="legacy/config.py",
        line_start=22,
        line_end=22,
        source_hash=source_hash,
        value={"pattern": "ADS_API_VERSION"},
        provider_subject=None,
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="RAW",
    )


def _ledger(record: dict[str, Any], status: str, run_id: str, proof_scope_hash: str) -> Ledger:
    return Ledger(
        provider="p",
        run_id=run_id,
        proof_scope_hash=proof_scope_hash,
        evidence=(record,),
        candidates=(
            make_candidate(
                candidate_id=candidate_identity("p", "surface_reference", "legacy/config.py:22"),
                run_id=run_id,
                proof_scope_hash=proof_scope_hash,
                provider="p",
                evidence_ids=[record["id"]],
                status=status,
                reason="the version is loaded from ADS_API_VERSION at runtime",
                close_with="capture the value in production" if status == "UNKNOWN" else None,
            ),
        ),
    )


def test_rewritten_file_closes_unknown_is_still_a_conservation_failure() -> None:
    attack = BY_NAME["rewritten_file_closes_unknown"]
    before = _observation("a" * 64, RUN, SCOPE)
    after = _observation("b" * 64, CANDIDATE_RUN, CANDIDATE_SCOPE)

    result = conserve.compare(
        _ledger(before, "UNKNOWN", RUN, SCOPE),
        _ledger(after, "NOT_AFFECTED_WITH_EVIDENCE", CANDIDATE_RUN, CANDIDATE_SCOPE),
    )

    assert result.report().passed is False, f"P0: {attack.summary}"
    assert any(attack.reason_contains in reason for reason in result.report().reasons)


def test_crlf_rewrite_leaves_every_untouched_terminator_alone(tmp_path: Path) -> None:
    attack = BY_NAME["crlf_rewrite"]
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "client.py"
    source.write_bytes(CRLF_SOURCE.replace("\n", "\r\n").encode("utf-8"))
    before = source.read_bytes()
    record = make_evidence(
        run_id=RUN,
        proof_scope_hash=SCOPE,
        claim_type="call_version",
        observer="structure",
        repo_sha=None,
        path="src/client.py",
        line_start=1,
        line_end=1,
        source_hash="a" * 64,
        value={"version": "v1", "slot": "literal"},
        provider_subject="v1",
        dependency_context_hash=None,
        derivation="OBSERVED",
        confidence="PROVEN",
    )
    book = Ledger(
        provider="mockprov",
        run_id=RUN,
        proof_scope_hash=SCOPE,
        evidence=(record,),
        candidates=(
            make_candidate(
                candidate_id=candidate_identity("mockprov", "call_version", "src/client.py:1"),
                run_id=RUN,
                proof_scope_hash=SCOPE,
                provider="mockprov",
                evidence_ids=[record["id"]],
                status="AFFECTED",
                reason="the call site names v1",
                close_with=None,
            ),
        ),
    )

    result = migration.migrate(
        pack=registry.load_pack("_mock"),
        ledger=book,
        closure=source_closure.build(tmp_path),
        root=tmp_path,
        target="v2",
        write=True,
    )

    assert result.written == ("src/client.py",), f"P0: {attack.summary}"
    after = source.read_bytes()
    assert after.count(b"\r\n") == before.count(b"\r\n"), f"P0: {attack.summary}"
    assert after.split(b"\r\n")[1:] == before.split(b"\r\n")[1:]
    assert attack.expected_verdict == "PRESERVED"


def test_guard_without_retired_refuses_instead_of_passing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    attack = BY_NAME["guard_without_retired"]
    (tmp_path / "app.py").write_text("active.call()\n", encoding="utf-8")

    code = cli.main(["guard", "--repo", str(tmp_path)])

    printed = capsys.readouterr()
    assert code == cli.EXIT_FAILED, f"P0: {attack.summary}"
    assert "PASS" not in printed.out
    assert attack.reason_contains in printed.out + printed.err


def test_forged_receipt_cannot_prepare_a_proof_pack(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    attack = BY_NAME["forged_receipt"]
    repository = build_repository(tmp_path / "repo")
    obligations = write_obligations(tmp_path / "obligations.json", repository)
    root = tmp_path / "candidate"
    _clone_at(repository, root)
    state = root / ".hubbleops"
    assert _verify(repository, obligations, root, state) == cli.EXIT_OK
    state.joinpath("obligations.json").write_bytes(obligations.read_bytes())
    honest = Receipt(json.loads(next(state.rglob("receipt.json")).read_text(encoding="utf-8")))

    altered = json.loads(json.dumps(honest.record))
    altered["candidates_summary"]["unknown"] = (
        int(altered["candidates_summary"]["unknown"] or 0) + 1
    )
    forged = Receipt(altered)
    receipt_path = tmp_path / "forged-receipt.json"
    receipt_path.write_bytes(forged.json_bytes())
    body = tmp_path / "forged-body.md"
    capsys.readouterr()

    code = cli.main(
        [
            "prepare-pr",
            str(receipt_path),
            "--repo",
            str(root),
            "--body",
            str(body),
            "--state-dir",
            str(state),
        ]
    )

    printed = capsys.readouterr()
    assert code != cli.EXIT_OK, f"P0: {attack.summary}"
    assert attack.reason_contains in printed.out + printed.err
    assert not body.exists()


def _clone_at(repository: Repository, root: Path) -> None:
    subprocess.run(
        ["git", "clone", "--quiet", str(repository.path), str(root)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "checkout", "--quiet", repository.candidate_sha],
        check=True,
        capture_output=True,
    )


def _verify(repository: Repository, obligations: Path, root: Path, state: Path) -> int:
    return cli.main(
        [
            "verify",
            repository.base_sha,
            repository.candidate_sha,
            "--pack",
            "_mock",
            "--repo",
            str(root),
            "--from",
            "v1",
            "--to",
            "v2",
            "--obligations",
            str(obligations),
            "--state-dir",
            str(state),
        ]
    )
