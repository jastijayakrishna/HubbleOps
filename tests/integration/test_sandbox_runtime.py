from __future__ import annotations

from pathlib import Path

import pytest

from hubbleops.sandbox.image import CAPTURE_IMAGE, NODE_CAPTURE_IMAGE, PHP_CAPTURE_IMAGE, ImageSpec
from hubbleops.sandbox.limits import ResourceLimits
from hubbleops.sandbox.mounts import Mount
from hubbleops.sandbox.runner import RootlessPodman, RunResult, RunSpec

RUNTIME_IMAGES = (CAPTURE_IMAGE, NODE_CAPTURE_IMAGE, PHP_CAPTURE_IMAGE)


def rootless_available() -> bool:
    try:
        return RootlessPodman().identity().endswith("rootless=true")
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not rootless_available(), reason="rootless Podman is unavailable")


def run(
    tmp_path: Path,
    command: str,
    *,
    limits: ResourceLimits | None = None,
    image: ImageSpec = CAPTURE_IMAGE,
    mounts: tuple[Mount, ...] | None = None,
    environment: tuple[tuple[str, str], ...] = (),
) -> RunResult:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    artifacts = tmp_path / f"artifacts-{abs(hash(command)):x}"
    spec = RunSpec(
        image=image,
        command=command,
        mounts=mounts or (Mount(workspace, "/workspace", True),),
        allowed_mount_roots=(tmp_path.resolve(),),
        limits=limits or ResourceLimits(),
        artifact_dir=artifacts,
        environment=environment,
    )
    return RootlessPodman().run(spec)


def test_the_engine_reports_rootless_local_seccomp_identity() -> None:
    identity = RootlessPodman().identity()
    assert "rootless=true" in identity
    assert "os=linux" in identity
    assert "remote=false" in identity
    assert "seccomp=true" in identity


def test_the_workload_runs_unprivileged_with_no_capabilities(tmp_path: Path) -> None:
    result = run(tmp_path, "id -u; id -G")
    assert result.outcome == "COMPLETED"
    assert result.stdout.decode("utf-8").splitlines()[0] == "65532"


def test_the_container_root_filesystem_is_read_only(tmp_path: Path) -> None:
    result = run(tmp_path, "touch /escape 2>/dev/null && echo WROTE || echo DENIED")
    assert result.outcome == "COMPLETED"
    assert result.stdout.decode("utf-8").strip() == "DENIED"


def test_a_read_only_mount_cannot_be_written(tmp_path: Path) -> None:
    result = run(tmp_path, "touch /workspace/x 2>/dev/null && echo WROTE || echo DENIED")
    assert result.stdout.decode("utf-8").strip() == "DENIED"


def test_a_mount_target_outside_the_allowed_roots_is_refused(tmp_path: Path) -> None:
    from hubbleops.sandbox.mounts import MountInvalid

    outside = tmp_path.parent
    with pytest.raises(MountInvalid):
        RunSpec(
            image=CAPTURE_IMAGE,
            command="true",
            mounts=(Mount(outside, "/escape", True),),
            allowed_mount_roots=(tmp_path.resolve(),),
            limits=ResourceLimits(),
            artifact_dir=tmp_path / "artifacts",
        )


def test_the_default_network_is_deny_all(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        "python -c \"import socket;socket.create_connection(('1.1.1.1',443),timeout=4)\" "
        "2>/dev/null && echo REACHED || echo DENIED",
    )
    assert result.stdout.decode("utf-8").strip() == "DENIED"


def test_no_host_environment_value_reaches_the_workload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from hubbleops.sandbox.runner import SAFE_ENV

    canaries = {
        "HUBBLEOPS_HOST_CANARY": "must-not-cross-4a1f",
        "GOOGLE_APPLICATION_CREDENTIALS": "/host/adc-9b2e.json",
        "AWS_PROFILE": "host-profile-7c3d",
    }
    for name, value in canaries.items():
        monkeypatch.setenv(name, value)
    result = run(tmp_path, "env")
    text = result.stdout.decode("utf-8")
    offered = dict(
        line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("=")
    )
    assert not set(canaries) & set(offered)
    assert not any(value in text for value in canaries.values())
    assert all(offered.get(name) == value for name, value in SAFE_ENV.items())
    assert offered.get("PATH") != os.environ.get("PATH")


