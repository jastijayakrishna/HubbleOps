from __future__ import annotations

import datetime
import json
import os
from typing import Any

attestation = os.environ.get("HUBBLEOPS_INSTALL_PATH")
nonce = os.environ.get("HUBBLEOPS_INSTALL_NONCE")
if attestation and nonce:
    with open(attestation, "a", encoding="utf-8") as attestation_handle:
        attestation_handle.write(
            json.dumps({"language": "python", "nonce": nonce}, sort_keys=True) + "\n"
        )

destination = os.environ.get("HUBBLEOPS_EVENT_PATH")
if destination and os.environ.get("HUBBLEOPS_MOCK_CAPTURE") == "1":
    event: dict[str, Any] = {
        "version": "v1",
        "service": "MockService",
        "method": "Call",
        "request_text": None,
        "request_type": "mock",
        "stack": [],
        "ts": datetime.datetime.now(datetime.UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        "mode": "hook",
    }
    with open(destination, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
