import ast
import json
from pathlib import Path

import pytest
from hubbleops_sentinel.cli import main
from hubbleops_sentinel.events import CORPUS, SCHEMA, EventInvalid, normalize
from hubbleops_sentinel.wire import parse

FIXTURES = Path(__file__).parents[3] / "tests" / "fixtures" / "phase4" / "inputs"


def test_wire_conformance() -> None:
    for case in json.loads(CORPUS.read_bytes()):
        assert parse(case["path"], case["headers"]) == case["result"]


def test_proxy_output_and_manifest(tmp_path: Path) -> None:
    source = tmp_path / "input.jsonl"
    source.write_text(
        json.dumps(
            {
                "path": "/v25/customers/123/googleAds:search",
                "headers": {},
                "request_text": "SELECT campaign.id FROM campaign",
                "request_type": "rest",
                "stack": [],
                "ts": "2026-09-06T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "events.jsonl"
    assert main(["proxy", "--input", str(source), "--output", str(output)]) == 0
    assert json.loads(output.read_text("utf-8"))["mode"] == "proxy"
    assert output.with_name("events.jsonl.manifest.json").is_file()


def test_hook_installs_the_independent_adapter_and_restores_http_client(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text(
        "import http.client\n"
        "connection = http.client.HTTPConnection('127.0.0.1', 9, timeout=0.01)\n"
        "try:\n"
        "    connection.request('POST', "
        "'/google.ads.googleads.v24.services.GoogleAdsService/Search')\n"
        "except OSError:\n"
        "    pass\n",
        encoding="utf-8",
    )
    output = tmp_path / "events.jsonl"
    assert main(["hook", "--input", str(source), "--output", str(output)]) == 0
    event = json.loads(output.read_text("utf-8"))
    assert (event["service"], event["method"], event["version"]) == (
        "GoogleAdsService",
        "Search",
        "v24",
    )
    assert event["mode"] == "hook"
    repository_frames = [frame for frame in event["stack"] if frame["kind"] == "repository"]
    assert repository_frames
    assert repository_frames[-1]["path"] == "app.py"


def test_hook_accepts_an_explicit_repository_root(tmp_path: Path) -> None:
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text(
        "import http.client\n"
        "connection = http.client.HTTPConnection('127.0.0.1', 9, timeout=0.01)\n"
        "try:\n"
        "    connection.request('POST', "
        "'/google.ads.googleads.v24.services.GoogleAdsService/Search')\n"
        "except OSError:\n"
        "    pass\n",
        encoding="utf-8",
    )
    output = tmp_path / "events.jsonl"
    assert (
        main(
            [
                "hook",
                "--input",
                str(source),
                "--output",
                str(output),
                "--repo-root",
                str(tmp_path),
            ]
        )
        == 0
    )
    event = json.loads(output.read_text("utf-8"))
    repository_frames = [frame for frame in event["stack"] if frame["kind"] == "repository"]
    assert repository_frames[-1]["path"] == "src/app.py"


def test_both_modes_smoke_the_shipped_fixtures(tmp_path: Path) -> None:
    proxy_output = tmp_path / "proxy.jsonl"
    assert (
        main(
            [
                "proxy",
                "--input",
                str(FIXTURES / "sentinel_proxy_input.jsonl"),
                "--output",
                str(proxy_output),
            ]
        )
        == 0
    )
    proxy_events = [json.loads(line) for line in proxy_output.read_text("utf-8").splitlines()]
    assert {(item["method"], item["version"]) for item in proxy_events} == {
        ("Search", "v25"),
        ("SearchStream", "v25"),
        ("Mutate", "v24"),
    }
    assert all(item["mode"] == "proxy" for item in proxy_events)

    hook_output = tmp_path / "hook.jsonl"
    assert (
        main(
            [
                "hook",
                "--input",
                str(FIXTURES / "sentinel_hook_app.py"),
                "--output",
                str(hook_output),
            ]
        )
        == 0
    )
    hook_events = [json.loads(line) for line in hook_output.read_text("utf-8").splitlines()]
    assert [(item["method"], item["version"]) for item in hook_events] == [("Search", "v25")]
    assert all(item["mode"] == "hook" for item in hook_events)


def test_a_failed_url_export_is_reported_not_swallowed(tmp_path: Path) -> None:
    source = tmp_path / "input.jsonl"
    source.write_bytes((FIXTURES / "sentinel_proxy_input.jsonl").read_bytes())
    output = tmp_path / "events.jsonl"
    code = main(
        [
            "proxy",
            "--input",
            str(source),
            "--output",
            str(output),
            "--export-url",
            "http://127.0.0.1:9/ingest",
            "--timeout",
            "1",
        ]
    )
    assert code == 5
    assert output.is_file()


def test_an_export_url_that_is_not_http_is_refused(tmp_path: Path) -> None:
    output = tmp_path / "events.jsonl"
    code = main(
        [
            "proxy",
            "--input",
            str(FIXTURES / "sentinel_proxy_input.jsonl"),
            "--output",
            str(output),
            "--export-url",
            "file:///etc/passwd",
        ]
    )
    assert code == 5


def test_a_malformed_record_is_named_and_never_silently_dropped(tmp_path: Path) -> None:
    source = tmp_path / "input.jsonl"
    source.write_bytes(b"not-json\n\xff\xfe\x00bad\n")
    output = tmp_path / "events.jsonl"
    assert main(["proxy", "--input", str(source), "--output", str(output)]) == 5
    manifest = json.loads(output.with_name("events.jsonl.manifest.json").read_text("utf-8"))
    assert [issue["code"] for issue in manifest["issues"]] == ["EVENT_INVALID", "EVENT_INVALID"]
    assert output.read_bytes() == b""


def test_a_truncated_stack_is_retained_and_named(tmp_path: Path) -> None:
    frames: list[dict[str, object]] = [
        {"kind": "runtime", "path": "<runtime>/frame", "line": 1, "function": "call"}
        for _ in range(255)
    ]
    frames.append(
        {
            "kind": "truncation",
            "path": "<runtime>/truncated",
            "line": None,
            "function": None,
            "omitted": 4,
        }
    )
    source = tmp_path / "input.jsonl"
    source.write_text(
        json.dumps(
            {
                "path": "/v25/customers/123/googleAds:search",
                "headers": {},
                "request_text": None,
                "request_type": "rest",
                "stack": frames,
                "ts": "2026-09-06T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "events.jsonl"
    assert main(["proxy", "--input", str(source), "--output", str(output)]) == 5
    manifest = json.loads(output.with_name("events.jsonl.manifest.json").read_text("utf-8"))
    assert manifest["issues"] == [
        {"code": "STACK_TRUNCATED", "reason": "event stack exceeded frame bound", "row": 1}
    ]
    assert json.loads(output.read_text("utf-8"))["stack"][-1]["omitted"] == 4


def test_the_sensor_emits_observations_and_never_a_verdict(tmp_path: Path) -> None:
    output = tmp_path / "events.jsonl"
    main(
        [
            "proxy",
            "--input",
            str(FIXTURES / "sentinel_proxy_input.jsonl"),
            "--output",
            str(output),
        ]
    )
    manifest_text = output.with_name("events.jsonl.manifest.json").read_text("utf-8")
    forbidden = ("VERIFIED_FOR_SCOPE", "HUMAN_REQUIRED", "FAILED", "AFFECTED", "status", "verdict")
    assert not any(word in output.read_text("utf-8") for word in forbidden)
    assert not any(word in manifest_text for word in forbidden)


def test_the_schema_rejects_anything_outside_its_declared_revision() -> None:
    document = json.loads(SCHEMA.read_bytes())
    assert document["properties"]["mode"]["enum"] == ["hook", "proxy"]
    with pytest.raises(EventInvalid):
        normalize({"version": "v25"}, "proxy")
    with pytest.raises(EventInvalid):
        normalize(
            {
                "version": "v25",
                "service": "GoogleAdsService",
                "method": "Search",
                "request_text": None,
                "request_type": "rest",
                "stack": [],
                "ts": "2026-09-06T00:00:00Z",
                "mode": "hook",
            },
            "proxy",
        )


def test_the_package_never_imports_the_engine_it_reports_to() -> None:
    root = Path(__file__).parents[1] / "src"
    offences: list[str] = []
    for source in sorted(root.rglob("*.py")):
        tree = ast.parse(source.read_text("utf-8"), filename=str(source))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offences.extend(
                f"{source.name} imports {name}"
                for name in names
                if name == "hubbleops" or name.startswith("hubbleops.")
            )
    assert offences == []
