from __future__ import annotations

import ast
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hubbleops.app import capture, registry
from hubbleops.core.canonical import content_id
from hubbleops.core.observer import ObserverContext
from hubbleops.observe.dynamic.runner import (
    DynamicEventInvalid,
    EventBatch,
    EventIssue,
    event_schema_hash,
    events_from_jsonl,
    events_to_evidence,
    normalize_event,
)
from hubbleops.observe.ledger import Ledger
from hubbleops.observe.telemetry import ProductionTuple, production_coverage, reconcile


def event(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "method": "Search",
        "mode": "hook",
        "request_text": "SELECT campaign.id FROM campaign",
        "request_type": "grpc",
        "service": "GoogleAdsService",
        "stack": [],
        "ts": "2026-09-06T12:00:00Z",
        "version": "v22",
    }
    value.update(updates)
    return value


def context() -> ObserverContext:
    return ObserverContext(
        provider="google_ads",
        run_id=content_id("run"),
        proof_scope_hash=content_id("scope"),
        repo_sha=None,
        dependency_context_hash=None,
        surface=registry.load_pack("google_ads").surface,
    )


def test_shared_event_contract_is_strict_and_versioned() -> None:
    assert normalize_event(event())["version"] == "v22"
    with pytest.raises(DynamicEventInvalid):
        normalize_event({**event(), "extra": True})
    with pytest.raises(DynamicEventInvalid):
        normalize_event({**event(), "version": "22"})


@pytest.mark.parametrize(
    "path",
    ("../secret.py", "/host/file.py", "src\\app.py", "src/../../secret.py"),
)
def test_repository_stack_paths_fail_closed(path: str) -> None:
    frame = {"function": "call", "kind": "repository", "line": 1, "path": path}
    with pytest.raises(DynamicEventInvalid):
        normalize_event(event(stack=[frame]))


def test_event_jsonl_is_deterministic_and_retains_named_failures() -> None:
    first = json.dumps(event(version="v23")).encode()
    second = json.dumps(event(version="v22")).encode()
    batch = events_from_jsonl(first + b"\nnot-json\n" + second + b"\n")
    assert [item["version"] for item in batch.events] == ["v22", "v23"]
    assert batch.issues[0].code == "EVENT_INVALID"
    assert batch.bytes() == events_from_jsonl(second + b"\n" + first + b"\n").bytes()


def test_truncated_stack_never_looks_complete() -> None:
    frame = {
        "function": None,
        "kind": "truncation",
        "line": None,
        "omitted": 2,
        "path": "<runtime>/truncated",
    }
    batch = events_from_jsonl(json.dumps(event(stack=[frame])).encode())
    assert [issue.code for issue in batch.issues] == ["STACK_TRUNCATED"]


def test_dynamic_events_emit_production_and_source_bound_site_evidence(tmp_path: Path) -> None:
    source = tmp_path / "wrapper.py"
    source.write_text("def wrapper():\n    pass\n", encoding="utf-8")
    frame = {
        "function": "wrapper",
        "kind": "repository",
        "line": 1,
        "path": "wrapper.py",
    }
    records = events_to_evidence(
        EventBatch((normalize_event(event(stack=[frame])),), ()), context(), tmp_path
    )
    assert {record["claim_type"] for record in records} == {
        "call_version",
        "production_version",
        "request_text",
    }
    site = next(record for record in records if record["path"] == "wrapper.py")
    assert site["source_hash"] == hashlib.sha256(source.read_bytes()).hexdigest()


def test_sentinel_cannot_claim_a_static_site(tmp_path: Path) -> None:
    with pytest.raises(DynamicEventInvalid):
        events_to_evidence(EventBatch((), ()), context(), tmp_path, observer="sentinel")


