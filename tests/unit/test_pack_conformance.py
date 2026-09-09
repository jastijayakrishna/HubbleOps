from pathlib import Path

import pytest

from hubbleops.app.registry import load_pack
from hubbleops.core.errors import PackDataError
from hubbleops.packs._protocol import (
    CaptureHooks,
    ChangeCompiler,
    ContractOracle,
    Falsifier,
    ProviderPack,
    RuleSet,
    TelemetryAdapter,
    Transform,
    WireSignature,
)


@pytest.mark.parametrize("name", ["google_ads", "_mock"])
def test_full_provider_contract(name: str, tmp_path: Path) -> None:
    pack = load_pack(name)
    assert isinstance(pack.implementation, ProviderPack)
    assert isinstance(pack.changes, ChangeCompiler)
    assert isinstance(pack.contract, ContractOracle)
    assert isinstance(pack.telemetry, TelemetryAdapter)
    assert isinstance(pack.wire_signature, WireSignature)
    assert isinstance(pack.rules("python"), RuleSet)
    assert isinstance(pack.capture_hooks("python"), CaptureHooks)
    assert pack.rules("unknown-language").paths == ()
    assert pack.capture_hooks("unknown-language").paths == ()
    assert pack.repair_tools() == [], "ToolSpec stays deferred until a PROVIDER_TOOL class ships"
    repairs = pack.repair_transforms()
    assert repairs, "a pack with no transform leaves every DETERMINISTIC obligation undischarged"
    assert all(isinstance(item, Transform) for item in repairs)
    assert len({item.name for item in repairs}) == len(repairs)
    assert all(item.failure_class for item in repairs)
    falsifiers = pack.falsifiers()
    assert falsifiers, "a pack with no falsifier makes falsifiers_pass vacuous"
    assert all(isinstance(item, Falsifier) for item in falsifiers)
    assert len({item.name for item in falsifiers}) == len(falsifiers)
    assert all(item.failure_class for item in falsifiers)
    versions = pack.versions()
    assert len(versions) >= 2
    for version in versions:
        catalog = pack.contract.catalog(version.id)
        assert catalog.sha256 == version.catalog_hash
        assert catalog.facts
        assert all(
            fact.source_url and len(fact.sha256) == 64 and fact.retrieved_at
            for fact in catalog.facts
        )
    diff = pack.contract.diff(versions[0].id, versions[-1].id)
    assert diff.facts
    assert pack.contract.diff(versions[0].id, versions[-1].id) == diff
    assert pack.wire_signature.parse("", {}).code == "UNKNOWN_WIRE_SIGNATURE"
    assert pack.telemetry.parse("not,a,method\n1,2,3\n").issues
    report = pack.changes.build(tmp_path)
    assert report == pack.changes.verify()
    assert report.lattice_hash == pack.changes.lattice_hash
    with pytest.raises(PackDataError):
        pack.contract.catalog("v99999")
