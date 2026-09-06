from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from hubbleops.core.canonical import content_id
from hubbleops.core.errors import HubbleOpsError


class NetworkPolicyInvalid(HubbleOpsError):
    pass


@dataclass(frozen=True, order=True, slots=True)
class Destination:
    scheme: str
    host: str
    port: int

    @classmethod
    def parse(cls, value: str) -> Destination:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise NetworkPolicyInvalid(f"allowlist entry must be an absolute HTTP URL: {value}")
        if parsed.username is not None or parsed.password is not None:
            raise NetworkPolicyInvalid("allowlist authority cannot contain credentials")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise NetworkPolicyInvalid("allowlist entry names only scheme, host, and port")
        host = parsed.hostname.casefold().rstrip(".")
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as error:
            raise NetworkPolicyInvalid(f"allowlist port is invalid: {value}") from error
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as error:
            raise NetworkPolicyInvalid(f"allowlist hostname is invalid: {value}") from error
        if not 1 <= port <= 65535:
            raise NetworkPolicyInvalid(f"allowlist port is out of range: {port}")
        return cls(parsed.scheme, host, port)

    def mapping(self) -> dict[str, object]:
        return {"scheme": self.scheme, "host": self.host, "port": self.port}


@dataclass(frozen=True, slots=True)
class NetworkPolicy:
    destinations: tuple[Destination, ...] = ()

    @classmethod
    def from_values(cls, values: tuple[str, ...]) -> NetworkPolicy:
        return cls(tuple(sorted(set(Destination.parse(value) for value in values))))

    def allows(self, scheme: str, host: str, port: int) -> bool:
        offered = Destination(scheme.casefold(), host.casefold().rstrip("."), port)
        return offered in self.destinations

    def validate_addresses(self, host: str, addresses: tuple[str, ...]) -> None:
        if not addresses:
            raise NetworkPolicyInvalid(f"destination did not resolve: {host}")
        for address in addresses:
            parsed = ipaddress.ip_address(address)
            if _forbidden(parsed):
                raise NetworkPolicyInvalid(f"destination resolves to forbidden address: {address}")

    def resolve(self, destination: Destination) -> tuple[str, ...]:
        try:
            results = socket.getaddrinfo(
                destination.host, destination.port, type=socket.SOCK_STREAM
            )
        except OSError as error:
            raise NetworkPolicyInvalid(
                f"destination did not resolve: {destination.host}: {error}"
            ) from error
        addresses = tuple(sorted({str(item[4][0]) for item in results}))
        self.validate_addresses(destination.host, addresses)
        return addresses

    def fingerprint(self) -> str:
        return content_id([item.mapping() for item in self.destinations])


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    sensitive = ("authorization", "cookie", "set-cookie", "x-api-key", "api-key")
    return {
        key.lower(): "<redacted>" if key.lower() in sensitive else value
        for key, value in sorted(headers.items(), key=lambda item: item[0].lower())
    }


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


__all__ = [
    "Destination",
    "NetworkPolicy",
    "NetworkPolicyInvalid",
    "redact_headers",
]
