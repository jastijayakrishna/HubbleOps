from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from tests.corpus.mutate import surface_files
from tests.corpus.shell import run
from tests.corpus.spec import Family, Label, normalise_path

TARGET_RELEASE = "31.2.0"
SOURCE_RELEASES = {
    "v19": "25.1.0",
    "v20": "27.0.0",
    "v21": "28.0.0",
    "v22": "29.0.0",
    "v23": "30.0.0",
    "v24": "31.0.0",
}
SUPPRESSION_MARKERS = ("# type: ignore", "# pyright: ignore", "# noqa", "# mypy: ignore")
UNRESOLVED_RULES = (
    "reportMissingImports",
    "reportMissingModuleSource",
    "reportAttributeAccessIssue",
)


@dataclass(frozen=True)
class Diagnostic:
    path: str
    line: int
    rule: str
    message: str

    def key(self) -> tuple[str, int, str]:
        return (self.path, self.line, self.rule or self.message[:60])


@dataclass(frozen=True)
class SideResult:
    installed: bool
    release: str
    versions: tuple[str, ...]
    diagnostics: tuple[Diagnostic, ...]
    detail: str


@dataclass(frozen=True)
class TypecheckResult:
    available: bool
    reason: str
    source: SideResult | None
    target: SideResult | None
    labels: tuple[Label, ...]
    coverage: tuple[dict[str, object], ...]
    notes: tuple[str, ...]


def python_of(venv: Path) -> Path:
    windows = venv / "Scripts" / "python.exe"
    return windows if windows.is_file() else venv / "bin" / "python"


def installed_versions(venv: Path) -> tuple[str, ...]:
    probe = (
        "import pkgutil, google.ads.googleads as m; "
        "print(' '.join(sorted(x.name for x in pkgutil.iter_modules(m.__path__) "
        "if x.name.startswith('v') and x.name[1:].split('_')[0].isdigit())))"
    )
    shown = run((str(python_of(venv)), "-c", probe), cwd=venv.parent, timeout=300.0)
    if not shown.ok:
        return ()
    return tuple(shown.stdout.split())


def prepare(root: Path, release: str, label: str) -> SideResult:
    venv = root / label
    made = run(("uv", "venv", "--quiet", str(venv)), cwd=root, timeout=300.0)
    if not made.ok:
        return SideResult(False, release, (), (), f"uv venv failed: {made.tail(300)}")
    installed = run(
        (
            "uv",
            "pip",
            "install",
            "--quiet",
            "--python",
            str(python_of(venv)),
            f"google-ads=={release}",
        ),
        cwd=root,
        timeout=1200.0,
    )
    if not installed.ok:
        return SideResult(False, release, (), (), f"install failed: {installed.tail(400)}")
    return SideResult(True, release, installed_versions(venv), (), "")


def _diagnostics(body: dict[str, object], repo: Path) -> tuple[Diagnostic, ...]:
    raw = body.get("generalDiagnostics")
    if not isinstance(raw, list):
        return ()
    out: list[Diagnostic] = []
    for item in cast("list[object]", raw):
        if not isinstance(item, dict):
            continue
        record = {str(k): v for k, v in cast("dict[object, object]", item).items()}
        target = str(record.get("file", ""))
        try:
            relative = normalise_path(str(Path(target).relative_to(repo)).replace("\\", "/"))
        except ValueError:
            relative = normalise_path(target)
        span = record.get("range")
        line = 0
        if isinstance(span, dict):
            start = cast("dict[object, object]", span).get("start")
            if isinstance(start, dict):
                value = cast("dict[object, object]", start).get("line")
                if isinstance(value, int) and not isinstance(value, bool):
                    line = value + 1
        out.append(
            Diagnostic(
                path=relative,
                line=line,
                rule=str(record.get("rule", "")),
                message=str(record.get("message", ""))[:200],
            )
        )
    return tuple(out)


def run_pyright(
    repo: Path, venv: Path, files: tuple[str, ...]
) -> tuple[tuple[Diagnostic, ...], str]:
    if not files:
        return ((), "no first-party python file to check")
    done = run(
        (
            "uv",
            "run",
            "pyright",
            "--outputjson",
            "--pythonpath",
            str(python_of(venv)),
            *files,
        ),
        cwd=repo,
        timeout=1800.0,
    )
    text = done.stdout.strip()
    if not text.startswith("{"):
        start = text.find("{")
        if start == -1:
            return ((), f"pyright produced no json: {done.tail(300)}")
        text = text[start:]
    body: object
    try:
        body = json.loads(text)
    except json.JSONDecodeError as error:
        return ((), f"pyright json unreadable: {error}")
    if not isinstance(body, dict):
        return ((), "pyright json was not an object")
    typed = {str(k): v for k, v in cast("dict[object, object]", body).items()}
    return (_diagnostics(typed, repo), "")


