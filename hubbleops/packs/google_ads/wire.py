from __future__ import annotations

import re
from collections.abc import Mapping

from hubbleops.packs._protocol import WireObservation, WireResult

GRPC = re.compile(
    r"^/?google\.ads\.googleads\.(v[0-9]+)\.services\."
    r"([A-Za-z][A-Za-z0-9]*Service)/([A-Za-z][A-Za-z0-9]*)$"
)
REST_GOOGLE_ADS = re.compile(
    r"^/?(v[0-9]+)/customers/[^/?]+/googleAds:(searchStream|search|mutate)(?:\?.*)?$"
)


class GoogleAdsWireSignature:
    def parse(self, path: str, headers: Mapping[str, str]) -> WireResult:
        normalized = {key.lower(): value for key, value in headers.items()}
        targets = [candidate for candidate in (path, normalized.get(":path", "")) if candidate]
        if not targets:
            return WireResult(
                code="UNKNOWN_WIRE_SIGNATURE",
                observation=None,
                reason=(
                    "request target is missing; client metadata cannot identify endpoint version"
                ),
            )
        parsed = tuple(self._parse_target(candidate) for candidate in targets)
        failures = [reason for observation, reason in parsed if observation is None]
        if failures:
            return WireResult(
                code="UNKNOWN_WIRE_SIGNATURE",
                observation=None,
                reason="; ".join(sorted(set(failures))),
            )
        observations = {observation for observation, _ in parsed if observation is not None}
        if len(observations) != 1:
            return WireResult(
                code="UNKNOWN_WIRE_SIGNATURE",
                observation=None,
                reason="request target and :path header disagree",
            )
        return WireResult(
            code="MATCH", observation=observations.pop(), reason="request target matched"
        )

    def _parse_target(self, path: str) -> tuple[WireObservation | None, str]:
        match = GRPC.fullmatch(path)
        if match:
            return WireObservation(service=match[2], method=match[3], version=match[1]), ""
        match = REST_GOOGLE_ADS.fullmatch(path)
        if match:
            methods = {"search": "Search", "searchStream": "SearchStream", "mutate": "Mutate"}
            return (
                WireObservation(
                    service="GoogleAdsService",
                    method=methods[match[2]],
                    version=match[1],
                ),
                "",
            )
        return None, f"unrecognized Google Ads request target: {path}"


WIRE_SIGNATURE = GoogleAdsWireSignature()
