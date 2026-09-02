from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from hubbleops.core.errors import PackNotFound
from hubbleops.core.surface import SurfaceSpec

PACKS_ROOT = Path(__file__).resolve().parent.parent / "packs"
SURFACE_FILENAME = "surface.yaml"


@dataclass(frozen=True, slots=True)
class LoadedPack:
    name: str
    root: Path
    surface: SurfaceSpec


def available_packs() -> tuple[str, ...]:
    if not PACKS_ROOT.is_dir():
        return ()
    return tuple(
        sorted(
            child.name
            for child in PACKS_ROOT.iterdir()
            if child.is_dir() and (child / SURFACE_FILENAME).is_file()
        )
    )


def load_pack(name: str) -> LoadedPack:
    if name != Path(name).name or name in ("", ".", ".."):
        raise PackNotFound(f"{name!r} is not a pack name")
    surface_path = PACKS_ROOT / name / SURFACE_FILENAME
    if not surface_path.is_file():
        raise PackNotFound(
            f"no pack named {name!r}; available: {', '.join(available_packs()) or 'none'}"
        )
    data = yaml.safe_load(surface_path.read_text(encoding="utf-8"))
    surface = SurfaceSpec.from_mapping(data)
    if surface.name != name:
        raise PackNotFound(
            f"pack directory {name!r} declares surface name {surface.name!r}; they must match"
        )
    return LoadedPack(name=name, root=PACKS_ROOT / name, surface=surface)
