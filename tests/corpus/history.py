from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from tests.corpus.mutate import IDENTIFIERS, SKIP_DIRS
from tests.corpus.shell import run
from tests.corpus.spec import Family, Label, normalise_path

VERSION_TOKEN = re.compile(r"(?<![A-Za-z0-9_.])[vV](\d{2})(?![0-9A-Za-z_])")
SOURCE_RANGE = tuple(f"v{number}" for number in range(19, 25))
MIGRATION_PROBE = (
    r"googleads\.googleapis\.com/v[0-9]+"
    r"|google\.ads\.googleads\.v[0-9]+"
    r"|GoogleAds\\V[0-9]+"
    r"|GOOGLE_ADS_API_VERSION|ADS_API_VERSION|API_VERSION|apiVersion|googleAdsVersion"
)
TEXT_SUFFIX = frozenset(
    {
        ".py",
        ".php",
        ".ts",
        ".tsx",
        ".js",
        ".mjs",
        ".cjs",
        ".json",
        ".yml",
        ".yaml",
        ".toml",
        ".ini",
        ".cfg",
        ".env",
        ".lock",
    }
)
DOCUMENTATION_SUFFIX = frozenset({".md", ".rst", ".txt", ".adoc"})
DOCUMENTATION_STEM = ("changelog", "readme", "contributing", "history", "upgrading", "license")


@dataclass(frozen=True)
class MigrationCommit:
    sha: str
    files: tuple[str, ...]
    from_versions: tuple[str, ...]
    to_versions: tuple[str, ...]


def unshallow(repo: Path) -> bool:
    marker = repo / ".git" / "shallow"
    if not marker.exists():
        return True
    done = run(("git", "fetch", "--unshallow", "--quiet"), cwd=repo, timeout=1800.0)
    return done.ok


def migration_commits(repo: Path, limit: int = 400) -> tuple[MigrationCommit, ...]:
    listed = run(
        ("git", "log", "--format=%H", f"-G{MIGRATION_PROBE}", "--max-count", str(limit)),
        cwd=repo,
        timeout=900.0,
    )
    if not listed.ok:
        return ()
    out: list[MigrationCommit] = []
    for sha in [line.strip() for line in listed.stdout.splitlines() if line.strip()]:
        shown = run(("git", "show", "-U0", "--format=", "--no-color", sha), cwd=repo, timeout=300.0)
        if not shown.ok:
            continue
        current = ""
        touched: dict[str, tuple[set[str], set[str]]] = {}
        for line in shown.stdout.splitlines():
            if line.startswith("+++ b/"):
                current = normalise_path(line[6:])
                continue
            if not current or len(line) < 2 or line[0] not in "+-":
                continue
            if line.startswith(("+++", "---")):
                continue
            versions = {f"v{found.group(1)}" for found in VERSION_TOKEN.finditer(line)}
            if not versions:
                continue
            removed, added = touched.setdefault(current, (set(), set()))
            (added if line[0] == "+" else removed).update(versions)
        files: list[str] = []
        from_versions: set[str] = set()
        to_versions: set[str] = set()
        for path, (removed, added) in touched.items():
            moved = (removed - added) | (added - removed)
            if removed and added and moved:
                files.append(path)
                from_versions |= removed - added
                to_versions |= added - removed
        if files:
            out.append(
                MigrationCommit(
                    sha=sha,
                    files=tuple(sorted(files)),
                    from_versions=tuple(sorted(from_versions)),
                    to_versions=tuple(sorted(to_versions)),
                )
            )
    return tuple(out)


def _mechanism(line: str, path: str) -> str:
    lowered = line.lower()
    if "googleads.googleapis.com" in lowered or "/customers/" in lowered:
        return "endpoint_path"
    if (
        "google.ads.googleads." in lowered
        or "googleads\\v" in lowered
        or "\\googleads\\" in lowered
    ):
        return "namespace_segment"
    if re.search(r"(google_ads_api_version|ads_api_version|api_version|apiversion)", lowered):
        return "config_version"
    if path.endswith(("composer.json", "package.json", "pyproject.toml", "requirements.txt")):
        return "manifest_pin"
    if path.endswith(
        (".lock", "composer.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml")
    ):
        return "lockfile_pin"
    return "version_literal"


def tracked_at(repo: Path, sha: str) -> frozenset[str]:
    listed = run(("git", "ls-tree", "-r", "--name-only", sha), cwd=repo, timeout=600.0)
    if not listed.ok:
        return frozenset()
    return frozenset(normalise_path(line) for line in listed.stdout.splitlines() if line.strip())


