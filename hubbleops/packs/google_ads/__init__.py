from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from hubbleops.core.surface import SurfaceSpec
from hubbleops.packs._protocol import CaptureHooks, Falsifier, RuleSet, ToolSpec, Transform, Version
from hubbleops.packs.google_ads.changes import CHANGES
from hubbleops.packs.google_ads.contract import CONTRACT
from hubbleops.packs.google_ads.telemetry import TELEMETRY
from hubbleops.packs.google_ads.wire import WIRE_SIGNATURE

ROOT = Path(__file__).resolve().parent


@dataclass(slots=True)
class EmptyBundle:
    language: str
    paths: tuple[Path, ...] = ()


class GoogleAdsPack:
    name = "google_ads"
    surface = SurfaceSpec.from_mapping(yaml.safe_load((ROOT / "surface.yaml").read_text("utf-8")))
    wire_signature = WIRE_SIGNATURE
    contract = CONTRACT
    changes = CHANGES
    telemetry = TELEMETRY

    def versions(self) -> tuple[Version, ...]:
        return self.changes.versions()

    def rules(self, language: str) -> RuleSet:
        return EmptyBundle(language)

    def capture_hooks(self, language: str) -> CaptureHooks:
        return EmptyBundle(language)

    def repair_transforms(self) -> list[Transform]:
        return []

    def repair_tools(self) -> list[ToolSpec]:
        return []

    def falsifiers(self) -> list[Falsifier]:
        return []


PACK = GoogleAdsPack()
