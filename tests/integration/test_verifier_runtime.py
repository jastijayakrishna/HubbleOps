from __future__ import annotations

from pathlib import Path

import pytest

from hubbleops.sandbox.limits import ResourceLimits
from hubbleops.sandbox.mounts import Mount
from hubbleops.sandbox.runner import RootlessPodman, RunResult, RunSpec
from hubbleops.sandbox.verifier_image import (
    BASE_TARGET,
    CANDIDATE_TARGET,
    OUTPUT_TARGET,
    VERIFIER_IMAGE,
    verify_read_only,
)


def rootless_available() -> bool:
    try:
        return RootlessPodman().identity().endswith("rootless=true")
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not rootless_available(), reason="rootless Podman is unavailable")


@pytest.fixture
def trees(tmp_path: Path) -> tuple[Path, Path, Path]:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    output = tmp_path / "output"
    for tree in (base, candidate, output):
        tree.mkdir()
    (base / "app.py").write_text("VERSION = 1\n", encoding="utf-8")
    (candidate / "app.py").write_text("VERSION = 2\n", encoding="utf-8")
    return base, candidate, output


def run_in_verifier(tmp_path: Path, trees: tuple[Path, Path, Path], command: str) -> RunResult:
    base, candidate, output = trees
    mounts = VERIFIER_IMAGE.mounts(base, candidate, output)
    verify_read_only(mounts)
    spec = RunSpec(
        image=VERIFIER_IMAGE.spec(),
        command=command,
        mounts=mounts,
        allowed_mount_roots=(tmp_path.resolve(),),
        limits=ResourceLimits(),
        artifact_dir=tmp_path / f"artifacts-{abs(hash(command)):x}",
        container_workdir=OUTPUT_TARGET,
        network=VERIFIER_IMAGE.network,
    )
    return RootlessPodman().run(spec)


def test_the_verifier_image_starts_and_sees_both_trees(
    tmp_path: Path, trees: tuple[Path, Path, Path]
) -> None:
    result = run_in_verifier(tmp_path, trees, f"cat {BASE_TARGET}/app.py {CANDIDATE_TARGET}/app.py")
    assert result.exit_code == 0, result.stderr.decode("utf-8", errors="replace")
    assert b"VERSION = 1" in result.stdout
    assert b"VERSION = 2" in result.stdout


def test_the_candidate_cannot_be_written_from_inside_the_verifier(
    tmp_path: Path, trees: tuple[Path, Path, Path]
) -> None:
    result = run_in_verifier(tmp_path, trees, f"echo tampered > {CANDIDATE_TARGET}/app.py")
    assert result.exit_code != 0, "the candidate mount must be read-only inside the verifier"
    assert (trees[1] / "app.py").read_text(encoding="utf-8") == "VERSION = 2\n"


def test_the_base_cannot_be_written_from_inside_the_verifier(
    tmp_path: Path, trees: tuple[Path, Path, Path]
) -> None:
    result = run_in_verifier(tmp_path, trees, f"echo tampered > {BASE_TARGET}/app.py")
    assert result.exit_code != 0
    assert (trees[0] / "app.py").read_text(encoding="utf-8") == "VERSION = 1\n"


def test_the_verifier_writes_only_its_own_output_directory(
    tmp_path: Path, trees: tuple[Path, Path, Path]
) -> None:
    result = run_in_verifier(tmp_path, trees, f"echo receipt > {OUTPUT_TARGET}/receipt.txt")
    assert result.exit_code == 0, result.stderr.decode("utf-8", errors="replace")
    assert (trees[2] / "receipt.txt").read_text(encoding="utf-8").strip() == "receipt"


def test_the_verifier_has_no_network(tmp_path: Path, trees: tuple[Path, Path, Path]) -> None:
    probe = (
        "import socket,sys;"
        "s=socket.socket();"
        "s.settimeout(3);"
        "sys.exit(0 if s.connect_ex(('1.1.1.1',443))!=0 else 1)"
    )
    result = run_in_verifier(tmp_path, trees, f"python -c {probe!r}")
    assert result.exit_code == 0, "the verifier container must reach nothing"


def test_the_verifier_root_filesystem_is_read_only(
    tmp_path: Path, trees: tuple[Path, Path, Path]
) -> None:
    result = run_in_verifier(tmp_path, trees, "echo x > /usr/local/tampered")
    assert result.exit_code != 0


def test_the_running_image_is_the_pinned_verifier_digest(
    tmp_path: Path, trees: tuple[Path, Path, Path]
) -> None:
    spec = VERIFIER_IMAGE.spec()
    argv = RootlessPodman().command(
        RunSpec(
            image=spec,
            command="true",
            mounts=VERIFIER_IMAGE.mounts(*trees),
            allowed_mount_roots=(tmp_path.resolve(),),
            limits=ResourceLimits(),
            artifact_dir=tmp_path / "argv",
            container_workdir=OUTPUT_TARGET,
            network=VERIFIER_IMAGE.network,
        )
    )
    assert spec.reference in argv
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv
    candidate_mount = next(item for item in argv if CANDIDATE_TARGET in item)
    assert ":ro," in candidate_mount


def test_a_mount_outside_the_allowed_roots_is_refused(
    tmp_path: Path, trees: tuple[Path, Path, Path]
) -> None:
    from hubbleops.sandbox.mounts import MountInvalid

    outside = tmp_path.parent
    with pytest.raises(MountInvalid):
        RunSpec(
            image=VERIFIER_IMAGE.spec(),
            command="true",
            mounts=(Mount(outside, CANDIDATE_TARGET, True),),
            allowed_mount_roots=(tmp_path.resolve(),),
            limits=ResourceLimits(),
            artifact_dir=tmp_path / "refused",
            container_workdir=OUTPUT_TARGET,
            network=VERIFIER_IMAGE.network,
        )
