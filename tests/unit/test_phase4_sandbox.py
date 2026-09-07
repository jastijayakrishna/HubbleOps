from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from hubbleops.app import registry
from hubbleops.sandbox import capture as sandbox_capture
from hubbleops.sandbox.capture import DetachedWorktree
from hubbleops.sandbox.image import CAPTURE_IMAGE, PROXY_IMAGE, ImageInvalid, ImageSpec
from hubbleops.sandbox.limits import LimitsInvalid, ResourceLimits
from hubbleops.sandbox.mounts import Mount, MountInvalid, validate_mounts
from hubbleops.sandbox.network import (
    Destination,
    NetworkPolicy,
    NetworkPolicyInvalid,
    redact_headers,
)
from hubbleops.sandbox.proxy import ProxySession
from hubbleops.sandbox.runner import RootlessPodman, RunSpec, SandboxInvalid, command_record


def test_images_require_an_immutable_digest_and_numeric_user() -> None:
    with pytest.raises(ImageInvalid):
        ImageSpec("python:latest", "0:0")
    with pytest.raises(ImageInvalid):
        ImageSpec("docker.io/library/python@sha256:" + "a" * 64, "root")


def test_limits_reject_zero_and_generate_all_mandatory_rlimits() -> None:
    with pytest.raises(LimitsInvalid):
        ResourceLimits(cpu_seconds=0)
    command = ResourceLimits().shell("python test.py")
    assert all(value in command for value in ("-t", "-d", "-v", "-u", "-n", "-f"))


def test_the_resident_bound_and_the_address_space_bound_are_separate() -> None:
    limits = ResourceLimits(memory_bytes=134_217_728, address_space_bytes=8_589_934_592)
    command = limits.shell("node test.js")
    assert "ulimit -d 131072" in command
    assert "ulimit -v 8388608" in command
    assert limits.fingerprint() != ResourceLimits().fingerprint()


def test_an_address_space_bound_below_the_memory_bound_is_refused() -> None:
    with pytest.raises(LimitsInvalid, match="silently replace the resident bound"):
        ResourceLimits(memory_bytes=536_870_912, address_space_bytes=134_217_728)