def test_every_shipped_hook_writes_the_install_attestation() -> None:
    for pack_name in ("google_ads", "_mock"):
        pack = registry.load_pack(pack_name)
        for language in ("python", "php", "node"):
            try:
                hooks = pack.capture_hooks(language)
            except Exception:
                continue
            for path in hooks.paths:
                source = path.read_text("utf-8")
                assert "HUBBLEOPS_INSTALL_PATH" in source, path
                assert "HUBBLEOPS_INSTALL_NONCE" in source, path


def test_the_attestation_contract_refuses_a_nonce_that_is_not_a_digest() -> None:
    from hubbleops.observe.dynamic.loaders import (
        LoaderInvalid,
        attestation_environment,
        attested,
    )

    with pytest.raises(LoaderInvalid):
        attestation_environment("short")
    with pytest.raises(LoaderInvalid):
        attested(b"", "short")
    assert dict(attestation_environment("a" * 64)) == {
        "HUBBLEOPS_INSTALL_NONCE": "a" * 64,
        "HUBBLEOPS_INSTALL_PATH": "/hops/output/install.jsonl",
    }


def test_a_corrupt_attestation_file_is_not_an_installed_hook() -> None:
    from hubbleops.observe.dynamic.loaders import attested

    nonce = "b" * 64
    assert attested(b"\xff\xfe\x00not-utf8", nonce) is False
    assert attested(b"{}\n[]\nnot-json\n", nonce) is False
    assert attested(('{"nonce":"' + nonce + '"}\n').encode("utf-8"), nonce) is True


def test_zero_event_issue_becomes_a_named_unknown_candidate_input(tmp_path: Path) -> None:
    records = events_to_evidence(
        EventBatch((), (EventIssue("UNKNOWN_DYNAMIC", 0, "nothing executed"),)),
        context(),
        tmp_path,
    )
    assert records[0]["claim_type"] == "dynamic_state"
    assert records[0]["value"]["code"] == "UNKNOWN_DYNAMIC"


def test_telemetry_exact_matching_counts_once_and_preserves_ambiguity() -> None:
    ctx = context()
    static = Ledger(
        "google_ads",
        ctx.run_id,
        ctx.proof_scope_hash,
        (),
        (),
    )
    result = reconcile((ProductionTuple("Svc", "Call", "v1"),), (), static, ctx)
    assert result.accounted == 0
    assert result.total == 1
    book = Ledger("google_ads", ctx.run_id, ctx.proof_scope_hash, result.evidence, ())
    assert production_coverage(book) == (0, 1)


def test_sentinel_contract_accepts_byte_identical_output(tmp_path: Path) -> None:
    pack = registry.load_pack("google_ads")
    payload = (
        json.dumps(event(mode="proxy"), sort_keys=True, separators=(",", ":")).encode() + b"\n"
    )
    events = tmp_path / "events.jsonl"
    events.write_bytes(payload)
    adapter = Path("packages/hubbleops-sentinel/src/hubbleops_sentinel/adapters/google_ads.py")
    corpus = Path("packages/hubbleops-sentinel/src/hubbleops_sentinel/data/wire_conformance.json")
    schema = Path("packages/hubbleops-sentinel/src/hubbleops_sentinel/data/schema.json")
    sidecar: dict[str, object] = {
        "adapter_sha256": hashlib.sha256(adapter.read_bytes()).hexdigest(),
        "corpus_sha256": hashlib.sha256(corpus.read_bytes()).hexdigest(),
        "events_sha256": hashlib.sha256(payload).hexdigest(),
        "issues": [],
        "mode": "proxy",
        "output_limits": {"events": 10_000, "input_bytes": 16_777_216},
        "package_version": "0.1.0",
        "schema_sha256": hashlib.sha256(schema.read_bytes()).hexdigest(),
    }
    manifest = tmp_path / "events.jsonl.manifest.json"
    manifest.write_text(json.dumps(sidecar), encoding="utf-8")
    imported = capture.sentinel_input(events, manifest, pack)
    assert imported.batch is not None
    assert imported.batch.events[0]["mode"] == "proxy"


