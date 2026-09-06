from __future__ import annotations

import re
from collections.abc import Mapping

GRPC = re.compile(
    r"^/?google\.ads\.googleads\.(v[0-9]+)\.services\."
    r"([A-Za-z][A-Za-z0-9]*Service)/([A-Za-z][A-Za-z0-9]*)$"
)
REST = re.compile(r"^/?(v[0-9]+)/customers/[^/?]+/googleAds:(searchStream|search|mutate)(?:\?.*)?$")


def parse(path: str, headers: Mapping[str, str]) -> dict[str, str] | None:
    normalized = {key.lower(): value for key, value in headers.items()}
    targets = [candidate for candidate in (path, normalized.get(":path", "")) if candidate]
    if not targets:
        return None
    results = [_target(candidate) for candidate in targets]
    if any(result is None for result in results):
        return None
    unique = {(item["service"], item["method"], item["version"]) for item in results if item}
    if len(unique) != 1:
        return None
    service, method, version = unique.pop()
    return {"service": service, "method": method, "version": version}


def _target(path: str) -> dict[str, str] | None:
    match = GRPC.fullmatch(path)
    if match:
        return {"service": match[2], "method": match[3], "version": match[1]}
    match = REST.fullmatch(path)
    if match:
        methods = {"search": "Search", "searchStream": "SearchStream", "mutate": "Mutate"}
        return {
            "service": "GoogleAdsService",
            "method": methods[match[2]],
            "version": match[1],
        }
    return None
