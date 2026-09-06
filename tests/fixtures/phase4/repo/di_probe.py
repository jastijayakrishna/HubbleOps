from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable

ENDPOINT = "https://googleads.googleapis.com/v22/customers/1/googleAds:search"


def build_transport() -> Callable[[bytes], None]:
    def send(payload: bytes) -> None:
        request = urllib.request.Request(ENDPOINT, data=payload, method="POST")
        try:
            urllib.request.urlopen(request, timeout=3)
        except (OSError, urllib.error.URLError):
            pass

    return send


class ReportingGateway:
    def __init__(self, transport: Callable[[bytes], None]) -> None:
        self._transport = transport

    def search(self, query: str) -> None:
        self._transport(query.encode("utf-8"))


class ReportingService:
    def __init__(self, gateway: ReportingGateway) -> None:
        self._gateway = gateway

    def daily_campaign_report(self) -> None:
        self._gateway.search("SELECT campaign.id FROM campaign")


def main() -> None:
    ReportingService(ReportingGateway(build_transport())).daily_campaign_report()


main()