def coverage_rows(
    repo: Path,
    files: tuple[str, ...],
    source: tuple[Diagnostic, ...],
    target: tuple[Diagnostic, ...],
) -> tuple[dict[str, object], ...]:
    unresolved: dict[str, int] = {}
    for entry in source + target:
        if entry.rule in UNRESOLVED_RULES:
            unresolved[entry.path] = unresolved.get(entry.path, 0) + 1
    out: list[dict[str, object]] = []
    for path in files:
        target_file = repo / path
        try:
            text = target_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        suppressions = sum(text.count(marker) for marker in SUPPRESSION_MARKERS)
        reaches = "google.ads" in text or "google_ads" in text
        blocked = unresolved.get(path, 0)
        adjudicated = bool(text) and blocked == 0 and suppressions == 0
        out.append(
            {
                "path": path,
                "sources": ["S1"],
                "adjudicated": adjudicated,
                "checked": bool(text),
                "unresolved_imports": blocked,
                "suppressions_present": suppressions,
                "checked_namespace_reached": reaches,
                "reason_if_not": (
                    ""
                    if adjudicated
                    else (
                        f"{blocked} unresolved import diagnostic(s)"
                        if blocked
                        else f"{suppressions} suppression(s) in the file"
                        if suppressions
                        else "the checker read nothing"
                    )
                ),
            }
        )
    return tuple(out)


def build(repo: Path, family: Family, work: Path) -> TypecheckResult:
    language = family.language.strip().lower()
    if language != "python":
        return TypecheckResult(
            False,
            f"S1 is built for python only; {family.language} needs a checker this machine does "
            "not have (php requires php and composer; typescript requires a v25-carrying "
            "google-ads-api major that does not yet exist)",
            None,
            None,
            (),
            (),
            (),
        )
    release = SOURCE_RELEASES.get(family.source_api_version)
    if release is None:
        return TypecheckResult(
            False,
            f"no source SDK release is mapped for {family.source_api_version}",
            None,
            None,
            (),
            (),
            (),
        )
    work.mkdir(parents=True, exist_ok=True)
    source = prepare(work, release, "sdk-source")
    target = prepare(work, TARGET_RELEASE, "sdk-target")
    notes: list[str] = []
    if not source.installed or not target.installed:
        return TypecheckResult(
            False,
            f"could not install both SDKs: source {source.detail or 'ok'}; "
            f"target {target.detail or 'ok'}",
            source,
            target,
            (),
            (),
            (),
        )
    if family.source_api_version not in source.versions:
        notes.append(
            f"google-ads=={release} does not carry {family.source_api_version} "
            f"(it carries {', '.join(source.versions) or 'nothing'}), so the source side is not "
            "the version this family actually targets"
        )
    if "v25" not in target.versions:
        notes.append(
            f"google-ads=={TARGET_RELEASE} does not carry v25 "
            f"(it carries {', '.join(target.versions) or 'nothing'})"
        )
    files = surface_files(repo, "python")
    source_diagnostics, source_problem = run_pyright(repo, work / "sdk-source", files)
    target_diagnostics, target_problem = run_pyright(repo, work / "sdk-target", files)
    for problem in (source_problem, target_problem):
        if problem:
            notes.append(problem)
    if source_problem or target_problem:
        return TypecheckResult(False, "; ".join(notes), source, target, (), (), tuple(notes))
    before = {entry.key() for entry in source_diagnostics}
    new = [entry for entry in target_diagnostics if entry.key() not in before]
    coverage = coverage_rows(repo, files, source_diagnostics, target_diagnostics)
    adjudicated = {row["path"] for row in coverage if row["adjudicated"]}
    labels: list[Label] = []
    for index, entry in enumerate(
        sorted(new, key=lambda item: (item.path, item.line, item.rule)), start=1
    ):
        if entry.path not in adjudicated or entry.line <= 0:
            continue
        labels.append(
            Label(
                label_id=f"S1-{index:04d}",
                path=entry.path,
                line=entry.line,
                scope="line",
                mechanism="removed_field_read",
                verdict="ACTIONABLE",
                kind="natural",
                sources=("S1",),
                subject="",
                evidence=(
                    f"{entry.path}:{entry.line} type-checks clean against google-ads=={release} "
                    f"and fails against google-ads=={TARGET_RELEASE}: "
                    f"[{entry.rule or 'error'}] {entry.message}"
                ),
            )
        )
    notes.append(
        f"source google-ads=={release} carries {', '.join(source.versions) or 'no versions'}; "
        f"target google-ads=={TARGET_RELEASE} carries {', '.join(target.versions) or 'no versions'}"
    )
    notes.append(
        f"{len(source_diagnostics)} diagnostic(s) under the source SDK, "
        f"{len(target_diagnostics)} under the target, {len(new)} new under the target"
    )
    return TypecheckResult(True, "", source, target, tuple(labels), coverage, tuple(notes))
