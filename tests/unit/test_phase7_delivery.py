from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
import yaml

import hubbleops
from hubbleops.app.registry import load_pack
from hubbleops.proof import exposure_workflow, guard, pr_body
from hubbleops.proof.guard import GuardInvalid
from hubbleops.proof.memory import MemoryInvalid, workflow
from hubbleops.proof.receipt import authority_lines


def _catalog_check(reason: str) -> dict[str, str]:
    return {"code": "VALID", "authority": "CATALOG", "reason": reason}


def test_catalog_scope_rendering_is_order_independent_and_deduplicated() -> None:
    first = authority_lines(
        "CATALOG",
        [
            _catalog_check("every supplied mutate field resolves"),
            _catalog_check("every field resolves against the inventory"),
            _catalog_check("every supplied mutate field resolves"),
        ],
    )
    second = authority_lines(
        "CATALOG",
        [
            _catalog_check("every field resolves against the inventory"),
            _catalog_check("every supplied mutate field resolves"),
        ],
    )
    assert first == second
    assert first[1:] == (
        "  scope: every field resolves against the inventory",
        "  scope: every supplied mutate field resolves",
    )


def test_a_catalog_acceptance_that_states_no_scope_says_it_proves_nothing() -> None:
    lines = authority_lines("CATALOG", [{"code": "VALID", "authority": "CATALOG", "reason": " "}])
    assert lines[1] == "  the accepting pack recorded no scope, so this acceptance proves nothing"


def test_only_accepted_catalog_checks_contribute_scope() -> None:
    lines = authority_lines(
        "CATALOG",
        [
            _catalog_check("the only accepted catalog scope"),
            {"code": "INVALID", "authority": "CATALOG", "reason": "a rejection is not a scope"},
            {"code": "VALID", "authority": "LIVE", "reason": "a live acceptance is not a scope"},
        ],
    )
    assert lines[1:] == ("  scope: the only accepted catalog scope",)


def test_guard_passes_then_fails_on_a_seeded_reintroduction(tmp_path: Path) -> None:
    retired = guard.write_retired(
        tmp_path,
        patterns=("retired.namespace", "v1"),
        provider="_mock",
        proof_scope_hash="a" * 64,
    )
    source = tmp_path / "app.py"
    source.write_text("VERSION = 'v2'\n", encoding="utf-8")
    started = time.perf_counter()
    clean = guard.run(tmp_path, retired)
    assert time.perf_counter() - started < guard.TIMEOUT_SECONDS
    assert clean.matches == ()
    source.write_text("VERSION = 'v1'\n", encoding="utf-8")
    failed = guard.run(tmp_path, retired)
    assert len(failed.matches) == 1
    assert "app.py:1" in failed.matches[0]


def test_guard_rejects_a_retired_entry_whose_identity_was_forged(tmp_path: Path) -> None:
    path = tmp_path / "retired.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "entries": [
                    {
                        "id": "0" * 64,
                        "pattern": "v1",
                        "provider": "_mock",
                        "proof_scope_hash": "a" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(GuardInvalid, match="does not match its content"):
        guard.load(path)


def holding_record(verdict: str = "VERIFIED_FOR_SCOPE", **overrides: Any) -> dict[str, Any]:
    passing: dict[str, Any] = {"passed": True, "unresolved": []}
    record: dict[str, Any] = {
        "verdict": verdict,
        "migration_audit": dict(passing),
        "request_shape_differential": dict(passing),
        "response_consumer_check": dict(passing),
        "unknown_conservation": dict(passing),
        "frozen_baseline_tests": {
            "executed": True,
            "outcome": "COMPLETED",
            "passed": 3,
            "failed": 0,
        },
        "oracle_authority": "CATALOG",
        "oracle_results": [{"code": "VALID"}],
        "falsifiers": [{"result": "PASS"}, {"result": "NOT_APPLICABLE"}],
        "blast_radius": {"unknown_blast": [], "containment": {"unexplained": 0}},
        "reasons": [],
    }
    record.update(overrides)
    return record


def test_a_receipt_whose_conjuncts_hold_is_eligible() -> None:
    assert pr_body.eligible(holding_record()) is True
    blocked = holding_record(
        "HUMAN_REQUIRED", blast_radius={"unknown_blast": ["mod"], "containment": {"unexplained": 0}}
    )
    assert pr_body.eligible(blocked) is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"migration_audit": {"passed": False, "unresolved": []}},
        {"unknown_conservation": {"passed": True, "unresolved": ["something"]}},
        {
            "frozen_baseline_tests": {
                "executed": False,
                "outcome": "TOOLING_MISSING",
                "passed": 0,
                "failed": 0,
            }
        },
        {"oracle_authority": "ORACLE_UNAVAILABLE"},
        {"oracle_results": [{"code": "INVALID"}]},
        {"falsifiers": [{"result": "FAIL"}]},
        {"falsifiers": [{"result": "NOT_RUN"}]},
        {"falsifiers": [{"result": "SKIPPED"}]},
        {"blast_radius": {"unknown_blast": [], "containment": {"unexplained": 2}}},
        {"reasons": ["audit_pass is FAIL"]},
        {"blast_radius": {"unknown_blast": ["mod"], "containment": {"unexplained": 0}}},
    ],
)
def test_a_verdict_written_over_failing_conjuncts_is_never_eligible(
    overrides: dict[str, Any],
) -> None:
    assert pr_body.eligible(holding_record(**overrides)) is False


