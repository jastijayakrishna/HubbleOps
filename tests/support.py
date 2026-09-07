from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "hubbleops"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "phase1"

GENERIC_LAYERS = (
    "core",
    "closure",
    "observe",
    "graph",
    "obligations",
    "verify",
    "proof",
    "store",
    "sandbox",
)

PHASE_ONE_LAYERS = ("core", "closure", "observe", "store")


def fixture_repos() -> list[Path]:
    return sorted(path for path in FIXTURE_ROOT.iterdir() if (path / "repo").is_dir())


def generic_layer_dirs() -> list[Path]:
    return [PACKAGE_ROOT / name for name in GENERIC_LAYERS if (PACKAGE_ROOT / name).is_dir()]


def python_sources(directory: Path) -> list[Path]:
    return sorted(directory.rglob("*.py"))


def module_layer(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) < 2 or parts[0] != "hubbleops":
        return None
    return parts[1]


def internal_imports() -> list[tuple[str, str, Path]]:
    edges: list[tuple[str, str, Path]] = []
    for source in python_sources(PACKAGE_ROOT):
        relative = source.relative_to(PACKAGE_ROOT)
        if len(relative.parts) < 2:
            continue
        origin = relative.parts[0]
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            for module in modules:
                target = module_layer(module)
                if target is None or target == origin:
                    continue
                edges.append((origin, module, source))
    return edges


def layer_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for origin, module, _ in internal_imports():
        target = module_layer(module)
        if target is None:
            continue
        graph.setdefault(origin, set()).add(target)
    return graph


def import_cycles() -> list[tuple[str, ...]]:
    graph = layer_graph()
    found: set[tuple[str, ...]] = set()

    def walk(node: str, path: list[str]) -> None:
        for nxt in sorted(graph.get(node, set())):
            if nxt in path:
                found.add(tuple([*path[path.index(nxt) :], nxt]))
                continue
            walk(nxt, [*path, nxt])

    for start in sorted(graph):
        walk(start, [start])
    return sorted(found)