def read_at(repo: Path, sha: str, path: str) -> str | None:
    shown = run(("git", "show", f"{sha}:{path}"), cwd=repo, timeout=180.0)
    if not shown.ok:
        return None
    return shown.stdout


UNSETTLED_LINE_CAP = 3000


def unsettled_sites(
    repo: Path, sha: str, paths: frozenset[str], settled: set[tuple[str, int]]
) -> tuple[list[tuple[str, int, str]], list[str]]:
    out: list[tuple[str, int, str]] = []
    capped: list[str] = []
    for path in sorted(paths):
        if any(part in SKIP_DIRS for part in Path(path).parts[:-1]):
            continue
        text = read_at(repo, sha, path)
        if text is None or len(text) > 3_000_000:
            continue
        lines = text.splitlines()
        if len(lines) > UNSETTLED_LINE_CAP:
            capped.append(f"{path} has {len(lines)} lines; only the first {UNSETTLED_LINE_CAP}")
        for number, line in enumerate(lines[:UNSETTLED_LINE_CAP], start=1):
            if not line.strip() or (path, number) in settled:
                continue
            out.append((path, number, line.strip()[:180]))
    return (out, capped)


def is_documentation(path: str) -> bool:
    target = Path(path)
    if target.suffix.lower() in DOCUMENTATION_SUFFIX:
        return True
    return target.stem.lower() in DOCUMENTATION_STEM


LINE_COMMENT = {
    ".py": ("#",),
    ".yml": ("#",),
    ".yaml": ("#",),
    ".toml": ("#",),
    ".ini": ("#", ";"),
    ".cfg": ("#", ";"),
    ".env": ("#",),
    ".php": ("//", "#", "*", "/*"),
    ".ts": ("//", "*", "/*"),
    ".tsx": ("//", "*", "/*"),
    ".js": ("//", "*", "/*"),
    ".mjs": ("//", "*", "/*"),
    ".cjs": ("//", "*", "/*"),
}
DOC_URL = ("developers.google.com", "://docs.", "/docs/", "github.com/")
TRIPLE = ('"""', "'''")


def prose_lines(text: str, suffix: str) -> set[int]:
    out: set[int] = set()
    open_quote = ""
    block_comment = False
    prefixes = LINE_COMMENT.get(suffix, ())
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if suffix == ".py":
            if open_quote:
                out.add(number)
                if open_quote in line:
                    open_quote = ""
                continue
            for quote in TRIPLE:
                if not stripped.startswith(quote):
                    continue
                out.add(number)
                if stripped.count(quote) < 2:
                    open_quote = quote
                break
        else:
            if block_comment:
                out.add(number)
                if "*/" in line:
                    block_comment = False
                continue
            if stripped.startswith("/*") and "*/" not in stripped:
                block_comment = True
                out.add(number)
                continue
        if number in out:
            continue
        if prefixes and any(stripped.startswith(prefix) for prefix in prefixes):
            out.add(number)
            continue
        if any(marker in line for marker in DOC_URL):
            out.add(number)
    return out


def in_trailing_comment(line: str, index: int, suffix: str) -> bool:
    for marker in ("#", "//") if suffix != ".py" else ("#",):
        at = line.find(marker)
        if at != -1 and at < index:
            return True
    return False


def current_sites(
    repo: Path, sha: str, paths: frozenset[str]
) -> tuple[list[tuple[str, int, str, str]], list[tuple[str, int, str]], list[tuple[str, int, str]]]:
    code: list[tuple[str, int, str, str]] = []
    prose: list[tuple[str, int, str]] = []
    undecided: list[tuple[str, int, str]] = []
    for path in sorted(paths):
        suffix = Path(path).suffix
        documentation = is_documentation(path)
        if not documentation and suffix not in TEXT_SUFFIX:
            continue
        if any(part in SKIP_DIRS for part in Path(path).parts[:-1]):
            continue
        text = read_at(repo, sha, path)
        if text is None or len(text) > 3_000_000:
            continue
        if not any(marker in text for marker in IDENTIFIERS):
            continue
        commented: set[int] = set() if documentation else prose_lines(text, suffix)
        for number, line in enumerate(text.splitlines(), start=1):
            for found in VERSION_TOKEN.finditer(line):
                version = f"v{found.group(1)}"
                if version not in SOURCE_RANGE:
                    continue
                if documentation:
                    undecided.append((path, number, line.strip()[:180]))
                elif number in commented or in_trailing_comment(line, found.start(), suffix):
                    prose.append((path, number, line.strip()[:180]))
                else:
                    code.append((path, number, _mechanism(line, path), line.strip()[:180]))
                break
    return (code, prose, undecided)