def test_generated_actions_are_exact_sha_read_only_and_merge_queue_aware() -> None:
    verification = workflow(
        "uv run hops",
        "_mock",
        "v1",
        "v2",
        {"PROVIDER_TOKEN": "PROVIDER_TOKEN"},
    ).decode()
    backslide = guard.workflow().decode()
    assert "contents: read" in verification
    assert "GITHUB_SHA" in verification
    assert "github.event.pull_request.base.sha || github.event.merge_group.base_sha" in verification
    assert "merge_group:" in verification
    assert "actions/upload-artifact@v4" in verification
    assert "PROVIDER_TOKEN: ${{ secrets.PROVIDER_TOKEN }}" in verification
    assert "hops guard --repo ." in backslide
    assert "merge_group:" in backslide
    assert "write" not in yaml.safe_dump(yaml.safe_load(verification)["permissions"])


def test_provider_owned_secrets_reach_the_workflow_without_entering_generic_code() -> None:
    environment = load_pack("google_ads").verification_environment()
    verification = workflow("uv run hops", "google_ads", "v22", "v25", environment).decode()
    assert environment
    assert all(
        f"{name}: ${{{{ secrets.{secret} }}}}" in verification
        for name, secret in environment.items()
    )


def test_workflow_rejects_an_environment_name_that_could_inject_yaml() -> None:
    with pytest.raises(MemoryInvalid, match="cannot be embedded safely"):
        workflow("uv run hops", "_mock", "v1", "v2", {"BAD\nNAME": "SAFE_NAME"})


def test_exposure_action_is_read_only_pull_request_only_and_installs_nothing_but_uv() -> None:
    text = exposure_workflow.workflow("google_ads", "v25", "hubbleops==0.1.0").decode()
    document = yaml.safe_load(text)
    assert document["permissions"] == {"contents": "read"}
    assert "on:\n  pull_request:\npermissions:\n" in text
    assert "merge_group" not in text
    assert "push" not in text
    assert "secrets." not in text
    assert 'uvx --from "hubbleops==0.1.0" hops scan . --pack google_ads --target v25' in text
    assert (
        'uvx --from "hubbleops==0.1.0" hops exposure --pack google_ads --target v25'
        " | tee hubbleops-exposure.txt"
    ) in text
    assert "${{ github.sha }}" in text
    steps = document["jobs"]["exposure"]["steps"]
    uses = [step["uses"] for step in steps if "uses" in step]
    assert uses == ["actions/checkout@v4", "astral-sh/setup-uv@v6", "actions/upload-artifact@v4"]
    runs = [step["run"] for step in steps if "run" in step]
    assert all(run.startswith("uvx ") for run in runs)
    assert not any(tool in text for tool in ("pip ", "npm ", "apt", "curl "))


@pytest.mark.parametrize(
    ("provider", "target", "source"),
    [
        ("google\nads", "v25", "hubbleops==0.1.0"),
        ("google_ads", "${{ github.sha }}", "hubbleops==0.1.0"),
        ("google_ads", "v25", 'hubbleops"; rm -rf /'),
        ("google_ads", "v25", "hubbleops ==0.1.0"),
        ("google_ads", "v25", "hubbleops==$VERSION"),
        ("google_ads", "v25", "`hubbleops`"),
        ("google_ads", "v25", "-e ."),
        ("google_ads", "v25", ""),
    ],
)
def test_exposure_action_refuses_values_that_could_inject_yaml_or_shell(
    provider: str, target: str, source: str
) -> None:
    with pytest.raises(MemoryInvalid, match="cannot be embedded safely"):
        exposure_workflow.workflow(provider, target, source)


def test_exposure_action_installs_at_the_exact_path_and_is_byte_identical(tmp_path: Path) -> None:
    first = exposure_workflow.install(tmp_path, "_mock", "v2", "hubbleops==0.1.0")
    assert first == tmp_path.resolve() / ".github" / "workflows" / "hubbleops-exposure.yml"
    payload = first.read_bytes()
    second = exposure_workflow.install(tmp_path, "_mock", "v2", "hubbleops==0.1.0")
    assert second == first
    assert second.read_bytes() == payload
    assert payload == exposure_workflow.workflow("_mock", "v2", "hubbleops==0.1.0")


def test_exposure_action_default_source_pins_the_installed_package_version() -> None:
    source = exposure_workflow.default_source()
    assert source == f"hubbleops=={hubbleops.__version__}"
    assert f'--from "{source}"' in exposure_workflow.workflow("_mock", "v2", source).decode()
