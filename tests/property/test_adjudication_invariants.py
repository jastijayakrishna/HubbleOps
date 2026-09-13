from __future__ import annotations

from pathlib import Path
from typing import Any

from hubbleops.app.cli import scan_repository
from hubbleops.app.registry import load_pack

BASE = {
    "src/Client.php": (
        "<?php\n"
        "namespace Shop\\Ads;\n"
        "\n"
        "class Client {\n"
        "\tpublic function send( string $payload ): string {\n"
        "\t\treturn $payload;\n"
        "\t}\n"
        "}\n"
    ),
}

PROVIDER_IMPORT = (
    "<?php\n"
    "namespace Shop\\Ads;\n"
    "\n"
    "use Google\\Ads\\GoogleAds\\V22\\Services\\SearchGoogleAdsRequest;\n"
    "\n"
    "class Reporter {\n"
    "\tpublic function build() {\n"
    "\t\treturn new SearchGoogleAdsRequest();\n"
    "\t}\n"
    "}\n"
)

COMMENTED = (
    "<?php\n"
    "namespace Shop\\Ads;\n"
    "\n"
    "/**\n"
    " * Talks to GoogleAdsService one day, via GoogleAdsClient.\n"
    " */\n"
    "class Documented {\n"
    "\tpublic function describe(): string {\n"
    "\t\treturn 'nothing';\n"
    "\t}\n"
    "}\n"
)

STYLED = ".shop-google-ads-banner {\n\tcolor: red;\n}\n"


def write_tree(root: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def scan(root: Path, **kwargs: Any) -> Any:
    return scan_repository(root, load_pack("google_ads"), **kwargs)


def statuses(result: Any) -> dict[str, str]:
    return {item["id"]: item["status"] for item in result.ledger.ordered_candidates()}


def test_a_comment_naming_the_provider_never_increases_affected(tmp_path: Path) -> None:
    plain = scan(write_tree(tmp_path / "plain", dict(BASE)))
    documented = scan(
        write_tree(tmp_path / "documented", {**BASE, "src/Documented.php": COMMENTED})
    )

    assert documented.ledger.counts()["affected"] == plain.ledger.counts()["affected"]
    assert documented.ledger.counts()["unexplained"] == 0


def test_a_stylesheet_class_carrying_the_provider_name_never_creates_provider_usage(
    tmp_path: Path,
) -> None:
    plain = scan(write_tree(tmp_path / "plain", dict(BASE)))
    styled = scan(write_tree(tmp_path / "styled", {**BASE, "assets/banner.scss": STYLED}))

    assert styled.ledger.counts()["affected"] == plain.ledger.counts()["affected"]
    assert (
        styled.ledger.counts()["not_affected_with_evidence"]
        == plain.ledger.counts()["not_affected_with_evidence"] + 1
    )
    carried = [
        candidate
        for candidate in styled.ledger.ordered_candidates()
        if styled.ledger.location_of(candidate).path == "assets/banner.scss"
        and styled.ledger.location_of(candidate).claim_type == "surface_reference"
    ]
    assert [candidate["status"] for candidate in carried] == ["NOT_AFFECTED_WITH_EVIDENCE"]
    assert all("STYLESHEET" in candidate["reason"] for candidate in carried)
    assert styled.ledger.counts()["unexplained"] == 0


def test_importing_a_provider_symbol_resolves_the_uses_bound_to_it(tmp_path: Path) -> None:
    plain = scan(write_tree(tmp_path / "plain", dict(BASE)))
    imported = scan(
        write_tree(tmp_path / "imported", {**BASE, "src/Reporter.php": PROVIDER_IMPORT})
    )

    assert imported.ledger.counts()["affected"] > plain.ledger.counts()["affected"]
    bound = [
        record
        for record in imported.ledger.evidence
        if record["observer"] == "structure"
        and record["claim_type"] == "surface_reference"
        and record["value"]["binding_versions"]
    ]
    assert bound
    assert {record["line_start"] for record in bound} >= {4, 8}


def test_losing_the_structural_layer_may_raise_unknown_and_never_lowers_a_verdict(
    tmp_path: Path,
) -> None:
    root = write_tree(
        tmp_path / "both", {**BASE, "src/Reporter.php": PROVIDER_IMPORT, "src/Doc.php": COMMENTED}
    )
    adjudicated = scan(root)
    recall_only = scan(root, force=True, ast_grep_executable="hubbleops-no-such-analyzer")

    adjudicated_status = statuses(adjudicated)
    recall_status = statuses(recall_only)
    assert recall_only.ledger.counts()["unknown"] >= adjudicated.ledger.counts()["unknown"]
    for identifier, status in recall_status.items():
        if status == "AFFECTED":
            assert adjudicated_status.get(identifier) != "NOT_AFFECTED_WITH_EVIDENCE"


def test_the_structural_layer_may_resolve_candidates_and_never_lose_one(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path / "both", {**BASE, "src/Reporter.php": PROVIDER_IMPORT, "src/Doc.php": COMMENTED}
    )
    adjudicated = scan(root)
    recall_only = scan(root, force=True, ast_grep_executable="hubbleops-no-such-analyzer")

    adjudicated_status = statuses(adjudicated)
    tooling = [
        item
        for item in recall_only.ledger.ordered_candidates()
        if item["status"] == "UNSCANNED" and "TOOLING_MISSING" in item["reason"]
    ]
    assert len(tooling) == 3
    absent = [
        item["id"]
        for item in recall_only.ledger.ordered_candidates()
        if item["id"] not in adjudicated_status and item not in tooling
    ]
    assert absent == []
    assert adjudicated.ledger.counts()["unexplained"] == 0
    assert recall_only.ledger.counts()["unexplained"] == 0


def test_no_candidate_leaves_unknown_without_a_new_evidence_record(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path / "both", {**BASE, "src/Reporter.php": PROVIDER_IMPORT, "src/Doc.php": COMMENTED}
    )
    adjudicated = scan(root)
    records = {record["id"]: record for record in adjudicated.ledger.evidence}

    for item in adjudicated.ledger.ordered_candidates():
        attached = [records[identifier] for identifier in item["evidence_ids"]]
        surface = [record for record in attached if record["claim_type"] == "surface_reference"]
        if not surface:
            continue
        if item["status"] in ("AFFECTED", "NOT_AFFECTED_WITH_EVIDENCE"):
            assert any(record["observer"] == "structure" for record in surface)


def test_one_observation_never_becomes_two_independent_unresolved_candidates(
    tmp_path: Path,
) -> None:
    root = write_tree(
        tmp_path / "dual",
        {**BASE, "src/Note.php": "<?php\n$label = 'google-ads';\n"},
    )
    result = scan(root)

    located: dict[tuple[str, int | None, str | None], set[str]] = {}
    for record in result.ledger.evidence:
        if record["observer"] != "text":
            continue
        key = (str(record["path"]), record["line_start"], record["provider_subject"])
        located.setdefault(key, set()).add(str(record["claim_type"]))
    assert all(len(kinds) == 1 for kinds in located.values())
