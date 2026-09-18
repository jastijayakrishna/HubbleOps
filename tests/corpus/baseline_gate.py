from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINES = Path(__file__).resolve().parent / "baselines"
ENGINE_SCOPE = REPO_ROOT / "dev" / "engine-v0.json"
PACK = "google_ads"
GIT_TIMEOUT_SECONDS = 2400.0
SCAN_TIMEOUT_SECONDS = 5400.0
VERSION_TIMEOUT_SECONDS = 60.0
COUNT_KEYS = (
    "total",
    "affected",
    "not_affected_with_evidence",
    "unknown",
    "human_required",
    "excluded_with_evidence",
    "provider_reference_data",
    "unsupported",
    "unscanned",
    "human_accepted_risk",
    "unexplained",
)
PRESERVED_SCOPE_KEYS = ("engine", "tag", "frozen_on", "predecessor", "harness")


class GateFailed(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Pinned:
    slug: str
    repository: str
    url: str
    sha: str


PINNED: tuple[Pinned, ...] = (
    Pinned(
        "tap-google-ads",
        "singer-io/tap-google-ads",
        "https://github.com/singer-io/tap-google-ads.git",
        "5b6a201558875a2859caa04fc992adf892fd30c7",
    ),
    Pinned(
        "dub",
        "dubinc/dub",
        "https://github.com/dubinc/dub.git",
        "b8866f413cec065438d6e5faabbd9dac7d1ceea5",
    ),
    Pinned(
        "google-listings-and-ads",
        "woocommerce/google-listings-and-ads",
        "https://github.com/woocommerce/google-listings-and-ads.git",
        "b43b322771071ed88d5a817422dd222acbaa5f33",
    ),
)


def _run(command: Sequence[str], timeout: float, cwd: Path | None = None) -> str:
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=None if cwd is None else str(cwd),
            check=False,
        )
    except FileNotFoundError as error:
        raise GateFailed(f"TOOLING_MISSING {command[0]}: {error}") from error
    except subprocess.TimeoutExpired as error:
        raise GateFailed(f"UNKNOWN {command[0]} exceeded {timeout:.0f}s") from error
    if completed.returncode != 0:
        tail = (completed.stdout + "\n" + completed.stderr).strip()[-4000:]
        raise GateFailed(f"{' '.join(command)} exited {completed.returncode}\n{tail}")
    return completed.stdout


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _drop_read_only(action: Callable[[str], object], path: str, _error: BaseException) -> None:
    Path(path).chmod(stat.S_IWRITE)
    action(path)


def clone(pin: Pinned, into: Path) -> Path:
    target = into / pin.slug
    if target.exists():
        shutil.rmtree(target, onexc=_drop_read_only)
    target.mkdir(parents=True)
    _run(["git", "init", "--quiet"], GIT_TIMEOUT_SECONDS, target)
    _run(["git", "remote", "add", "origin", pin.url], GIT_TIMEOUT_SECONDS, target)
    _run(
        ["git", "fetch", "--quiet", "--depth", "1", "origin", pin.sha],
        GIT_TIMEOUT_SECONDS,
        target,
    )
    _run(["git", "checkout", "--quiet", "FETCH_HEAD"], GIT_TIMEOUT_SECONDS, target)
    head = _run(["git", "rev-parse", "HEAD"], VERSION_TIMEOUT_SECONDS, target).strip()
    if head != pin.sha:
        raise GateFailed(f"{pin.slug} checked out {head}, expected {pin.sha}")
    return target


def _hops() -> list[str]:
    found = shutil.which("hops")
    if found is not None:
        return [found]
    return [sys.executable, "-m", "hubbleops.app.cli"]


