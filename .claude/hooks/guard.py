from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
PACKAGE = REPO / "hubbleops"

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
SENTINEL_ROOT = "packages/hubbleops-sentinel/"
SCHEMA_ROOT = "hubbleops/core/schemas/"
REPAIR_FORBIDDEN = (
    "hubbleops/verify/",
    "hubbleops/sandbox/verifier_image.py",
    ".hubbleops/decisions.yml",
)
COMMENT_EXCEPTIONS = ("# noqa", "# type: ignore", "# pragma: no cover")
PROTOCOL_FILE = "hubbleops/packs/_protocol.py"
MARKDOWN_ALWAYS_ALLOWED = ("dev/", "docs/FAILURE_ATLAS.md", ".claude/")

STAGE_ALL = (
    re.compile(r"\bgit\s+add\s+(-A\b|--all\b|\.(\s|$))"),
    re.compile(r"\bgit\s+commit\b[^\n]*\s-[a-zA-Z]*a"),
)
ATTRIBUTION = ("co-authored-by", "generated with claude code", "🤖")
EMOJI = re.compile("[\U0001f000-\U0001faff☀-➿]")
BRANCH_PHASE = re.compile(r"^phase-(\d+)-")

WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def main() -> int:
    payload = _payload()
    event = payload.get("hook_event_name", "")
    if event == "PreToolUse":
        return _pre_tool_use(payload)
    if event == "PostToolUse":
        return _post_tool_use(payload)
    if event == "Stop":
        return _stop()
    return 0


def _payload() -> dict[str, object]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _tool_input(payload: dict[str, object]) -> dict[str, object]:
    value = payload.get("tool_input")
    return value if isinstance(value, dict) else {}


def _pre_tool_use(payload: dict[str, object]) -> int:
    tool = str(payload.get("tool_name", ""))
    data = _tool_input(payload)
    if tool == "Bash":
        return _check_command(str(data.get("command", "")))
    if tool in WRITE_TOOLS:
        target = str(data.get("file_path", ""))
        if not target:
            return 0
        return _check_write(target, _written_text(tool, data))
    return 0


def _written_text(tool: str, data: dict[str, object]) -> str:
    if tool == "Write":
        return str(data.get("content", ""))
    if tool == "Edit":
        return str(data.get("new_string", ""))
    if tool == "MultiEdit":
        edits = data.get("edits")
        if isinstance(edits, list):
            return "\n".join(
                str(edit.get("new_string", "")) for edit in edits if isinstance(edit, dict)
            )
    if tool == "NotebookEdit":
        return str(data.get("new_source", ""))
    return ""


def _check_command(command: str) -> int:
    for pattern in STAGE_ALL:
        if pattern.search(command):
            return _block(
                "Stage explicit paths. Run `git status --short`, then `git add <path>` for each "
                "file you actually changed."
            )
    lowered = command.lower()
    if "git commit" in lowered or "gh pr" in lowered:
        for marker in ATTRIBUTION:
            if marker in lowered:
                return _block(
                    f"No AI attribution in commit messages or PR bodies: found {marker!r}."
                )
        if EMOJI.search(command):
            return _block("No emoji footer in commit messages or PR bodies.")
    return 0


def _check_write(target: str, text: str) -> int:
    relative = _relative(target)
    phase = _phase()

    if relative.startswith(SCHEMA_ROOT) and (phase is None or phase > 1):
        return _block(
            f"{relative} is a frozen schema. Propose the change in dev/proposals.md instead of "
            "editing it in place."
        )

    if _role() == "repair":
        for forbidden in REPAIR_FORBIDDEN:
            if relative.startswith(forbidden):
                return _block(
                    f"The repair role cannot edit {relative}: repair never touches the authority "
                    "that judges it."
                )

    if relative.startswith(SENTINEL_ROOT) and re.search(
        r"\bimport\s+hubbleops\b|\bfrom\s+hubbleops\b", text
    ):
        return _block(
            "packages/hubbleops-sentinel must never import hubbleops.*; its output is "
            "observational evidence, never a verdict."
        )

    if _in_generic_layer(relative):
        if re.search(
            r"\bfrom\s+hubbleops\.packs\b|\bimport\s+hubbleops\.packs\b|\bfrom\s+packs\b", text
        ):
            return _block(
                f"{relative} is a generic layer; it receives pack parts as parameters and never "
                "imports packs/."
            )
        leak = _provider_leak(text)
        if leak is not None:
            return _block(
                f"{relative} is a generic layer and must contain no provider name; found {leak!r}."
            )

    if relative.endswith(".md") and not _markdown_allowed(relative):
        return _block(
            f"{relative} is a new .md file that no phase's DEFINITION OF DONE names. Evidence goes "
            "in the terminal; running state goes in dev/context.md and dev/tasks.md."
        )

    if relative.endswith(".py") and relative != PROTOCOL_FILE:
        comment = _first_comment(text)
        if comment is not None:
            return _block(
                f"{relative} contains the comment {comment!r}. Rename the thing instead. The only "
                "exceptions are JSON Schema descriptions, the packs/_protocol.py docstrings, a "
                "tool-required noqa, and a one-line external constraint."
            )
    return 0


