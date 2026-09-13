from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / ".claude" / "hooks" / "guard.py"


def invoke(path: Path) -> subprocess.CompletedProcess[str]:
    payload = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(path), "content": "{}"},
        }
    )
    return subprocess.run(
        [sys.executable, str(HOOK)],
        cwd=ROOT,
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )


def test_hook_allows_only_frozen_schemas_named_by_accepted_proposals() -> None:
    accepted = invoke(ROOT / "hubbleops" / "core" / "schemas" / "proof_scope.json")
    unaccepted = invoke(ROOT / "hubbleops" / "core" / "schemas" / "evidence.json")
    assert accepted.returncode == 0
    assert unaccepted.returncode == 2
    assert "frozen schema" in unaccepted.stderr