def build(repo: Path, family: Family) -> tuple[tuple[Label, ...], tuple[str, ...], tuple[str, ...]]:
    commits = migration_commits(repo)
    adjudicated: set[str] = set()
    for commit in commits:
        adjudicated.update(commit.files)
    tracked = tracked_at(repo, family.sha)
    present = frozenset(path for path in adjudicated if path in tracked)
    sites, prose, undecided = current_sites(repo, family.sha, present)
    labels: list[Label] = []
    for index, (path, line, text) in enumerate(prose, start=1):
        labels.append(
            Label(
                label_id=f"S2C-{index:04d}",
                path=path,
                line=line,
                scope="line",
                mechanism="version_literal",
                verdict="CLEAN",
                kind="natural",
                sources=("S2",),
                subject="",
                evidence=f"{path}:{line} = {text}",
            )
        )
    for index, (path, line, mechanism, text) in enumerate(sites, start=1):
        labels.append(
            Label(
                label_id=f"S2-{index:04d}",
                path=path,
                line=line,
                scope="line",
                mechanism=mechanism,
                verdict="ACTIONABLE",
                kind="natural",
                sources=("S2",),
                subject="",
                evidence=f"{path}:{line} = {text}",
            )
        )
    for index, (path, line, text) in enumerate(undecided, start=1):
        labels.append(
            Label(
                label_id=f"S2D-{index:04d}",
                path=path,
                line=line,
                scope="line",
                mechanism="version_literal",
                verdict="UNSETTLED",
                kind="natural",
                sources=("S2",),
                subject="",
                evidence=f"{path}:{line} = {text}",
            )
        )
    settled = {(label.path, label.line) for label in labels}
    unsettled, capped = unsettled_sites(repo, family.sha, present, settled)
    for index, (path, line, text) in enumerate(unsettled, start=1):
        labels.append(
            Label(
                label_id=f"S2U-{index:04d}",
                path=path,
                line=line,
                scope="line",
                mechanism="version_literal",
                verdict="UNSETTLED",
                kind="natural",
                sources=("S2",),
                subject="",
                evidence=f"{path}:{line} = {text}",
            )
        )
    notes: list[str] = []
    if unsettled:
        notes.append(
            f"{len(unsettled)} line(s) in adjudicated files carry no version token S2 can read; "
            "S2 rules only on version tokens, so every other line is UNSETTLED and scores "
            "neither way, whatever reason an arm gives for flagging it"
        )
    notes.extend(capped)
    if not commits:
        notes.append(
            "this repository's history records no commit that moved a Google Ads version, so S2 "
            "adjudicates nothing here; that is a coverage gap, not a clean bill"
        )
    gone = sorted(adjudicated - set(present))
    if gone:
        notes.append(
            f"{len(gone)} file(s) a past migration touched no longer exist at the pinned SHA and "
            "are not adjudicated; renames are not followed"
        )
    return tuple(labels), tuple(sorted(present)), tuple(notes)


def write_labels(
    path: Path,
    family: Family,
    sha: str,
    labels: tuple[Label, ...],
    adjudicated: tuple[str, ...],
    eligible_files: int,
    notes: tuple[str, ...],
) -> None:
    body: dict[str, object] = {
        "schema": "hubbleops.corpus.labels/1",
        "family_id": family.family_id,
        "repo": family.repo,
        "sha": sha,
        "source": "S2",
        "kind": "natural",
        "signed": False,
        "signer": "",
        "eligible_files": eligible_files,
        "coverage_note": (
            "S2 adjudicates only files this repository has itself migrated before, found by "
            "mining its own history for a commit that replaced one Google Ads version with "
            "another. Silence elsewhere is not cleanliness. Sites are detected with a "
            "provider-neutral version token rather than the pack's own carriers, so a site the "
            "pack cannot see still counts against the arm. A version token in a comment, a "
            "docstring, a documentation file or a documentation URL is CLEAN, not actionable: "
            "prose is not a call site, and an arm that flags it is over-reporting. A version token "
            "in a documentation file is UNSETTLED rather than clean: whether a migration must "
            "also update a README or a changelog is a judgement for the owner, not for S2. A "
            "line naming the Google Ads surface with no version token at all is UNSETTLED."
        ),
        "notes": list(notes),
        "adjudicated_files": [
            {"path": entry, "sources": ["S2"], "adjudicated": True, "reason_if_not": ""}
            for entry in adjudicated
        ],
        "labels": [
            {
                "id": label.label_id,
                "path": label.path,
                "line": label.line,
                "scope": label.scope,
                "mechanism": label.mechanism,
                "verdict": label.verdict,
                "kind": label.kind,
                "sources": list(label.sources),
                "subject": label.subject,
                "evidence": label.evidence,
            }
            for label in labels
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