def scan(pin: Pinned, clone_path: Path, state_dir: Path, export: Path) -> dict[str, int]:
    for outside in (state_dir, export):
        if clone_path.resolve() in outside.resolve().parents:
            raise GateFailed(f"{outside} is inside {clone_path}; a scan never writes what it reads")
    state_dir.mkdir(parents=True, exist_ok=True)
    export.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            *_hops(),
            "scan",
            str(clone_path),
            "--pack",
            PACK,
            "--state-dir",
            str(state_dir),
            "--export",
            str(export),
        ],
        SCAN_TIMEOUT_SECONDS,
        REPO_ROOT,
    )
    payload: object = json.loads(export.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise GateFailed(f"{pin.slug} export is not an object")
    document = cast(Mapping[str, object], payload)
    raw = document.get("counts")
    if not isinstance(raw, dict):
        raise GateFailed(f"{pin.slug} export carries no counts")
    counts = cast(Mapping[str, object], raw)
    observed: dict[str, int] = {}
    for key in COUNT_KEYS:
        value = counts.get(key)
        if not isinstance(value, int):
            raise GateFailed(f"{pin.slug} export has no integer count for {key}")
        observed[key] = value
    return observed


def baseline_path(pin: Pinned, directory: Path = BASELINES) -> Path:
    return directory / f"{pin.slug}.json"


def record(pin: Pinned, counts: Mapping[str, int]) -> str:
    document = {
        "repository": pin.repository,
        "slug": pin.slug,
        "sha": pin.sha,
        "pack": PACK,
        "counts": {key: counts[key] for key in COUNT_KEYS},
    }
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def compare(pin: Pinned, counts: Mapping[str, int], directory: Path = BASELINES) -> list[str]:
    path = baseline_path(pin, directory)
    if not path.is_file():
        raise GateFailed(f"BASELINE_MISSING {path}")
    loaded: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise GateFailed(f"{path} is not an object")
    document = cast(Mapping[str, object], loaded)
    if document.get("sha") != pin.sha:
        raise GateFailed(f"{path} pins {document.get('sha')!r}, the runner pins {pin.sha}")
    raw = document.get("counts")
    if not isinstance(raw, dict):
        raise GateFailed(f"{path} carries no counts")
    expected = cast(Mapping[str, object], raw)
    moved: list[str] = []
    for key in COUNT_KEYS:
        before = expected.get(key)
        after = counts[key]
        if before != after:
            moved.append(f"{pin.slug} {key} {before} -> {after}")
    return moved


def _tool(name: str) -> dict[str, str]:
    from hubbleops.core.toolchain import locate

    binary = locate(name)
    return {"origin": binary.origin, "resolved": binary.path, "sha256": binary.sha256}


def _indexers() -> dict[str, dict[str, str]]:
    from hubbleops.core.errors import ToolingFailed, ToolingMissing
    from hubbleops.graph import indexers

    recorded: dict[str, dict[str, str]] = {}
    for runner in indexers.runners():
        try:
            binary = runner.binary()
            recorded[runner.name] = {
                "origin": binary.origin,
                "resolved": binary.path,
                "sha256": binary.sha256,
                "identity": runner.identity(),
            }
        except (ToolingMissing, ToolingFailed) as error:
            recorded[runner.name] = {"status": "TOOLING_MISSING", "detail": str(error)}
    return recorded


def engine_scope() -> dict[str, Any]:
    from hubbleops.app import registry

    existing: object = json.loads(ENGINE_SCOPE.read_text(encoding="utf-8"))
    if not isinstance(existing, dict):
        raise GateFailed(f"{ENGINE_SCOPE} is not an object")
    previous = cast(Mapping[str, Any], existing)
    pack = registry.load_pack(PACK)
    with tempfile.TemporaryDirectory(prefix="hops-engine-scope-") as directory:
        report = pack.changes.build(Path(directory))
    if report.lattice_hash != pack.changes.lattice_hash:
        raise GateFailed("the rebuilt pack lattice differs from the shipped catalogs")
    from hubbleops.sandbox.verifier_image import VERIFIER_REFERENCE

    scope: dict[str, Any] = {key: previous[key] for key in PRESERVED_SCOPE_KEYS if key in previous}
    scope["source"] = {
        "branch": _run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], VERSION_TIMEOUT_SECONDS, REPO_ROOT
        ).strip(),
        "commit": _run(["git", "rev-parse", "HEAD"], VERSION_TIMEOUT_SECONDS, REPO_ROOT).strip(),
        "tree": _run(
            ["git", "rev-parse", "HEAD^{tree}"], VERSION_TIMEOUT_SECONDS, REPO_ROOT
        ).strip(),
        "engine_tree": _run(
            ["git", "rev-parse", "HEAD:hubbleops"], VERSION_TIMEOUT_SECONDS, REPO_ROOT
        ).strip(),
        "pack_tree": _run(
            ["git", "rev-parse", f"HEAD:hubbleops/packs/{PACK}"],
            VERSION_TIMEOUT_SECONDS,
            REPO_ROOT,
        ).strip(),
    }
    scope["pack"] = {
        "name": pack.name,
        "lattice_hash": report.lattice_hash,
        "sources": len(report.source_hashes),
        "catalogs": dict(sorted(report.catalog_hashes.items())),
    }
    scope["toolchain"] = {
        "python": f"{sys.version.split()[0]} {sys.implementation.name}",
        "platform": sys.platform,
        "uv_lock_sha256": _sha256(REPO_ROOT / "uv.lock"),
        "rg": _tool("rg"),
        "ast_grep": _tool("ast-grep"),
        "indexers": _indexers(),
        "verifier_image": VERIFIER_REFERENCE,
    }
    scope["baselines"] = {
        pin.slug: {"repository": pin.repository, "sha": pin.sha} for pin in PINNED
    }
    return scope


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="baseline-gate")
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--baselines-dir", default=str(BASELINES))
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--engine-scope", default=None)
    parser.add_argument("--only", action="append", default=None)
    arguments = parser.parse_args(list(argv) if argv is not None else None)

    work = Path(arguments.work_dir).resolve()
    clones = work / "repos"
    state_root = work / "state"
    exports = work / "exports"
    clones.mkdir(parents=True, exist_ok=True)

    directory = Path(arguments.baselines_dir).resolve()
    selected = [pin for pin in PINNED if arguments.only is None or pin.slug in arguments.only]
    if not selected:
        raise GateFailed("no pinned repository selected")

    moved: list[str] = []
    for pin in selected:
        print(f"CLONE   {pin.repository} {pin.sha[:8]}", flush=True)
        checkout = clone(pin, clones)
        print(f"SCAN    {pin.slug}", flush=True)
        counts = scan(pin, checkout, state_root / pin.slug, exports / f"{pin.slug}.json")
        rendered = " · ".join(f"{key}={counts[key]}" for key in COUNT_KEYS)
        print(f"COUNTS  {pin.slug} {rendered}", flush=True)
        if arguments.record:
            path = baseline_path(pin, directory)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(record(pin, counts), encoding="utf-8", newline="\n")
            print(f"RECORD  {path}", flush=True)
        else:
            moved.extend(compare(pin, counts, directory))

    if arguments.engine_scope is not None:
        destination = Path(arguments.engine_scope).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(engine_scope(), indent=2, sort_keys=True) + "\n"
        destination.write_text(text, encoding="utf-8", newline="\n")
        print(f"SCOPE   {destination}", flush=True)

    if moved:
        for line in moved:
            print(f"MOVED   {line}", flush=True)
        print(f"GATE: FAIL  {len(moved)} count(s) moved", flush=True)
        return 1
    print("GATE: PASS  candidate counts unchanged", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GateFailed as failure:
        print(f"GATE: FAIL  {failure}", file=sys.stderr, flush=True)
        sys.exit(1)
