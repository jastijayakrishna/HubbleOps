from __future__ import annotations

import base64
import ipaddress
import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path

from mitmproxy import ctx, http

MAX_BODY = 65_536
MAX_HEADERS = 128
MAX_HEADER_VALUE = 8_192
SENSITIVE = frozenset({"api-key", "authorization", "cookie", "set-cookie", "x-api-key"})


class CaptureAddon:
    def load(self, loader: object) -> None:
        loader.add_option("hops_output", str, "", "neutral JSONL capture output")
        loader.add_option("hops_allowlist", str, "W10=", "exact destination allowlist")
        loader.add_option("hops_fixture", str, "e30=", "test-only fixture destination")

    def request(self, flow: http.HTTPFlow) -> None:
        request = flow.request
        scheme = request.scheme.casefold()
        host = request.host.casefold().rstrip(".")
        port = request.port
        allowed, policy_reason, address = self._allowed(scheme, host, port)
        body = request.raw_content or b""
        opaque = len(body) > MAX_BODY
        body_reason = "BODY_TOO_LARGE" if opaque else None
        try:
            request_text = None if opaque else body.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            opaque = True
            request_text = None
            body_reason = "BODY_OPAQUE"
        if len(request.headers) > MAX_HEADERS or any(
            len(value) > MAX_HEADER_VALUE for value in request.headers.values()
        ):
            allowed = False
            policy_reason = "HEADER_LIMIT"
        if len(body) > MAX_BODY:
            allowed = False
            policy_reason = "BODY_TOO_LARGE"
        if allowed and address is not None:
            flow.server_conn.address = (address, port)
        headers = {
            key.casefold(): (
                "<redacted>" if key.casefold() in SENSITIVE else value[:MAX_HEADER_VALUE]
            )
            for key, value in list(request.headers.items())[:MAX_HEADERS]
        }
        record = {
            "body_reason": body_reason,
            "headers": dict(sorted(headers.items())),
            "host": host,
            "path": request.path,
            "policy": "ALLOW" if allowed else "DENY",
            "policy_reason": policy_reason,
            "port": port,
            "request_text": request_text,
            "request_type": headers.get("content-type", "http"),
            "scheme": scheme,
            "stack": [],
            "ts": datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        }
        destination = Path(ctx.options.hops_output)
        descriptor = os.open(destination, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, _canonical(record) + b"\n")
        finally:
            os.close(descriptor)
        if not allowed:
            flow.response = http.Response.make(403, b"destination denied by capture policy")

    def _allowed(self, scheme: str, host: str, port: int) -> tuple[bool, str, str | None]:
        offered = {"host": host, "port": port, "scheme": scheme}
        allowlist = _decoded(ctx.options.hops_allowlist)
        if offered not in allowlist:
            return False, "DESTINATION_NOT_ALLOWLISTED", None
        try:
            results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            addresses = {str(item[4][0]) for item in results}
        except OSError:
            return False, "DESTINATION_DNS_FAILED", None
        if not addresses:
            return False, "DESTINATION_DNS_FAILED", None
        fixture = _decoded(ctx.options.hops_fixture)
        fixture_match = (
            isinstance(fixture, dict)
            and fixture.get("container_id")
            and fixture.get("network_id")
            and fixture.get("hostname") == host
            and fixture.get("port") == port
            and set(addresses) == {fixture.get("address")}
        )
        if fixture_match:
            return True, "FIXTURE_DESTINATION", sorted(addresses)[0]
        if any(_forbidden(ipaddress.ip_address(value)) for value in addresses):
            return False, "DESTINATION_ADDRESS_FORBIDDEN", None
        return True, "DESTINATION_ALLOWLISTED", sorted(addresses)[0]


def _forbidden(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_unspecified,
            address.is_reserved,
        )
    )


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _decoded(value: str) -> object:
    return json.loads(base64.urlsafe_b64decode(value.encode("ascii")))


addons = [CaptureAddon()]