def test_sentinel_contract_rejects_tampering_before_evidence(tmp_path: Path) -> None:
    pack = registry.load_pack("google_ads")
    events = tmp_path / "events.jsonl"
    events.write_text("{}\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(capture.CaptureInvalid):
        capture.sentinel_input(events, manifest, pack)


@pytest.mark.parametrize("field", ("adapter_sha256", "corpus_sha256", "schema_sha256"))
def test_sentinel_contract_rejects_each_tampered_compatibility_hash(
    tmp_path: Path, field: str
) -> None:
    pack = registry.load_pack("google_ads")
    payload = (
        json.dumps(event(mode="proxy"), sort_keys=True, separators=(",", ":")).encode() + b"\n"
    )
    events = tmp_path / "events.jsonl"
    events.write_bytes(payload)
    contract = json.loads(
        pack.root.joinpath("capture", "sentinel_contract.json").read_text("utf-8")
    )
    sidecar: dict[str, object] = {
        **contract["versions"]["0.1.0"]["proxy"],
        "events_sha256": hashlib.sha256(payload).hexdigest(),
        "issues": [],
        "mode": "proxy",
        "output_limits": {"events": 10_000, "input_bytes": 16_777_216},
        "package_version": "0.1.0",
    }
    sidecar[field] = "0" * 64
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(sidecar), encoding="utf-8")
    with pytest.raises(capture.CaptureInvalid, match="not byte-compatible"):
        capture.sentinel_input(events, manifest, pack)


def test_sentinel_import_retains_an_event_with_a_named_truncated_stack(tmp_path: Path) -> None:
    pack = registry.load_pack("google_ads")
    frame = {
        "function": None,
        "kind": "truncation",
        "line": None,
        "omitted": 3,
        "path": "<runtime>/truncated",
    }
    payload = (
        json.dumps(
            event(mode="proxy", stack=[frame]), sort_keys=True, separators=(",", ":")
        ).encode()
        + b"\n"
    )
    events = tmp_path / "events.jsonl"
    events.write_bytes(payload)
    supported = json.loads(
        pack.root.joinpath("capture", "sentinel_contract.json").read_text("utf-8")
    )["versions"]["0.1.0"]["proxy"]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                **supported,
                "events_sha256": hashlib.sha256(payload).hexdigest(),
                "issues": [],
                "mode": "proxy",
                "output_limits": {"events": 10_000, "input_bytes": 16_777_216},
                "package_version": "0.1.0",
            }
        ),
        encoding="utf-8",
    )
    imported = capture.sentinel_input(events, manifest, pack)
    assert imported.batch is not None
    assert len(imported.batch.events) == 1
    assert [issue.code for issue in imported.batch.issues] == ["STACK_TRUNCATED"]


def test_schema_hash_is_stable_for_the_exact_shipped_bytes() -> None:
    path = Path("hubbleops/observe/dynamic/schema.json")
    assert event_schema_hash() == hashlib.sha256(path.read_bytes()).hexdigest()
    sentinel = Path("packages/hubbleops-sentinel/src/hubbleops_sentinel/data/schema.json")
    assert sentinel.read_bytes() == path.read_bytes()
    assert datetime.now(UTC).tzinfo is UTC


def test_wire_corpus_is_vendored_byte_identically() -> None:
    pack = Path("hubbleops/packs/google_ads/capture/wire_conformance.json")
    sentinel = Path("packages/hubbleops-sentinel/src/hubbleops_sentinel/data/wire_conformance.json")
    assert sentinel.read_bytes() == pack.read_bytes()


def test_standalone_sentinel_never_imports_main_hubbleops_or_emits_a_verdict() -> None:
    root = Path("packages/hubbleops-sentinel")
    for path in sorted(root.rglob("*.py")):
        source = path.read_text("utf-8")
        tree = ast.parse(source)
        imports = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        imports.extend(
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        )
        assert not any(name == "hubbleops" or name.startswith("hubbleops.") for name in imports)
        if root / "src" in path.parents:
            assert '"verdict"' not in source
