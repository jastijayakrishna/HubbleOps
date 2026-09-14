from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from tests.corpus.shell import run
from tests.corpus.typecheck import python_of

PROBE = """
import json, sys, importlib
version = sys.argv[1]
resources = ["campaign", "ad_group", "customer", "ad_group_ad", "keyword_plan_campaign"]
out = {}
for name in resources:
    parts = "".join(piece.capitalize() for piece in name.split("_"))
    try:
        loaded = importlib.import_module(
            f"google.ads.googleads.{version}.resources.types.{name}"
        )
    except Exception:
        continue
    holder = getattr(loaded, parts, None)
    if holder is None:
        continue
    try:
        out[name] = sorted(field.name for field in holder.meta.fields.values())
    except Exception:
        continue
try:
    common = importlib.import_module(f"google.ads.googleads.{version}.common.types.metrics")
    out["metrics"] = sorted(field.name for field in common.Metrics.meta.fields.values())
except Exception:
    pass
print(json.dumps(out))
"""


@dataclass(frozen=True)
class RemovedField:
    resource: str
    field: str
    source_version: str
    target_version: str

    @property
    def subject(self) -> str:
        return f"{self.resource}.{self.field}"

    def query(self) -> str:
        return f"SELECT {self.resource}.{self.field} FROM {self.resource}"


def probe_fields(venv: Path, version: str, work: Path) -> dict[str, tuple[str, ...]]:
    shown = run((str(python_of(venv)), "-c", PROBE, version), cwd=work, timeout=600.0)
    if not shown.ok:
        return {}
    text = shown.stdout.strip()
    start = text.rfind("{")
    if start == -1:
        return {}
    body: object
    try:
        body = json.loads(text[start:])
    except json.JSONDecodeError:
        return {}
    if not isinstance(body, dict):
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for key, value in cast("dict[object, object]", body).items():
        if not isinstance(value, list):
            continue
        out[str(key)] = tuple(str(entry) for entry in cast("list[object]", value))
    return out


def find_removed(
    source_venv: Path, target_venv: Path, source_version: str, work: Path
) -> tuple[RemovedField | None, str]:
    before = probe_fields(source_venv, source_version, work)
    after = probe_fields(target_venv, "v25", work)
    if not before:
        return (None, f"the source SDK exposes no {source_version} protos to compare")
    if not after:
        return (None, "the target SDK exposes no v25 protos to compare")
    for resource in sorted(before):
        if resource not in after:
            continue
        gone = sorted(set(before[resource]) - set(after[resource]))
        for field in gone:
            return (
                RemovedField(resource, field, source_version, "v25"),
                f"{resource}.{field} is present in {source_version} and absent in v25 according "
                "to Google's own published client library, not to the pack's own change data",
            )
    return (
        None,
        "no field on the probed resources is present in the source version and absent in v25, "
        "so no removed-field GAQL mutation can be planted honestly for this family",
    )
