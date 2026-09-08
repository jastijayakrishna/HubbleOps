from __future__ import annotations

import ast
from pathlib import Path

import pytest

from hubbleops.sandbox import CAPTURE_IMAGE, NODE_CAPTURE_IMAGE, PHP_CAPTURE_IMAGE, PROXY_IMAGE
from hubbleops.sandbox.mounts import Mount
from hubbleops.sandbox.verifier_image import (
    BASE_TARGET,
    CANDIDATE_TARGET,
    FORBIDDEN_SOURCE_MARKERS,
    VERIFIER_IMAGE,
    VerifierImage,
    VerifierIsolationViolated,
    verify_read_only,
)
from tests.support import PACKAGE_ROOT, python_sources

ALLOWED_SANDBOX_IMPORT = "hubbleops.sandbox.verifier_image"
FORBIDDEN_FOR_VERIFY = (
    "hubbleops.repair",
    "repair",
    "hubbleops.packs",
    "packs",
    "hubbleops.sandbox.runner",
    "hubbleops.sandbox.capture",
    "hubbleops.sandbox.proxy",
    "hubbleops.sandbox.image",
    "hubbleops.sandbox.network",
    "hubbleops.sandbox.limits",
    "hubbleops.sandbox.mounts",
    "hubbleops.app",
    "hubbleops.proof",
)


def imported(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.append(node.module)
    return modules


def trees(tmp_path: Path) -> tuple[Path, Path, Path]:
    for name in ("base", "candidate", "output"):
        (tmp_path / name).mkdir()
    return tmp_path / "base", tmp_path / "candidate", tmp_path / "output"


def test_the_verifier_image_differs_from_every_capture_image() -> None:
    others = {
        CAPTURE_IMAGE.reference,
        NODE_CAPTURE_IMAGE.reference,
        PHP_CAPTURE_IMAGE.reference,
        PROXY_IMAGE.reference,
    }
    assert VERIFIER_IMAGE.reference not in others, (
        "verifier isolation is never a mode of the repair runner; a shared image is a shared mode"
    )
    assert VERIFIER_IMAGE.spec().reference == VERIFIER_IMAGE.reference


def test_the_verifier_has_no_network() -> None:
    assert VERIFIER_IMAGE.network == "none"
    assert VERIFIER_IMAGE.root_read_only is True


def test_the_candidate_and_base_are_mounted_read_only(tmp_path: Path) -> None:
    base, candidate, output = trees(tmp_path)
    mounts = VERIFIER_IMAGE.mounts(base, candidate, output)
    by_target = {mount.target: mount for mount in mounts}
    assert by_target[BASE_TARGET].read_only is True
    assert by_target[CANDIDATE_TARGET].read_only is True
    verify_read_only(mounts)


def test_a_writable_candidate_mount_is_refused(tmp_path: Path) -> None:
    _, candidate, _ = trees(tmp_path)
    writable = (Mount(source=candidate, target=CANDIDATE_TARGET, read_only=False),)
    with pytest.raises(VerifierIsolationViolated):
        verify_read_only(writable)


@pytest.mark.parametrize("marker", sorted(FORBIDDEN_SOURCE_MARKERS))
def test_a_repair_side_mount_source_is_refused_not_filtered(tmp_path: Path, marker: str) -> None:
    base, _, output = trees(tmp_path)
    tainted = tmp_path / marker / "candidate"
    tainted.mkdir(parents=True)
    with pytest.raises(VerifierIsolationViolated) as error:
        VERIFIER_IMAGE.mounts(base, tainted, output)
    assert marker in str(error.value)


def test_the_fingerprint_moves_when_the_policy_moves() -> None:
    baseline = VERIFIER_IMAGE.fingerprint()
    assert baseline != VerifierImage(network="hops-verify").fingerprint()
    assert baseline != VerifierImage(root_read_only=False).fingerprint()
    assert baseline != VerifierImage(user="0:0").fingerprint()
    assert baseline == VerifierImage().fingerprint()


def test_the_image_hash_reaches_the_proof_scope() -> None:
    from hubbleops.app.verification import verifier_version

    assert VERIFIER_IMAGE.reference in verifier_version()


def test_verify_imports_only_the_verifier_image_from_sandbox() -> None:
    offences: list[str] = []
    for source in python_sources(PACKAGE_ROOT / "verify"):
        for module in imported(source):
            if module == ALLOWED_SANDBOX_IMPORT:
                continue
            for prefix in (*FORBIDDEN_FOR_VERIFY, "hubbleops.sandbox"):
                if module == prefix or module.startswith(f"{prefix}."):
                    offences.append(f"{source.name} imports {module}")
    assert offences == [], (
        "law L6: verify/ never imports repair/, packs/ or sandbox/runner; "
        f"it uses sandbox/verifier_image only. Found: {offences}"
    )


def test_the_verifier_never_reads_a_repair_artifact_path(tmp_path: Path) -> None:
    from hubbleops.app.registry import load_pack
    from hubbleops.app.verification import VerificationRequest, refuse_repair_inputs

    agent_input = tmp_path / "agent" / "manifest.json"
    agent_input.parent.mkdir(parents=True)
    agent_input.write_text("[]", encoding="utf-8")
    request = VerificationRequest(
        repository=tmp_path,
        pack=load_pack("_mock"),
        base="a" * 40,
        candidate="b" * 40,
        obligations_path=agent_input,
    )
    with pytest.raises(VerifierIsolationViolated):
        refuse_repair_inputs(request)