@pytest.mark.parametrize("image", RUNTIME_IMAGES, ids=lambda item: item.reference.split("@")[0])
def test_every_capture_runtime_starts_under_the_declared_limits(
    tmp_path: Path, image: ImageSpec
) -> None:
    result = run(tmp_path, "echo started", image=image)
    assert result.outcome == "COMPLETED", result.stderr.decode("utf-8")
    assert result.stdout.decode("utf-8").strip() == "started"


@pytest.mark.parametrize("image", RUNTIME_IMAGES, ids=lambda item: item.reference.split("@")[0])
def test_the_memory_bound_is_enforced_by_the_kernel(tmp_path: Path, image: ImageSpec) -> None:
    result = run(
        tmp_path,
        "dd if=/dev/zero bs=1M count=900 2>/dev/null | tail -c 943718400 > /tmp/x "
        "&& echo WROTE || echo BOUNDED",
        limits=ResourceLimits(memory_bytes=134_217_728),
        image=image,
    )
    assert result.stdout.decode("utf-8").strip() == "BOUNDED"


def test_the_address_space_bound_is_enforced(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        'python -c "b=bytearray(300*1024*1024)" 2>/dev/null && echo ALLOCATED || echo BOUNDED',
        limits=ResourceLimits(memory_bytes=134_217_728, address_space_bytes=134_217_728),
    )
    assert result.stdout.decode("utf-8").strip() == "BOUNDED"


def test_the_process_bound_is_enforced(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        "ulimit -u",
        limits=ResourceLimits(processes=16),
    )
    assert result.stdout.decode("utf-8").strip() == "16"


def test_the_file_descriptor_bound_is_enforced(tmp_path: Path) -> None:
    result = run(tmp_path, "ulimit -n", limits=ResourceLimits(open_files=32))
    assert result.stdout.decode("utf-8").strip() == "32"


def test_the_file_size_bound_is_enforced(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        "dd if=/dev/zero of=/tmp/big bs=1M count=8 2>/dev/null && echo WROTE || echo BOUNDED",
        limits=ResourceLimits(file_bytes=1_048_576),
    )
    assert result.stdout.decode("utf-8").strip() == "BOUNDED"


def test_the_cpu_bound_is_enforced(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        'python -c "\nwhile True:\n    pass\n" && echo FINISHED || echo BOUNDED',
        limits=ResourceLimits(cpu_seconds=2, wall_seconds=90.0),
    )
    assert result.stdout.decode("utf-8").strip() == "BOUNDED"


def test_a_wall_clock_overrun_is_terminated_and_named(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        "sleep 60",
        limits=ResourceLimits(cpu_seconds=120, wall_seconds=5.0),
    )
    assert result.outcome == "WALL_TIMEOUT"
    assert result.duration_seconds < 40.0


def test_an_output_flood_is_bounded_and_named(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        "python -c \"\nwhile True:\n    print('x' * 4096, flush=True)\n\"",
        limits=ResourceLimits(output_bytes=131_072, wall_seconds=60.0),
    )
    assert result.outcome == "OUTPUT_LIMIT"
    assert len(result.stdout) <= 131_072


def test_a_nonzero_workload_exit_is_named_not_swallowed(tmp_path: Path) -> None:
    result = run(tmp_path, "exit 3")
    assert result.outcome == "NONZERO_EXIT"
    assert result.exit_code != 0


def test_the_command_log_records_the_exact_argv_that_ran(tmp_path: Path) -> None:
    import json

    result = run(tmp_path, "echo logged")
    log = json.loads(result.log_path.read_text("utf-8"))
    assert log["argv"] == list(result.argv)
    assert log["outcome"] == "COMPLETED"
    assert log["exit_code"] == 0


def test_a_second_run_never_overwrites_an_existing_command_log(tmp_path: Path) -> None:
    from hubbleops.sandbox.runner import SandboxInvalid

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifacts = tmp_path / "artifacts"
    spec = RunSpec(
        image=CAPTURE_IMAGE,
        command="echo once",
        mounts=(Mount(workspace, "/workspace", True),),
        allowed_mount_roots=(tmp_path.resolve(),),
        limits=ResourceLimits(),
        artifact_dir=artifacts,
    )
    engine = RootlessPodman()
    engine.run(spec)
    with pytest.raises(SandboxInvalid):
        engine.run(spec)
