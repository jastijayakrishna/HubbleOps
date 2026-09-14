from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from tests.corpus.shell import run
from tests.corpus.spec import CorpusError, read_json


@dataclass(frozen=True)
class EngineScope:
    tag: str
    commit: str
    engine_tree: str
    pack_tree: str
    pack_name: str
    lattice_hash: str
    python: str
    uv_lock_sha256: str
    verifier_image: str
    tools: dict[str, str]


def load_scope(path: Path) -> EngineScope:
    head = read_json(path)
    where = str(path)
    source = head.get("source")
    pack = head.get("pack")
    toolchain = head.get("toolchain")
    if (
        not isinstance(source, dict)
        or not isinstance(pack, dict)
        or not isinstance(toolchain, dict)
    ):
        raise CorpusError(f"{where}: source, pack and toolchain are all required")
    src = {str(k): v for k, v in cast("dict[object, object]", source).items()}
    pk = {str(k): v for k, v in cast("dict[object, object]", pack).items()}
    tc = {str(k): v for k, v in cast("dict[object, object]", toolchain).items()}
    tools: dict[str, str] = {}
    for name in ("rg", "ast_grep"):
        entry = tc.get(name)
        if isinstance(entry, dict):
            body = {str(k): v for k, v in cast("dict[object, object]", entry).items()}
            value = body.get("sha256")
            if isinstance(value, str):
                tools[name] = value
    return EngineScope(
        tag=str(head.get("tag", "")),
        commit=str(src.get("commit", "")),
        engine_tree=str(src.get("engine_tree", "")),
        pack_tree=str(src.get("pack_tree", "")),
        pack_name=str(pk.get("name", "")),
        lattice_hash=str(pk.get("lattice_hash", "")),
        python=str(tc.get("python", "")),
        uv_lock_sha256=str(tc.get("uv_lock_sha256", "")),
        verifier_image=str(tc.get("verifier_image", "")),
        tools=tools,
    )


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class ScopeCheck:
    ok: bool
    engine_tree: str
    pack_tree: str
    uv_lock: str
    tools: dict[str, str]
    problems: tuple[str, ...]


def check_scope(scope: EngineScope, engine_root: Path) -> ScopeCheck:
    problems: list[str] = []
    if not (engine_root / "hubbleops").is_dir():
        problems.append(f"{engine_root} holds no hubbleops package")
    engine_tree = run(("git", "rev-parse", "HEAD:hubbleops"), cwd=engine_root, timeout=120.0)
    pack_tree = run(
        ("git", "rev-parse", f"HEAD:hubbleops/packs/{scope.pack_name}"),
        cwd=engine_root,
        timeout=120.0,
    )
    found_engine = engine_tree.stdout.strip() if engine_tree.ok else ""
    found_pack = pack_tree.stdout.strip() if pack_tree.ok else ""
    if found_engine != scope.engine_tree:
        problems.append(
            f"engine tree is {found_engine or 'unreadable'}, the scope pins {scope.engine_tree}"
        )
    if scope.pack_tree and found_pack and found_pack != scope.pack_tree:
        problems.append(f"pack tree is {found_pack}, the scope pins {scope.pack_tree}")
    lock = engine_root / "uv.lock"
    lock_hash = sha256_of(lock) if lock.is_file() else ""
    if lock_hash != scope.uv_lock_sha256:
        problems.append(
            f"uv.lock is {lock_hash or 'absent'}, the scope pins {scope.uv_lock_sha256}"
        )
    tool_hashes: dict[str, str] = {}
    for name, want in scope.tools.items():
        binary = "rg" if name == "rg" else "ast-grep"
        found = shutil.which(binary)
        if found is None:
            tool_hashes[binary] = ""
            problems.append(f"{binary} is not on PATH; the scope pins {want}")
            continue
        got = sha256_of(Path(found))
        tool_hashes[binary] = got
        if got != want:
            problems.append(f"{binary} hashes {got}, the scope pins {want}")
    return ScopeCheck(
        ok=not problems,
        engine_tree=found_engine,
        pack_tree=found_pack,
        uv_lock=lock_hash,
        tools=tool_hashes,
        problems=tuple(problems),
    )


def materialise(scope: EngineScope, repo_root: Path, engine_root: Path) -> tuple[str, ...]:
    if (engine_root / "hubbleops").is_dir():
        return ()
    engine_root.parent.mkdir(parents=True, exist_ok=True)
    added = run(
        ("git", "worktree", "add", "--detach", str(engine_root), scope.commit or scope.tag),
        cwd=repo_root,
        timeout=900.0,
    )
    if not added.ok:
        return (f"git worktree add failed: {added.tail(400)}",)
    synced = run(("uv", "sync", "--all-packages"), cwd=engine_root, timeout=1800.0)
    if not synced.ok:
        return (f"uv sync failed in the engine worktree: {synced.tail(400)}",)
    return ()
