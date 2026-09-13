from __future__ import annotations

from pathlib import Path
from typing import Any

from hubbleops.app.cli import ScanResult, scan_repository
from hubbleops.app.registry import load_pack

QUERY = "SELECT campaign.id FROM campaign"


def write_repo(root: Path, bridge_name: str, entry: str, wrapper_name: str) -> None:
    root.mkdir()
    (root / bridge_name).write_text(
        "import requests\n\n"
        f"def {wrapper_name}(query: str) -> object:\n"
        '    endpoint = "https://googleads.googleapis.com/v24/customers/1/googleAds:search"\n'
        '    return requests.post(endpoint, json={"query": query})\n',
        encoding="utf-8",
    )
    (root / "entry.py").write_text(entry, encoding="utf-8")


def semantics(result: ScanResult) -> dict[str, set[tuple[Any, ...]]]:
    candidates = {item["id"]: item for item in result.ledger.candidates}
    requests: set[tuple[Any, ...]] = set()
    versions: set[tuple[Any, ...]] = set()
    for evidence in result.ledger.evidence:
        if evidence["observer"] != "structure":
            continue
        attached = next(
            candidate
            for candidate in candidates.values()
            if evidence["id"] in candidate["evidence_ids"]
        )
        if evidence["claim_type"] == "request_text":
            skeleton = evidence["value"]["skeleton"]
            requests.add(
                (
                    attached["status"],
                    evidence["provider_subject"],
                    "".join(skeleton["fragments"]),
                    tuple(skeleton["holes"]),
                    evidence["value"]["resolution"],
                )
            )
        if evidence["claim_type"] == "call_version":
            versions.add((attached["status"], evidence["provider_subject"]))
    return {"requests": requests, "versions": versions}


def test_wrapper_rename_move_query_split_and_intermediate_hop_preserve_semantics(
    tmp_path: Path,
) -> None:
    repositories = {
        "base": (
            "bridge.py",
            f'from bridge import wrapper\n\ndef run():\n    return wrapper("{QUERY}")\n',
            "wrapper",
        ),
        "renamed": (
            "bridge.py",
            f'from bridge import relay_query\n\ndef run():\n    return relay_query("{QUERY}")\n',
            "relay_query",
        ),
        "moved": (
            "transport.py",
            f'from transport import wrapper\n\ndef run():\n    return wrapper("{QUERY}")\n',
            "wrapper",
        ),
        "split": (
            "bridge.py",
            "from bridge import wrapper\n\n"
            "def run():\n"
            '    prefix = "SELECT campaign.id "\n'
            '    suffix = "FROM campaign"\n'
            "    return wrapper(prefix + suffix)\n",
            "wrapper",
        ),
        "hop": (
            "bridge.py",
            f"from bridge import wrapper\n\n"
            "def intermediate(query: str):\n"
            "    return wrapper(query)\n\n"
            "def run():\n"
            f'    return intermediate("{QUERY}")\n',
            "wrapper",
        ),
    }
    observed: dict[str, dict[str, set[tuple[Any, ...]]]] = {}
    for name, (bridge_name, entry, wrapper_name) in repositories.items():
        root = tmp_path / name
        write_repo(root, bridge_name, entry, wrapper_name)
        observed[name] = semantics(scan_repository(root, load_pack("google_ads")))
    assert all(value == observed["base"] for value in observed.values())
    assert observed["base"]["requests"] == {
        ("AFFECTED", "gaql", QUERY, (), "CONTRACT_VALIDATION_DEFERRED")
    }
    assert observed["base"]["versions"] == {("AFFECTED", "v24")}