def test_mounts_are_confined_read_only_by_value(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    child = root / "child"
    child.mkdir()
    mount = Mount(child, "/workspace", True)
    assert validate_mounts((mount,), (root,))
    with pytest.raises(MountInvalid):
        validate_mounts((Mount(tmp_path, "/escape", True),), (root,))


def test_network_allowlist_is_exact_and_rejects_authority_credentials() -> None:
    destination = Destination.parse("https://example.com")
    policy = NetworkPolicy((destination,))
    assert policy.allows("https", "EXAMPLE.COM.", 443)
    assert not policy.allows("https", "sub.example.com", 443)
    with pytest.raises(NetworkPolicyInvalid):
        Destination.parse("https://user:pass@example.com")


@pytest.mark.parametrize("address", ("127.0.0.1", "10.0.0.1", "::1", "fe80::1"))
def test_private_and_special_addresses_are_denied(address: str) -> None:
    with pytest.raises(NetworkPolicyInvalid):
        NetworkPolicy().validate_addresses("target", (address,))


def test_sensitive_headers_are_redacted_case_insensitively() -> None:
    assert redact_headers({"Authorization": "secret", "X-Test": "ok"}) == {
        "authorization": "<redacted>",
        "x-test": "ok",
    }


def test_runner_command_is_hardened_and_contains_no_host_environment(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    artifacts = tmp_path / "artifacts"
    workspace.mkdir()
    artifacts.mkdir()
    spec = RunSpec(
        CAPTURE_IMAGE,
        "python test.py",
        (Mount(workspace, "/workspace", True),),
        (tmp_path,),
        ResourceLimits(),
        artifacts,
    )
    argv = RootlessPodman().command(spec)
    joined = " ".join(argv)
    assert "--cap-drop=all" in joined
    assert "--security-opt=no-new-privileges" in joined
    assert "--read-only" in joined
    assert "--network none" in joined
    assert "AWS_SECRET_ACCESS_KEY" not in joined


def test_subprocess_transcript_is_bounded_and_records_exact_outcome() -> None:
    record = command_record(("tool", "arg"), 1.25, 7, b"x" * 70_000, b"failure", "NONZERO_EXIT")
    assert record["argv"] == ["tool", "arg"]
    assert record["duration_seconds"] == 1.25
    assert record["exit_code"] == 7
    assert record["outcome"] == "NONZERO_EXIT"
    assert record["stdout_size"] == 70_000
    assert record["stdout_truncated"] is True
    assert len(str(record["stdout"])) == 65_536
    assert record["stderr"] == "failure"


def test_proxy_command_has_the_same_kernel_and_output_bounds(tmp_path: Path) -> None:
    limits = ResourceLimits(
        cpu_seconds=7,
        memory_bytes=134_217_728,
        address_space_bytes=268_435_456,
        processes=17,
        open_files=31,
        file_bytes=1_048_576,
        output_bytes=131_072,
    )
    session = ProxySession(RootlessPodman(), tmp_path / "proxy", NetworkPolicy(), "nonce", limits)
    argv = session.start_arguments(tmp_path / "ca", tmp_path / "output")
    joined = " ".join(argv)
    assert f"--user {PROXY_IMAGE.user}" in joined
    assert "--cap-drop=all" in joined
    assert "--security-opt=no-new-privileges" in joined
    assert "--read-only" in joined
    assert "/hops/proxy/entrypoint.py" in joined
    assert "7 134217728 268435456 17 31 1048576" in joined
    assert "max-size=131072" in joined


def test_runner_refuses_credential_like_environment_names(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    with pytest.raises(SandboxInvalid):
        RunSpec(
            CAPTURE_IMAGE,
            "true",
            (),
            (tmp_path,),
            ResourceLimits(),
            tmp_path,
            environment=(("API_TOKEN", "value"),),
        )


def test_capture_assets_are_bound_into_the_pack_contract_hash() -> None:
    pack = registry.load_pack("google_ads")
    digest = pack.contract_hash()
    assert len(digest) == 64
    assert digest != hashlib.sha256(pack.root.joinpath("surface.yaml").read_bytes()).hexdigest()


def test_detached_worktree_records_and_restores_exact_git_metadata(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    repository.joinpath("source.py").write_text("value = 1\n", encoding="utf-8")
    for command in (
        ("git", "init", "-q"),
        ("git", "config", "user.email", "phase4@example.invalid"),
        ("git", "config", "user.name", "Phase 4"),
        ("git", "add", "source.py"),
        ("git", "commit", "-q", "-m", "fixture"),
    ):
        subprocess.run(command, cwd=repository, check=True, capture_output=True)
    manager = DetachedWorktree(repository, tmp_path / "detached", "HEAD")
    with manager as destination:
        assert destination.joinpath("source.py").is_file()
    attestation = manager.attestation()
    assert len(attestation["created"]) == 1
    assert attestation["before"] == attestation["after_cleanup"]
    assert attestation["recovered"] is False
    assert len(attestation["commands"]) == 4
    assert all("stdout" in record and "stderr" in record for record in attestation["commands"])


def test_a_worktree_whose_removal_is_refused_still_restores_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    repository.joinpath("source.py").write_text("value = 1\n", encoding="utf-8")
    for command in (
        ("git", "init", "-q"),
        ("git", "config", "user.email", "phase4@example.invalid"),
        ("git", "config", "user.name", "Phase 4"),
        ("git", "add", "source.py"),
        ("git", "commit", "-q", "-m", "fixture"),
    ):
        subprocess.run(command, cwd=repository, check=True, capture_output=True)

    monkeypatch.setattr(sandbox_capture, "RELEASE_SECONDS", 0.0)
    executed = subprocess.run

    def refuse_removal(argv: tuple[str, ...], **options: Any) -> subprocess.CompletedProcess[str]:
        if "worktree" in argv and "remove" in argv:
            return subprocess.CompletedProcess(
                argv, 1, "", "error: failed to delete: Permission denied"
            )
        return cast("subprocess.CompletedProcess[str]", executed(argv, **options))

    monkeypatch.setattr(subprocess, "run", refuse_removal)
    manager = DetachedWorktree(repository, tmp_path / "detached", "HEAD")
    with manager as destination:
        assert destination.joinpath("source.py").is_file()
    attestation = manager.attestation()
    assert attestation["recovered"] is True
    assert attestation["before"] == attestation["after_cleanup"]
    residue = repository / ".git" / "worktrees"
    assert not residue.exists() or not list(residue.iterdir())
    assert (
        subprocess.run(
            ("git", "status", "--porcelain"), cwd=repository, capture_output=True, text=True
        ).stdout
        == ""
    )
    assert (
        subprocess.run(
            ("git", "worktree", "list"), cwd=repository, capture_output=True, text=True
        ).stdout.count("\n")
        == 1
    )