def _post_tool_use(payload: dict[str, object]) -> int:
    data = _tool_input(payload)
    target = str(data.get("file_path", ""))
    if not target.endswith(".py"):
        return 0
    path = Path(target)
    if not path.is_file():
        return 0
    for command in (["ruff", "format", str(path)], ["ruff", "check", "--fix", str(path)]):
        subprocess.run(["uv", "run", *command], cwd=REPO, capture_output=True, check=False)
    return 0


def _stop() -> int:
    suite = subprocess.run(
        ["uv", "run", "pytest", "tests/unit", "tests/property", "-q"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if suite.returncode != 0:
        tail = "\n".join((suite.stdout + suite.stderr).strip().splitlines()[-25:])
        return _block(f"The suite is red, so the session is not complete:\n{tail}")
    if _session_changed_the_build() and not _context_is_current():
        return _block(
            "dev/context.md not updated this session. Write where the build stands, what is "
            "blocking, and every decision a later phase must not re-litigate, including each "
            "answered OPEN QUESTION from dev/plan.md, which the next phase overwrites."
        )
    return 0


def _session_changed_the_build() -> bool:
    dirty = _git("status", "--porcelain")
    commits = _git("log", "main..HEAD", "--oneline")
    return bool(dirty.strip() or commits.strip())


def _context_is_current() -> bool:
    staged = _git("status", "--porcelain", "dev/context.md")
    committed = _git("log", "main..HEAD", "--name-only", "--", "dev/context.md")
    return bool(staged.strip() or committed.strip())


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
    )
    return completed.stdout if completed.returncode == 0 else ""


def _phase() -> int | None:
    branch = _git("rev-parse", "--abbrev-ref", "HEAD").strip()
    match = BRANCH_PHASE.match(branch)
    return int(match.group(1)) if match else None


def _role() -> str:
    return os.environ.get("HUBBLEOPS_ROLE", "")


def _relative(target: str) -> str:
    try:
        return Path(target).resolve().relative_to(REPO).as_posix()
    except ValueError:
        return Path(target).as_posix()


def _in_generic_layer(relative: str) -> bool:
    return any(relative.startswith(f"hubbleops/{layer}/") for layer in GENERIC_LAYERS)


def _provider_leak(text: str) -> str | None:
    names_file = REPO / "tests" / "unit" / "provider_names.txt"
    if not names_file.is_file():
        return None
    lowered = text.lower()
    for line in names_file.read_text(encoding="utf-8").splitlines():
        name = line.strip().lower()
        if name and not name.startswith("#") and name in lowered:
            return name
    return None


def _markdown_allowed(relative: str) -> bool:
    if (REPO / relative).exists():
        return True
    if any(relative.startswith(prefix) for prefix in MARKDOWN_ALWAYS_ALLOWED):
        return True
    prompts = REPO / "prompts" / "phases"
    if not prompts.is_dir():
        return False
    return any(
        relative in prompt.read_text(encoding="utf-8", errors="replace")
        for prompt in prompts.glob("*.md")
    )


def _first_comment(text: str) -> str | None:
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return _first_comment_by_line(text)
    for token in tokens:
        if token.type == tokenize.COMMENT and not _allowed_comment(token.string):
            return token.string.strip()
    return None


def _first_comment_by_line(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and not _allowed_comment(stripped):
            return stripped
    return None


def _allowed_comment(comment: str) -> bool:
    body = comment.strip()
    return any(body.startswith(marker) for marker in COMMENT_EXCEPTIONS)


def _block(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
