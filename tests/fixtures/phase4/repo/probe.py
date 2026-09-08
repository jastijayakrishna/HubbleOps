from __future__ import annotations

import urllib.error
import urllib.request


def observed_wrapper() -> None:
    request = urllib.request.Request(
        "https://googleads.googleapis.com/v22/customers/1/googleAds:search",
        data=b"SELECT campaign.id FROM campaign",
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=3)
    except (OSError, urllib.error.URLError):
        pass


observed_wrapper()
