import dataclasses
import json
from pathlib import Path

import pytest

from hubbleops.app.registry import load_pack
from hubbleops.core.canonical import blob_hash, canonical_text, content_id
from hubbleops.core.surface import SurfaceSpec
from hubbleops.packs.google_ads.changes import DATA_ROOT, GoogleAdsChanges


def test_every_surface_field_reaches_composite_contract_hash() -> None:
    pack = load_pack("google_ads")
    original = pack.contract_hash()
    for field in dataclasses.fields(SurfaceSpec):
        current = getattr(pack.surface, field.name)
        altered = f"{current}_variant" if isinstance(current, str) else (*current, current[0])
        variant = dataclasses.replace(
            pack, surface=dataclasses.replace(pack.surface, **{field.name: altered})
        )
        assert variant.contract_hash() != original, field.name


def test_every_lattice_node_reaches_composite_contract_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = load_pack("google_ads")
    original = pack.contract_hash()
    versions = [item.to_mapping() for item in pack.versions()]
    for index, node in enumerate(versions):
        altered = [dict(item) for item in versions]
        path = DATA_ROOT / f"catalog_{node['id']}.jsonl"
        altered[index]["catalog_hash"] = blob_hash(path.read_bytes() + b" ")
        changed = content_id(altered)
        monkeypatch.setattr(
            GoogleAdsChanges, "lattice_hash", property(lambda _self, value=changed: value)
        )
        assert pack.contract_hash() != original, node["id"]


def test_every_catalog_fact_is_part_of_the_hashed_bytes() -> None:
    for path in Path(DATA_ROOT).glob("catalog_*.jsonl"):
        payload = path.read_bytes()
        lines = payload.splitlines(keepends=True)
        assert b"".join(lines) == payload
        for line in lines:
            record = json.loads(line)
            assert (canonical_text(record) + "\n").encode() == line
            record["attributes"]["mutation_probe"] = True
            assert blob_hash((canonical_text(record) + "\n").encode()) != blob_hash(line)
