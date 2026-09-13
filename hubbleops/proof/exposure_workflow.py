from __future__ import annotations

import re
from pathlib import Path

import hubbleops
from hubbleops.proof.memory import MemoryInvalid
from hubbleops.store import write_atomic

WORKFLOW_NAME = "hubbleops-exposure.yml"
IDENTIFIER = re.compile(r"[A-Za-z0-9._-]+")
SOURCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._=<>!~,+/:@-]*")


def default_source() -> str:
    return f"hubbleops=={hubbleops.__version__}"


def workflow(provider: str, target: str, source: str) -> bytes:
    for label, value in (("provider", provider), ("target", target)):
        if IDENTIFIER.fullmatch(value) is None:
            raise MemoryInvalid(f"{label} cannot be embedded safely in the workflow")
    if SOURCE.fullmatch(source) is None:
        raise MemoryInvalid("source cannot be embedded safely in the workflow")
    hops = f'uvx --from "{source}" hops'
    text = f"""name: HubbleOps exposure map
on:
  pull_request:
permissions:
  contents: read
jobs:
  exposure:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: {hops} scan . --pack {provider} --target {target}
      - run: {hops} exposure --pack {provider} --target {target} | tee hubbleops-exposure.txt
      - if: always()
        uses: actions/upload-artifact@v4
        with:
          name: hubbleops-exposure-${{{{ github.sha }}}}
          path: |
            hubbleops-exposure.txt
            .hubbleops/artifacts/**/ledger.json
"""
    return text.encode("utf-8")


def install(repository: Path, provider: str, target: str, source: str) -> Path:
    path = repository.resolve() / ".github" / "workflows" / WORKFLOW_NAME
    write_atomic(path, workflow(provider, target, source))
    return path


__all__ = ["default_source", "install", "workflow"]
