from __future__ import annotations

import http.client


def reporting_gateway_search(query: str) -> None:
    connection = http.client.HTTPConnection("127.0.0.1", 9, timeout=0.01)
    try:
        connection.request(
            "POST",
            "/google.ads.googleads.v25.services.GoogleAdsService/Search",
            body=query,
        )
    except OSError:
        pass


def daily_campaign_report() -> None:
    reporting_gateway_search("SELECT campaign.id FROM campaign")


daily_campaign_report()
