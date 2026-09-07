from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from hubbleops.app import capture, registry
from hubbleops.app.cli import EXIT_OK, main
from hubbleops.closure import source_closure
from hubbleops.sandbox.image import PROXY_IMAGE
from hubbleops.sandbox.proxy import FixtureService
from hubbleops.store.sqlite import Store

FIXTURE = Path(__file__).parents[1] / "fixtures" / "phase4" / "repo"


def repository(tmp_path: Path) -> Path:
    target = tmp_path / "repo"
    shutil.copytree(FIXTURE, target)
    tracked = sorted(path.name for path in target.iterdir() if path.is_file())
    commands = (
        ("git", "init", "-q"),
        ("git", "config", "user.email", "phase4@example.invalid"),
        ("git", "config", "user.name", "Phase 4"),
        ("git", "add", *tracked),
        ("git", "commit", "-q", "-m", "fixture"),
    )
    for command in commands:
        subprocess.run(command, cwd=target, check=True, capture_output=True)
    return target


def rootless_available() -> bool:
    try:
        return capture.RootlessPodman().identity().endswith("rootless=true")
    except Exception:
        return False


@pytest.mark.skipif(not rootless_available(), reason="rootless Podman is unavailable")
@pytest.mark.parametrize("mode", ("hook", "proxy"))
def test_rootless_capture_observes_the_same_tuple_in_both_modes(tmp_path: Path, mode: str) -> None:
    target = repository(tmp_path)
    closure = source_closure.build(target)
    fixture = (
        FixtureService(PROXY_IMAGE, FIXTURE / "fixture_service.py", "fixture-service", 8443)
        if mode == "proxy"
        else None
    )
    result = capture.execute(
        target,
        closure.repo_sha,
        registry.load_pack("google_ads"),
        "python proxy_probe.py" if mode == "proxy" else "python probe.py",
        "python",
        mode,
        ("https://fixture-service:8443",) if mode == "proxy" else (),
        tmp_path / "attempts",
        fixture=fixture,
    )
    tuples = {
        (event["service"], event["method"], event["version"]) for event in result.batch.events
    }
    assert tuples == {("GoogleAdsService", "Search", "v22")}
    assert result.manifest["engine"].endswith("rootless=true")
    assert result.manifest["image"]
    if mode == "proxy":
        raw = json.loads(result.artifacts["raw-events.jsonl"])
        assert raw["policy"] == "ALLOW"
        assert raw["policy_reason"] == "FIXTURE_DESTINATION"
        identity = result.manifest["proxy"]["fixture"]
        assert identity["hostname"] == "fixture-service"
        assert identity["port"] == 8443
        assert all(identity[field] for field in ("address", "container_id", "network_id"))
        commands = [
            json.loads(line) for line in result.artifacts["proxy-commands.jsonl"].splitlines()
        ]
        assert commands
        assert all("duration_seconds" in command for command in commands)


@pytest.mark.skipif(not rootless_available(), reason="rootless Podman is unavailable")
def test_capture_container_does_not_inherit_host_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = repository(tmp_path)
    closure = source_closure.build(target)
    monkeypatch.setenv("HUBBLEOPS_HOST_SECRET", "must-not-cross")
    result = capture.execute(
        target,
        closure.repo_sha,
        registry.load_pack("google_ads"),
        'test -z "${HUBBLEOPS_HOST_SECRET:-}"',
        "python",
        "hook",
        (),
        tmp_path / "attempts",
    )
    assert result.manifest["outcome"] == "COMPLETED"


@pytest.mark.skipif(not rootless_available(), reason="rootless Podman is unavailable")
@pytest.mark.parametrize(
    ("language", "command"),
    (("php", "php probe.php"), ("node", "node probe.cjs")),
)
def test_php_and_node_hook_loaders_emit_the_shared_schema(
    tmp_path: Path, language: str, command: str
) -> None:
    target = repository(tmp_path)
    closure = source_closure.build(target)
    result = capture.execute(
        target,
        closure.repo_sha,
        registry.load_pack("google_ads"),
        command,
        language,
        "hook",
        (),
        tmp_path / "attempts",
    )
    assert result.manifest["outcome"] == "COMPLETED"
    assert result.manifest["hook_installed"] is True
    assert [event["version"] for event in result.batch.events] == ["v23"]


@pytest.mark.skipif(not rootless_available(), reason="rootless Podman is unavailable")
def test_hook_capture_records_the_injected_wrapper_chain(tmp_path: Path) -> None:
    target = repository(tmp_path)
    closure = source_closure.build(target)
    result = capture.execute(
        target,
        closure.repo_sha,
        registry.load_pack("google_ads"),
        "python di_probe.py",
        "python",
        "hook",
        (),
        tmp_path / "attempts",
    )
    assert result.manifest["outcome"] == "COMPLETED"
    assert result.manifest["hook_installed"] is True
    event = next(item for item in result.batch.events if item["version"] == "v22")
    chain = [
        (frame["path"], frame["function"])
        for frame in event["stack"]
        if frame["kind"] == "repository"
    ]
    assert chain == [
        ("di_probe.py", "send"),
        ("di_probe.py", "search"),
        ("di_probe.py", "daily_campaign_report"),
        ("di_probe.py", "main"),
        ("di_probe.py", "<module>"),
    ]


@pytest.mark.skipif(not rootless_available(), reason="rootless Podman is unavailable")
def test_a_loader_that_never_runs_is_not_reported_as_absence_of_usage(tmp_path: Path) -> None:
    target = repository(tmp_path)
    closure = source_closure.build(target)
    result = capture.execute(
        target,
        closure.repo_sha,
        registry.load_pack("google_ads"),
        "php -r 'echo 1;'",
        "php",
        "hook",
        (),
        tmp_path / "attempts",
    )
    codes = {issue["code"] for issue in result.manifest["issues"]}
    assert result.manifest["hook_installed"] is False
    assert "HOOK_NOT_INSTALLED" in codes
    assert "UNKNOWN_DYNAMIC" not in codes


@pytest.mark.skipif(not rootless_available(), reason="rootless Podman is unavailable")
def test_capture_cli_persists_a_scoped_ledger_and_promotes_observed_wrapper(
    tmp_path: Path,
) -> None:
    target = repository(tmp_path)
    state = tmp_path / "state"
    code = main(
        [
            "capture",
            str(target),
            "--pack",
            "google_ads",
            "--cmd",
            "python probe.py",
            "--capture-mode",
            "hook",
            "--state-dir",
            str(state),
        ]
    )
    assert code == EXIT_OK
    with Store(state) as store:
        run = store.latest_run("google_ads")
        assert run is not None
        dynamic_candidate = next(
            candidate
            for candidate in store.candidates_for(run.run_id)
            if "OBSERVED_NOT_STATIC" in candidate["reason"]
        )
    assert (
        main(
            [
                "promote",
                str(target),
                "--run",
                run.run_id,
                "--candidate",
                str(dynamic_candidate["id"]),
                "--state-dir",
                str(state),
            ]
        )
        == EXIT_OK
    )
    assert target.joinpath(".hubbleops", "surface.yml").is_file()
    artifact_names = {path.name for path in state.joinpath("artifacts", run.run_id).iterdir()}
    assert {
        "command.json",
        "engine-commands.jsonl",
        "events.jsonl",
        "execution-manifest.json",
        "ledger.json",
        "proxy-commands.jsonl",
    } <= artifact_names
